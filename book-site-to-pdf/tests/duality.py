#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""对偶测试：每条判据都要有一个**负样本**证明它会在坏输入下 FAIL。

只验证"通过路径"的判据不算判据。

用法：
    python tests/duality.py            # 跑全部
    python tests/duality.py theme_no_hit
    python tests/duality.py --list
"""
from __future__ import annotations

import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

from book2pdf import rewrite as RW                                    # noqa: E402
from book2pdf import verify as VF                                     # noqa: E402
from book2pdf.adapters import detect                                  # noqa: E402
from book2pdf.adapters.base import SiteProbe                          # noqa: E402
from book2pdf.adapters.base import find_container_span                # noqa: E402
from book2pdf.adapters.mdbook import MdBookAdapter                    # noqa: E402
from book2pdf.ir import Chapter, TocEntry                             # noqa: E402
from book2pdf.render import (mermaid_counts_consistent, mermaid_give_up,   # noqa: E402
                             mermaid_verdict, paras_ok)
from book2pdf.report import Report                                    # noqa: E402
from book2pdf.theme import apply as theme_apply                       # noqa: E402
from book2pdf.translate import placeholders as PH                     # noqa: E402
from book2pdf.translate import run as tr_run, TranslateOptions        # noqa: E402
from book2pdf.translate.glossary import Glossary                      # noqa: E402
from book2pdf.translate.tm import TranslationMemory                   # noqa: E402

CASES: dict[str, tuple] = {}


def case(name: str, what: str):
    def deco(fn):
        CASES[name] = (fn, what)
        return fn
    return deco


# --------------------------------------------------------------------------- #
# 1. 主题：命中 0 处必须 FAIL（v1 是静默继续）
# --------------------------------------------------------------------------- #

@case("theme_no_hit", "主题补丁命中 0 处 → 必须报错，而不是静默产出深色 PDF")
def _theme_no_hit():
    html = '<html lang="zh"><head></head><body><p>x</p></body></html>'
    patches = [('const default_light_theme = "navy";', 'const default_light_theme = "light";')]
    r = Report()
    theme_apply(html, patches, r, required=True)
    codes = [n["code"] for n in r.error_notes]
    return ("theme_no_hit" in codes,
            f"errors={codes}")


@case("theme_not_required_no_false_alarm", "非 mdBook 站点无补丁时**不得**误报（反向判据）")
def _theme_not_required():
    html = "<html><head></head><body><p>x</p></body></html>"
    r = Report()
    theme_apply(html, [], r, required=False)
    return (not r.error_notes, f"errors={[n['code'] for n in r.error_notes]}")


# --------------------------------------------------------------------------- #
# 2. 容器选择器链：全失败必须返回 None（v1 是静默用整页 HTML）
# --------------------------------------------------------------------------- #

@case("container_not_found", "容器选择器链全部落空 → 必须返回 None 而不是静默退化为整页")
def _container_not_found():
    sel, s, e = find_container_span("<div>只有这个</div>", ["main", ".foo", "#bar"])
    return (sel is None and s == -1 and e == -1, f"sel={sel!r} s={s} e={e}")


# --------------------------------------------------------------------------- #
# 3. Mermaid 未渲染率超阈值必须 FAIL（v1 是超时后静默 break）
# --------------------------------------------------------------------------- #

@case("mermaid_unrendered", "Mermaid 未渲染率 >5% → error；0 未渲染 → ok")
def _mermaid():
    sev_bad, _ = mermaid_verdict(100, 50)
    sev_ok, _ = mermaid_verdict(100, 100)
    sev_warn, _ = mermaid_verdict(100, 99)
    return (sev_bad == "error" and sev_ok == "ok" and sev_warn == "warn",
            f"bad={sev_bad} warn={sev_warn} ok={sev_ok}")


@case("mermaid_give_up_early", "页面未加载 mermaid 运行时 → 必须提前放弃，不得空等总超时")
def _mermaid_give_up():
    # 短书夹具实测过：无库时旧逻辑会空等 900s。这里要求 30s 宽限后即返回 lib_missing。
    lib_missing = mermaid_give_up(31, 31, False, 0, timeout=900)
    # 有渲染过 ⇒ 不算缺库（避免把"库在跑但慢"误判成"没加载库"）
    rendered = mermaid_give_up(31, 31, False, 2, timeout=900)
    # 进展中不得放弃
    progressing = mermaid_give_up(10, 0, True, 3, timeout=900)
    # 停滞超阈值才放弃
    stalled = mermaid_give_up(200, 91, True, 5, timeout=900)
    still = mermaid_give_up(200, 10, True, 5, timeout=900)
    timeout = mermaid_give_up(901, 0, True, 5, timeout=900)
    return (lib_missing == "lib_missing" and rendered == "" and progressing == ""
            and stalled == "stalled" and still == "" and timeout == "timeout",
            f"lib={lib_missing!r} rendered={rendered!r} progressing={progressing!r} "
            f"stalled={stalled!r} still={still!r} timeout={timeout!r}")


@case("mermaid_count_inconsistent", "容器数与容器内 svg 数被并集求和 → 必须被判不自洽")
def _mermaid_counts():
    # 负样本：旧实现把「容器」与「容器内 svg」并集求和 —— 2 张图只渲染 1 张
    # 得到 done=2=total，于是判 "ok"，静默放过一张空白图。这里必须被拦下。
    bad, _ = mermaid_counts_consistent(2, 2, 1)
    # 正样本：2 张容器、2 张已渲染、2 个 svg
    ok, _ = mermaid_counts_consistent(2, 2, 2)
    # 正样本：2 张容器、1 张已渲染（含 svg）→ 仍自洽，由 verdict 报 error
    partial, _ = mermaid_counts_consistent(2, 1, 1)
    # 负样本：svg 多于已渲染容器
    more_svg, _ = mermaid_counts_consistent(2, 1, 2)
    # 负样本：已渲染数超过容器总数（并集求和的另一种签名）
    over, _ = mermaid_counts_consistent(2, 3, 3)
    # 正样本：无图
    none_ok, _ = mermaid_counts_consistent(0, 0, 0)
    return ((not bad) and ok and partial and (not more_svg) and (not over) and none_ok,
            f"bad={bad} ok={ok} partial={partial} more_svg={more_svg} "
            f"over={over} none={none_ok}")


@case("base_page_url_not_normalized", "--base 给 .../index.html 时同站资源不得被判成站外")
def _base_normalize():
    import os as _os
    from book2pdf.adapters.base import site_prefix, url_to_local
    from book2pdf.adapters.multipage import _same_site_page

    # 负样本签名：把整条 path（含 index.html）当前缀 → print.html 被判成站外
    raw_prefix = "/book/index.html"
    naive_blocked = not "/book/print.html".startswith(raw_prefix)
    # 修好之后：归一化前缀让同级文件留在站内
    fixed_prefix = site_prefix(raw_prefix)
    fixed_ok = "/book/print.html".startswith(fixed_prefix)

    # 落盘路径不得多出一层同名目录
    with tempfile.TemporaryDirectory() as d:
        root = _os.path.join(d, "site")
        p_page = url_to_local("https://h/book/print.html", "https://h/book/index.html", root)
        p_dir = url_to_local("https://h/book/print.html", "https://h/book/", root)
    same = _os.path.normpath(p_page) == _os.path.normpath(p_dir)
    no_nest = "book" not in _os.path.relpath(p_page, root).split(_os.sep)

    rel_page = _same_site_page("https://h/book/a/one.html", "https://h/book/index.html")
    return (naive_blocked and fixed_ok and same and no_nest and rel_page == "a/one.html",
            f"naive_blocked={naive_blocked} fixed_prefix={fixed_prefix!r} "
            f"fixed_ok={fixed_ok} same={same} no_nest={no_nest} rel={rel_page!r}")


@case("render_few_paragraphs", "段落数判据必须相对源 HTML；绝对阈值会误杀短书")
def _paras():
    # 负样本：渲染后段落远少于源 HTML
    bad, _ = paras_ok(1, 100)
    # 正样本：短书（源 7 段）不得被误判——旧版写死 <=100 会在这里 FAIL
    short_book, _ = paras_ok(7, 7)
    # 边界：恰好一半算过，差一段算不过
    edge_ok, _ = paras_ok(50, 100)
    edge_bad, _ = paras_ok(49, 100)
    # 未知期望值时的兜底：0 段必须失败
    zero_bad, _ = paras_ok(0, 0)
    return ((not bad) and short_book and edge_ok and (not edge_bad) and (not zero_bad),
            f"bad={bad} short={short_book} edge_ok={edge_ok} "
            f"edge_bad={edge_bad} zero={zero_bad}")


# --------------------------------------------------------------------------- #
# 4. 目录为空必须记 warn（否则跨章链接会静默退化为外网 URL）
# --------------------------------------------------------------------------- #

@case("toc_empty", "缺少 toc-*.js → 必须记 toc_empty 告警")
def _toc_empty():
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "print.html"), "w", encoding="utf-8").write(
            '<html><body><main><h1 id="a">第一章</h1><p>正文</p></main></body></html>')
        ir = MdBookAdapter().build(d, "https://x/")
    codes = [n.code for n in ir.notes]
    return ("toc_empty" in codes, f"notes={codes}")


# --------------------------------------------------------------------------- #
# 5. 对齐质量低于阈值必须 FAIL（打乱目录顺序 = 错位的负样本）
# --------------------------------------------------------------------------- #

@case("align_low_quality", "目录顺序被打乱 → 平均相似度应低于阈值并报错")
def _align_low_quality():
    titles = ["第一章 概述", "第二章 安装", "第三章 配置", "第四章 进阶", "第五章 部署"]
    chapters = [Chapter(anchor_id=f"c{i}", title=t, level=1) for i, t in enumerate(reversed(titles))]
    ir = _IR(toc=[TocEntry(path=f"p{i}.html", title=t) for i, t in enumerate(titles)],
             chapters=chapters)
    r = Report()
    RW.align_ir(ir, r)
    q = r.metrics.get("align_quality", 1.0)
    return (bool(r.error_notes), f"quality={q} errors={[n['code'] for n in r.error_notes]}")


@case("align_ok", "顺序正确时不得误报（反向判据）")
def _align_ok():
    titles = ["第一章 概述", "第二章 安装", "第三章 配置", "第四章 进阶", "第五章 部署"]
    chapters = [Chapter(anchor_id=f"c{i}", title=t, level=1) for i, t in enumerate(titles)]
    ir = _IR(toc=[TocEntry(path=f"p{i}.html", title=t) for i, t in enumerate(titles)],
             chapters=chapters)
    r = Report()
    RW.align_ir(ir, r)
    return (not r.error_notes, f"quality={r.metrics.get('align_quality')} "
                               f"errors={[n['code'] for n in r.error_notes]}")


# --------------------------------------------------------------------------- #
# 6. 空白页 / 裁切 / 书签为空 —— 用合成 PDF 做真实负样本（不必等 8 分钟渲染）
# --------------------------------------------------------------------------- #

@case("blank_pages", "含空白页的 PDF → verify 必须报 verify_blank_pages")
def _blank_pages():
    import pymupdf
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "blank.pdf")
        doc = pymupdf.open()
        doc.new_page()
        doc[0].insert_text((72, 100), "page with text")
        doc.new_page()                                   # 完全空白
        doc.save(p)
        doc.close()
        r = VF.collect(p)
        rep = Report()
        VF.assess(r, rep)
    codes = [n["code"] for n in rep.error_notes]
    return ("verify_blank_pages" in codes, f"blank={r.blank_pages} errors={codes}")


@case("clipped", "图形触底的 PDF → verify 必须报 verify_clipped")
def _clipped():
    import pymupdf
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "clip.pdf")
        doc = pymupdf.open()
        pg = doc.new_page()
        pg.insert_text((72, 100), "text")
        h = pg.rect.height
        pg.draw_rect(pymupdf.Rect(50, h - 42, 300, h - 1))     # 触底
        doc.save(p)
        doc.close()
        r = VF.collect(p)
        rep = Report()
        VF.assess(r, rep)
    codes = [n["code"] for n in rep.error_notes]
    return ("verify_clipped" in codes, f"clipped={r.clipped_pages} errors={codes}")


@case("no_bookmarks", "无书签的 PDF → verify 必须报 verify_no_bookmarks")
def _no_bookmarks():
    import pymupdf
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "nobook.pdf")
        doc = pymupdf.open()
        doc.new_page().insert_text((72, 100), "content")
        doc.save(p)
        doc.close()
        r = VF.collect(p)
        rep = Report()
        VF.assess(r, rep)
    codes = [n["code"] for n in rep.error_notes]
    return ("verify_no_bookmarks" in codes, f"toc={r.metrics['toc_entries']} errors={codes}")


# --------------------------------------------------------------------------- #
# 7. 占位符不守恒 → 该段回退原文并报错
# --------------------------------------------------------------------------- #

class _DroppingProvider:
    """故意吃掉占位符的 provider —— 模拟机器翻译破坏内联标签。"""
    tag = "test:dropping"

    def translate(self, batch, *, src_lang, dst_lang, glossary):
        return [PH.PH_RE.sub("", t) for t in batch]

    def describe(self):
        return {"provider": "test:dropping"}


@case("placeholder_broken", "provider 吃掉占位符 → 该段必须被判 broken 并回退原文")
def _placeholder_broken():
    html = "<p>请阅读 <a href='#x'>前置知识</a> 后运行 <code>git add</code>。</p>"
    with tempfile.TemporaryDirectory() as d:
        rep = Report()
        tm = TranslationMemory(os.path.join(d, "tm.json"), target_lang="en",
                               glossary_hash="h", provider_tag="test:dropping")
        res = tr_run(html, TranslateOptions(mode="translated", target_lang="en",
                                            segments_path=os.path.join(d, "seg.jsonl")),
                     _DroppingProvider(), Glossary(), tm, rep)
    codes = [n["code"] for n in rep.error_notes]
    return (res.broken > 0 and "placeholder_broken" in codes,
            f"broken={res.broken} errors={codes}")


# --------------------------------------------------------------------------- #
# 8. TM 键必须含术语表哈希 —— 换术语表后不得复用旧译文
# --------------------------------------------------------------------------- #

@case("tm_glossary_invalidation", "换了术语表 → 旧 TM 条目不命中（否则静默复用错误译文）")
def _tm_invalidation():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "tm.json")
        a = TranslationMemory(p, target_lang="en", glossary_hash="AAA", provider_tag="prov")
        a.put("key1", "old translation")
        a.save()
        b = TranslationMemory(p, target_lang="en", glossary_hash="BBB", provider_tag="prov")
        got_same = TranslationMemory(p, target_lang="en", glossary_hash="AAA",
                                     provider_tag="prov").get("key1")
        got_new = b.get("key1")
    return (got_new is None and got_same == "old translation",
            f"same_glossary={got_same!r} new_glossary={got_new!r}")

@case("tm_provider_invalidation", "换了 provider → 旧 TM 条目同样不得命中")
def _tm_provider_invalidation():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "tm.json")
        TranslationMemory(p, target_lang="en", glossary_hash="A",
                          provider_tag="prov1").__class__  # noqa: B018
        a = TranslationMemory(p, target_lang="en", glossary_hash="A", provider_tag="prov1")
        a.put("k", "v")
        a.save()
        b = TranslationMemory(p, target_lang="en", glossary_hash="A", provider_tag="prov2")
    return (b.get("k") is None, f"got={b.get('k')!r}")


# --------------------------------------------------------------------------- #
# 9. 适配器探测：把 MkDocs 当 mdBook 喂，必须选对
# --------------------------------------------------------------------------- #

@case("adapter_detect_mkdocs", "MkDocs 站（无 print.html）不得被判成 mdBook")
def _detect_mkdocs():
    probe = SiteProbe(base="https://x/", home_html="<meta name='generator' content='mkdocs-1.5'>",
                      has_mkdocs_meta=True, has_mkdocs_body=True, has_print=False)
    chosen, scores = detect(probe, "auto", Report())
    return (chosen.name == "mkdocs" and scores["mkdocs"] > scores["mdbook"],
            f"chosen={chosen.name} scores={scores}")


@case("adapter_detect_mdbook", "mdBook 站必须判成 mdbook（反向判据）")
def _detect_mdbook():
    probe = SiteProbe(base="https://x/", has_print=True, print_size=4_000_000,
                      home_size=20_000, has_toc_js=True, has_mdbook_marker=True)
    chosen, scores = detect(probe, "auto", Report())
    return (chosen.name == "mdbook", f"chosen={chosen.name} scores={scores}")


# --------------------------------------------------------------------------- #
# 10. 链接重写后不得残留相对路径（残留 = PDF 里点了跳空）
# --------------------------------------------------------------------------- #

@case("residual_links", "无法映射的相对链接必须退化为绝对 URL，绝不留下相对路径")
def _residual_links():
    ir = _IR(toc=[TocEntry(path="a/one.html", title="第一章 概述")],
             chapters=[Chapter(anchor_id="h-one", title="第一章 概述", level=1)])
    r = Report()
    idx = RW.align_ir(ir, r)
    body, finalize = RW.make_resolver(ir, idx, {"h-one"}, "https://site/book/", None, Report())
    out = body('<p><a href="a/one.html">一</a><a href="ghost/missing.html">二</a>'
               '<a href="../../examples/x">三</a></p>')
    finalize(3)
    relatives = re.findall(r'href="(?!#|https?:|mailto:)([^"]+)"', out)
    return (not relatives and 'href="#h-one"' in out,
            f"relatives={relatives} out={out[:160]}")


# --------------------------------------------------------------------------- #

class _IR:
    """轻量 BookIR 替身，避免测试里构造完整 HTML。"""

    def __init__(self, toc, chapters):
        from book2pdf.ir import BookIR
        self._ir = BookIR(base_url="https://site/book/", source_kind="test",
                          full_html="", container_selector="main", toc=toc, chapters=chapters)

    def __getattr__(self, item):
        return getattr(self._ir, item)


def main(argv: list[str]) -> int:
    if "--list" in argv or not argv[1:]:
        if "--list" in argv:
            for n, (_, what) in CASES.items():
                print(f"  {n:34s} {what}")
            return 0
        names = list(CASES)
    else:
        names = [a for a in argv[1:] if not a.startswith("-")]

    unknown = [n for n in names if n not in CASES]
    if unknown:
        print(f"未知判据：{unknown}\n可用：{sorted(CASES)}")
        return 2

    print(f"对偶测试（{len(names)} 条判据，期望坏输入被拒）")
    print("-" * 78)
    bad = 0
    for n in names:
        fn, what = CASES[n]
        try:
            ok, detail = fn()
        except Exception as e:                                    # noqa: BLE001
            ok, detail = False, f"异常 {type(e).__name__}: {e}"
        mark = "PASS" if ok else "FAIL"
        if not ok:
            bad += 1
        print(f"[{mark}] {n:34s} {detail}")
        if not ok:
            print(f"        判据：{what}")
    print("-" * 78)
    print(f"{len(names) - bad}/{len(names)} 通过"
          + ("" if not bad else f"  —— {bad} 条判据没有区分力或实现有缺陷"))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
