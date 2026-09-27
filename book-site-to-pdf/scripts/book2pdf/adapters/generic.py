# -*- coding: utf-8 -*-
"""通用兜底适配器：不认得的站点也尽量导出来。

发现顺序：侧栏导航 → sitemap → 首页全部同站链接。
三者都拿不到时**明确报错**，绝不产出一本空书。
"""
from __future__ import annotations

import urllib.parse

from .base import SiteProbe
from .multipage import MultiPageAdapter, RE_A, SKIP_PREFIX, _same_site_page, pick_pages


def pages_from_home_links(home_html: str, base: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for m in RE_A.finditer(home_html):
        href = m.group(1).strip()
        if href.startswith(SKIP_PREFIX) or "${" in href:
            continue
        rel = _same_site_page(urllib.parse.urljoin(base, href), base)
        if rel and rel not in seen:
            seen.add(rel)
            out.append(rel)
    return out


class GenericAdapter(MultiPageAdapter):
    name = "generic"
    container_candidates = ["main", "article", ".content", "#content", ".post-content",
                            ".markdown-body", "body"]

    def confidence(self, p: SiteProbe) -> int:
        if p.sitemap_urls or p.home_html:
            return 10
        return 0

    def entry_urls(self, base: str, print_path: str = "print.html") -> list[str]:
        return [""]

    def select_pages(self, site_dir: str, base: str, home_html: str) -> list[str]:
        pages = pick_pages(home_html, base, self._fetcher, min_pages=3)
        if len(pages) < 3:
            for rel in pages_from_home_links(home_html, base):
                if rel not in pages:
                    pages.append(rel)
        return pages
