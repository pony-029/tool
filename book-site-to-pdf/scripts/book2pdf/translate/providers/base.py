# -*- coding: utf-8 -*-
"""Provider 协议。

`translate` 返回 `list[str | None]`：None 表示该条失败，调用方保留 `pending`
状态，下次 `--resume` 续跑——**不允许静默产出空译文**。
"""
from __future__ import annotations

from typing import Protocol

from ..glossary import Glossary
from ..segmenter import estimate_tokens


class ProviderError(Exception):
    """不可恢复的 provider 错误（缺 key、端点不可达）。映射到 EXIT_ENV。"""


class Provider(Protocol):
    tag: str

    def translate(self, batch: list[str], *, src_lang: str, dst_lang: str,
                  glossary: Glossary) -> list[str | None]:
        ...

    def describe(self) -> dict:
        ...


def build_system_prompt(src_lang: str, dst_lang: str, glossary: Glossary) -> str:
    """统一 system prompt。占位符规则必须与 placeholders.py 的正则口径一致。"""
    from .. import placeholders as PH

    parts = [
        f"You are a professional technical translator. Translate from {src_lang} to {dst_lang}.",
        "Preserve the original meaning precisely; this is technical documentation, "
        "not marketing copy. Keep terminology consistent across the whole book.",
        PH.PLACEHOLDER_HINT,
        "Keep each item's leading index line EXACTLY as given (e.g. `@@3@@`).",
        "Do not add explanations, notes, or extra lines.",
    ]
    gb = glossary.prompt_block(dst_lang)
    if gb:
        parts.append(gb)
    return "\n\n".join(parts)


def estimate_budget(texts: list[str]) -> tuple[int, int]:
    """预算口径：字符与 token 都报（中文 1 字符 = 3 字节，wc -c 是陷阱）。"""
    chars = sum(len(t) for t in texts)
    import re
    cjk = sum(len(re.findall(r"[\u4e00-\u9fff]", t)) for t in texts)
    lo, hi = estimate_tokens(chars, cjk)
    return chars, (lo, hi)
