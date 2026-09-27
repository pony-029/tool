# -*- coding: utf-8 -*-
"""Provider 工厂。"""
from __future__ import annotations

from .base import Provider, ProviderError, build_system_prompt, estimate_budget
from .null import NullProvider

__all__ = ["Provider", "ProviderError", "build_provider", "build_system_prompt",
           "estimate_budget", "NullProvider"]


def build_provider(cfg: dict, verbose: bool = True) -> Provider:
    """按配置构造 provider。`local` 就是指向本地端点的 openai_compat。"""
    name = (cfg.get("provider") or "local").lower()

    if name == "null":
        return NullProvider()

    if name in ("local", "openai_compat", "openai", "deepseek"):
        from .openai_compat import OpenAICompatProvider
        base_url = cfg.get("base_url") or ""
        if name == "local" and not base_url:
            base_url = "http://127.0.0.1:15721/v1"
        if name == "openai" and not base_url:
            base_url = "https://api.openai.com/v1"
        return OpenAICompatProvider(base_url=base_url,
                                    model=cfg.get("model") or "",
                                    api_key=cfg.get("api_key") or "",
                                    verbose=verbose)

    if name == "anthropic":
        from .anthropic import AnthropicProvider
        return AnthropicProvider(base_url=cfg.get("base_url") or "https://api.anthropic.com/v1",
                                 model=cfg.get("model") or "",
                                 api_key=cfg.get("api_key") or "",
                                 verbose=verbose)

    raise ProviderError(f"未知 provider：{name}（可选 local / openai / anthropic / null）")
