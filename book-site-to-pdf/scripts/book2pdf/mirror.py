# -*- coding: utf-8 -*-
"""静态资源镜像。

两个必须处理的坑（v1 已踩）：
1. **同域越界**：netloc 相同不代表同站。站点在 `/my-book/` 子路径下时，
   `https://host/blog/xxx` 的 netloc 完全一样。必须按**路径前缀**过滤。
2. **无扩展名目录链接**：`href="00-guide"` 要落盘成 `00-guide/index.html`，
   否则 `open()` 会撞到已存在的同名目录并抛 PermissionError。
"""
from __future__ import annotations

import concurrent.futures as cf
import os
import re
import threading
import time
import urllib.parse
from dataclasses import dataclass, field

from .adapters.base import HttpFetcher, site_prefix, url_to_local

ASSET_RE = re.compile(r'(?:src|href)="([^"]+?)"')

SKIP_SCHEMES = ("http://", "https://", "//", "data:", "mailto:", "javascript:", "tel:")


@dataclass
class MirrorResult:
    files: list[str] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    bytes_total: int = 0
    followed: int = 0

    def summary(self) -> dict:
        return {"files": len(self.files), "errors": len(self.errors),
                "mb": round(self.bytes_total / 1048576, 2), "followed": self.followed}


def crawl(base: str, entry_urls: list[str], site_dir: str, *,
          follow_html: bool = False, print_path: str = "print.html",
          allow_hosts: tuple[str, ...] = (), max_files: int = 1500,
          workers: int = 12, verbose: bool = True) -> MirrorResult:
    """从 entry_urls 出发，镜像页面与静态资源。"""
    os.makedirs(site_dir, exist_ok=True)
    res = MirrorResult()
    seen: set[str] = set()
    lock = threading.Lock()
    fetcher = HttpFetcher()
    host = urllib.parse.urlparse(base).netloc
    # 必须归一化：`--base` 可能是 `.../index.html`，直接当前缀会把同站资源判成站外
    prefix = site_prefix(urllib.parse.urlparse(base).path)

    def same_site(u: urllib.parse.ParseResult) -> bool:
        if u.netloc == host:
            return u.path.startswith(prefix)
        return bool(allow_hosts) and u.netloc in allow_hosts

    def fetch(url: str):
        path = url_to_local(url, base, site_dir)
        os.makedirs(os.path.dirname(path) or site_dir, exist_ok=True)
        try:
            data = fetcher.get(url)
        except Exception as e:                                   # noqa: BLE001
            with lock:
                res.errors.append((url, str(e)[:160]))
            return None, path
        if data is None:
            return None, path
        try:
            with open(path, "wb") as f:
                f.write(data)
        except OSError as e:                                     # 目录/文件同名冲突
            with lock:
                res.errors.append((url, f"落盘失败（可能是目录同名冲突）: {e}"))
            return None, path
        return data, path

    with cf.ThreadPoolExecutor(workers) as ex:
        futs: dict = {}

        def add(u: str) -> None:
            with lock:
                if u in seen or len(seen) >= max_files:
                    return
                seen.add(u)
            futs[ex.submit(fetch, u)] = u

        for e in entry_urls:
            add(urllib.parse.urljoin(base, e))

        while futs:
            done = [f for f in futs if f.done()]
            if not done:
                time.sleep(0.02)
                continue
            for f in done:
                url = futs.pop(f)
                data, path = f.result()
                if data is None:
                    continue
                with lock:
                    res.files.append(path)
                    res.bytes_total += len(data)

                if not url.endswith((".html", ".css", ".js")):
                    continue
                txt = data.decode("utf-8", "replace")
                for m in ASSET_RE.findall(txt):
                    if m.startswith(SKIP_SCHEMES) or "${" in m or not m.strip():
                        continue
                    mu = urllib.parse.urljoin(url, m)
                    pu = urllib.parse.urlparse(mu)
                    if not same_site(pu):
                        continue
                    is_page = pu.path.endswith(".html") or not re.search(r"\.[A-Za-z0-9]{1,6}$", pu.path)
                    if is_page and not follow_html:
                        # 合并页已含全部章节，不必抓其它页
                        if not pu.path.endswith(("print.html", "index.html")):
                            continue
                    with lock:
                        res.followed += 1
                    add(mu)

    if verbose:
        print(f"[镜像] {len(res.files)} 个文件 / {res.bytes_total / 1048576:.1f} MB；"
              f"失败 {len(res.errors)}")
        for u, e in res.errors[:5]:
            print(f"       ERR {u} — {e}")
    return res


def local_page(site_dir: str, base: str, url_path: str) -> str:
    return url_to_local(urllib.parse.urljoin(base, url_path), base, site_dir)
