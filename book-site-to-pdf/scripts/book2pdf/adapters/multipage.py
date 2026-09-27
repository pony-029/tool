# -*- coding: utf-8 -*-
"""多页站点合并：把「侧栏导航 + 逐页抓取」的站点拼成一份合并页。

MkDocs / Docusaurus / GitBook 都没有 mdBook 的 `print.html`，
必须逐页抓取后自己合并。合并的三个必要动作：
1. 每页正文抽成容器内层，剥掉 script/style（head 只保留第一页的）
2. **每页注入一个稳定的 h1 锚点**（`pg-<slug>`）——跨页链接才能重写成页内锚点
3. **重写页内相对链接与资源路径**（页面在子目录里，合并后基准变了）
"""
from __future__ import annotations

import os
import re
import urllib.parse

from ..ir import BookIR, Chapter, TocEntry
from .base import (extract_headings, find_container, find_container_span,
                   site_prefix, strip_tags, url_to_local)

RE_PAIR = re.compile(r"<(nav|aside|header)\b[^>]*>(.*?)</\1>", re.S | re.I)
RE_A = re.compile(r'<a\b[^>]*href="([^"#][^"]*)"[^>]*>(.*?)</a>', re.S | re.I)
RE_H1 = re.compile(r"<h1\b([^>]*)>(.*?)</h1>", re.S | re.I)
RE_ATTR = re.compile(r'(href|src)="([^"]+)"')
RE_SCRIPT_STYLE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.S | re.I)
RE_ID = re.compile(r'\bid="([^"]*)"')

SKIP_PREFIX = ("http", "//", "data:", "mailto:", "javascript:", "tel:", "#")
PAGE_EXT = (".html", ".htm", "/")


def slug(text: str) -> str:
    """稳定锚点片段。

    **保留扩展名**：`a/one.html` → `a-one-html`。若把 `.html` 去掉，
    `a/one.html` 与目录 `a/one/` 会撞成同一个锚点。
    """
    s = text.lower().strip("/")
    s = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", s).strip("-")
    return s or "page"


def _is_page_path(path: str) -> bool:
    if path.endswith((".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
                      ".woff", ".woff2", ".ttf", ".ico", ".xml", ".json", ".pdf", ".zip")):
        return False
    return path.endswith(PAGE_EXT) or not re.search(r"\.[A-Za-z0-9]{1,6}$", path)


class MultiPageAdapter:
    """多页站点的公共实现。子类只需给出 `select_pages`。"""

    name = "multipage"
    follow_html = True
    container_candidates = ["main", ".md-content__inner", "article", "#content", ".content"]
    page_limit = 4000

    # ---------- 页面选择（子类实现） ----------
    def select_pages(self, site_dir: str, base: str, home_html: str) -> list[str]:
        raise NotImplementedError

    # ---------- 公共 ----------
    def confidence(self, p) -> int:                              # noqa: ANN001
        return 10

    def entry_urls(self, base: str, print_path: str = "print.html") -> list[str]:
        home = url_to_local(base, base, "_")                     # 仅用于推导相对名
        return [""] if not home else [""]

    def _home_local(self, site_dir: str, base: str) -> str | None:
        for cand in ("index.html", ""):
            p = url_to_local(urllib.parse.urljoin(base, cand), base, site_dir)
            if os.path.exists(p):
                return p
        return None

    def build(self, site_dir: str, base: str, print_path: str = "print.html",
              fetcher=None, probe=None) -> BookIR:                     # noqa: ANN001
        self._fetcher = fetcher
        home_local = self._home_local(site_dir, base)
        home_html = open(home_local, encoding="utf-8", errors="replace").read() if home_local else ""

        pages = self.select_pages(site_dir, base, home_html)
        pages = [p for p in pages if p][: self.page_limit]
        if not pages:
            raise RuntimeError(
                "没有发现任何页面：该站的侧栏/sitemap 不可用。"
                "请用 --adapter 指定类型，或确认站点可访问。")

        frags: list[tuple[str, str, str]] = []       # (rel, anchor, fragment_html)
        toc: list[TocEntry] = []
        missing: list[str] = []

        for rel in pages:
            local = url_to_local(urllib.parse.urljoin(base, rel), base, site_dir)
            if not os.path.exists(local):
                missing.append(rel)
                continue
            raw = open(local, encoding="utf-8", errors="replace").read()
            _sel, inner = find_container(raw, self.container_candidates)
            if inner is None:
                inner = raw
            inner = RE_SCRIPT_STYLE.sub("", inner)
            anchor = "pg-" + slug(rel)
            title = self._title_of(inner) or slug(rel)
            inner = self._ensure_h1(inner, anchor, title)
            frags.append((rel, anchor, inner))
            toc.append(TocEntry(path=rel, title=title, level=1))

        if not frags:
            raise RuntimeError("页面全部抓取失败：请检查镜像是否成功（--keep 可保留现场）")

        page_index = {urllib.parse.urljoin(base, rel): aid for rel, aid, _ in frags}
        body_parts = []
        for rel, _aid, inner in frags:
            body_parts.append(self._rewrite_rel(inner, base, rel, page_index, site_dir))
        merged = "\n".join(body_parts)

        shell = home_html or (
            '<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"></head>'
            "<body></body></html>")
        sel, s, e = find_container_span(shell, self.container_candidates)
        if sel is None:
            sel = "main"
            shell = shell.replace("</body>", f"<main>{merged}</main></body>", 1)
        else:
            shell = shell[:s] + merged + shell[e:]

        out_path = os.path.join(site_dir, "merged.html")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(shell)

        chapters = [Chapter(anchor_id=aid, title=t, level=lvl)
                    for lvl, aid, t in extract_headings(merged, max_level=3)]

        ir = BookIR(
            base_url=base, source_kind=self.name, full_html=shell,
            container_selector=sel, toc=toc, chapters=chapters,
            theme_patches=[],           # 非 mdBook 站点不走 JS 主题机制
            print_path="merged.html", merged_from=[r for r, _a, _h in frags],
            theme_required=False,
        )
        if missing:
            ir.warn("pages_missing",
                    f"{len(missing)} 个页面未在镜像中找到（示例 {missing[:3]}），内容不完整",
                    base)
        if not re.search(r"\bh1\b", merged):
            ir.error("no_h1", "合并后的正文里没有 h1，书签与服务端分页将不可用", base)
        print(f"[合并] {len(frags)} 页 → merged.html（{len(merged)} 字符），"
              f"容器选择器 {sel}，缺页 {len(missing)}")
        return ir

    # ---------- 工具 ----------
    @staticmethod
    def _title_of(inner: str) -> str:
        m = RE_H1.search(inner)
        if not m:
            return ""
        return re.sub(r"\s+", " ", strip_tags(m.group(2))).strip()

    @staticmethod
    def _ensure_h1(inner: str, anchor: str, title: str) -> str:
        m = RE_H1.search(inner)
        if m:
            attrs = re.sub(r"\s+", " ", RE_ID.sub("", m.group(1))).strip()
            attrs = f" {attrs}" if attrs else ""
            return (inner[: m.start()] + f'<h1 id="{anchor}"{attrs}>{m.group(2)}</h1>'
                    + inner[m.end():])
        return f'<h1 id="{anchor}">{title}</h1>\n' + inner

    @staticmethod
    def _rewrite_rel(fragment: str, base: str, rel: str, page_index: dict[str, str],
                     site_dir: str) -> str:
        page_url = urllib.parse.urljoin(base, rel)
        root = urllib.parse.urlparse(base).path

        def repl(m):
            attr, val = m.group(1), m.group(2)
            if val.startswith(SKIP_PREFIX) or "${" in val or val.startswith("//"):
                return m.group(0)
            abs_url = urllib.parse.urljoin(page_url, val)
            pu = urllib.parse.urlparse(abs_url)
            target_page = pu._replace(fragment="").geturl()
            if target_page in page_index:
                frag = pu.fragment
                return f'{attr}="#{page_index[target_page]}"' + (f'#{frag}' if frag else "")
            local = url_to_local(abs_url, base, site_dir)
            try:
                rel_new = os.path.relpath(local, site_dir).replace(os.sep, "/")
            except ValueError:
                return f'{attr}="{abs_url}"'
            if rel_new.startswith(".."):
                return f'{attr}="{abs_url}"'
            if pu.fragment:
                rel_new += "#" + pu.fragment
            return f'{attr}="{rel_new}"'

        return RE_ATTR.sub(repl, fragment)


