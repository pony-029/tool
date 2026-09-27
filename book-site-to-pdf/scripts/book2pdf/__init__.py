# -*- coding: utf-8 -*-
"""book2pdf：书站 → 可导航 PDF（可选译文）。

分层：
    adapters/   站点适配器（mdBook / MkDocs / GitBook / Docusaurus / generic）
    rewrite     跨章链接 → 页内锚点（NW 序列对齐）
    theme       主题锁定（命中 0 次即 FAIL）
    style       参数化打印 CSS
    translate   译文子系统（段级 / 占位符守恒 / TM / 术语表）
    render      Playwright + Edge 渲染导出
    verify      阈值化校验
"""
from __future__ import annotations

__version__ = "2.0.0"

from .ir import BookIR, Chapter, Note, TocEntry
from .report import (EXIT_BUILD_FAIL, EXIT_ENV, EXIT_NAMES, EXIT_PASS,
                     EXIT_VERIFY_FAIL, EXIT_WARN, BuildAbort, Report)

__all__ = [
    "__version__", "BookIR", "Chapter", "Note", "TocEntry",
    "Report", "BuildAbort",
    "EXIT_PASS", "EXIT_WARN", "EXIT_BUILD_FAIL", "EXIT_VERIFY_FAIL", "EXIT_ENV",
    "EXIT_NAMES",
]
