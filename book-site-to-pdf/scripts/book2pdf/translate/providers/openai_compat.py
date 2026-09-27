# -*- coding: utf-8 -*-
"""OpenAI 兼容端点 provider。

一个实现覆盖三种场景：本地 Codex/DeepSeek 代理、OpenAI、任何 OpenAI 兼容服务。
端点必须**外置可配**——本机 `~/.codex/config.toml` 并不存在，不能假设代理在跑。
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from ..glossary import Glossary
from .base import ProviderError, build_system_prompt

MARK = re.compile(r"@@\s*(\d+)\s*@@")

DEFAULT_TIMEOUT = 120
DEFAULT_RETRIES = 3


class OpenAICompatProvider:
    def __init__(self, base_url: str, model: str, api_key: str = "",
                 timeout: int = DEFAULT_TIMEOUT, retries: int = DEFAULT_RETRIES,
                 temperature: float = 0.0, verbose: bool = True):
        if not base_url:
            raise ProviderError(
                "未配置翻译端点。请用 --base-url 指定，或写入 ~/.book2pdf/config.json，"
                "或设环境变量 BOOK2PDF_BASE_URL。"
                "（若只想先审阅待翻内容，用 --provider null 干跑）")
        if not model:
            raise ProviderError("未配置模型名。请用 --model 指定，或设 BOOK2PDF_MODEL。")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.retries = retries
        self.temperature = temperature
        self.verbose = verbose
        self.requests = 0
        self.failures = 0
        self.tag = f"openai_compat:{_host_of(self.base_url)}:{model}"

    # ---------- HTTP ----------
    def _post(self, payload: dict) -> dict:
        url = self.base_url + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def _probe_hint(self, exc: Exception) -> str:
        host = _host_of(self.base_url)
        return (f"无法连接翻译端点 {self.base_url}（{exc}）。\n"
                f"  1) 确认本地代理已启动（如 127.0.0.1:15721 或 {host}）\n"
                f"  2) 或改用 --provider null 先审阅待翻段清单\n"
                f"  3) 或用 --base-url 指向可用的 OpenAI 兼容端点")

    # ---------- 翻译 ----------
    def translate(self, batch: list[str], *, src_lang: str, dst_lang: str,
                  glossary: Glossary) -> list[str | None]:
        if not batch:
            return []
        system = build_system_prompt(src_lang, dst_lang, glossary)
        user = _encode(batch)
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
        }

        last_err: Exception | None = None
        for attempt in range(self.retries):
            try:
                self.requests += 1
                doc = self._post(payload)
                raw = doc["choices"][0]["message"]["content"]
                out = _parse(raw, len(batch))
                if all(x is None for x in out) and len(batch) > 1:
                    raise ValueError("批次响应无法解析（未找到任何 @@n@@ 标记）")
                return out
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code in (401, 403):
                    raise ProviderError(
                        f"端点拒绝认证（HTTP {e.code}）：请检查 API key 是否正确") from e
                if e.code < 500 and e.code != 429:
                    break
            except ProviderError:
                raise
            except Exception as e:                               # noqa: BLE001
                last_err = e
            if attempt < self.retries - 1:
                time.sleep(0.8 * (2 ** attempt))

        if isinstance(last_err, urllib.error.URLError):
            raise ProviderError(self._probe_hint(last_err)) from last_err

        self.failures += 1
        if self.verbose:
            print(f"       [provider] 批次失败（{len(batch)} 段）：{last_err}")
        if len(batch) > 1:
            # 降级：拆成单段重试，避免一批失败拖垮整批
            return self._per_item(batch, system, glossary, dst_lang)
        return [None]

    def _per_item(self, batch, system, glossary, dst_lang) -> list[str | None]:
        out: list[str | None] = []
        for text in batch:
            got = self._one(text, system)
            if got is None:
                out.append(None)
                continue
            miss = glossary.check(text, got, dst_lang)
            out.append(got if not miss else self._retry_with_glossary(text, system, got, miss))
        return out

    def _one(self, text: str, system: str) -> str | None:
        try:
            self.requests += 1
            doc = self._post({"model": self.model, "temperature": self.temperature,
                              "messages": [{"role": "system", "content": system},
                                           {"role": "user", "content": text}]})
            return doc["choices"][0]["message"]["content"].strip()
        except Exception:                                        # noqa: BLE001
            return None

    def _retry_with_glossary(self, text, system, got, miss) -> str | None:
        nudge = ("Your previous output missed required glossary renderings "
                 f"({', '.join(miss)}). Please retranslate and satisfy them exactly.")
        try:
            self.requests += 1
            doc = self._post({"model": self.model, "temperature": self.temperature,
                              "messages": [{"role": "system", "content": system},
                                           {"role": "user", "content": text},
                                           {"role": "assistant", "content": got},
                                           {"role": "user", "content": nudge}]})
            return doc["choices"][0]["message"]["content"].strip()
        except Exception:                                        # noqa: BLE001
            return got

    def describe(self) -> dict:
        return {"provider": "openai_compat", "base_url": self.base_url,
                "model": self.model, "requests": self.requests, "failures": self.failures}


# --------------------------------------------------------------------------- #
# 批协议：@@n@@ 编号，一段一行标记
# --------------------------------------------------------------------------- #

def _encode(batch: list[str]) -> str:
    return "\n".join(f"@@{i}@@\n{t}" for i, t in enumerate(batch))


def _parse(raw: str, n: int) -> list[str | None]:
    out: list[str | None] = [None] * n
    marks = list(MARK.finditer(raw))
    if not marks:
        if n == 1:
            txt = raw.strip()
            return [txt or None]
        return out
    for k, m in enumerate(marks):
        idx = int(m.group(1))
        end = marks[k + 1].start() if k + 1 < len(marks) else len(raw)
        if 0 <= idx < n:
            body = raw[m.end():end].strip()
            out[idx] = body or None
    return out


def _host_of(url: str) -> str:
    u = urllib.parse.urlparse(url)
    return u.netloc or url
