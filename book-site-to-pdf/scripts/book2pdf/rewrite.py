# -*- coding: utf-8 -*-
"""链接重写：把 print.html 里的**跨章断链**改写成页内锚点。

`print.html` 里 `href="01-introduction"` / `href="05-skills/creating-skills.html"`
在单页文档里根本不存在这些文件——不重写的话，PDF 里点目录会跳浏览器。

v1 的两个局限在这里修掉：
- `norm()` 里硬编码了某本书的 7 个中文括注 → 改为**通用归一化**（泛化去括号内容）
- 只支持「一个未收录页面」的单点插入 → 改为 **NW 序列对齐**，支持任意多个插入/删除
"""
from __future__ import annotations

import difflib
import os
import re
import urllib.parse

from .ir import BookIR, Chapter, TocEntry

PAREN = re.compile(r"[（(【\[｛{][^）)】\]｝}]*[）)】\]｝}]")
PUNCT = re.compile(r"[\s:：;；—\-－_、，,。.．·!！?？'\"“”‘’|<>\u3001\u3002"
                   r"（）()【】\[\]｛｝{}《》〈〉「」『』/\\~～`＋+＝=*&#]+")

BREAK_DIV = re.compile(r'\s*<div style="[^"]*break-before:\s*page[^"]*"\s*>\s*</div>\s*')

RE_REPO = re.compile(r"https://github\.com/[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+")

ALIGN_THRESHOLD = 0.6


# --------------------------------------------------------------------------- #
# 归一化与相似度（通用，不含任何站点专有词）
# --------------------------------------------------------------------------- #

def norm(s: str, drop_paren: bool = False) -> str:
    if drop_paren:
        s = PAREN.sub("", s)
    s = PUNCT.sub("", s)
    return s.lower()


def _sim(a: str, b: str) -> float:
    """标题相似度。同时比较「全量」与「去括号」两种归一化，取较大值。"""
    best = 0.0
    for da in (False, True):
        x = norm(a, da)
        if not x:
            continue
        for db in (False, True):
            y = norm(b, db)
            if not y:
                continue
            if x == y:
                return 1.0
            best = max(best, difflib.SequenceMatcher(None, x, y).ratio())
    return best


# --------------------------------------------------------------------------- #
# 序列对齐（NW 动态规划，支持任意多个插入/删除）
# --------------------------------------------------------------------------- #

def align(toc_titles: list[str], h1_titles: list[str], *,
          threshold: float = ALIGN_THRESHOLD, gap: float = -0.35) -> tuple[dict[int, int], float, float]:
    """把目录条目对齐到 h1 锚点。

    返回 (idx_map, quality, coverage)：
      - `idx_map[toc 下标] = h1 下标`；未匹配上的用相邻匹配的偏移量补齐
      - `quality` = 已匹配对的相似度之和 **÷ 全部目录条数**（覆盖率加权）
      - `coverage` = 匹配上的比例

    **分母必须是全部目录条数，不能只除以已匹配对数。**
    否则「5 条里只对上 1 条」会算出 1.0 的假高分——这个坑由
    `tests/duality.py align_low_quality` 抓到（v1 的分母本来就是 len(A)）。
    """
    n, m = len(toc_titles), len(h1_titles)
    if not n or not m:
        return {}, 0.0, 0.0

    S = [[_sim(toc_titles[i], h1_titles[j]) for j in range(m)] for i in range(n)]

    NEG = -9e9
    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    bt = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        dp[i][0] = dp[i - 1][0] + gap
        bt[i][0] = 1
    for j in range(1, m + 1):
        dp[0][j] = dp[0][j - 1] + gap
        bt[0][j] = 2

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            s = S[i - 1][j - 1]
            diag = dp[i - 1][j - 1] + (s - 0.45 if s >= threshold else NEG)
            up = dp[i - 1][j] + gap
            left = dp[i][j - 1] + gap
            best, b = diag, 0
            if up > best:
                best, b = up, 1
            if left > best:
                best, b = left, 2
            dp[i][j], bt[i][j] = best, b

    pairs: list[tuple[int | None, int | None]] = []
    i, j = n, m
    while i > 0 or j > 0:
        b = bt[i][j]
        if b == 0:
            pairs.append((i - 1, j - 1))
            i -= 1
            j -= 1
        elif b == 1:
            pairs.append((i - 1, None))
            i -= 1
        else:
            pairs.append((None, j - 1))
            j -= 1
    pairs.reverse()

    idx_map: dict[int, int] = {}
    matched: list[tuple[int, int]] = []
    for a, b in pairs:
        if a is not None and b is not None:
            idx_map[a] = b
            matched.append((a, b))

    # 未匹配的 toc 条目：按最近的前一个匹配做偏移补齐
    for i in range(n):
        if i in idx_map:
            continue
        prev = [(a, b) for a, b in matched if a < i]
        if prev:
            a0, b0 = prev[-1]
            cand = b0 + (i - a0)
        else:
            nxt = [(a, b) for a, b in matched if a > i]
            cand = (nxt[0][1] - (nxt[0][0] - i)) if nxt else i
        idx_map[i] = max(0, min(m - 1, cand))

    quality = sum(S[a][b] for a, b in matched) / n
    coverage = len(matched) / max(1, min(n, m))
    return idx_map, quality, coverage


