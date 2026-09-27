# -*- coding: utf-8 -*-
"""Anthropic Messages API provider。"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from ..glossary import Glossary
from .base import ProviderError, build_system_prompt
from .openai_compat import MARK, _encode, _parse


class AnthropicProvider:
    def __init__(self, base_url: str = "https://api.anthropic.com/v1", model: str = "",
                 api_key: str = "", timeout: int = 120, retries: int = 3,
                 verbose: bool = True):
        if not api_key:
            raise ProviderError(
                "Anthropic provider 需要 API key：请设环境变量 BOOK2PDF_API_KEY 后重试。")
        if not model:
            raise ProviderError("Anthropic provider 需要 --model，例如 claude-sonnet-4-5。")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.retries = retries
        self.verbose = verbose
        self.requests = 0
        self.failures = 0
        self.tag = f"anthropic:{self.model}"

    def _post(self, payload: dict) -> dict:
        req = urllib.request.Request(
            self.base_url + "/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers={"content-type": "application/json",
                     "x-api-key": self.api_key,
                     "anthropic-version": "2023-06-01"},
            method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def _call(self, system: str, user: str) -> str:
        doc = self._post({"model": self.model, "max_tokens": 8192, "temperature": 0.0,
                          "system": system,
                          "messages": [{"role": "user", "content": user}]})
        blocks = doc.get("content") or []
        return "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()

    def translate(self, batch: list[str], *, src_lang: str, dst_lang: str,
                  glossary: Glossary) -> list[str | None]:
        if not batch:
            return []
        system = build_system_prompt(src_lang, dst_lang, glossary)
        user = _encode(batch)
        last = None
        for attempt in range(self.retries):
            try:
                self.requests += 1
                out = _parse(self._call(system, user), len(batch))
                if all(x is None for x in out) and len(batch) > 1:
                    raise ValueError("批次响应无法解析")
                return out
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    raise ProviderError(f"Anthropic 拒绝认证（HTTP {e.code}）。") from e
                last = e
            except Exception as e:                               # noqa: BLE001
                last = e
            if attempt < self.retries - 1:
                time.sleep(0.8 * (2 ** attempt))
        self.failures += 1
        if self.verbose:
            print(f"       [provider] 批次失败（{len(batch)} 段）：{last}")
        return [None] * len(batch)

    def describe(self) -> dict:
        return {"provider": "anthropic", "model": self.model,
                "requests": self.requests, "failures": self.failures}