def nav_links(html: str, base: str) -> list[tuple[str, str]]:
    """从 nav/aside/header 里抽有序链接，返回 [(abs_url, link_text)]。"""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for m in RE_PAIR.finditer(html):
        for a in RE_A.finditer(m.group(2)):
            href = a.group(1).strip()
            if href.startswith(SKIP_PREFIX) and not href.startswith("/"):
                continue
            abs_url = urllib.parse.urljoin(base, href.split("#")[0])
            if abs_url in seen:
                continue
            seen.add(abs_url)
            out.append((abs_url, re.sub(r"\s+", " ", strip_tags(a.group(2))).strip()))
    return out


def _same_site_page(abs_url: str, base: str, prefix_filter: str | None = None) -> str | None:
    pu = urllib.parse.urlparse(abs_url)
    bu = urllib.parse.urlparse(base)
    if pu.netloc and pu.netloc != bu.netloc:
        return None
    # 用 site_prefix 而不是 bu.path 原值：base 可能是 `.../index.html`，
    # 那样每个同级页面都会判成"站外"而全部丢失。
    prefix = site_prefix(bu.path)
    if not pu.path.startswith(prefix):
        return None
    rel = pu.path[len(prefix):] or "index.html"
    if not _is_page_path(rel):
        return None
    if prefix_filter and not rel.startswith(prefix_filter):
        return None
    return rel


def pages_from_nav(home_html: str, base: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for abs_url, _text in nav_links(home_html, base):
        rel = _same_site_page(abs_url, base)
        if rel and rel not in seen:
            seen.add(rel)
            out.append(rel)
    return out


def pages_from_sitemap(base: str, fetcher, prefix_filter: str | None = None) -> list[str]:  # noqa: ANN001
    """用 sitemap 兜底。**保持 sitemap 的顺序**（多数生成器按导航顺序输出）。"""
    from .base import fetch_sitemap
    if fetcher is None:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for abs_url in fetch_sitemap(base, fetcher):
        rel = _same_site_page(abs_url, base, prefix_filter)
        if rel and rel not in seen:
            seen.add(rel)
            out.append(rel)
    return out


def pick_pages(home_html: str, base: str, fetcher, *, prefer: str | None = None,
               min_pages: int = 3, prefix_filter: str | None = None) -> list[str]:
    """先试侧栏导航（顺序最可靠），不足再用 sitemap 补。"""
    pages = pages_from_nav(home_html, base)
    if prefix_filter:
        pages = [p for p in pages if p.startswith(prefix_filter)] or pages
    if len(pages) < min_pages:
        extra = pages_from_sitemap(base, fetcher, prefix_filter)
        for p in extra:
            if p not in pages:
                pages.append(p)
    if prefer:
        pages.sort(key=lambda p: (0 if p.startswith(prefer) else 1, pages.index(p)))
    return pages
