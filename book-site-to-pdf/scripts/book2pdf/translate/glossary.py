# -*- coding: utf-8 -*-
"""术语表：送翻前约束、译后校验、以及"不得翻译"的保护。

三重作用（规格 §6.4）：
1. 作为 system prompt 的术语约束送入模型
2. 译后校验：src 术语的 dst 必须出现在译文里，否则计入 glossary_miss
3. `dst: __KEEP__` 的术语原样保留（品牌名、专有名词、代码名）

术语表哈希进 TM 键 —— 改了术语表，旧译文不允许复用。
"""
from __future__ import annotations

import hashlib
import json
import os
import re

KEEP = "__KEEP__"


class GlossaryError(Exception):
    pass


class Glossary:
    """空术语表也是合法的（hash 为常量，不影响 TM 键的稳定性）。"""

    def __init__(self, terms: dict[str, list[tuple[str, str, str]]] | None = None,
                 source: str = ""):
        self.terms = terms or {}
        self.source = source
        self._hash = None

    # ---------- 加载 ----------
    @classmethod
    def load(cls, path: str | None) -> "Glossary":
        if not path:
            return cls({}, "")
        if not os.path.exists(path):
            raise GlossaryError(f"术语表不存在：{path}")
        with open(path, encoding="utf-8") as f:
            raw = f.read()
        if path.endswith((".yaml", ".yml")):
            data = _load_yaml(raw, path)
        else:
            try:
                data = json.loads(raw)
            except Exception as e:                               # noqa: BLE001
                raise GlossaryError(f"术语表解析失败（{path}）：{e}") from e
        if not isinstance(data, dict):
            raise GlossaryError(f"术语表顶层必须是「语言 → 术语数组」的映射：{path}")

        terms: dict[str, list[tuple[str, str, str]]] = {}
        for lang, items in data.items():
            if not isinstance(items, list):
                raise GlossaryError(f"语言 {lang} 下必须是数组")
            rows = []
            for it in items:
                if not isinstance(it, dict) or "src" not in it or "dst" not in it:
                    raise GlossaryError(f"语言 {lang} 下的条目必须有 src 与 dst：{it!r}")
                rows.append((str(it["src"]), str(it["dst"]), str(it.get("note", ""))))
            terms[lang] = rows
        return cls(terms, path)

    # ---------- 身份 ----------
    def for_lang(self, lang: str) -> list[tuple[str, str, str]]:
        return self.terms.get(lang, [])

    def hash(self) -> str:
        if self._hash is None:
            h = hashlib.sha256()
            for lang in sorted(self.terms):
                for src, dst, note in self.terms[lang]:
                    h.update(f"{lang}\x1f{src}\x1f{dst}\x1f{note}\x1e".encode("utf-8"))
            self._hash = h.hexdigest()
        return self._hash

    # ---------- prompt ----------
    def prompt_block(self, lang: str) -> str:
        rows = self.for_lang(lang)
        if not rows:
            return ""
        lines = ["Glossary — these terms MUST be rendered exactly as specified:"]
        for src, dst, note in rows:
            if dst == KEEP:
                lines.append(f'- "{src}" → keep the original text unchanged'
                             + (f"  ({note})" if note else ""))
            else:
                lines.append(f'- "{src}" → "{dst}"' + (f"  ({note})" if note else ""))
        lines.append("Use each required rendering at least once per item where its "
                     "source term appears. Do not paraphrase glossary terms.")
        return "\n".join(lines)

    # ---------- 校验 ----------
    def check(self, source: str, translated: str, lang: str) -> list[str]:
        """返回未满足的术语列表（空表示全部满足）。"""
        missing: list[str] = []
        for src, dst, _note in self.for_lang(lang):
            if src not in source:
                continue
            if dst == KEEP:
                if src not in translated:
                    missing.append(f"{src}(keep)")
            elif dst not in translated:
                missing.append(f"{src}→{dst}")
        return missing

    def keep_terms(self, lang: str) -> list[str]:
        """`__KEEP__` 术语的源词列表——这些词在译文中必须原样出现。"""
        return [src for src, dst, _n in self.for_lang(lang) if dst == KEEP]

    def describe(self) -> dict:
        return {"source": self.source or "(inline)",
                "hash": self.hash()[:12],
                "languages": {k: len(v) for k, v in self.terms.items()}}


def _load_yaml(raw: str, path: str):
    """极简 YAML 子集：支持两层「lang:」+「- src/dst/note」列表。

    不引入 PyYAML 依赖——术语表结构固定，手写解析足够且没有安装负担。
    """
    try:
        import yaml                                          # type: ignore
        return yaml.safe_load(raw)
    except ImportError:
        pass

    data: dict[str, list[dict]] = {}
    cur_lang = None
    cur: dict | None = None
    for lineno, line in enumerate(raw.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if re.match(r"^\S.*:\s*$", line):
            cur_lang = line.split(":", 1)[0].strip()
            data.setdefault(cur_lang, [])
            cur = None
            continue
        m = re.match(r"^\s*-\s*(\w+)\s*:\s*(.*)$", line)
        if m and cur_lang is not None:
            cur = {}
            data[cur_lang].append(cur)
            cur[m.group(1)] = _scalar(m.group(2))
            continue
        m = re.match(r"^\s+(\w+)\s*:\s*(.*)$", line)
        if m and cur is not None:
            cur[m.group(1)] = _scalar(m.group(2))
            continue
        raise GlossaryError(f"{path}:{lineno} 无法解析：{line!r}")
    return data


def _scalar(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1]
    return v
