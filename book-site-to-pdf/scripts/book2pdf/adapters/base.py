# -*- coding: utf-8 -*-
"""适配器协议、站点探针与通用抓取器。

一个适配器只做一件事：**把某类书站的差异消解成同一份 BookIR**。
下游（重写/主题/样式/翻译/渲染/校验）不得访问站点专有的东西。
"""
from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Protocol

from ..ir import BookIR

UA = "Mozilla/5.0 (compatible; book2pdf/2.0)"

# 各站探测特征
RE_MD_BOOK_THEME = re.compile(r'const default_(?:light|dark)_theme\s*=\s*"([^"]*)"')
RE_TOC_JS = re.compile(r"toc-[0-9a-f]+\.js")
RE_MKDOCS_META = re.compile(r'name="generator"\s+content="[^"]*mkdocs', re.I)
RE_MKDOCS_BODY = re.compile(r'class="[^"]*md-content')
RE_DOCUSAURUS = re.compile(r"__docusaurus|docusaurus\.config")
RE_GITBOOK = re.compile(r"gitbook", re.I)


# --------------------------------------------------------------------------- #
# 网络
# --------------------------------------------------------------------------- #

class FetchError(Exception):
    pass


class HttpFetcher:
    """带重试的极简抓取器。所有网络访问都走它，便于测试时替身。"""

    def __init__(self, timeout: int = 60, retries: int = 3):
        self.timeout = timeout
        self.retries = retries
        self.calls = 0

    def get(self, url: str) -> bytes | None:
        last = None
        for attempt in range(self.retries):
            self.calls += 1
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                return urllib.request.urlopen(req, timeout=self.timeout).read()
            except urllib.error.HTTPError as e:
                if e.code in (403, 404, 410):
                    return None                                  # 不存在就不重试
                last = e
            except Exception as e:                               # noqa: BLE001
                last = e
            if attempt < self.retries - 1:
                import time
                time.sleep(0.6 * (2 ** attempt))
        if last is not None:
            raise FetchError(f"{url}: {last}") from last
        return None

    def get_text(self, url: str) -> str:
        data = self.get(url)
        return "" if data is None else data.decode("utf-8", "replace")

    def head_ok(self, url: str) -> tuple[bool, int]:
        """返回 (是否 200, 内容长度)。用于探测 print.html 这类入口。"""
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA}, method="GET")
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                body = r.read()
                return r.status == 200, len(body)
        except urllib.error.HTTPError:
            return False, 0
        except Exception:                                        # noqa: BLE001
            return False, 0


# --------------------------------------------------------------------------- #
# 探针
# --------------------------------------------------------------------------- #

@dataclass
class SiteProbe:
    """运行时收集的站点特征，供各适配器打分。"""
    base: str
    home_html: str = ""
    has_print: bool = False
    print_size: int = 0
    home_size: int = 0
    has_toc_js: bool = False
    has_mdbook_marker: bool = False
    has_mkdocs_meta: bool = False
    has_mkdocs_body: bool = False
    has_docusaurus_data: bool = False
    has_gitbook: bool = False
    sitemap_urls: list[str] = field(default_factory=list)

    def report(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k != "sitemap_urls"} | {
            "sitemap_urls": len(self.sitemap_urls)}


def probe_site(base: str, fetcher: HttpFetcher, print_path: str = "print.html") -> SiteProbe:
    """抓首页与 print.html 的头信息，并对各站特征打分所需的事实。"""
    p = SiteProbe(base=base)
    home = fetcher.get_text(base)
    p.home_html = home
    p.home_size = len(home)

    ok, size = fetcher.head_ok(urllib.parse.urljoin(base, print_path))
    p.has_print = ok
    p.print_size = size

    probe_doc = home
    if ok and size > max(p.home_size * 2, 100_000):
        # print.html 体量远大于首页 → 它就是合并页，用它做特征检测更准
        probe_doc = fetcher.get_text(urllib.parse.urljoin(base, print_path))[:400_000]

    p.has_toc_js = bool(RE_TOC_JS.search(probe_doc)) or "toc.js" in probe_doc
    p.has_mdbook_marker = bool(RE_MD_BOOK_THEME.search(probe_doc))
    p.has_mkdocs_meta = bool(RE_MKDOCS_META.search(home))
    p.has_mkdocs_body = bool(RE_MKDOCS_BODY.search(home))
    p.has_docusaurus_data = bool(RE_DOCUSAURUS.search(home))
    p.has_gitbook = bool(RE_GITBOOK.search(home)) and not p.has_mkdocs_meta
    p.sitemap_urls = fetch_sitemap(base, fetcher)
    return p


