# -*- coding: utf-8 -*-
"""配置合并：CLI > 配置文件 > 环境变量 > 默认值。

密钥只从环境变量读，**不落盘**。
"""
from __future__ import annotations

import json
import os

CONFIG_PATH = os.path.expanduser("~/.book2pdf/config.json")

DEFAULTS = {
    "adapter": "auto",
    "print_path": "print.html",
    "paper": "A4",
    "max_diagram_height": "auto",
    "toc_depth": 2,
    "footer": True,
    "mode": "mono",
    "target_lang": "",
    "provider": "local",
    "base_url": "",
    "model": "",
    "glossary": "",
    "tm": "",
    "translate_mermaid": False,
    "concurrency": 4,
    "budget_tokens": 0,
    "source_lang": "zh",
}

# 只允许从环境变量读取的键（不落盘）
ENV_KEYS = {
    "BOOK2PDF_BASE_URL": "base_url",
    "BOOK2PDF_MODEL": "model",
    "BOOK2PDF_API_KEY": "api_key",
    "BOOK2PDF_PROVIDER": "provider",
}

# 允许写进配置文件的键（白名单，防止误把密钥写进去）
FILE_KEYS = set(DEFAULTS) | {"api_key_env"}


def load_file(path: str | None = None) -> dict:
    p = path or CONFIG_PATH
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:                                   # noqa: BLE001
        raise ValueError(f"配置文件无法解析：{p} — {e}") from e
    cfg = {k: v for k, v in raw.items() if k in FILE_KEYS}
    leaked = [k for k in raw if k in ("api_key", "token", "auth_token", "password")]
    if leaked:
        raise ValueError(
            f"配置文件含敏感键 {leaked}：请改用环境变量（如 BOOK2PDF_API_KEY），配置文件不落盘密钥。")
    return cfg


def from_env(env: dict | None = None) -> dict:
    env = env if env is not None else os.environ
    out = {}
    for k, target in ENV_KEYS.items():
        v = env.get(k)
        if v:
            out[target] = v
    return out


def resolve(cli: dict, file_cfg: dict | None = None, env: dict | None = None) -> dict:
    """优先级：CLI（非 None）> 配置文件 > 环境变量 > 默认值。"""
    cfg = dict(DEFAULTS)
    cfg.update(from_env(env))
    cfg.update(file_cfg or {})
    for k, v in (cli or {}).items():
        if v is not None:
            cfg[k] = v
    return cfg


def default_config_file() -> str:
    """生成一份示例配置文件内容（不写盘，由调用方决定）。"""
    return json.dumps({
        "provider": "local",
        "base_url": "http://127.0.0.1:15721/v1",
        "model": "deepseek-chat",
        "concurrency": 4,
        "paper": "A4",
        "mode": "mono",
    }, ensure_ascii=False, indent=2)
