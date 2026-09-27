# 适配器体系：如何支持任意书站

## 分层

```
cli.py                 参数解析（四组：源 / 版面 / 译文 / 工程）
  └ pipeline.py        编排：BuildOptions → run()
      ├ adapters/      站点识别 + 合并成单页 HTML
      ├ mirror.py      静态资源与页面镜像
      ├ rewrite.py     目录对齐 + 链接重写 + 目录页生成
      ├ theme.py       主题补丁
      ├ style.py       打印 CSS
      ├ render.py      Playwright 渲染导出
      └ verify.py      PDF 校验
```

`BookIR`（`ir.py`）是所有适配器的统一产出——**适配器只负责"把站点变成 IR"，
下游不知道站点类型**：

```python
@dataclass
class BookIR:
    base_url: str
    source_kind: str            # mdbook / mkdocs / docusaurus / gitbook / generic
    full_html: str              # 合并后的单页 HTML
    container_selector: str     # 正文容器选择器
    toc: list[TocEntry]         # 有序目录 (path, title, level)
    chapters: list[Chapter]     # 章节锚点 (anchor_id, title, level)
    theme_patches: list[tuple[str, str]]
    assets: list[str]
    notes: list[Note]
    print_path: str = "print.html"
    merged_from: list[str] = field(default_factory=list)
    theme_required: bool = True   # 非 mdBook 站点为 False
```

新增站点类型 = 新增一个适配器文件 + 在 `registry.py::ADAPTERS` 注册一行。
**不需要改任何下游代码。**

## 探测与打分

```python
SiteAdapter Protocol:
    confidence(probe: SiteProbe) -> int      # 0~100
    build(site_dir, base, print_path, fetcher, probe=None) -> BookIR
```

`probe_site()` 先抓这些证据：`print.html` 是否存在及体积、`<meta name="generator">`、
`toc-*.js`、`sitemap.xml`、首页内链、`~gitbook` 资源路径。

`registry.detect(probe, forced, report)`：
- `forced`（`--adapter X`）非空 → 直接用，跳过打分（并记一条 note 说明是强制）。
- 否则全部适配器打分，取最高；**最高分 < `MIN_CONFIDENCE`(=20) → `adapter_unknown` error**，
  报告里列出每个候选的得分与判据，便于人工判断。

这是 v1 缺陷 B（泛化）的核心修补点：v1 只有一条 mdBook 路径，
非 mdBook 站会"成功"产出一本内容残缺的 PDF，**没有任何信号**。

## 各适配器识别特征

| 适配器 | confidence 主要依据 | 合并入口 |
|---|---|---|
| `mdbook` | 有 `print.html` 且体积 > 首页 ×2（+40）；内联 `default_light_theme`（+30）；有 `toc-*.js`（+20） | `print.html` |
| `mkdocs` | `<meta name="generator" content="mkdocs">`（+50）；`mkdocs` 主题 class（+20） | nav / sitemap |
| `docusaurus` | `<meta name="generator" content="docusaurus">`（+50）；`__docusaurus` 资源（+20） | sidebar 数据 |
| `gitbook` | `~gitbook` 路径（+40）；`gitbook` class（+20） | 目录逐页 |
| `generic` | 恒返回较小常数 | sitemap / 首页内链 |

## 多页合并（`MultiPageAdapter` 基类）

mdBook 之外全走这条路。基类共享逻辑：

### 1. 页面选择

```python
nav_links(html) -> list[str]        # 从 nav / sidebar DOM 取
pages_from_nav(site_dir)            # 优先
pages_from_sitemap(site_dir)        # 次选
pick_pages(...)                     # 合并去重 + 排序，保持站点原序
```

顺序很重要——它决定 PDF 的章节顺序，也就是书签顺序。

### 2. 稳定锚点 `slug()`

每页注入 `<h1 id="pg-<slug>">`，原 `id` 被剥掉（避免与合并页里其它 id 冲突）。

- **`slug()` 必须保留扩展名**：`a/one.html` 与目录 `a/one/` 去扩展名后会撞成同一个 `a-one`。
  保留后分别是 `a-one-html` 与 `a-one`。
- `_ensure_h1()` 若页面没有 h1，就按标题注入一个。
  注意 `re.sub` 剥 id 后可能留下 `id="pg-a" >` 这种带尾随空格的标签——
  要 `re.sub(r"\s+", " ", RE_ID.sub("", attrs)).strip()` 再按需补空格。

### 3. 跨页链接重写 `_rewrite_rel()`

把页面间的相对链接映射到合并页的锚点；同时把资源路径（css/js/img）改写到镜像目录。
映射表就是 `toc` + `chapters`。

### 4. 容器

多页合并后正文容器通常是站点主内容区（`main` / `.md-content` / `.theme-doc-markdown`）。
`find_container_span()` 返回选择器与内层偏移，供后续"只取容器内部"使用。

## 镜像的两个必处理情况

`mirror.py::crawl()`：

- **同域越界**：`netloc` 相同不代表同站。站点位于 `/my-book/` 子路径时，
  `https://host/blog/xxx` 的 netloc 完全一样。
  **必须按路径前缀过滤**，否则会顺着博客链接爬出几百个无关页面。
- **无扩展名目录链接**：`href="00-guide"` 要落盘成 `00-guide/index.html`，
  否则 `open()` 会撞到已存在的同名目录并抛 `PermissionError`。

`follow_html` 开关控制是否跟随其它 HTML：
mdBook 关（`print.html` 里已全有），多页站开。

## 加新适配器的步骤

1. 在 `adapters/` 新建 `mysite.py`，实现 `confidence()` 与 `build()`。
   若是多页站，继承 `MultiPageAdapter` 只写页面选择逻辑。
2. `registry.py::ADAPTERS` 追加一行。
3. `tests/duality.py` 加两条判据：**正样本**（真站特征 → 选中我的适配器）
   与**负样本**（伪造特征 → 不得选中）。
4. 跑 `tests/run_tests.py` 与 `tests/duality.py`，全绿才算完成。
5. 跑 `~/.agent-skills/sync.py` 分发。
