# -*- coding: utf-8 -*-
"""命令行入口。

**向后兼容 v1**：`--base / --out / --workdir / --print-path / --max-diagram-height /
--port / --keep` 语义不变，老的命令行原样可跑。
"""
from __future__ import annotations

import argparse
import sys

from . import config as cfg_mod
from .config import DEFAULTS
from .pipeline import BuildOptions, run
from .translate import MODES


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="book2pdf",
        description="把书站（mdBook / MkDocs / GitBook / Docusaurus / 任意站点）导出为"
                    "可导航 PDF，可选产出译文。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  book2pdf --base https://host/book/ --out out/书.pdf\n"
            "  book2pdf --base https://host/book/ --out out/书.pdf --mode mono\n"
            "  book2pdf --base https://host/book/ --out out/书.pdf \\\n"
            "           --mode translated --target-lang en --provider local \\\n"
            "           --base-url http://127.0.0.1:15721/v1 --model deepseek-chat\n"
            "  book2pdf --base https://host/book/ --out out/书.pdf --provider null   # 只审阅段清单\n"
        ))

    g = ap.add_argument_group
    ap.add_argument("--base", required=True, help="书站根 URL，或本地目录（离线复用镜像）")
    ap.add_argument("--out", required=True, help="输出 PDF 路径")
    ap.add_argument("--workdir", default=None, help="构建目录（默认 <out>-build）")
    ap.add_argument("--report", dest="report_path", default=None,
                    help="report.json 路径（默认 <workdir>/report.json）")

    s = g("源")
    s.add_argument("--adapter", default=None,
                   choices=["auto", "mdbook", "mkdocs", "gitbook", "docusaurus", "generic"],
                   help="站点适配器；默认 auto 按置信度探测")
    s.add_argument("--print-path", default=None, help="mdBook 合并页路径（默认 print.html）")

    p = g("版面")
    p.add_argument("--paper", default=None, choices=["A4", "Letter", "A3"])
    p.add_argument("--max-diagram-height", default=None,
                   help="Mermaid 最大像素高；'auto' 按纸张可用高度计算（A4→880）")
    p.add_argument("--toc-depth", type=int, default=None, choices=[1, 2, 3],
                   help="目录页深度（默认 2）")
    p.add_argument("--no-footer", dest="footer", action="store_false", default=None,
                   help="不打印页脚页码")

    t = g("译文")
    t.add_argument("--mode", default=None, choices=list(MODES),
                   help="mono(默认) / translated / bilingual / both")
    t.add_argument("--target-lang", default=None, help="目标语言，逗号分隔，如 en,ja")
    t.add_argument("--source-lang", default=None, help="源语言（默认 zh）")
    t.add_argument("--provider", default=None,
                   choices=["local", "openai", "anthropic", "null"],
                   help="翻译后端；local=指向本地 OpenAI 兼容端点")
    t.add_argument("--base-url", default=None, help="OpenAI 兼容端点，如 http://127.0.0.1:15721/v1")
    t.add_argument("--model", default=None, help="模型名")
    t.add_argument("--glossary", default=None, help="术语表（yaml/json）")
    t.add_argument("--tm", default=None, help="翻译记忆路径（默认 <workdir>/tm.json）")
    t.add_argument("--translate-mermaid", action="store_true", default=None,
                   help="翻译 Mermaid 图内文字（默认关：只占全量 1.4%，却有语法破坏风险）")
    t.add_argument("--limit", type=int, default=None, help="只翻前 N 个段（试跑）")
    t.add_argument("--budget-tokens", type=int, default=None,
                   help="token 预算；超出且无 --yes 则拒绝执行")
    t.add_argument("--concurrency", type=int, default=None, help="并发请求数（默认 4）")
    t.add_argument("--yes", action="store_true", default=None, help="确认超出预算仍继续")

    e = g("工程")
    e.add_argument("--port", type=int, default=None, help="本地服务端口；0=自动选空闲（默认）")
    e.add_argument("--browser-channel", default=None,
                   help="msedge / chrome / ''=用 playwright 自带 Chromium")
    e.add_argument("--keep", action="store_true", default=None, help="保留下载的镜像")
    e.add_argument("--resume", action="store_true", default=None, help="从阶段断点续跑")
    e.add_argument("--strict", action="store_true", default=None, help="把所有警告升级为失败")
    e.add_argument("--fail-on", default=None, choices=["warn", "error"],
                   help="什么级别算失败（默认 warn）")
    e.add_argument("--print-config", action="store_true", help="打印示例配置文件后退出")
    return ap


def _cli_dict(ns: argparse.Namespace) -> dict:
    out = {}
    for k in DEFAULTS:
        v = getattr(ns, k, None)
        if v is not None:
            out[k] = v
    return out


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    ns = ap.parse_args(argv)

    if ns.print_config:
        print(cfg_mod.default_config_file())
        return 0

    try:
        file_cfg = cfg_mod.load_file()
    except ValueError as e:
        print(f"[错误] {e}", file=sys.stderr)
        return 4

    resolved = cfg_mod.resolve(_cli_dict(ns), file_cfg)

    opts = BuildOptions(
        base=ns.base, out=ns.out,
        workdir=ns.workdir or "",
        adapter=resolved["adapter"], print_path=resolved["print_path"],
        paper=resolved["paper"], max_diagram_height=resolved["max_diagram_height"],
        toc_depth=int(resolved["toc_depth"]), footer=bool(resolved["footer"]),
        mode=resolved["mode"], target_lang=resolved["target_lang"],
        source_lang=resolved["source_lang"], provider=resolved["provider"],
        base_url=resolved["base_url"], model=resolved["model"],
        api_key=resolved.get("api_key", ""), glossary=resolved["glossary"],
        tm=resolved["tm"], translate_mermaid=bool(resolved["translate_mermaid"]),
        concurrency=int(resolved["concurrency"]), budget_tokens=int(resolved["budget_tokens"]),
        limit=ns.limit or 0, keep=bool(ns.keep), port=ns.port or 0,
        browser_channel=resolved.get("browser_channel", "msedge"),
        report_path=ns.report_path or "", strict=bool(ns.strict),
        fail_on=resolved.get("fail_on", "warn"), resume=bool(ns.resume),
        yes=bool(resolved.get("yes")),
    )
    return run(opts)
