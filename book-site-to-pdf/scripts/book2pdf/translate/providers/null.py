# -*- coding: utf-8 -*-
"""干跑 provider：不调任何 API，只让流水线产出段清单。

用途：审阅待翻内容、核对预算、CI 里跑通链路。
"""
from __future__ import annotations

from ..glossary import Glossary


class NullProvider:
    tag = "null"

    def translate(self, batch: list[str], *, src_lang: str, dst_lang: str,
                  glossary: Glossary) -> list[str | None]:
        return [None] * len(batch)

    def describe(self) -> dict:
        return {"provider": "null", "note": "干跑：不调用任何 API，只产出段清单"}
