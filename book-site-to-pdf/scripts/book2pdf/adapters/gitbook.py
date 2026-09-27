# -*- coding: utf-8 -*-
"""GitBook 适配器。

GitBook（含 legacy 与新版）的侧栏在 `<aside>` 里，正文在 `main` / `.page-inner`。
新版 GitBook 是 SPA，静态 HTML 里可能没有正文——这时要靠 sitemap + 单页 HTML 兜底，
若两者都拿不到内容，明确报错而不是产出一本空书。
"""
from __future__ import annotations

from .base import SiteProbe
from .multipage import MultiPageAdapter, pick_pages


class GitBookAdapter(MultiPageAdapter):
    name = "gitbook"
    container_candidates = [".page-inner", "main", ".markdown-section", "article", "#content"]

    def confidence(self, p: SiteProbe) -> int:
        score = 0
        if p.has_gitbook:
            score += 50
        if p.has_print:
            score -= 20
        if p.has_docusaurus_data or p.has_mkdocs_meta:
            score -= 40
        return max(0, min(100, score))

    def entry_urls(self, base: str, print_path: str = "print.html") -> list[str]:
        return [""]

    def select_pages(self, site_dir: str, base: str, home_html: str) -> list[str]:
        return pick_pages(home_html, base, self._fetcher, min_pages=3)
