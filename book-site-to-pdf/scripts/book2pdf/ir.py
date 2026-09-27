# -*- coding: utf-8 -*-
"""中间表示（IR）：适配器唯一的产出契约，下游不得访问站点专有的东西。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TocEntry:
    """侧栏目录条目（有序）。"""
    path: str
    title: str
    level: int = 1


@dataclass
class Chapter:
    """页内章节锚点（有序）。"""
    anchor_id: str
    title: str
    level: int = 1


@dataclass
class Note:
    """降级/异常记录。会进 report.json，并参与退出码判定。"""
    code: str
    message: str
    severity: str = "warn"          # warn | error
    where: str = ""

    def as_dict(self) -> dict:
        return {"code": self.code, "severity": self.severity,
                "message": self.message, "where": self.where}


@dataclass
class BookIR:
    """整本书的归一化表示。"""
    base_url: str
    source_kind: str
    full_html: str
    container_selector: str
    toc: list[TocEntry] = field(default_factory=list)
    chapters: list[Chapter] = field(default_factory=list)
    theme_patches: list[tuple[str, str]] = field(default_factory=list)
    assets: list[str] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)
    print_path: str = "print.html"
    merged_from: list[str] = field(default_factory=list)
    # True = 该站点用 JS 运行时写主题（mdBook），补丁命中 0 次必须 FAIL；
    # False = 站点不用这套机制，补丁为空是正常的，不该报错。
    theme_required: bool = True

    # ---- 便捷访问 ----
    @property
    def chapter_ids(self) -> list[str]:
        return [c.anchor_id for c in self.chapters]

    @property
    def errors(self) -> list[Note]:
        return [n for n in self.notes if n.severity == "error"]

    @property
    def warnings(self) -> list[Note]:
        return [n for n in self.notes if n.severity == "warn"]

    def warn(self, code: str, message: str, where: str = "") -> None:
        self.notes.append(Note(code, message, "warn", where))

    def error(self, code: str, message: str, where: str = "") -> None:
        self.notes.append(Note(code, message, "error", where))

    def summary(self) -> dict:
        return {
            "source_kind": self.source_kind,
            "base_url": self.base_url,
            "container_selector": self.container_selector,
            "toc_entries": len(self.toc),
            "chapters": len(self.chapters),
            "chapter_levels": {lvl: sum(1 for c in self.chapters if c.level == lvl)
                               for lvl in sorted({c.level for c in self.chapters})},
            "theme_patches": len(self.theme_patches),
            "assets": len(self.assets),
            "merged_from": len(self.merged_from),
            "html_chars": len(self.full_html),
        }
