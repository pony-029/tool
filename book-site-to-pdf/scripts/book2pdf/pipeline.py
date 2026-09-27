# -*- coding: utf-8 -*-
"""编排层：镜像 → 适配 → 重写 → 主题 → 样式 → 译文 → 目录页 → 渲染 → 校验。

阶段级断点落在 `<workdir>/state.json`；译文级断点落在 TM。
"""
from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass, field

from . import render as render_mod
from . import rewrite as rewrite_mod
from . import style as style_mod
from . import theme as theme_mod
from . import verify as verify_mod
from .adapters import HttpFetcher, detect, probe_site
from .adapters.base import SiteProbe, site_root
from .ir import BookIR
from .mirror import crawl
from .report import (EXIT_BUILD_FAIL, EXIT_ENV, EXIT_PASS, EXIT_NAMES,
                     BuildAbort, Report)
from .translate import TranslateOptions, apply_mode
from .translate import run as translate_run
from .translate.glossary import Glossary
from .translate.providers import build_provider
from .translate.tm import TranslationMemory

STATE_FILE = "state.json"


@dataclass
class BuildOptions:
    base: str
    out: str
    workdir: str = ""
    adapter: str = "auto"
    print_path: str = "print.html"
    paper: str = "A4"
    max_diagram_height: int | str = "auto"
    toc_depth: int = 2
    footer: bool = True
    mode: str = "mono"
    target_lang: str = ""
    source_lang: str = "zh"
    provider: str = "local"
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    glossary: str = ""
    tm: str = ""
    translate_mermaid: bool = False
    concurrency: int = 4
    budget_tokens: int = 0
    limit: int = 0
    keep: bool = False
    port: int = 0
    browser_channel: str = "msedge"
    report_path: str = ""
    strict: bool = False
    fail_on: str = "warn"
    resume: bool = False
    yes: bool = False
    extra: dict = field(default_factory=dict)


