# -*- coding: utf-8 -*-
"""占位符保护：把内联标签换成 ⟦n⟧ 再送翻，翻完还原。

**这是整个译文子系统最重要的机制。** 没有它，机器翻译会悄悄吃掉内联标签
（`<a>` / `<code>` / `<strong>`），产生结构损坏的 PDF。

守恒是硬规则：译文里的占位符集合与出现次数必须与原文完全一致，
否则该段拒收、回退原文，并计入报告（不允许静默通过）。
"""
from __future__ import annotations

import re

PLACEHOLDER_OPEN = "\u27e6"
PLACEHOLDER_CLOSE = "\u27e7"

PH_RE = re.compile(r"\u27e6\s*(\d+)\s*\u27e7")
FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")

VOID_RE = re.compile(r"<(img|br|hr|input)\b[^>]*?/?>", re.I)
INNER_RE = re.compile(
    r"<(?P<tag>a|code|strong|em|b|i|kbd|sup|sub|span|del|mark|var|samp|small|abbr|time)\b"
    r"(?P<attrs>[^>]*)>(?P<content>[^<]*)</(?P=tag)>",
    re.I)

MAX_NEST = 60

# 送进 prompt 的说明，必须与正则口径一致
PLACEHOLDER_HINT = (
    "Text may contain placeholders like ⟦0⟧, ⟦1⟧. "
    "Copy every placeholder into your translation EXACTLY as-is, "
    "including the same number and the same count. "
    "Never translate, renumber, merge, drop or reformat a placeholder. "
    "Do not translate the content of code placeholders."
)


def make(text: str, index: int) -> str:
    return f"{PLACEHOLDER_OPEN}{index}{PLACEHOLDER_CLOSE}"


def protect(fragment: str) -> tuple[str, list[str]]:
    """把内联元素与 void 标签换成占位符。

    返回 (送翻文本, 占位符原文列表)。**由内向外**替换，支持任意嵌套。
    """
    slots: list[str] = []

    def slot(literal: str) -> str:
        slots.append(literal)
        return make("", len(slots) - 1)

    out = VOID_RE.sub(lambda m: slot(m.group(0)), fragment)
    for _ in range(MAX_NEST):
        new = INNER_RE.sub(lambda m: slot(m.group(0)), out)
        if new == out:
            break
        out = new
    return out, slots


def restore(text: str, slots: list[str]) -> str:
    """还原占位符。

    **必须递归**：嵌套内联元素会被由内向外逐层占位，外层槽位的字面量里
    还含有内层占位符，只还原一次会留下 ⟦n⟧。
    越界或已损坏的占位符原样保留（由守恒校验兜底）。
    """
    out = normalize(text)
    for _ in range(MAX_NEST):
        new = PH_RE.sub(lambda m: slots[int(m.group(1))]
                        if 0 <= int(m.group(1)) < len(slots) else m.group(0), out)
        if new == out:
            break
        out = new
    return out


def normalize(text: str) -> str:
    """容忍全角数字等常见模型输出偏差。"""
    return text.translate(FULLWIDTH_DIGITS)


def signature(text: str) -> list[int]:
    return sorted(int(x) for x in PH_RE.findall(normalize(text)))


def conserved(source: str, translated: str) -> bool:
    """占位符集合与出现次数必须完全一致。"""
    return signature(source) == signature(translated)


def missing(source: str, translated: str) -> list[int]:
    """返回丢失/多出的占位符编号，便于报告定位。"""
    a, b = signature(source), signature(translated)
    sa, sb = set(a), set(b)
    return sorted(sa ^ sb) or sorted(
        [x for x in set(a) if a.count(x) != b.count(x)])
