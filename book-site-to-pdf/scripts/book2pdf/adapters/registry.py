# -*- coding: utf-8 -*-
"""适配器注册表：按置信度打分自动选，可强制覆盖。

**打分必须可被检验**：把 MkDocs 站当 mdBook 喂时，mdbook 的分数必须低于 mkdocs。
（`tests/duality.py adapter_detect` 就是这条判据的负样本。）
"""
from __future__ import annotations

from .docusaurus import DocusaurusAdapter
from .generic import GenericAdapter
from .gitbook import GitBookAdapter
from .mdbook import MdBookAdapter
from .mkdocs import MkDocsAdapter

ADAPTERS = [MdBookAdapter, MkDocsAdapter, DocusaurusAdapter, GitBookAdapter, GenericAdapter]

# 低于这个分数就不认，交给 generic
MIN_CONFIDENCE = 20


def score_all(probe) -> dict[str, int]:                          # noqa: ANN001
    return {cls.name: cls().confidence(probe) for cls in ADAPTERS}


def detect(probe, forced: str | None = None, report=None):        # noqa: ANN001
    """返回 (adapter 实例, 全部得分)。"""
    by_name = {cls.name: cls for cls in ADAPTERS}
    scores = score_all(probe)

    if forced and forced != "auto":
        if forced not in by_name:
            raise ValueError(f"未知适配器 {forced}；可选 {sorted(by_name)}")
        chosen = by_name[forced]()
        if report is not None:
            report.metric("adapter_scores", scores)
            report.metric("adapter_chosen", chosen.name)
            report.metric("adapter_forced", True)
            report.stage("adapter", chosen=chosen.name, forced=True, scores=scores)
        print(f"[适配] 强制使用 {chosen.name}（探测得分 {scores}）")
        return chosen, scores

    best = max(scores, key=lambda k: scores[k])
    if scores[best] < MIN_CONFIDENCE:
        best = "generic"
    chosen = by_name[best]()

    if report is not None:
        report.metric("adapter_scores", scores)
        report.metric("adapter_chosen", chosen.name)
        report.metric("adapter_forced", False)
        report.stage("adapter", chosen=chosen.name, forced=False, scores=scores, probe=probe.report())
    print(f"[适配] 探测得分 {scores} → 选用 {chosen.name}")
    return chosen, scores