def run(opts: BuildOptions) -> int:
    out_pdf = os.path.abspath(opts.out)
    work = os.path.abspath(opts.workdir or os.path.splitext(out_pdf)[0] + "-build")
    site = os.path.join(work, "site")
    os.makedirs(work, exist_ok=True)
    os.makedirs(os.path.dirname(out_pdf) or ".", exist_ok=True)

    report = Report(target=opts.base)
    report.source = {"base": opts.base, "mode": opts.mode,
                     "target_lang": opts.target_lang, "paper": opts.paper,
                     "adapter": opts.adapter, "provider": opts.provider}
    state = _load_state(work) if opts.resume else {}

    local_dir = _local_dir(opts.base)
    # base 一律归一到**站点根目录**：`--base` 给 `.../index.html` 这类页面 URL 时，
    # 若原样当路径前缀用，同站资源会被判成站外（镜像只落 1 个文件）。
    base_url = site_root(opts.base) if not local_dir else _file_url(local_dir)

    try:
        if not local_dir:
            fetcher = HttpFetcher()
            report.stage("probe")
            probe = probe_site(base_url, fetcher, opts.print_path)
            # 打印**摘要**而不是 probe.report()：后者含 home_html 全文（实测 28KB 一行），
            # 会让日志不可读、也把页面内容铺得到处都是。完整证据留在 report.json。
            print(f"[探测] {base_url}｜print={probe.has_print}({probe.print_size}B) "
                  f"首页={probe.home_size}B toc={probe.has_toc_js} "
                  f"mdbook={probe.has_mdbook_marker} mkdocs={probe.has_mkdocs_meta} "
                  f"docusaurus={probe.has_docusaurus_data} gitbook={probe.has_gitbook}")
            report.metric("probe", {k: v for k, v in probe.report().items() if k != "home_html"})
        else:
            probe = _probe_local(local_dir, opts.print_path)
            fetcher = None
            print(f"[探测] 本地目录模式：{local_dir}")

        adapter, scores = detect(probe, opts.adapter, report)

        # ---- 镜像 ----
        site_is_user_dir = bool(local_dir) and os.path.abspath(site) == os.path.abspath(local_dir)
        if state.get("mirror_done") and os.path.isdir(site) and os.listdir(site):
            print(f"[镜像] 断点续跑：沿用已有镜像 {site}")
            report.stage("mirror", resumed=True)
        elif local_dir:
            # **不要**把 local_dir 直接当工作目录：那样 print-fixed.html / merged.html /
            # segments.jsonl 会被写进**用户指定的源目录**，污染输入。
            # 复制一份到 workdir 下，行为与远程模式一致，源目录保持只读。
            if not site_is_user_dir:
                shutil.copytree(local_dir, site, dirs_exist_ok=True,
                                ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))
            print(f"[镜像] 本地目录模式：{local_dir} → 工作副本 {site}")
            report.stage("mirror", local=True, source_dir=local_dir)
        else:
            report.stage("mirror")
            res = crawl(base_url, [(e or "") for e in adapter.entry_urls(base_url, opts.print_path)],
                        site, follow_html=getattr(adapter, "follow_html", False),
                        print_path=opts.print_path)
            report.metrics_update({"mirror": res.summary()})
            if not res.files:
                raise BuildAbort(
                    f"镜像失败：没有抓到任何文件。请确认 {base_url} 可访问"
                    f"（失败 {len(res.errors)} 个）", EXIT_ENV)
            _save_state(work, {"mirror_done": True})

        # ---- 适配 → IR ----
        report.stage("adapter_build")
        ir = adapter.build(site, base_url, opts.print_path, fetcher=fetcher, probe=probe)
        report.merge_ir_notes(ir)
        report.metrics_update({"ir": ir.summary()})
        print(f"[适配] {ir.source_kind}：目录 {len(ir.toc)} 条 / 章节 {len(ir.chapters)} 个 / "
              f"容器 {ir.container_selector}")

        if ir.errors:
            report.print_summary()
            raise BuildAbort("适配阶段存在致命问题，已停止（避免产出坏 PDF）")

        # ---- 多语言循环 ----
        langs = [""] if opts.mode == "mono" else [
            x.strip() for x in (opts.target_lang or "").split(",") if x.strip()]
        if opts.mode != "mono" and not langs:
            raise BuildAbort("--mode 非 mono 时必须指定 --target-lang（如 en,ja）", EXIT_ENV)

        tm_path = opts.tm or os.path.join(work, "tm.json")
        glossary = Glossary.load(opts.glossary)
        provider = None
        if opts.mode != "mono":
            provider = build_provider({**opts.extra, "provider": opts.provider,
                                       "base_url": opts.base_url, "model": opts.model,
                                       "api_key": opts.api_key})
        report.metric("glossary", glossary.describe())
        report.metric("provider", provider.describe() if provider else {"provider": "(none)"})

        pdfs: list[tuple[str, str]] = []
        for lang in (langs if opts.mode != "mono" else [""]):
            tag = lang or "src"
            html_path = os.path.join(site, f"print-fixed{('.' + lang) if lang else ''}.html")
            pdf_path = out_pdf if len(langs) == 1 else _lang_out(out_pdf, lang)
            tm = TranslationMemory(tm_path, target_lang=lang or opts.source_lang,
                                   glossary_hash=glossary.hash(),
                                   provider_tag=provider.tag if provider else "none")
            _build_one(opts, report, ir, html_path, pdf_path, lang, tm, glossary, provider, work)
            pdfs.append((lang or "原文", pdf_path))

        # ---- 校验 ----
        for label, pdf in pdfs:
            report.stage("verify")
            verify_mod.run(pdf, report)

        # 清理工作副本；**绝不删除用户指定的源目录**
        if not opts.keep and not site_is_user_dir:
            shutil.rmtree(site, ignore_errors=True)

    except BuildAbort as e:
        report.error("build_abort", str(e), "pipeline")
        code = e.exit_code
    except FileNotFoundError as e:
        report.error("missing_input", str(e), "pipeline")
        code = EXIT_ENV
    except Exception as e:                                       # noqa: BLE001
        import traceback
        report.error("unexpected", f"{type(e).__name__}: {e}", "pipeline")
        traceback.print_exc()
        code = EXIT_BUILD_FAIL
    else:
        code = report.exit_code(strict=opts.strict, fail_on=opts.fail_on)

    ridx = opts.report_path or os.path.join(work, "report.json")
    report.dump(ridx)
    report.print_summary()
    print(f"[报告] {ridx}")
    print(f"[结论] {EXIT_NAMES.get(code, code)}（退出码 {code}）")
    return code