def fetch_sitemap(base: str, fetcher: HttpFetcher) -> list[str]:
    for name in ("sitemap.xml", "sitemap_index.xml"):
        txt = fetcher.get_text(urllib.parse.urljoin(base, name))
        if not txt.strip():
            continue
        urls = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", txt)
        if urls:
            return urls
        nested = re.findall(r"<sitemap>.*?<loc>\s*([^<\s]+)\s*</loc>", txt, re.S)
        out: list[str] = []
        for n in nested[:5]:
            sub = fetcher.get_text(n)
            out.extend(re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", sub))
        if out:
            return out
    return []


# --------------------------------------------------------------------------- #
# 适配器协议
# --------------------------------------------------------------------------- #

class SiteAdapter(Protocol):
    name: str
    follow_html: bool          # 镜像时是否跟随其它 .html 页（多页站点为 True）

    def confidence(self, p: SiteProbe) -> int:
        """0–100。registry 取最高分；全部 <20 时退回 generic。"""

    def entry_urls(self, base: str, print_path: str = "print.html") -> list[str]:
        """需要抓取的入口页。"""

    def build(self, site_dir: str, base: str, print_path: str = "print.html",
              fetcher: HttpFetcher | None = None, probe: SiteProbe | None = None) -> BookIR:
        """读取本地镜像，归一化为 BookIR。失败写进 notes，不抛未捕获异常。"""


# --------------------------------------------------------------------------- #
# 公共工具
# --------------------------------------------------------------------------- #

FILE_SEG_RE = re.compile(r"\.[A-Za-z0-9]{1,6}$")


def site_prefix(path: str) -> str:
    """把 URL 的 path 归一到**站点根目录**形态（必以 `/` 结尾）。

    `--base` 两种写法都要支持：目录（`.../my-book/`）与页面（`.../my-book/index.html`）。
    后者若被直接当成路径前缀会连出两个错：
    - `same_site()` 里 `print.html`.startswith(`.../index.html`) 为假 →
      **同站资源被判成站外全部拦掉**，只mirror 到一个入口文件；
    - `url_to_local()` 落盘时前缀不匹配 → 退回完整 path，
      多出一层 `my-book/` 同名目录，`print.html` 找不到。

    实测踩点：真书用 `.../index.html` 作为 `--base` 时，镜像只落 1 个文件即崩。
    """
    if not path:
        return "/"
    if path.endswith("/"):
        return path
    head, _, last = path.rpartition("/")
    if FILE_SEG_RE.search(last):                 # 末段是文件名 → 裁掉
        return f"{head}/" if head else "/"
    return path + "/"


def site_root(base: str) -> str:
    """把 base URL 归一到站点根目录（保留 scheme/netloc，path 走 site_prefix）。"""
    p = urllib.parse.urlparse(base)
    return urllib.parse.urlunparse(p._replace(path=site_prefix(p.path)))


def url_to_local(url: str, base: str, root: str) -> str:
    """URL → 本地落盘路径。无扩展名的目录链接落成 index.html。"""
    prefix = site_prefix(urllib.parse.urlparse(base).path)
    path = urllib.parse.urlparse(url).path
    rel = path[len(prefix):] if path.startswith(prefix) else path.lstrip("/")
    if rel == "" or rel.endswith("/"):
        rel += "index.html"
    elif not FILE_SEG_RE.search(rel):
        rel += "/index.html"
    import os
    return os.path.join(root, rel.replace("/", os.sep))


def local_to_url(local: str, base: str, root: str) -> str:
    import os
    rel = os.path.relpath(local, root).replace(os.sep, "/")
    return urllib.parse.urljoin(base, rel)


def strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s)


HEAD_RE = re.compile(r'<h([1-6])\s+id="([^"]*)"[^>]*>(.*?)</h\1>', re.S)


def extract_headings(body: str, max_level: int = 3) -> list[tuple[int, str, str]]:
    """抽取 (level, anchor_id, title)。**保持文档顺序**。"""
    out = []
    for m in HEAD_RE.finditer(body):
        lvl = int(m.group(1))
        if lvl > max_level:
            continue
        title = re.sub(r"\s+", " ", strip_tags(m.group(3))).strip()
        if not title:
            continue
        out.append((lvl, m.group(2), title))
    return out


def _container_re(sel: str) -> re.Pattern | None:
    if sel.startswith("."):
        cls = sel[1:]
        return re.compile(r'<(\w+)[^>]*class="[^"]*\b' + re.escape(cls) + r'\b[^"]*"[^>]*>(.*)</\1>',
                          re.S)
    if sel.startswith("#"):
        return re.compile(r'<(\w+)[^>]*id="' + re.escape(sel[1:]) + r'"[^>]*>(.*)</\1>', re.S)
    return re.compile(r"<" + re.escape(sel) + r"[^>]*>(.*)</" + re.escape(sel) + r">", re.S)


def find_container_span(html: str, candidates: list[str]) -> tuple[str | None, int, int]:
    """按候选链找正文容器，返回 (选择器, 内层起始偏移, 内层结束偏移)。"""
    for sel in candidates:
        m = _container_re(sel).search(html)
        if m:
            return sel, m.start(m.lastindex), m.end(m.lastindex)
    return None, -1, -1


def find_container(html: str, candidates: list[str]) -> tuple[str, str] | tuple[None, None]:
    """按候选链找正文容器，返回 (选择器, 内层 HTML)。"""
    sel, s, e = find_container_span(html, candidates)
    if sel is None:
        return None, None
    return sel, html[s:e]
