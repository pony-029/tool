# -*- coding: utf-8 -*-
"""译文子系统：段切分 → 占位符保护 → TM → provider → 校验 → 回填。

设计要点（规格 §6）：
- 翻译发生在**离线 Python 侧**，不在浏览器里：可断点、可单测、可人工审查段清单
- **占位符守恒是硬校验**：破损则该段回退原文并计入报告
- **TM 键含术语表哈希与 provider 标签**：换术语表/换模型不允许复用旧译文
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import os
from dataclasses import dataclass, field

from . import placeholders as PH
from . import segmenter
from .glossary import Glossary
from .providers.base import Provider, estimate_budget
from .segmenter import Segment
from .tm import TranslationMemory

MODES = ("mono", "translated", "bilingual", "both")


@dataclass
class TranslateOptions:
    mode: str = "mono"
    src_lang: str = "zh"
    target_lang: str = ""
    dry_run: bool = False
    limit: int = 0
    batch_chars: int = 4000
    budget_tokens: int = 0
    assume_yes: bool = False
    concurrency: int = 4
    translate_mermaid: bool = False
    segments_path: str = ""


@dataclass
class Result:
    segments: list[Segment] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    unique_total: int = 0
    from_tm: int = 0
    translated_new: int = 0
    failed: int = 0
    broken: int = 0
    glossary_miss: int = 0

    def summary(self) -> dict:
        return {
            "segments": len(self.segments),
            "unique_total": self.unique_total,
            "from_tm": self.from_tm,
            "translated_new": self.translated_new,
            "failed": self.failed,
            "placeholder_broken": self.broken,
            "glossary_miss": self.glossary_miss,
        }


def run(container_html: str, opts: TranslateOptions, provider: Provider,
        glossary: Glossary, tm: TranslationMemory, report) -> Result:
    res = Result()
    segs = segmenter.segment(container_html, translate_mermaid=opts.translate_mermaid)
    res.segments = segs
    st = segmenter.stats(segs)
    res.stats = st

    report.metrics_update({
        "tr_segments": st["segments"],
        "tr_unique_segments": st["unique_segments"],
        "tr_chars": st["chars"],
        "tr_unique_chars": st["unique_chars"],
        "tr_cjk_chars": st["cjk_chars"],
        "tr_by_kind": st["by_kind"],
        "tr_dedup_saving_segment_pct": st["dedup_saving_segment_pct"],
        "tr_dedup_saving_char_pct": st["dedup_saving_char_pct"],
        "tr_tokens_est_low": st["tokens_est_low"],
        "tr_tokens_est_high": st["tokens_est_high"],
        "tr_tokens_est_low_unique": st["tokens_est_low_unique"],
        "tr_tokens_est_high_unique": st["tokens_est_high_unique"],
    })

    print(f"[译文] 段 {st['segments']}（去重 {st['unique_segments']}，"
          f"段省 {st['dedup_saving_segment_pct']}% / 字符省 {st['dedup_saving_char_pct']}%）")
    print(f"       字符口径 {st['chars']}（汉字 {st['cjk_chars']}）| "
          f"token 口径 {st['tokens_est_low']}~{st['tokens_est_high']}"
          f"（去重后 {st['tokens_est_low_unique']}~{st['tokens_est_high_unique']}）")

    if opts.segments_path:
        _write_segments(opts.segments_path, segs)

    if opts.dry_run:
        print(f"[译文] 干跑模式（provider=null）：只产出段清单，不调用任何 API")
        return res

    # ---- 唯一段 → 第一批 TM 命中 ----
    order: list[str] = []
    by_key: dict[str, list[Segment]] = {}
    for s in segs:
        if s.key not in by_key:
            by_key[s.key] = []
            order.append(s.key)
        by_key[s.key].append(s)

    missing: list[tuple[str, str]] = []
    for key in order:
        hit = tm.get(key)
        if hit is None:
            missing.append((key, by_key[key][0].text))
        else:
            for s in by_key[key]:
                s.translated = hit
                s.status = "tm"
            res.from_tm += 1

    res.unique_total = len(order)

    if opts.limit:
        missing = missing[: opts.limit]

    print(f"[译文] TM 命中 {res.from_tm}/{res.unique_total}；待翻 {len(missing)} 段 "
          f"(provider={provider.tag})")

    if not missing:
        tm.save()
        _finalize(res, report, tm)
        return res

    # ---- 预算闸门 ----
    chars, (lo, hi) = estimate_budget([t for _, t in missing])
    print(f"[预算] 待翻字符 {chars} | token 口径 {lo}~{hi}")
    if opts.budget_tokens and hi > opts.budget_tokens and not opts.assume_yes:
        from ..report import BuildAbort, EXIT_ENV
        raise BuildAbort(
            f"预计 token {lo}~{hi} 超出预算 {opts.budget_tokens}。"
            f"确认后加 --yes，或用 --limit N 试跑。", EXIT_ENV)

    # ---- 分批 + 并发翻译 ----
    batches = _make_batches(missing, opts.batch_chars)
    print(f"[译文] 分 {len(batches)} 批，并发 {opts.concurrency}")
    results: dict[str, str | None] = {}
    with cf.ThreadPoolExecutor(max(1, opts.concurrency)) as ex:
        futs = {ex.submit(provider.translate, [t for _, t in b], src_lang=opts.src_lang,
                          dst_lang=opts.target_lang, glossary=glossary): b
                for b in batches}
        done = 0
        for f in cf.as_completed(futs):
            b = futs[f]
            try:
                out = f.result()
            except Exception as e:                               # noqa: BLE001
                report.error("api_error", f"批次失败：{e}", "translate")
                out = [None] * len(b)
            for (key, src), got in zip(b, out):
                results[key] = got
                if got is not None:
                    tm.put(key, got)
            done += 1
            if done % 10 == 0 or done == len(batches):
                print(f"       批次 {done}/{len(batches)}")

    # ---- 校验 + 回填 ----
    for key, src in missing:
        got = results.get(key)
        group = by_key[key]
        if got is None:
            for s in group:
                s.status = "pending"
            res.failed += 1
            continue
        got = got.strip()
        if not got:
            for s in group:
                s.status = "pending"
            res.failed += 1
            continue
        if not PH.conserved(src, got):
            lost = PH.missing(src, got)
            for s in group:
                s.status = "broken"          # 回退原文，不输出结构损坏的 PDF
            res.broken += 1
            report.error("placeholder_broken",
                         f"占位符不守恒（丢失/多出 {lost}），该段回退原文",
                         f"sid={group[0].sid} · {src[:60]!r}")
            continue
        miss = glossary.check(src, got, opts.target_lang)
        if miss:
            res.glossary_miss += 1
            report.warn("glossary_miss",
                        f"术语未按表渲染 {miss}，已保留译文但需人工复核",
                        f"sid={group[0].sid}")
        for s in group:
            s.translated = got
            s.status = "ok"
        res.translated_new += 1

    tm.save()
    _finalize(res, report, tm)
    return res


def _finalize(res: Result, report, tm: TranslationMemory) -> None:
    tm_sum = tm.summary()
    report.metric("tr_summary", res.summary())
    report.metric("tm", tm_sum)
    report.stage("translate", **res.summary(), tm_hit_rate=tm_sum["hit_rate"])
    print(f"[译文] 完成：新译 {res.translated_new} / TM {res.from_tm} / "
          f"失败 {res.failed} / 占位符破损 {res.broken} / 术语缺口 {res.glossary_miss}")
    if res.broken:
        report.error("translate_broken",
                     f"{res.broken} 段因占位符不守恒回退原文", "translate")


def _make_batches(missing: list[tuple[str, str]], batch_chars: int) -> list[list[tuple[str, str]]]:
    batches, cur, size = [], [], 0
    for key, text in missing:
        if cur and size + len(text) > batch_chars:
            batches.append(cur)
            cur, size = [], 0
        cur.append((key, text))
        size += len(text)
    if cur:
        batches.append(cur)
    return batches


def _write_segments(path: str, segs: list[Segment]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for s in segs:
            f.write(json.dumps({"sid": s.sid, "kind": s.kind, "key": s.key,
                                "chars": len(s.text), "text": s.text},
                               ensure_ascii=False) + "\n")
    print(f"[译文] 段清单 → {path}")


def apply_mode(container_html: str, segs: list[Segment], mode: str) -> str:
    """按模式产出最终 HTML。`mono` 原样返回（回归基线）。"""
    if mode == "mono" or not segs:
        return container_html
    if mode == "bilingual":
        return segmenter.apply_bilingual(container_html, segs)
    return segmenter.apply(container_html, segs)


def changed_headings(segs: list[Segment]) -> int:
    """统计标题被改写的数量——用于验证「目录页/书签/正文标题三处同步」。"""
    n = 0
    for s in segs:
        if s.kind.startswith("h") and len(s.kind) == 2 and s.status in ("ok", "tm"):
            n += 1
    return n