def align_ir(ir: BookIR, report) -> dict[int, int]:
    h1 = [c for c in ir.chapters if c.level == 1]
    if not ir.toc:
        report.warn("toc_empty", "目录为空：跨章链接将退化为线上 URL，PDF 内跳转失效", "rewrite")
        return {}
    if not h1:
        report.error("no_chapters", "页面里没有 h1 锚点，无法建立链接映射", "rewrite")
        return {}

    idx_map, quality, coverage = align([t.title for t in ir.toc], [c.title for c in h1])
    unmatched_h1 = len(h1) - len(set(idx_map.values()))
    report.metric("align_quality", round(quality, 3))
    report.metric("align_coverage", round(coverage, 3))
    report.metric("align_toc_entries", len(ir.toc))
    report.metric("align_h1_total", len(h1))
    report.metric("align_inserted_pages", max(0, unmatched_h1))
    report.stage("align", quality=round(quality, 3), coverage=round(coverage, 3),
                 k_insertions=max(0, unmatched_h1))

    if quality < ALIGN_THRESHOLD:
        report.error(
            "align_low_quality",
            f"目录与章节标题对齐质量 {quality:.3f} < {ALIGN_THRESHOLD}"
            f"（覆盖率 {coverage:.1%}）：对齐不可靠，跨章链接可能指错页",
            "rewrite")
    else:
        print(f"[对齐] 目录 {len(ir.toc)} 条 vs h1 {len(h1)} 个 → "
              f"未收录页 {max(0, unmatched_h1)} 个，质量 {quality:.3f}，覆盖率 {coverage:.1%}")
    return idx_map


# --------------------------------------------------------------------------- #
# 路径索引与解析
# --------------------------------------------------------------------------- #

def _path_variants(p: str) -> set[str]:
    out = {p.lstrip("./")}
    if p.endswith("README.html"):
        out.add(p[: -len("README.html")] + "index.html")
    head = p.split("#")[0]
    if p.endswith("/") or not os.path.splitext(head)[1]:
        out.add(p.rstrip("/") + "/index.html")
    return out


def build_index(ir: BookIR, idx_map: dict[int, int]):
    h1 = [c for c in ir.chapters if c.level == 1]
    path2anchor: dict[str, str] = {}
    by_base: dict[str, list[str]] = {}
    by_dir: dict[str, list[str]] = {}

    for i, e in enumerate(ir.toc):
        j = idx_map.get(i)
        if j is None or j >= len(h1):
            continue
        anchor = h1[j].anchor_id
        for v in _path_variants(e.path):
            path2anchor.setdefault(v, anchor)
        base = os.path.basename(e.path.split("#")[0]) or e.path
        by_base.setdefault(base, []).append(anchor)
        if base in ("index.html", ""):
            d = os.path.basename(os.path.dirname(e.path.rstrip("/")))
            if d:
                by_dir.setdefault(d, []).append(anchor)

    # 非 level-1 的锚点也可作为 fragment 目标
    return path2anchor, by_base, by_dir


def detect_repo(html: str) -> str | None:
    m = RE_REPO.search(html)
    return m.group(0) if m else None


