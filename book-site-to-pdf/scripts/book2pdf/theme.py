# -*- coding: utf-8 -*-
"""主题锁定。

坑 1（见 references/traps.md）：mdBook 的主题由**内联脚本在运行时写入**
`document.documentElement.classList`，静态写 `class="light"` 会被覆盖。
必须同时改四处，且**命中数为 0 时必须 FAIL**——v1 是静默继续，
结果是"报告成功、PDF 白底浅灰字"。
"""
from __future__ import annotations

import re

HTML_TAG = re.compile(r"<html\b[^>]*>")
DARK_NAMES = ("navy", "coal", "ayu", "rust")


def apply(html: str, patches: list[tuple[str, str]], report, lang: str | None = None,
          required: bool = True) -> str:
    """应用主题补丁并统计命中数。返回改写后的 HTML。

    `required=True` 且命中数为 0 时 **FAIL**（v1 是静默继续）。
    `required=False`（非 mdBook 站点）时补丁为空属正常，只记 metric。
    """
    hits: dict[str, int] = {}
    applied = 0
    for old, new in patches:
        n = html.count(old)
        label = _label(old)
        hits[label] = n
        if n:
            html = html.replace(old, new)
            applied += n

    # <html> 标签：锁定 light 类（同时给 Docusaurus 用的 data-theme），语言按需改写
    m = HTML_TAG.search(html)
    cur_lang = None
    if m:
        lm = re.search(r'lang="([^"]*)"', m.group(0))
        cur_lang = lm.group(1) if lm else None
        new_lang = lang or cur_lang or "zh"
        attrs = f' lang="{new_lang}" class="light" data-theme="light" dir="ltr"'
        html = html[: m.start()] + "<html" + attrs + ">" + html[m.end():]

    report.metric("theme_patch_hits", hits)
    report.metric("theme_patch_applied", applied)
    report.metric("theme_required", required)
    report.metric("html_lang", lang or cur_lang or "zh")
    report.stage("theme", patches=len(patches), applied=applied, required=required, hits=hits)

    if applied:
        report.metric("theme_locked", True)
    elif required:
        if not patches:
            report.error("theme_no_patch",
                         "没有可用的主题补丁：站点主题不是 mdBook 的默认机制，PDF 会保持深色",
                         "theme")
        else:
            report.error("theme_no_hit",
                         f"主题补丁命中 0 处（补丁 {len(patches)} 条全部未匹配）："
                         "站点主题变量已变，PDF 会保持深色。"
                         "请对照 references/traps.md 坑 1 更新补丁", "theme")
    else:
        report.metric("theme_locked", "forced-by-css")

    if lang:
        report.metric("theme_target_lang", lang)
    return html


def _label(old: str) -> str:
    """把补丁原文压成短标签，便于报告里阅读。"""
    s = re.sub(r"\s+", " ", old).strip()
    return s[:64]
