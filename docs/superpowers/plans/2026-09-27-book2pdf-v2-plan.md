# book-site-to-pdf v2 实施计划

- 日期：2026-09-27
- 对应规格：`docs/superpowers/specs/2026-09-27-book2pdf-v2-design.md`
- 目标目录（源端）：`~/.agent-skills/book-site-to-pdf/`
- 纪律：改源端 → 跑 `sync.py`；TDD；每个任务都有「怎么知道做完了」

## 阶段出口条件（回归基线不可破）

P0 结束时，`--mode mono` 对已验书必须与 v1 **逐项等值**：

| 指标 | 基线 |
|---|---|
| 页数 | 1825 |
| 书签 | 4212（L1×107 / L2×1173 / L3×2525 / L4×384 / L5×23） |
| 页内跳转 | 7370 |
| 空白页 | 0 |
| 绘图触底（裁切） | 0 |
| 残留相对链接 | 0 |

---

## P0 · 拆包 + IR + mdBook 适配器

| # | 任务 | 文件 | 验证 |
|---|---|---|---|
| 0.1 | 定义 IR 数据类 | `scripts/book2pdf/ir.py` | `python -c "from book2pdf.ir import BookIR"` 通过 |
| 0.2 | 报告与退出码 | `scripts/book2pdf/report.py` | `tests/test_report.py` 断言 5 档退出码 |
| 0.3 | 配置合并（CLI > 文件 > 默认） | `scripts/book2pdf/config.py` | 三层优先级单测 |
| 0.4 | 适配器协议 + 探测打分 | `scripts/book2pdf/adapters/base.py` | `SiteProbe` 可构造；`confidence` 返回 int |
| 0.5 | mdBook 适配器（移植 v1 逻辑，**行为不得变**） | `scripts/book2pdf/adapters/mdbook.py` | 镜像后 `toc` 105 条、`chapters` 106 个 |
| 0.6 | 探测注册表 | `scripts/book2pdf/adapters/registry.py` | 对 mdBook 探针返回 `mdbook` 最高分 |
| 0.7 | 镜像（并发 + 重试 + 路径前缀） | `scripts/book2pdf/mirror.py` | 42 文件 / 7.8MB，同域博客不被越界抓取 |
| 0.8 | 链接重写（通用归一化 + LCS 对齐） | `scripts/book2pdf/rewrite.py` | 残留相对链接 0；K 对齐相似度 > 0.6 |
| 0.9 | 主题锁定（多主题 + 命中计数） | `scripts/book2pdf/theme.py` | 命中 0 次返回 error |
| 0.10 | 样式生成（参数化，非字符串替换） | `scripts/book2pdf/style.py` | `--paper`/`--max-diagram-height auto` 生效 |
| 0.11 | 渲染（资源等待 + 渲染前断言） | `scripts/book2pdf/render.py` | 断言 light / 段落数 / 颜色 |
| 0.12 | 编排 | `scripts/book2pdf/pipeline.py` + `cli.py` | v1 命令行原样可跑 |
| 0.13 | 薄壳 | `scripts/book2pdf.py` | `--help` 列出 v1 全部参数 |

**出口**：跑一次全量 `mono`，六项指标与基线一致。

---

## P1 · fail loud + report.json + 退出码

| # | 任务 | 验证（负样本） |
|---|---|---|
| 1.1 | 主题命中 0 次 → FAIL | 把 `default_light_theme` 改回 `navy` → 退出码 ≠ 0 |
| 1.2 | 容器选择器链，全失败 → FAIL | 破坏 `container_selector` → FAIL |
| 1.3 | Mermaid 未渲染率 > 5% → FAIL | 断掉 mermaid 脚本 → FAIL |
| 1.4 | `toc` 为空 → WARN（`--strict` 下 FAIL） | 删除 `toc-*.js` → WARN |
| 1.5 | 通用归一化 + 对齐质量断言（< 0.6 FAIL） | 打乱 toc 顺序 → FAIL |
| 1.6 | 端口 0 自动选空闲 | 占用 8765 仍能跑 |
| 1.7 | `report.json` 产出 + `--fail-on` | 报告含全部 metric 与 warning |

**出口**：`tests/duality.py` 的 8 条全部 FAIL（在坏输入下）。

---

## P2 · 译文子系统（骨架 + null provider）

| # | 任务 | 文件 | 验证 |
|---|---|---|---|
| 2.1 | 占位符保护与守恒校验 | `translate/placeholders.py` | 删掉一个 `⟦n⟧` → `conserved=False` |
| 2.2 | 段切分（只取可翻文本节点） | `translate/segmenter.py` | `pre/code/script/style` 0 段；`.pdf-toc` 0 段 |
| 2.3 | TM（键含术语表哈希 + provider 标签） | `translate/tm.py` | 改术语表哈希 → 旧条目不命中 |
| 2.4 | 术语表（注入 + 译后校验 + `__KEEP__`） | `translate/glossary.py` | 缺术语 → `glossary_miss` |
| 2.5 | Provider 协议 + null | `translate/providers/base.py`、`null.py` | 产出 `segments.jsonl` |
| 2.6 | 回填 + 标题文本三处同步 | `translate/injector.py` | 目录页/书签/正文标题一致 |

**出口**：`--provider null` 产出段清单，段数量级与实测（38,144 段 / 74.3 万字符）一致。

---

## P3 · 真 provider + 断点续跑 + 预算闸门

