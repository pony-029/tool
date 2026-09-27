# -*- coding: utf-8 -*-
"""mdBook 适配器。

mdBook 自带 `print.html`，把整本书合并成单页——抓它一份就等于拿到全书。
这是 v1 唯一支持的路径，也是 v2 的**回归基线**，行为不得改变。
"""
from __future__ import annotations

import os
import re

from ..ir import BookIR, Chapter, TocEntry
from .base import (FetchError, HttpFetcher, SiteProbe, extract_headings,
                   find_container, strip_tags)

RE_LIGHT = re.compile(r'const default_light_theme\s*=\s*"([^"]*)"\s*;')
RE_DARK = re.compile(r'const default_dark_theme\s*=\s*"([^"]*)"\s*;')
RE_REMOVE = re.compile(r"html\.classList\.remove\('([^']*)'\)")
RE_ADD = re.compile(r"html\.classList\.add\(theme\)\s*;")
RE_TOC_FILE = re.compile(r"^toc-.*\.js$")
RE_TOC_LINK = re.compile(r'<a href="([^"]+)">([^<]*)</a>')

# mdBook 内置主题名（灯箱/深色）
KNOWN_THEMES = ("light", "rust", "coal", "navy", "ayu")

CONTAINER_CANDIDATES = ["main", "#mdbook-content", ".content", "article"]


def build_theme_patches(html: str) -> list[tuple[str, str]]:
    """生成主题锁定补丁。

    必须同时改四处（缺一不可，见 references/traps.md 坑 1）：
    默认浅色、默认深色、classList.remove、classList.add。
    站点改名/换主题时命中数会变 0，由 theme.py 判 FAIL —— 不静默继续。
    """
    patches: list[tuple[str, str]] = []

    m = RE_LIGHT.search(html)
    if m and m.group(1) != "light":
        patches.append((m.group(0), 'const default_light_theme = "light";'))

    m = RE_DARK.search(html)
    if m and m.group(1) != "light":
        patches.append((m.group(0), 'const default_dark_theme = "light";'))

    m = RE_REMOVE.search(html)
    if m:
        names = sorted(set([m.group(1)] + list(KNOWN_THEMES)))
        repl = "html.classList.remove(" + ",".join(f"'{n}'" for n in names) + ")"
        patches.append((m.group(0), repl))

    m = RE_ADD.search(html)
    if m:
        patches.append((m.group(0), "html.classList.add('light');"))

    return patches


def read_toc(site_dir: str) -> list[TocEntry]:
    """从 toc-*.js 读有序侧栏目录。文件缺失时返回空表（由调用方判 WARN）。"""
    files = sorted(f for f in os.listdir(site_dir) if RE_TOC_FILE.match(f))
    if not files:
        return []
    txt = open(os.path.join(site_dir, files[0]), encoding="utf-8", errors="replace").read()
    out = []
    for href, title in RE_TOC_LINK.findall(txt):
        title = strip_tags(title).strip()
        if not title:
            continue
        out.append(TocEntry(path=href, title=title, level=1))
    return out


class MdBookAdapter:
    name = "mdbook"
    follow_html = False

    def confidence(self, p: SiteProbe) -> int:
        score = 0
        if p.has_print:
            score += 50
            if p.print_size > max(p.home_size * 2, 100_000):
                score += 20
        if p.has_mdbook_marker:
            score += 50
        if p.has_toc_js:
            score += 20
        if not p.has_print and not p.has_toc_js:
            score -= 30
        return max(0, min(100, score))

    def entry_urls(self, base: str, print_path: str = "print.html") -> list[str]:
        return [print_path]

    def build(self, site_dir: str, base: str, print_path: str = "print.html",
              fetcher: HttpFetcher | None = None, probe: SiteProbe | None = None) -> BookIR:
        src = os.path.join(site_dir, print_path.replace("/", os.sep))
        if not os.path.exists(src):
            raise FetchError(f"没有找到合并页 {print_path}（{src}）—— 该站可能不是 mdBook")

        html = open(src, encoding="utf-8", errors="replace").read()

        selector, body = find_container(html, CONTAINER_CANDIDATES)
        if selector is None:
            body = html
        container_selector = selector or "html"

        toc = read_toc(site_dir)
        heads = extract_headings(body, max_level=3)
        chapters = [Chapter(anchor_id=aid, title=t, level=lvl) for lvl, aid, t in heads]

        ir = BookIR(
            base_url=base,
            source_kind=self.name,
            full_html=html,
            container_selector=container_selector,
            toc=toc,
            chapters=chapters,
            theme_patches=build_theme_patches(html),
            print_path=print_path,
            merged_from=[t.path for t in toc],
        )

        if selector is None:
            ir.warn("container_fallback", "未匹配到正文容器，退化为整页 HTML", container_selector)
        if not toc:
            ir.warn("toc_empty",
                    "没有找到 toc-*.js：跨章链接将退化为线上 URL（PDF 内跳转失效）", src)
        if len(chapters) < len(toc):
            ir.warn("chapter_shortfall",
                    f"章节锚点 {len(chapters)} 少于目录 {len(toc)} 条", src)
        if not ir.theme_patches:
            ir.error("theme_no_patch",
                     "主题补丁命中 0 处：站点主题变量已变，PDF 会保持深色", src)
        return ir
