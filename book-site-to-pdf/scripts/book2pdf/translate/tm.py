# -*- coding: utf-8 -*-
"""翻译记忆（TM）：既是缓存，也是断点。

**键必须包含目标语言 ∥ 术语表哈希 ∥ provider 标签。**
否则换了术语表或换了模型后，重跑会**静默复用**旧译文——这是本子系统
自己会引入的静默失败点，必须在设计层堵死（见规格 §6.3）。
"""
from __future__ import annotations

import hashlib
import json
import os
import time


def _sha(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()


class TranslationMemory:
    """JSON 落盘的翻译记忆。`--resume` 时命中率即"已完成比例"。"""

    SCHEMA = "book2pdf/tm@1"

    def __init__(self, path: str, *, target_lang: str, glossary_hash: str,
                 provider_tag: str, autoload: bool = True):
        self.path = path
        self.target_lang = target_lang
        self.glossary_hash = glossary_hash
        self.provider_tag = provider_tag
        self.entries: dict[str, dict] = {}
        self.hits = 0
        self.misses = 0
        self.loaded_entries = 0
        self.invalidated = 0
        if autoload and path and os.path.exists(path):
            self.load()

    # ---------- 键 ----------
    def key(self, source_key: str) -> str:
        return _sha(source_key, self.target_lang, self.glossary_hash, self.provider_tag)

    # ---------- 读写 ----------
    def get(self, source_key: str) -> str | None:
        rec = self.entries.get(self.key(source_key))
        if rec is None:
            self.misses += 1
            return None
        self.hits += 1
        rec["hits"] = rec.get("hits", 0) + 1
        return rec.get("t")

    def put(self, source_key: str, translated: str) -> None:
        self.entries[self.key(source_key)] = {"t": translated, "ts": int(time.time()), "hits": 0}

    def has(self, source_key: str) -> bool:
        return self.key(source_key) in self.entries

    # ---------- 落盘 ----------
    def load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as f:
                doc = json.load(f)
        except Exception:                                        # noqa: BLE001
            return
        header_ok = (doc.get("target_lang") == self.target_lang
                     and doc.get("glossary_hash") == self.glossary_hash
                     and doc.get("provider") == self.provider_tag)
        self.entries = doc.get("entries", {}) or {}
        self.loaded_entries = len(self.entries)
        if not header_ok:
            # 头部不一致 → 旧条目不会命中（键里已含这些维度），但要显式记账
            self.invalidated = self.loaded_entries
            self.entries = {}

    def save(self) -> str:
        os.makedirs(os.path.dirname(os.path.abspath(self.path)) or ".", exist_ok=True)
        doc = {
            "schema": self.SCHEMA,
            "target_lang": self.target_lang,
            "glossary_hash": self.glossary_hash,
            "provider": self.provider_tag,
            "updated": int(time.time()),
            "count": len(self.entries),
            "entries": self.entries,
        }
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)
        return self.path

    # ---------- 统计 ----------
    def summary(self) -> dict:
        total = self.hits + self.misses
        return {
            "path": self.path,
            "target_lang": self.target_lang,
            "glossary_hash": self.glossary_hash[:12],
            "provider": self.provider_tag,
            "loaded_entries": self.loaded_entries,
            "entries": len(self.entries),
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total, 3) if total else 0.0,
            "invalidated_by_header": self.invalidated,
        }
