# tool

把在线书籍 / 文档站点导出为**单份高质量、可导航的 PDF**——书签大纲可跳转、目录与正文链接在 PDF 内可点击；
并可对正文 / 表格 / 图内文字做**多语言翻译**，产出译文版或中英对照双语版。

面向 mdBook、MkDocs、Docusaurus、GitBook 及任意静态文档站，站点类型自动探测。

## 目录结构

```
book-site-to-pdf/           导出与翻译工具（可独立使用）
├─ SKILL.md                 入口文档：触发条件、参数、四坑速查、排障表
├─ scripts/book2pdf.py      命令行入口
├─ scripts/book2pdf/        核心实现（31 个模块，见下）
├─ tests/                   26 项单元测试 + 20 条对偶测试判据
└─ references/              四坑机理 / 站点适配 / 译文子系统

docs/superpowers/           设计规格与实施计划
├─ specs/2026-09-27-*.md    设计规格（13 章：缺陷体检、架构、数据流、验收判据）
└─ plans/2026-09-27-*.md    P0–P7 分阶段实施计划与状态记录

e2e/site/                   端到端合成书站夹具（离线可复现，不依赖 CDN）
```

### 核心实现分层

```
book2pdf/
├─ ir.py report.py config.py            中间表示 · 结构化报告 · 三层配置
├─ style.py theme.py rewrite.py mirror.py
├─ render.py verify.py pipeline.py cli.py
├─ adapters/    base mdbook multipage mkdocs docusaurus gitbook generic registry
└─ translate/   placeholders segmenter tm glossary providers/{null,openai_compat,anthropic}
```

## 快速开始

```bash
python book-site-to-pdf/scripts/book2pdf.py \
    --base https://example.github.io/my-book/ \
    --out  ./我的书.pdf
```

一条命令走完：适配器识别 → 镜像 → 链接重写 →（可选）翻译 → 渲染 → 导出 → 校验 → 落盘 `report.json`。

常用参数：

```bash
# 版面
--paper A4|Letter|A5      # 超高图的高度上限按纸张可用高度自动推算
--toc-depth 3             # 目录页收录到几级标题

# 译文（默认关闭）
--translate --lang zh-CN --to en
--mode replace|bilingual  # 只出译文 / 中英对照
--glossary glossary.yaml  # 术语表：固定译法 + 禁译词（__KEEP__）
--tm tm.jsonl             # 翻译记忆：缓存 + 断点续跑
--provider null|openai|anthropic
--base-url http://127.0.0.1:15721/v1

# 工程
--strict                  # 把 warn 也升级为失败
--fail-on CODE[,CODE]
--keep --workdir DIR
```

**退出码语义固定**，出问题就退出，绝不静默产出坏 PDF：

| 码 | 含义 |
|---|---|
| 0 | PASS |
| 1 | WARN（`--strict` 下升级为失败） |
| 2 | BUILD_FAIL（抓取 / 适配 / 渲染失败） |
| 3 | VERIFY_FAIL（产出了 PDF，但校验判据不达标） |
| 4 | ENV（缺依赖、浏览器不可用、参数非法） |

排障入口是 `report.json`：每条记录带 `code` / `severity` / `where` / `stage`。

## 测试

```bash
PY=~/.workbuddy/binaries/python/versions/3.13.12/python.exe

$PY book-site-to-pdf/tests/run_tests.py     # 单元测试
$PY book-site-to-pdf/tests/duality.py       # 对偶测试（负样本判据）
$PY book-site-to-pdf/tests/duality.py --list
```

**对偶测试是硬要求**：每条验收判据都必须配一个坏输入证明它会 FAIL。
只验证「通过路径」的判据不算判据。本仓库的开发过程中，这条纪律抓到过
若干判据本身没有区分力的情况（例如把不变式写成不等式而非结构等式）。

## 已验证的事实

四个必踩的坑（完整机理见 `book-site-to-pdf/references/traps.md`）：

1. **主题由 JS 运行时写入** —— 静态改 `class="light"` 会被内联脚本覆盖。
2. **双重分页** —— 源站的 `break-before:page` 占位 div 与 CSS `page-break-before` 叠加，
   每章后多一张空白页。
3. **超高 Mermaid 图被裁切**（内容丢失，非排版问题）—— 必须限高并配 `width/height:auto` 保比例。
4. **`outline=True` 必须配 `tagged=True`** —— 少了 `tagged` 得到 0 条书签。

另有两个容易误判的度量陷阱：

- Chromium 的页内跳转写成**命名目标**（pymupdf `kind=4 LINK_NAMED`），
  只统计 `LINK_GOTO` 会得到「页内跳转 0 个」的假警报。
- 中文 UTF-8 下 1 字符 = 3 字节，**`wc -c` 是陷阱**；成本必须同时给字符口径与 token 口径。

## 上游关系

`book-site-to-pdf/` 是本地技能库 `~/.agent-skills/book-site-to-pdf/` 的**同步快照**，
供分享与复现使用。技能的唯一真实来源是源端目录；改动请在源端进行，
再通过该仓库的同步脚本分发到各 Agent 产品，不要直接改这里的副本。

## 许可

MIT
