# -*- coding: utf-8 -*-
"""Docusaurus 适配器。

Docusaurus 也没有合并页。侧栏在 `<nav class="menu">`（含 `<aside>`）里，
文档页集中在 `/docs/` 前缀下；拿不到侧栏时用 sitemap 按 `/docs/` 过滤。
"""
from __future__ import annotations

from .base import SiteProbe
from .multipage import MultiPageAdapter, pick_pages


class DocusaurusAdapter(MultiPageAdapter):
    name = "docusaurus"
    container_candidates = ["article .theme-doc-markdown", "article", ".markdown", "main",
                            "#content"]

    def confidence(self, p: SiteProbe) -> int:
        score = 0
        if p.has_docusaurus_data:
            score += 60
        if p.has_print:
            score -= 30
        return max(0, min(100, score))

    def entry_urls(self, base: str, print_path: str = "print.html") -> list[str]:
        return [""]

    def select_pages(self, site_dir: str, base: str, home_html: str) -> list[str]:
        pages = pick_pages(home_html, base, self._fetcher, min_pages=3)
        docs = [p for p in pages if p.startswith(("docs/", "./docs/"))]
        return docs or pages
