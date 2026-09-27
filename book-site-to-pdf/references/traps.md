# 四个坑的完整机制与验证代码

本文件是 `SKILL.md`「四个必踩的坑」的展开。每条包含：机制 → 症状 → 修法 → 实测数字 → 验证方法。

---

## 坑 1：主题由 JS 运行时写入，静态改 class 无效

### 机制

mdBook 把主题写在 `print.html` 顶部的内联脚本里：

```js
const default_light_theme = "navy";
const default_dark_theme  = "navy";
...
html.classList.remove('navy')
html.classList.add(theme);            // ← 覆盖掉你静态写好的 class="light"
```

`theme` 来自 `localStorage['mdbook-theme']`，回落到 `default_*_theme`。
所以**在 HTML 里改 `class` 是无效的**，两处都要干预。

### 症状

极具迷惑性：**白底 + 浅灰正文几乎看不见**，但 h1 却是清晰的蓝色。
原因是 h1 内部是 `<a class="header">`，颜色来自 `print.css` 的链接色 `#4183c4`，
与主题变量无关。只截图首页会以为"能看清"。

### 修法（四处，缺一不可）

```python
html = html.replace('const default_light_theme = "navy";', 'const default_light_theme = "light";')
html = html.replace('const default_dark_theme = "navy";',  'const default_dark_theme = "light";')
html = html.replace("html.classList.remove('navy')",
                    "html.classList.remove('navy','coal','ayu','rust','light')")
html = html.replace("html.classList.add(theme);", "html.classList.add('light');")
```

对应实现 `scripts/book2pdf/adapters/mdbook.py::build_theme_patches()`。
`theme.py::apply()` 会统计每处命中数；**若四处全未命中且 `required=True` → `theme_no_hit` error**
（静默成功是 v1 的缺陷 A 之一）。
非 mdBook 站点 `required=False`，不做补丁也不误报（判据 `theme_not_required_no_false_alarm`）。

另外用 `context.add_init_script()` 清掉 `localStorage['mdbook-theme']`，否则旧偏好会盖过默认值。

### 出 PDF 一律转浅色

深色 2000 页 PDF 既费墨又难读，且 Mermaid 深色图会落在浅底上。
顺手关掉的面板：`#mdbook-sidebar`、`#mdbook-menu-bar`、`#mdbook-search-wrapper`、
`#mdbook-help-container`、`.sidetoc/.pagetoc`；
并把 `#mdbook-page-wrapper { transform: none }`（屏幕态被 sidebar 平移过）。

### 验证

**渲染前断言**，比生成完再看图便宜得多：

```js
"" + document.documentElement.classList.contains("light")
  && !/navy|coal|ayu|rust/.test(document.documentElement.className)
```

---

## 坑 2：双重分页 → 每章后多一张空白页

### 机制

`print.html` 里每个章节前，作者已注入占位符：

```html
<div style="break-before: page; page-break-before: always;"></div>
<h1 id="...">…</h1>
```

若你再补一条 `h1 { page-break-before: always }`，两者**不会合并**——
实测每章后面凭空多一张只印了页脚的空页。

### 修法

**二选一**，推荐删 div、由 CSS 统一分页（CSS 更可控、豁免规则更好写）：

```python
html = re.sub(r'\s*<div style="break-before: page; page-break-before: always;"></div>\s*', '\n', html)
```

```css
h1 { page-break-before: always; break-before: page; }
h1#pdf-toc-heading, .pdf-toc h1 { page-break-before: avoid !important; break-before: avoid !important; }
.pdf-toc { break-after: page; }   /* 目录与正文之间只需一次分页 */
```

实现：`rewrite.py::remove_page_break_divs()`；CSS 见 `style.py::build_css()`。

### 实测

| 阶段 | 页数 | 空白页 |
|---|---|---|
| 双重分页 | 2035 | 180 |
| 只删 div | 1914 | 45 |
| + 坑 3 修完 | 1825 | 0 |

