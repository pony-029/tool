---
name: book-site-to-pdf
description: 把在线书籍 / 文档站点（mdBook、MkDocs、Docusaurus、GitBook 及任意静态文档站）导出为单份高质量 PDF，保证目录与正文链接在 PDF 内可点击跳转、书签大纲可导航；并可对正文 / 表格 / 图内文字做多语言翻译，产出译文版或中英对照双语版。当用户给出「书站 / 文档站」链接并要求「做成 PDF / 导出全书 / 生成电子书 / 离线文档 / 带目录的 PDF / 翻译成中文（或英文）」时使用，或提到 print.html、mdBook、书签导航、可点击目录、中英对照、译文版 PDF、术语表时使用。也用于排查「PDF 里图表被裁切 / 每章后多出空白页 / 白底浅字看不清 / 书签为空 / 链接点击无反应 / 翻译后内联标签串位」这类导出缺陷。
license: MIT
metadata:
  author: pony-Feng
  version: "2.0"
  title_zh: 书站导出可导航 PDF + 多语言译文（分层适配器 + fail loud + 对偶测试）
  title_en: Web book sites to navigable PDF with translation (layered adapters, fail-loud, duality tests)
agent_created: true
---

# book-site-to-pdf — 书站 → 可导航 PDF（+ 译文）

核心思路：**不要逐页爬章节**。mdBook 类站点自带 `print.html`，全书已合并成单页；
其余站点（MkDocs / Docusaurus / GitBook）用各自的导航数据把页面合并成等价单页。
拿到合并 HTML 后，剩下的全是「让它变成合格 PDF」的工程问题。

```bash
python ~/.agent-skills/book-site-to-pdf/scripts/book2pdf.py \
    --base https://example.github.io/my-book/ \
    --out  "E:/output/我的书.pdf"
```

一条命令走完：适配器识别 → 镜像 → 链接重写 → （可选）翻译 → 渲染 → 导出 → 校验 → 落盘 `report.json`。
**任何一步出问题都会以非零退出码 + 结构化报告显式失败，不会静默产出坏 PDF。**

## 站点适配：先识别，再构建

`--adapter auto`（默认）对站点打分，取最高分；分数低于阈值直接报 `adapter_unknown` 并列出各候选得分，
不会「硬着头皮用错的适配器」。可用 `--adapter mdbook|mkdocs|docusaurus|gitbook|generic` 强制覆盖。

| 站点类型 | 识别特征 | 合并方式 |
|---|---|---|
| mdBook | 有 `print.html`，内联 `default_light_theme` | 直接抓 `print.html` 单页 |
| MkDocs (Material) | `<meta name="generator" content="mkdocs">` | 按 `nav` / `sitemap.xml` 逐页合并 |
| Docusaurus | `<meta name="generator" content="docusaurus">` | 按 sidebar 数据逐页合并 |
| GitBook | 有 `~gitbook` 资源路径 | 按目录逐页合并 |
| generic | 兜底 | `sitemap.xml` 或首页 `<a href>` 内链 |

多页站点的合并由 `MultiPageAdapter` 基类统一处理：注入稳定锚点（`pg-<slug>`）、
重写跨页相对链接与资源路径、把各页 `<h1>` 提升为章节边界。细节见 `references/adapters.md`。

## 关键参数

```bash
# 版面
--paper A4|Letter|A5        # 默认 A4；影响超高图的 max-height 上限，自动按可用高度算
--toc-depth 3               # 目录页收录到几级标题
--no-toc-page               # 不生成独立目录页（PDF 书签仍然有）

# 译文（默认关闭，开了才走）
--translate --lang zh-CN --to en          # 源语言 / 目标语言
--mode replace|bilingual                  # 只出译文 / 中英对照
--glossary path/to/glossary.yaml          # 术语表（强制固定译法 + 禁译词）
--tm path/to/tm.jsonl                     # 翻译记忆（缓存 + 断点续跑）
--provider null|openai|anthropic          # null = 只走假翻译，用于连调
--base-url http://127.0.0.1:15721/v1      # OpenAI 兼容端点
--translate-mermaid                       # 默认关（图内文字仅占 1.4%，性价比低）

# 工程
--strict                    # 把 warn 也升级为失败（默认 error 才失败）
--fail-on CODE[,CODE]       # 只对指定 code 失败，其余降级为警告
--keep                      # 保留中间产物目录，便于排查
--workdir DIR               # 中间产物目录
```

优先级：**CLI > 配置文件 > 环境变量 > 默认**。配置文件含 `api_key` / `token` 等敏感字段时会被**拒绝加载**
（凭据只走环境变量或 CLI，不落盘）。

## 四个必踩的坑（速查）

1. **主题是 JS 运行时写入的** —— 静态改 `class="light"` 会被内联脚本覆盖。
   症状：白底 + 浅灰正文几乎看不见，h1 却是清晰蓝色。必须改四处
   （`default_light_theme` / `default_dark_theme` / `classList.remove` / `classList.add`）。
2. **双重分页 → 每章后多一张空白页** —— 源站已有 `break-before:page` 占位 div 时，
   再加 `h1{page-break-before}` 不会合并。二选一，推荐**删 div、由 CSS 统一分页**。
3. **超高 Mermaid 图被裁切（内容丢失）** —— 必须给 `max-height` + `width/height:auto` 保比例，
   否则底部节点永久消失。上限由纸张可用高度算出，不要写死。
4. **`outline=True` 必须配 `tagged=True`** —— 少了 `tagged` 得到 0 条书签；
   代价是体积明显变大（结构树占地方）。需要 Chrome/Edge ≳ 128。

完整机制、CSS 片段、实测数字与验证代码见 `references/traps.md`。

## fail loud：出问题就退出，别产出坏 PDF

