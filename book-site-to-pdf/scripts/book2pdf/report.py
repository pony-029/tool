# -*- coding: utf-8 -*-
"""构建报告与退出码。

设计原则（见规格 §7）：**坏产物必须报错，不能报成功**。
所有降级都先计数、再比阈值、最后落到退出码。
"""
from __future__ import annotations

import json
import os
import time

# 退出码语义（规格 §7）
EXIT_PASS = 0
EXIT_WARN = 1
EXIT_BUILD_FAIL = 2        # 构建阶段失败：适配 / 镜像 / 翻译
EXIT_VERIFY_FAIL = 3       # 校验阶段不合格：空白页 / 裁切 / 书签空 / 占位符破损
EXIT_ENV = 4               # 环境或输入错误：URL 不可达 / 缺依赖 / 代理不可用

EXIT_NAMES = {
    EXIT_PASS: "PASS",
    EXIT_WARN: "WARN",
    EXIT_BUILD_FAIL: "BUILD_FAIL",
    EXIT_VERIFY_FAIL: "VERIFY_FAIL",
    EXIT_ENV: "ENV_ERROR",
}


class BuildAbort(Exception):
    """构建阶段致命错误，直接映射到 EXIT_BUILD_FAIL / EXIT_ENV。"""

    def __init__(self, message: str, exit_code: int = EXIT_BUILD_FAIL):
        super().__init__(message)
        self.exit_code = exit_code


class Report:
    """收集 metric / note，产出 report.json，并计算退出码。"""

    def __init__(self, target: str = ""):
        self.target = target
        self.started = time.time()
        self.finished = 0.0
        self.source: dict = {}
        self.stages: dict[str, dict] = {}
        self.metrics: dict = {}
        self.notes: list[dict] = []

    # ---------- 写入 ----------
    def note(self, code: str, message: str, severity: str = "warn", where: str = "",
             stage: str | None = None, **data) -> None:
        rec = {"code": code, "severity": severity, "message": message,
               "where": where, "stage": stage or self._current_stage}
        if data:
            rec.update(data)
        self.notes.append(rec)

    def warn(self, code: str, message: str, where: str = "", stage: str | None = None,
             **data) -> None:
        """告警。注意：位置参数第三位是 `where`，不要在它前面插 severity。"""
        self.note(code, message, "warn", where, stage, **data)

    def error(self, code: str, message: str, where: str = "", stage: str | None = None,
              **data) -> None:
        self.note(code, message, "error", where, stage, **data)

    _current_stage = "init"

    def stage(self, name: str, **data) -> None:
        self._current_stage = name
        self.stages.setdefault(name, {}).update(data)

    def metric(self, name: str, value) -> None:
        self.metrics[name] = value

    def metrics_update(self, d: dict) -> None:
        self.metrics.update(d)

    def merge_ir_notes(self, ir) -> None:
        for n in getattr(ir, "notes", []):
            self.notes.append({"code": n.code, "severity": n.severity,
                               "message": n.message, "where": n.where,
                               "stage": "adapter"})

    # ---------- 读取 ----------
    @property
    def error_notes(self) -> list[dict]:
        return [n for n in self.notes if n["severity"] == "error"]

    @property
    def warn_notes(self) -> list[dict]:
        return [n for n in self.notes if n["severity"] == "warn"]

    @property
    def codes(self) -> list[str]:
        return sorted({n["code"] for n in self.notes})

    # ---------- 判定 ----------
    def exit_code(self, strict: bool = False, fail_on: str = "error") -> int:
        """strict=True 把 warn 升级为 fail；fail_on 控制粒度。"""
        if self.error_notes:
            # 构建期错误与校验期错误用不同码
            if any(n.get("stage") in ("verify",) for n in self.error_notes):
                return EXIT_VERIFY_FAIL
            return EXIT_BUILD_FAIL
        if self.warn_notes and (strict or fail_on == "warn"):
            return EXIT_WARN
        if self.warn_notes:
            return EXIT_WARN
        return EXIT_PASS

    # ---------- 落盘 ----------
    def as_dict(self) -> dict:
        return {
            "schema": "book2pdf/report@1",
            "target": self.target,
            "started": self.started,
            "finished": self.finished or time.time(),
            "elapsed_sec": round((self.finished or time.time()) - self.started, 1),
            "source": self.source,
            "stages": self.stages,
            "metrics": self.metrics,
            "counts": {"errors": len(self.error_notes), "warnings": len(self.warn_notes),
                       "notes": len(self.notes)},
            "codes": self.codes,
            "notes": self.notes,
        }

    def dump(self, path: str) -> str:
        self.finished = time.time()
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.as_dict(), f, ensure_ascii=False, indent=2)
        return path

    def print_summary(self) -> None:
        print(f"[报告] 错误 {len(self.error_notes)} | 警告 {len(self.warn_notes)}")
        for n in self.error_notes[:10]:
            print(f"       ERROR [{n['code']}] {n['message']}")
        for n in self.warn_notes[:10]:
            print(f"       WARN  [{n['code']}] {n['message']}")
        extra = len(self.notes) - 20
        if extra > 0:
            print(f"       … 另有 {extra} 条，见 report.json")