# --------------------------------------------------------------------------- #

def _build_one(opts: BuildOptions, report: Report, ir: BookIR, html_path: str, pdf_path: str,
               lang: str, tm: TranslationMemory, glossary: Glossary, provider, work: str) -> None:
    report.stage("prepare")
    html = ir.full_html

    # 坑 2：先去掉源站的分页占位 div，改由 CSS 统一分页
    html = rewrite_mod.remove_page_break_divs(html, report)

    # 定位正文容器（选择器链；全失败即 FAIL——v1 是静默用整页）
    sel, s, e = _container_span(html, ir.container_selector)
    if sel is None:
        report.error("container_not_found",
                     f"容器选择器 {ir.container_selector!r} 未匹配到：正文抽取不确定，已停止",
                     "prepare")
        raise BuildAbort(f"找不到正文容器 {ir.container_selector!r}，拒绝产出坏 PDF")
    body = html[s:e]

    # 链接重写
    ids = set(re.findall(r'id="([^"]+)"', body))
    rewrite_body, finalize = rewrite_mod.make_resolver(
        ir, rewrite_mod.align_ir(ir, report), ids, ir.base_url,
        rewrite_mod.detect_repo(ir.full_html), report)
    body = rewrite_body(body)
    finalize(len(re.findall(r'href="', body)))

    segs = []
    if opts.mode != "mono":
        t_opts = TranslateOptions(
            mode=opts.mode, src_lang=opts.source_lang, target_lang=lang,
            dry_run=(opts.provider == "null"), limit=opts.limit,
            budget_tokens=opts.budget_tokens, assume_yes=opts.yes,
            concurrency=opts.concurrency, translate_mermaid=opts.translate_mermaid,
            segments_path=os.path.join(os.path.dirname(html_path),
                                       f"segments{('.' + lang) if lang else ''}.jsonl"))
        res = translate_run(body, t_opts, provider, glossary, tm, report)
        segs = res.segments
        if opts.provider == "null":
            report.warn("dry_run", "provider=null 干跑：未产出译文，只写了段清单", "translate")
            return
        body = apply_mode(body, segs, opts.mode if opts.mode != "both" else "translated")
        report.metric("tr_changed_headings", _count_translated_headings(segs))

    # 可点击目录页（**在翻译之后**生成 → 与正文标题天然一致）
    toc_html, toc_n = rewrite_mod.build_toc_page(body, opts.toc_depth)
    report.metric("toc_page_entries", toc_n)
    print(f"[目录] 生成可点击目录页（{toc_n} 条，深度 h{opts.toc_depth}）")
    html = html[:s] + toc_html + body + html[e:]

    # 主题锁定
    html = theme_mod.apply(html, ir.theme_patches, report, lang=lang or None,
                          required=ir.theme_required)

    # 样式注入
    maxh = (style_mod.auto_max_diagram_height(opts.paper)
            if opts.max_diagram_height == "auto" else int(opts.max_diagram_height))
    report.metric("max_diagram_height", maxh)
    css = style_mod.build_css(max_diagram_height=maxh, toc_depth=opts.toc_depth,
                             footer=opts.footer)
    html = html.replace("</head>", f"<style>{css}</style>\n</head>", 1)

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    report.metric("html_out", html_path)
    report.metric("html_chars", len(html))
    print(f"[输出] {html_path}（{len(html)} 字符）")

    # 双语模式：额外产出一份对照 HTML/PDF
    if opts.mode == "both":
        bi_html = os.path.join(os.path.dirname(html_path),
                               f"bilingual{('.' + lang) if lang else ''}.html")
        bi = _rebuild_with_body(html, body, apply_mode(body, segs, "bilingual"))
        with open(bi_html, "w", encoding="utf-8") as f:
            f.write(bi)
        render_mod.render(os.path.dirname(html_path), os.path.basename(bi_html),
                          _lang_out(pdf_path, "bilingual"), report=report,
                          port=opts.port, browser_channel=opts.browser_channel,
                          paper=opts.paper, footer=opts.footer,
                          container_selector=ir.container_selector,
                          doc_lang=lang or opts.source_lang)
        report.warn("bilingual_extra",
                    f"另有双语对照产物：{_lang_out(pdf_path, 'bilingual')}（未计入主校验）",
                    "render")

    render_mod.render(os.path.dirname(html_path), os.path.basename(html_path), pdf_path,
                      report=report, port=opts.port,
                      browser_channel=opts.browser_channel, paper=opts.paper,
                      footer=opts.footer, container_selector=ir.container_selector,
                      doc_lang=lang or opts.source_lang,
                      # 段落数相对校验：拿源 HTML 的 <p> 数当基准，避免绝对阈值误杀短书
                      expect_paras=len(re.findall(r"<p[\s>]", body)))
    _save_state(work, {"html_done": html_path, "render_done": True})


