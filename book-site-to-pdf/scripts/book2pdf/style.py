# -*- coding: utf-8 -*-
"""打印样式生成器。

v1 用 `EXTRA_CSS.replace("__MAXH__", str(max_h))` 这种字符串替换注入数值——
脆弱且不可读。v2 改为**程序生成**：值经参数传入，纸张与图高联动。
"""
from __future__ import annotations

# 纸张尺寸（mm）
PAPERS = {"A4": (210.0, 297.0), "Letter": (215.9, 279.4), "A3": (297.0, 420.0)}

# 页边距（mm）——与 render.py 的 pdf(margin=...) 必须一致
MARGIN = {"top": 14.0, "bottom": 16.0, "left": 12.0, "right": 12.0}

MM_TO_PX = 96.0 / 25.4


def usable_height_px(paper: str) -> float:
    _w, h = PAPERS.get(paper.upper(), PAPERS["A4"])
    return (h - MARGIN["top"] - MARGIN["bottom"]) * MM_TO_PX


def auto_max_diagram_height(paper: str) -> int:
    """图高上限 = 可用高度 × 0.872。

    A4 实测可用高度 ≈ 1009.13px → 880px（与 v1 手工取值一致，保证回归等值）。
    必须同时给 width/height: auto，否则带 viewBox 的 SVG 会被压扁。
    """
    return round(usable_height_px(paper) * 0.872)


def build_css(*, max_diagram_height: int, toc_depth: int = 2, footer: bool = True,
              mirrored_hosts: tuple[str, ...] = ()) -> str:
    parts = [_BASE]

    parts.append(
        "#mdbook-help-container, #mdbook-help-popup, .sidetoc, .pagetoc, #mdbook-theme-list,\n"
        ".theme-popup, #mdbook-sidebar, #mdbook-sidebar-resize-handle, #mdbook-menu-bar,\n"
        "#mdbook-menu-bar-hover-placeholder, #mdbook-search-wrapper, .nav-chapters,\n"
        ".mobile-nav-chapters, .buttons, .fa, nav, aside { display: none !important; }"
    )
    parts.append(_LAYOUT)
    parts.append(_TEXT)

    # 表格 / 代码
    parts.append(
        ".table-wrapper { overflow: visible !important; max-height: none !important; }\n"
        "table { max-width: 100% !important; page-break-inside: auto !important; font-size: 0.92em; }\n"
        "tr { page-break-inside: avoid !important; break-inside: avoid !important; }\n"
        "th, td { word-break: normal !important; overflow-wrap: break-word; hyphens: none; }\n"
        "pre { overflow: visible !important; max-width: 100% !important; }\n"
        "pre, pre code { white-space: pre-wrap !important; word-break: normal !important; "
        "overflow-wrap: anywhere; }"
    )

    # 图表：超高会被裁切（内容丢失），必须限高 + 保比例
    parts.append(
        ".mermaid, .vega-chart-container, .vega-wrapper canvas, .vega-wrapper svg "
        "{ max-width: 100% !important; overflow: visible !important; }\n"
        "pre.mermaid { padding: 0 !important; background: transparent !important; "
        "border: 0 !important; page-break-inside: avoid !important; break-inside: avoid !important; }\n"
        f".mermaid svg {{ max-width: 100% !important; max-height: {max_diagram_height}px !important; "
        "width: auto !important; height: auto !important; }"
    )

    # 分页：源站的分页占位 div 已被移除，这里只由 CSS 统一分页（坑 2）
    parts.append(
        "h1 { page-break-before: always; break-before: page; }\n"
        "h1#pdf-toc-heading, .pdf-toc h1 { page-break-before: avoid !important; "
        "break-before: avoid !important; }\n"
        ".pdf-toc { margin-bottom: 12px; break-after: page; }"
    )

    # 目录页
    toc_css = [
        ".pdf-toc-cols { column-count: 2; column-gap: 20px; }",
        ".pdf-toc ul { list-style: none !important; padding-left: 0 !important; margin: 0 !important; }",
        ".pdf-toc li { margin: 0 !important; line-height: 1.4; break-inside: avoid; }",
        ".pdf-toc a { text-decoration: none !important; }",
        ".pdf-toc .toc-h1 { font-weight: 700; margin-top: 7px !important; font-size: 10.5px; }",
        ".pdf-toc .toc-h1 > a { color: #0b3d70 !important; }",
        ".pdf-toc .toc-h2 { padding-left: 12px; font-size: 9.5px; }",
        ".pdf-toc .toc-h2 > a { color: #444444 !important; }",
    ]
    if toc_depth >= 3:
        toc_css += [
            ".pdf-toc .toc-h3 { padding-left: 24px; font-size: 9px; }",
            ".pdf-toc .toc-h3 > a { color: #666666 !important; }",
        ]
    parts.append("\n".join(toc_css))

    parts.append(_FONTS)
    parts.append("#MathJax_Message, .MathJax_Message { display: none !important; }")

    if not footer:
        parts.append("@page { margin-bottom: 8mm; }")

    return "\n".join(parts)


_BASE = """:root { color-scheme: light; }"""

_LAYOUT = (
    "html, body, #mdbook-body-container { background: #ffffff !important; }\n"
    "#mdbook-page-wrapper, .page-wrapper { transform: none !important; "
    "margin-inline-start: 0 !important; margin: 0 !important; padding: 0 !important; }\n"
    "#mdbook-content { max-width: none !important; margin: 0 !important; padding: 0 !important; }\n"
    "main, .content main, article { max-width: 100% !important; margin: 0 !important; "
    "padding: 0 !important; }"
)

_TEXT = (
    "main, article, p, li, td, th, blockquote, dl, dt, dd { color: #1a1a1a !important; }\n"
    "h1, h2, h3, h4, h5, h6 { color: #111111 !important; }\n"
    "h1 a.header, h2 a.header, h3 a.header, h4 a.header, h5 a.header, h6 a.header "
    "{ color: #111111 !important; }\n"
    "a, a:visited { color: #0b5cad !important; text-decoration: none !important; "
    "word-break: break-all; }"
)

_FONTS = (
    'body, main { font-family: "Segoe UI", "Microsoft YaHei", "PingFang SC", '
    '"Noto Sans CJK SC", "Source Han Sans SC", sans-serif !important; }\n'
    'code, pre, kbd { font-family: "Cascadia Mono", Consolas, "Courier New", monospace !important; }'
)