---

## 坑 3：超高的 Mermaid 图被裁切（内容丢失）

### 机制

Mermaid 在浏览器里按屏幕宽度渲染，`print.css` 又给了 `pre { page-break-inside: avoid }`。
一张比 A4 可用高度还高的图进不了任何一页：Chrome 既无法遵守 `avoid`，
就在它前面留一张空白页，**并把图从页面底部直接切掉**——底部节点永久消失。

### 修法

```css
.mermaid svg { max-width: 100% !important; max-height: 880px !important;
               width: auto !important; height: auto !important; }
pre.mermaid  { padding: 0 !important; background: transparent !important;
               border: 0 !important; page-break-inside: avoid !important; }
```

- **上限不要写死**。由纸张可用高度算：`style.py::auto_max_diagram_height(paper)`。
  A4：`round(1009.13 * 0.872)` = **880**。Letter / A5 各自不同。
  （注意用 `round` 而非 `int`——差 1px 会破坏"与 v1 等值"的回归判据。）
- 必须同时给 `width/height: auto`：SVG 带 `viewBox` 时有固有宽高比，
  浏览器会按比例缩放而非压扁（实测 218 张图失真率 0）。
- 只压不撑：天然高度 < 上限的图不受影响。

### 实测

空白页 45 → **0**；绘图触底（裁切）页数 → **0**；总页数 1914 → 1825。

### 验证（别只靠眼睛看几张图）

```python
for i in range(page_count):
    for d in page.get_drawings():
        if d["rect"].y1 > page.rect.height - 2 and d["rect"].height > 4:
            ...   # 触底 = 疑似裁切
```

实现：`verify.py::collect()` 的 `clipped_pages`；判据 `clipped`。

---

## 坑 4：`outline=True` 单独用不产生书签

Chromium 的 `printToPDF` 里，文档大纲从**无障碍结构树**来：

```python
page.pdf(path=out, format="A4", print_background=True,
         outline=True, tagged=True,          # ← 少了 tagged 就得到 0 条书签
         margin={"top": "14mm", "bottom": "16mm", "left": "12mm", "right": "12mm"},
         display_header_footer=True,
         footer_template='…<span class="pageNumber"></span>/<span class="totalPages"></span>…')
```

- 代价是体积变大（17.8MB → 44MB，结构树很占地方），换来 4212 条 5 级书签。
- `tagged=True` 需要 Chrome/Edge ≳ 128。
- 用 `channel="msedge"` 直接复用系统 Edge，**免 `playwright install`**。

实现：`render.py::render()`。老版本 Playwright 不认 `tagged` 参数会抛 `TypeError`，
代码里有回退分支（降级为无书签并记 `no_bookmarks`，而不是崩溃）。

---

## 附：Chromium 命名目标（陷阱中的陷阱）

```python
for p in d:
    for l in p.get_links():
        l["kind"]      # 内部链接是 LINK_NAMED(=4)，不是 LINK_GOTO(=1)
        l["nameddest"] # 目标名
```

**内部链接的 kind 不是 `LINK_GOTO`。** Chromium 用命名目标（named destination）写页内跳转。
只统计 `LINK_GOTO` 会得到「页内跳转 0 个」的假警报，进而误判链接全坏。
真正判据是 `l["nameddest"]` 能通过 `doc.resolve_link()` 解析出目标页。

实现：`verify.py::collect()`。

---

## 附：几个易被忽略的排版细节

- 代码块换行不裁切：`pre, pre code { white-space: pre-wrap; overflow-wrap: anywhere }`
- 表格不被容器裁掉：`.table-wrapper { overflow: visible !important }`
- 英文标识符不从中间断开：`th, td { word-break: normal; overflow-wrap: break-word }`
  —— 用 `word-break: break-word` 会把 `allow` 断成 `allo w`。