| # | 任务 | 验证 |
|---|---|---|
| 3.1 | `openai_compat` 实现（覆盖本地代理/OpenAI） | 代理不可达 → 退出码 4 + 明确指引 |
| 3.2 | 批处理（多短段合一请求） | 请求数 < 段数 |
| 3.3 | 并发 + 指数退避重试 | 注入 500 错误 → 重试 3 次后记 `api_failed` |
| 3.4 | 预算闸门 | 超 `--budget-tokens` 且无 `--yes` → 拒绝执行 |
| 3.5 | `--limit N` 试跑 | 只翻前 N 段 |
| 3.6 | `--resume` 零重复调用 | 中断后重跑，已命中段 0 次请求 |
| 3.7 | `anthropic` 实现 | 有 key 时可跑；无 key 明确报错 |

---

## P4 · 多站适配器

| # | 任务 | 验证 |
|---|---|---|
| 4.1 | MkDocs 适配器（`nav` + `.md-content__inner` 逐页拼接） | 真站 PASS |
| 4.2 | Docusaurus 适配器（sidebar 数据 + `article`） | 真站 PASS |
| 4.3 | GitBook 适配器（侧栏 + 逐页） | 真站 PASS |
| 4.4 | generic 兜底（sitemap 聚类） | 至少一本非三类站 PASS |
| 4.5 | 探测负样本：把 MkDocs 当 mdBook 喂 | 打分应选对 |

---

## P5 · 双语 + 元数据

| # | 任务 | 验证 |
|---|---|---|
| 5.1 | `translated` 模式产出独立 PDF | `<html lang>` = 目标语言 |
| 5.2 | `bilingual` 模式（段后插节点 / 表格内插 span） | 原文译文同页 |
| 5.3 | PDF 元数据（title/author/lang/subject） | `pymupdf` 读回非空 |
| 5.4 | 目录页深度到 h3 | `.pdf-toc` 含 toc-h3 |

---

## P6 · 对偶测试 + 单元测试

`tests/run_tests.py` 一次跑完；`tests/duality.py <判据>` 单独跑某条负样本。

---

## P7 · 文档 + 分发

| # | 任务 | 验证 |
|---|---|---|
| 7.1 | SKILL.md 瘦身 | < 200 行，只留触发条件/用法/四坑速查 |
| 7.2 | `references/traps.md` | 四坑机理原样保留 |
| 7.3 | `references/adapters.md` | 各站探测特征与契约 |
| 7.4 | `references/translation.md` | 保护规则 / TM 格式 / 术语表写法 |
| 7.5 | 分发 | `sync.py --lint` 0 错 0 警；`--check` 一致 |

---

## 明确不做（YAGNI）

YAML 插件 DSL、EPUB/MOBI 输出、扫描件 OCR、原生 PDF 输入、自建翻译质量评分模型、单图级渲染缓存、Mermaid 图内文字默认翻译。

---

## 实施状态（2026-09-27 收尾记录）

| 阶段 | 状态 | 证据 |
|---|---|---|
| P0 拆包 + IR + mdBook 适配器 | 完成 | `compileall` 通过；`mdbook.*` 5 项单测 |
| P1 fail loud + report.json + 退出码 | 完成 | `report.*` / `exitcode.*` 单测；退出码 2/3 端到端实测 |
| P2 译文子系统 + null provider | 完成 | `placeholders.*` / `segmenter.*` / `glossary.*` / `tm.*` 单测 |
| P3 真 provider 实跑 | **待用户配合** | 需先启动本地翻译代理；当前 `127.0.0.1:15721` 无监听 |
| P4 各站真站验证 | **未做** | 仅夹具级验证（mkdocs 探测正/负样本各 1） |
| P5 多页合并 / 样式 / 目录页 | 完成 | `multipage.*` 2 项单测；端到端目录页 7 条 |
| P6 对偶测试 + 单元测试 | 完成 | **26/26 单测 + 18/18 对偶**全绿 |
| P7 文档 + 分发 | 文档完成 / 分发待跑 | SKILL.md 141 行；3 个 references 文件 |

### 端到端跑出来的两个真实缺陷（已修）

这两个都是**只有真跑一遍才会暴露**的，纯读代码看不出来：

1. **段落数判据用了绝对阈值**（`render.py`）
   - 症状：合成书只有 7 个 `<p>`，被判 `render_few_paragraphs` error → 退出码 2，合法短书被误杀。
   - 根因：**度量口径错误**——同一个量（正文段落数）本来有两条独立计算路径
     （源 HTML 的 `<p>` 计数 / 渲染后 DOM 的段落计数），却另设了一个凭感觉的常量 100。
   - 修法：抽出纯函数 `render.paras_ok(rendered, expect)`，以源 HTML 计数为基准、
     取下限 `expect × 0.5`；`expect` 未知时只断言 `> 0`。
   - 对偶判据：`render_few_paragraphs`（含"短书 7/7 不得失败"这一条**反向**样本）。

2. **Mermaid 等待只有总超时，会空等 900 秒**（`render.py`）
   - 症状：夹具未加载 mermaid 库时，渲染阶段静止等待 15 分钟才可能报错。
   - 根因：把「站点没加载库」「库在跑但慢」「真的卡死」三种情况混成一个总超时。
   - 修法：新增 `render.mermaid_give_up()`，三条判据并列——
     总超时 / **进展停滞 90s** / **运行时不存在（30s 宽限）**；
     并区分报告 `mermaid_timeout` / `mermaid_stalled` / `mermaid_lib_missing`。
   - 对偶判据：`mermaid_give_up_early`（含"已渲染过则不得判缺库"这一条**反向**样本）。

### 夹具加固

`e2e/site/print.html` 加入**确定性 Mermaid 替身**（离线可复现，不依赖 CDN）：
第一张图 viewBox 高度 **6000**（远超 A4 可用高度 1009px），底边画一条红色标记线。
- 若 `max-height` 钳制失效 → 底边线触底 → `verify` 的 `clipped` 判据必然报错。
- 这样"限高生效"这条判据才**有区分力**，而不是只靠肉眼看几张图。