def make_resolver(ir: BookIR, idx_map: dict[int, int], ids: set[str],
                  live_base: str, repo: str | None, report):
    path2anchor, by_base, by_dir = build_index(ir, idx_map)
    stats: dict[str, int] = {}
    unmapped: list[str] = []
    prefix = urllib.parse.urlparse(live_base).path

    def resolve(target: str):
        frag = ""
        if "#" in target:
            p, frag = target.split("#", 1)
            frag = urllib.parse.unquote(frag)
        else:
            p = target
        p = p.strip()

        def pick(anchor: str) -> str:
            return frag if frag in ids else anchor

        for v in _path_variants(p):
            if v in path2anchor:
                return "anchor", pick(path2anchor[v]), "exact"
        p2 = re.sub(r"^(\.\./)+", "", p)
        if p2 != p:
            for v in _path_variants(p2):
                if v in path2anchor:
                    return "anchor", pick(path2anchor[v]), "stripped"
        base = os.path.basename(p) or p
        for key in (base, base + ".html"):
            c = by_base.get(key)
            if c and len(c) == 1:
                return "anchor", pick(c[0]), "base"
        stem = os.path.basename(p.rstrip("/")) or p
        if stem in by_dir and len(by_dir[stem]) == 1:
            return "anchor", by_dir[stem][0], "dir"
        if "." in p:
            st = os.path.splitext(stem)[0]
            hits = [v[0] for k, v in by_base.items()
                    if len(v) == 1 and k != "index.html"
                    and (k.split(".")[0] == st
                         or k.split(".")[0].startswith(st + "-")
                         or st.startswith(k.split(".")[0] + "-"))]
            if len(hits) == 1:
                return "anchor", hits[0], "stem"
        if not p and frag in ids:
            return "anchor", frag, "frag"
        # 兜底：不要留死链
        if p.startswith(("..", "/")) or (p and not p.startswith(live_base)):
            # 逃出站点路径前缀（如 ../../examples/...）→ 指到仓库
            if repo and "examples" in p:
                return "url", urllib.parse.urljoin(repo, "/tree/main/" + p.lstrip("./").replace("../", "")), "repo"
            if prefix and not p.startswith(prefix) and p.startswith("/"):
                return "url", urllib.parse.urljoin(live_base, p), "live"
        return "url", urllib.parse.urljoin(live_base, p), "live"

    def rewrite_body(body: str) -> str:
        def repl(mm):
            href = mm.group(1)
            if href.startswith(("#", "http", "mailto:", "javascript:", "tel:", "data:")) or "${" in href:
                return mm.group(0)
            kind, val, note = resolve(href)
            stats[note] = stats.get(note, 0) + 1
            if kind == "anchor":
                if not val:
                    unmapped.append(href)
                    return mm.group(0)
                return f'href="#{val}"'
            unmapped.append(href)
            return f'href="{val}"' if val else mm.group(0)

        return re.sub(r'href="([^"]+)"', repl, body)

    def finalize(relative_total: int) -> None:
        report.metric("links_total_href", relative_total)
        report.metric("links_by_layer", stats)
        report.metric("links_unmapped", len(set(unmapped)))
        report.stage("rewrite", by_layer=stats, unmapped=len(set(unmapped)))
        print(f"[链接] {sum(stats.values())} 个相对链接；{stats}；未映射 {len(set(unmapped))}")
        if unmapped:
            report.warn("links_unmapped",
                        f"{len(set(unmapped))} 个相对链接无法映射到页内锚点，已退化为线上 URL",
                        f"示例 {sorted(set(unmapped))[:3]}")

    return rewrite_body, finalize


# --------------------------------------------------------------------------- #
# 坑 2：移除源站的分页占位 div
# --------------------------------------------------------------------------- #

def remove_page_break_divs(html: str, report, stage: str = "rewrite") -> str:
    n = len(BREAK_DIV.findall(html))
    html = BREAK_DIV.sub("\n", html)
    report.metric("page_break_divs_removed", n)
    print(f"[分页] 移除重复的分页占位 div {n} 个（改由 CSS 统一分页）")
    return html


# --------------------------------------------------------------------------- #
# 可点击目录页
# --------------------------------------------------------------------------- #

def build_toc_page(container_html: str, depth: int = 2) -> str:
    heads = []
    for m in re.finditer(r'<h([1-6])\s+id="([^"]*)"[^>]*>(.*?)</h\1>', container_html, re.S):
        lvl = int(m.group(1))
        if lvl > depth:
            continue
        title = re.sub(r"<[^>]+>", "", m.group(3)).strip()
        if title:
            heads.append((lvl, m.group(2), title))

    items = "".join(
        f'<li class="toc-h{lvl}"><a href="#{aid}">{t}</a></li>'
        for lvl, aid, t in heads)
    return ('<section class="pdf-toc"><h1 id="pdf-toc-heading">目录</h1>'
            f'<div class="pdf-toc-cols"><ul>{items}</ul></div></section>'), len(heads)
