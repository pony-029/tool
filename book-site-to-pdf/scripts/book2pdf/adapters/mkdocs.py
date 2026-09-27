# -*- coding: utf-8 -*-
"""MkDocs 适配器（含 Material 主题）。

MkDocs 没有合并页，`print.html` 不存在。入口是侧栏 `<nav>` 的顺序 + 每页的
`.md-content__inner` 正文。
"""
from __future__ import annotations

from .base import SiteProbe
from .multipage import MultiPageAdapter, pick_pages


class MkDocsAdapter(MultiPageAdapter):
    name = "mkdocs"
    container_candidates = [".md-content__inner", ".md-content", "article", "main", "#content"]

    def confidence(self, p: SiteProbe) -> int:
        score = 0
        if p.has_mkdocs_meta:
            score += 60
        if p.has_mkdocs_body:
            score += 25
        if not p.has_print:
            score += 10
        if p.has_docusaurus_data:
            score -= 40
        return max(0, min(100, score))

    def entry_urls(self, base: str, print_path: str = "print.html") -> list[str]:
        return [""]

    def select_pages(self, site_dir: str, base: str, home_html: str) -> list[str]:
        return pick_pages(home_html, base, self._fetcher, min_pages=3)