def _rebuild_with_body(html: str, old_body: str, new_body: str) -> str:
    i = html.find(old_body)
    if i < 0:
        return html
    return html[:i] + new_body + html[i + len(old_body):]


def _container_span(html: str, selector: str) -> tuple[str | None, int, int]:
    from .adapters.base import find_container_span
    chain = [selector] + [c for c in ("main", ".md-content__inner", "article", "#content",
                                      ".content", "body") if c != selector]
    return find_container_span(html, chain)


def _count_translated_headings(segs) -> int:
    return sum(1 for s in segs
               if len(s.kind) == 2 and s.kind[0] == "h" and s.status in ("ok", "tm"))


def _lang_out(path: str, lang: str) -> str:
    root, ext = os.path.splitext(path)
    return f"{root}.{lang}{ext}"


def _local_dir(base: str) -> str | None:
    """本地目录模式：允许给目录，也允许给目录里的某个页面文件。"""
    if "://" in base:
        return None
    p = os.path.abspath(base)
    if os.path.isdir(p):
        return p
    if os.path.isfile(p):                    # 给了具体页面 → 用其所在目录
        return os.path.dirname(p)
    return None


def _file_url(path: str) -> str:
    url = "file:///" + path.replace("\\", "/").lstrip("/")
    return url if url.endswith("/") else url + "/"


def _probe_local(site_dir: str, print_path: str) -> SiteProbe:
    p = SiteProbe(base=_file_url(site_dir))
    for cand in ("index.html", ""):
        f = os.path.join(site_dir, cand or "index.html")
        if os.path.exists(f):
            p.home_html = open(f, encoding="utf-8", errors="replace").read()
            p.home_size = len(p.home_html)
            break
    pr = os.path.join(site_dir, print_path)
    if os.path.exists(pr):
        txt = open(pr, encoding="utf-8", errors="replace").read()
        p.has_print = True
        p.print_size = len(txt)
        if "default_light_theme" in txt or "default_dark_theme" in txt:
            p.has_mdbook_marker = True
        if re.search(r"toc-[0-9a-f]+\.js", txt):
            p.has_toc_js = True
    return p


def _state_path(work: str) -> str:
    return os.path.join(work, STATE_FILE)


def _load_state(work: str) -> dict:
    p = _state_path(work)
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:                                        # noqa: BLE001
            return {}
    return {}


def _save_state(work: str, patch: dict) -> None:
    st = _load_state(work)
    st.update(patch)
    with open(_state_path(work), "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