所有异常走统一报告通道（`report.json` + stderr），退出码语义固定：

| 码 | 含义 |
|---|---|
| 0 | PASS |
| 1 | WARN（有告警，`--strict` 下升级为失败） |
| 2 | BUILD_FAIL（抓取 / 适配 / 渲染阶段失败） |
| 3 | VERIFY_FAIL（产出了 PDF，但校验判据不达标） |
| 4 | ENV（缺依赖、浏览器不可用、参数非法） |

**排障入口就是 `report.json`**：每条 `note` 带 `code` / `severity` / `where` / `stage`。
常见 code 与处置：

| code | 含义 | 处置 |
|---|---|---|
| `adapter_unknown` | 无适配器过阈值 | 用 `--adapter generic` 或指定具体适配器 |
| `container_not_found` | 找不到正文容器 | 站点结构特殊，检查 `--adapter`；报告含探测器证据 |
| `theme_no_hit` | 主题补丁命中 0 处 | 非 mdBook 站请用 `--adapter` 显式声明以跳过；或站点改了脚本 |
| `theme_not_required_no_false_alarm` | 非 mdBook 站不做主题补丁 | 正常，无需处理 |
| `toc_empty` | 目录为空 | 站点无 `toc-*.js` / 无 nav；确认是否需要目录页 |
| `mermaid_lib_missing` | 页面根本没加载 Mermaid 运行时 | 站点把库放在被过滤的资源里；检查镜像的路径前缀过滤 |
| `mermaid_stalled` | 渲染停滞 90s 无进展 | 超大图或网络慢；已提前停止等待，不必等满总超时 |
| `mermaid_unrendered` | 有图未渲染完成比例超阈值 | 见上两行；确认网络能取到 mermaid 资源 |
| `render_few_paragraphs` | 渲染后段落数远少于源 HTML | 容器选择器不对；判据是**相对**源 `<p>` 数，短书不会被误杀 |
| `align_low_quality` | 目录与标题对齐质量低 | 侧栏改名过多；检查 `--toc-depth` |
| `blank_pages` / `clipped` / `no_bookmarks` | 校验不达标 | 见 `references/traps.md` |
| `placeholder_broken` | 翻译后内联标签不守恒 | 该段回退原文并计入失败；换 provider 或减小 batch |
| `residual_links` | 残留相对链接 | 未映射清单在报告里，补适配器的链接重写规则 |

## 译文子系统

- **占位符保护**：先把 `<code>`/`<a>`/`<strong>` 等内联标签替换为 `⟦n⟧` 再送翻译，
  回填时递归还原；**守恒硬校验**：译文中占位符集合必须与原文完全一致，否则该段回退原文并记 `placeholder_broken`。
- **TM（翻译记忆）**：键 = `sha256(源文 ∥ 目标语言 ∥ 术语表哈希 ∥ provider 标识)`。
  换术语表或换 provider 会自动失效对应条目，**不会拿旧译注新书**。
  实测价值在**断点续跑与幂等**，不在省钱（去重后字符只省 9.8%）。
- **术语表**：`__KEEP__` 表示禁译；命中禁译词时跳过该段，避免专有名词被翻坏。
- **越界防护**：默认只翻正文段落、列表、表格单元格；不翻代码块 / 行内 code / URL。
- 成本是**双口径**：字符口径与 token 口径都要看。中文 UTF-8 下 1 字符 = 3 字节，
  **`wc -c` 对中文是陷阱**。细节见 `references/translation.md`。

## 校验（别跳，四类产物缺陷都在这里被抓到）

```bash
python ~/.agent-skills/book-site-to-pdf/scripts/book2pdf.py --base <url> --out <pdf> ; echo "EXIT=$?"
# 已有 PDF 单独复检
python ~/.agent-skills/book-site-to-pdf/scripts/verify.py <pdf>
```

- **内部链接的 kind 不是 `LINK_GOTO`，而是 `LINK_NAMED`(=4)** —— Chromium 用命名目标写页内跳转。
  只统计 `LINK_GOTO` 会得到「页内跳转 0 个」的假警报。
- 空白页判据：`len(page.get_text().strip()) < 15`（只剩页脚数字）。
- 裁切判据：`get_drawings()` 里矩形触底（`y1 > 页高 - 2` 且高度 > 4）。
- 主题判据：**渲染前**在页面里断言 `"light" in classList` 且正文计算色为深色——比生成完再看图便宜得多。
- **核验结果一律看退出码，不要 `grep` 文本**。

## 开发与自测

```bash
PY=~/.workbuddy/binaries/python/versions/3.13.12/python.exe
$PY ~/.agent-skills/book-site-to-pdf/tests/run_tests.py     # 单元测试（26 项）
$PY ~/.agent-skills/book-site-to-pdf/tests/duality.py       # 对偶测试（16 条负样本判据）
```

**对偶测试是硬要求**：每条验收判据都必须配一个坏输入证明它会 FAIL，
只验证"通过路径"的判据不算判据（写代码前先跑 `duality.py` 看它是否真能失败）。
改过本技能任何文件后，**必须**跑 `~/.agent-skills/sync.py` 分发到各产品目录。

## 交付前自检

- [ ] 退出码 0，`report.json` 无 error 级 `note`
- [ ] `d.get_toc()` 非空，层级覆盖到 h3
- [ ] 目录页链接可点，抽查 1 条落到正确页
- [ ] 空白页 0、裁切 0、残留相对链接 0
- [ ] 正文深色字 / 浅色底（或用户明确要求深色）
- [ ] 译文模式下：占位符守恒 0 失败、术语表命中项全部按术语表译法
- [ ] 代码块换行不裁切；表格不被容器裁掉；英文标识符不从中间断开
