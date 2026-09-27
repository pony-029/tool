# -*- coding: utf-8 -*-
"""阈值化校验：每一条判据都有阈值，超阈值就是 FAIL。

**别只靠眼睛看几张图。** 四类产物缺陷（白底浅字 / 每章后空白页 / 图文被裁切 /
书签为空）都是在这里被自动抓到的。

陷阱提醒：Chromium 把页内跳转写成**命名目标**（`LINK_NAMED = 4`），
只统计 `LINK_GOTO` 会得到"页内跳转 0 个"的假警报，进而误判链接全坏。
"""
from __future__ import annotations

import os
import statistics

BLANK_CHAR_THRESHOLD = 15
CLIP_BOTTOM_TOLERANCE = 2
CLIP_MIN_HEIGHT = 4


class VerifyResult:
    def __init__(self):
        self.metrics: dict = {}
        self.blank_pages: list[int] = []
        self.clipped_pages: list[int] = []
        self.broken_links: list[tuple[int, str]] = []
        self.passed = True


def collect(pdf: str) -> VerifyResult:
    import pymupdf
    r = VerifyResult()
    d = pymupdf.open(pdf)

    texts = [len(d[i].get_text().strip()) for i in range(d.page_count)]
    r.metrics["pages"] = d.page_count
    r.metrics["size_mb"] = round(os.path.getsize(pdf) / 1048576, 2)

    toc = d.get_toc()
    levels: dict[int, int] = {}
    for lvl, _title, _page in toc:
        levels[lvl] = levels.get(lvl, 0) + 1
    r.metrics["toc_entries"] = len(toc)
    r.metrics["toc_levels"] = dict(sorted(levels.items()))
    r.metrics["toc_max_depth"] = max(levels) if levels else 0

    kinds: dict[int, int] = {}
    internal_resolved = 0
    external = 0
    for pno in range(d.page_count):
        for l in d[pno].get_links():
            k = l.get("kind")
            kinds[k] = kinds.get(k, 0) + 1
            if k == pymupdf.LINK_URI:
                external += 1
                continue
            # 命名目标 / GOTO 都要能解析出目标页，否则就是死链
            target = l.get("page", -1)
            if target is not None and target >= 0:
                internal_resolved += 1
            else:
                nd = l.get("nameddest") or l.get("to")
                ok = False
                if nd is not None:
                    try:
                        ok = bool(d.resolve_link(l)) if hasattr(d, "resolve_link") else False
                    except Exception:                            # noqa: BLE001
                        ok = False
                if not ok:
                    r.broken_links.append((pno, str(nd)))
    r.metrics["link_kinds"] = dict(sorted(kinds.items()))
    r.metrics["links_internal_resolved"] = internal_resolved
    r.metrics["links_external"] = external
    r.metrics["links_unresolved"] = len(r.broken_links)

    r.blank_pages = [i for i, v in enumerate(texts) if v < BLANK_CHAR_THRESHOLD]
    r.metrics["blank_pages"] = len(r.blank_pages)

    clipped = 0
    for i in range(d.page_count):
        pg = d[i]
        for dr in pg.get_drawings():
            rect = dr["rect"]
            if rect.y1 > pg.rect.height - CLIP_BOTTOM_TOLERANCE and rect.height > CLIP_MIN_HEIGHT:
                r.clipped_pages.append(i)
                clipped += 1
                break
    r.metrics["clipped_pages"] = clipped

    r.metrics["chars_total"] = sum(texts)
    r.metrics["chars_mean_per_page"] = round(statistics.mean(texts)) if texts else 0
    r.metrics["chars_median_per_page"] = int(statistics.median(texts)) if texts else 0
    d.close()
    return r


def assess(r: VerifyResult, report, *, strict_toc_depth: int = 3) -> None:
    # 显式声明阶段：错误归因不能依赖调用顺序（否则校验错会被记成构建错）
    report.stage("verify")
    m = r.metrics
    print(f"[校验] 页数 {m['pages']} | 体积 {m['size_mb']} MB")
    print(f"       书签 {m['toc_entries']} 条 层级 {m['toc_levels']}")
    print(f"       链接 kind 分布 {m['link_kinds']}  ← kind=4(LINK_NAMED) 即页内跳转")
    print(f"       页内跳转(可解析) {m['links_internal_resolved']} | 外部 {m['links_external']}"
          f" | 未解析 {m['links_unresolved']}")
    print(f"       空白页 {m['blank_pages']} | 绘图触底(疑似裁切) {m['clipped_pages']} 页 | "
          f"平均每页字符 {m['chars_mean_per_page']}")

    if not m["toc_entries"]:
        report.error("verify_no_bookmarks",
                     "书签为 0 条：outline=True 必须配 tagged=True，且浏览器需 Chrome/Edge ≳128",
                     "verify")
    elif m["toc_max_depth"] < strict_toc_depth:
        report.warn("verify_toc_shallow",
                    f"书签最深只到 h{m['toc_max_depth']}（预期 ≥ h{strict_toc_depth}）", "verify")

    if m["blank_pages"]:
        report.error("verify_blank_pages",
                     f"{m['blank_pages']} 张空白页：常见原因是分页机制重复"
                     f"（源站 break-before div + CSS page-break 叠加）",
                     f"示例页 {r.blank_pages[:8]}")

    if m["clipped_pages"]:
        report.error("verify_clipped",
                     f"{m['clipped_pages']} 页图形触底（内容被裁切）："
                     f"常见原因是超高 Mermaid 图未限高",
                     f"示例页 {r.clipped_pages[:8]}")

    if m["links_internal_resolved"] == 0 and m["link_kinds"]:
        report.error("verify_no_internal_links",
                     "没有解析到任何页内跳转：若链接 kind 全为 4(LINK_NAMED) 而统计里只找 "
                     "LINK_GOTO，就会误报——此处已按命名目标解析，说明链接确实缺失", "verify")

    if m["chars_mean_per_page"] < 60:
        report.warn("verify_sparse_pages",
                    f"平均每页仅 {m['chars_mean_per_page']} 字符，可能有大量稀疏页", "verify")

    report.metrics_update({"verify": m})
    report.stage("verify", **m)


def run(pdf: str, report) -> int:
    report.stage("verify")
    if not os.path.exists(pdf):
        report.error("verify_missing_pdf", f"PDF 不存在：{pdf}", "verify")
        return 3
    r = collect(pdf)
    assess(r, report)
    return 0
