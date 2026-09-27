#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""单元测试：纯逻辑部分（不联网、不渲染）。

    python tests/run_tests.py
    python tests/run_tests.py -v
"""
from __future__ import annotations

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

from book2pdf import config as CFG                                    # noqa: E402
from book2pdf import rewrite as RW                                    # noqa: E402
from book2pdf import style as ST                                      # noqa: E402
from book2pdf.adapters.mdbook import MdBookAdapter                    # noqa: E402
from book2pdf.adapters.mkdocs import MkDocsAdapter                    # noqa: E402
from book2pdf.report import (EXIT_BUILD_FAIL, EXIT_ENV, EXIT_PASS,    # noqa: E402
                             EXIT_VERIFY_FAIL, EXIT_WARN, Report)
from book2pdf.translate import placeholders as PH                     # noqa: E402
from book2pdf.translate import segmenter as SG                        # noqa: E402
from book2pdf.translate.glossary import Glossary                      # noqa: E402
from book2pdf.translate.tm import TranslationMemory                   # noqa: E402

VERBOSE = "-v" in sys.argv
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str):
    def deco(fn):
        try:
            detail = fn() or ""
            RESULTS.append((name, True, str(detail)))
        except AssertionError as e:
            RESULTS.append((name, False, str(e)))
        except Exception as e:                                        # noqa: BLE001
            RESULTS.append((name, False, f"{type(e).__name__}: {e}"))
        return fn
    return deco


def eq(got, want, what=""):
    assert got == want, f"{what} 期望 {want!r}，实际 {got!r}"
    return f"{what}={got!r}"


# --------------------------------------------------------------------------- #
# 占位符
# --------------------------------------------------------------------------- #

@check("placeholders.roundtrip")
def _ph_roundtrip():
    frag = '请阅读 <a href="#x">前置知识</a> 后运行 <code>git add</code>。'
    text, slots = PH.protect(frag)
    eq(len(slots), 2, "槽位数")
    assert PH.PH_RE.search(text), "应有占位符"
    eq(PH.restore(text, slots), frag, "还原")
    return f"text={text!r}"


@check("placeholders.nested")
def _ph_nested():
    frag = "<strong>加粗里有 <code>code</code> 和 <em>斜体</em></strong>"
    text, slots = PH.protect(frag)
    eq(len(slots), 3, "3 个内联元素（由内向外）")
    eq(PH.restore(text, slots), frag, "还原")


@check("placeholders.void_and_fullwidth")
def _ph_void():
    text, slots = PH.protect('图 <img src="a.png"> 与换行<br>')
    eq(len(slots), 2, "void 标签也占位")
    assert PH.conserved(text, PH.normalize(text)), "自身守恒"
    fw = PH.normalize(PH.make("", 0).replace("0", "０"))
    eq(PH.signature(fw), [0], "全角数字被归一")


@check("placeholders.conservation_detects_loss")
def _ph_loss():
    src, _ = PH.protect("先 <strong>A</strong> 后 <code>B</code>")
    assert not PH.conserved(src, PH.PH_RE.sub("", src)), "删掉占位符必须判为不守恒"
    eq(PH.missing(src, PH.PH_RE.sub("", src)), [0, 1], "丢失清单")
    return "守恒校验有效"


# --------------------------------------------------------------------------- #
# 段切分
# --------------------------------------------------------------------------- #

@check("segmenter.skips_code_and_scripts")
def _seg_skip():
    html = ("<p>正文一</p><pre><code>var a = 1; // 注释不该被翻</code></pre>"
            "<script>var x='不要翻'</script><p>正文二</p>")
    segs = SG.segment(html)
    kinds = [s.kind for s in segs]
    eq(kinds, ["p", "p"], "只切出两个 p")
    joined = "".join(s.text for s in segs)
    assert "注释不该被翻" not in joined and "不要翻" not in joined, "代码/脚本未泄漏进段"
    return f"kinds={kinds}"


@check("segmenter.innermost_only")
def _seg_innermost():
    html = "<table><tr><td>单元格文字</td><td>第二格</td></tr></table>"
    segs = SG.segment(html)
    eq([s.kind for s in segs], ["td", "td"], "td 内没有块时取 td 本身")
    html2 = "<td><p>段落在格子里</p></td>"
    segs2 = SG.segment(html2)
    eq([s.kind for s in segs2], ["p"], "td 内有 p 时只取最内层 p，避免重复计数")


@check("segmenter.dedup_metrics_are_two_calibers")
def _seg_stats():
    html = "<p>完全一样的句子</p><p>完全一样的句子</p><p>另一个完全不同的长句子内容</p>"
    st = SG.stats(SG.segment(html))
    eq(st["segments"], 3, "段数")
    eq(st["unique_segments"], 2, "去重段数")
    assert "dedup_saving_segment_pct" in st and "dedup_saving_char_pct" in st, "两个口径都要报"
    return f"段省{st['dedup_saving_segment_pct']}% 字符省{st['dedup_saving_char_pct']}%"


@check("segmenter.apply_roundtrip")
def _seg_apply():
    html = "<p>第一段</p><p>第二段</p>"
    segs = SG.segment(html)
    for s in segs:
        s.translated = "TRANSLATED"
        s.status = "ok"
    out = SG.apply(html, segs)
    eq(out, "<p>TRANSLATED</p><p>TRANSLATED</p>", "回填")


@check("segmenter.token_calibers")
def _seg_tokens():
    lo, hi = SG.estimate_tokens(1000, 1000)
    assert lo < hi, "下限应小于上限"
    return f"1000字符(全中文) → {lo}~{hi} token"


# --------------------------------------------------------------------------- #
# 术语表
# --------------------------------------------------------------------------- #

@check("glossary.yaml_minimal_parser")
def _gloss_yaml():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "g.yaml")
        open(p, "w", encoding="utf-8").write(
            "en:\n"
            "  - src: 驾驭工程\n"
            "    dst: Harness Engineering\n"
            "    note: 书名\n"
            "  - src: 技能\n"
            "    dst: Skill\n"
            "ja:\n"
            "  - src: 技能\n"
            "    dst: スキル\n")
        g = Glossary.load(p)
    eq(len(g.for_lang("en")), 2, "en 术语数")
    eq(len(g.for_lang("ja")), 1, "ja 术语数")
    assert g.hash(), "应有哈希"
    return f"hash={g.hash()[:12]}"


@check("glossary.check_and_keep")
def _gloss_check():
    g = Glossary({"en": [("驾驭工程", "Harness Engineering", ""), ("技能", "__KEEP__", "")]})
    miss = g.check("本书讲驾驭工程与技能", "This book covers Harness Engineering.", "en")
    eq(miss, ["技能(keep)"], "未保留的 __KEEP__ 术语")
    ok = g.check("本书讲驾驭工程与技能", "Harness Engineering and 技能.", "en")
    eq(ok, [], "全部满足")
    assert "Harness Engineering" in g.prompt_block("en"), "prompt 块含术语"
    assert "keep the original" in g.prompt_block("en"), "prompt 块说明 __KEEP__"


@check("glossary.hash_changes_with_terms")
def _gloss_hash():
    a = Glossary({"en": [("甲", "A", "")]})
    b = Glossary({"en": [("甲", "B", "")]})
    assert a.hash() != b.hash(), "改术语表必须换哈希（否则 TM 会错误复用）"
    return "哈希随术语变动"


# --------------------------------------------------------------------------- #
# TM
# --------------------------------------------------------------------------- #

@check("tm.resume_hits")
def _tm_resume():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "tm.json")
        a = TranslationMemory(p, target_lang="en", glossary_hash="H", provider_tag="P")
        a.put("k1", "v1")
        a.put("k2", "v2")
        a.save()
        b = TranslationMemory(p, target_lang="en", glossary_hash="H", provider_tag="P")
        eq(b.get("k1"), "v1", "命中 1")
        eq(b.get("k2"), "v2", "命中 2")
        eq(b.get("k3"), None, "未命中")
        eq(b.summary()["hit_rate"], 0.667, "命中率")
    return "断点续跑可用"


# --------------------------------------------------------------------------- #
# 重写 / 对齐
# --------------------------------------------------------------------------- #

@check("rewrite.generic_norm_no_site_specific_words")
def _rw_norm():
    eq(RW.norm("第五章：技能（智能体）"), RW.norm("第五章技能智能体"), "标点被抹平")
    # 括注带来的差异由 _sim 的「两种归一化取较大值」吸收，而不是靠硬编码词表
    eq(round(RW._sim("Ch.5 Skills", "Ch.5 (Skills)"), 3), 1.0, "去括号后相等")
    eq(RW.norm("（技能）", True), "", "纯括号内容被去掉")
    eq(RW.norm("第一章 概述"), "第一章概述", "空白被抹平")
    return "归一化不含任何站点专有词"


@check("rewrite.multi_insertion_alignment")
def _rw_align():
    toc = ["第一章", "第二章", "第三章", "第四章"]
    h1 = ["第一章", "第二章", "多出来的一页", "第三章", "第四章"]
    idx, q, cov = RW.align(toc, h1)
    eq([idx[i] for i in range(4)], [0, 1, 3, 4], "第三章应跳过插入页")
    assert q > 0.9, f"质量应高，实际 {q}"
    eq(round(cov, 3), 1.0, "覆盖率")
    return f"quality={q:.3f} 插入页 1 个"


@check("rewrite.style_auto_height_matches_v1")
def _style_auto():
    eq(ST.auto_max_diagram_height("A4"), 880, "A4 自动图高（与 v1 手工取值一致）")
    css = ST.build_css(max_diagram_height=880, toc_depth=3)
    assert "max-height: 880px" in css, "CSS 里应含图高"
    assert ".toc-h3" in css, "toc_depth=3 应含三级目录样式"
    assert "page-break-before: always" in css, "章节分页"
    return "A4→880px"


# --------------------------------------------------------------------------- #
# 配置优先级
# --------------------------------------------------------------------------- #

@check("config.priority_cli_over_file_over_env")
def _cfg_priority():
    file_cfg = {"model": "from-file", "paper": "Letter"}
    env = {"BOOK2PDF_MODEL": "from-env"}
    got = CFG.resolve({"model": "from-cli"}, file_cfg, env)
    eq(got["model"], "from-cli", "CLI 最高")
    got2 = CFG.resolve({}, file_cfg, env)
    eq(got2["model"], "from-file", "文件高于环境")
    got3 = CFG.resolve({}, {}, env)
    eq(got3["model"], "from-env", "环境高于默认")
    eq(CFG.resolve({}, {}, {})["mode"], "mono", "默认值")


@check("config.refuses_secrets_in_file")
def _cfg_secret():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "c.json")
        open(p, "w", encoding="utf-8").write('{"api_key":"sk-xxx"}')
        try:
            CFG.load_file(p)
        except ValueError as e:
            assert "敏感键" in str(e), str(e)
            return "正确拒绝把密钥写进配置文件"
    raise AssertionError("含密钥的配置文件应被拒绝")


# --------------------------------------------------------------------------- #
# 退出码
# --------------------------------------------------------------------------- #

@check("report.exit_codes")
def _report_codes():
    r = Report()
    eq(r.exit_code(), EXIT_PASS, "无 note → PASS")
    r.warn("w1", "warn")
    eq(r.exit_code(), EXIT_WARN, "有 warn → WARN")
    r2 = Report()
    r2.error("e1", "build 错", "prepare")
    eq(r2.exit_code(), EXIT_BUILD_FAIL, "构建错 → 2")
    r3 = Report()
    r3.stage("verify")
    r3.error("e2", "校验错", "verify")
    eq(r3.exit_code(), EXIT_VERIFY_FAIL, "校验错 → 3")
    eq(EXIT_ENV, 4, "环境错常量")


@check("report.dump_json")
def _report_dump():
    import json
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "r.json")
        r = Report(target="x")
        r.metric("pages", 10)
        r.warn("w", "msg", "where")
        r.dump(p)
        doc = json.load(open(p, encoding="utf-8"))
    eq(doc["counts"]["warnings"], 1, "warn 计数")
    eq(doc["metrics"]["pages"], 10, "metric 落盘")
    eq(doc["schema"], "book2pdf/report@1", "schema")


# --------------------------------------------------------------------------- #
# mdBook 适配器（合成站点）
# --------------------------------------------------------------------------- #

SYNTH_PRINT = """<!DOCTYPE html>
<html lang="zh" class="navy">
<head><meta charset="utf-8"></head>
<body>
<script>
const default_light_theme = "navy";
const default_dark_theme = "navy";
function go(theme){ html.classList.remove('navy'); html.classList.add(theme); }
</script>
<main>
<div style="break-before: page; page-break-before: always;"></div>
<h1 id="ch-one">第一章 概述</h1>
<p>这是第一章的正文，包含 <code>inline_code</code> 与 <a href="a/two.html">第二章</a>。</p>
<div style="break-before: page; page-break-before: always;"></div>
<h2 id="sec-one">1.1 小节</h2>
<p>小节内容。</p>
<pre class="mermaid">graph TD
  A[开始] --> B[结束]</pre>
<div style="break-before: page; page-break-before: always;"></div>
<h1 id="ch-extra">附录 未收录页</h1>
<p>这一页不在侧栏目录里（对应真实书里 print.html 比 toc 多一页的情形）。</p>
<div style="break-before: page; page-break-before: always;"></div>
<h1 id="ch-two">第二章 安装</h1>
<p>安装说明。</p>
</main>
</body></html>
"""

SYNTH_TOC = ('var x = `<ul><li><a href="a/one.html">第一章 概述</a></li>'
             '<li><a href="a/two.html">第二章 安装</a></li></ul>`;')


@check("mdbook.build_synthetic_site")
def _mdbook_build():
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "print.html"), "w", encoding="utf-8").write(SYNTH_PRINT)
        open(os.path.join(d, "toc-abc123.js"), "w", encoding="utf-8").write(SYNTH_TOC)
        ir = MdBookAdapter().build(d, "https://s/book/")
    eq(ir.source_kind, "mdbook", "适配器")
    eq(len(ir.toc), 2, "目录条数")
    eq([c.anchor_id for c in ir.chapters],
       ["ch-one", "sec-one", "ch-extra", "ch-two"], "章节锚点顺序")
    eq(ir.container_selector, "main", "容器选择器")
    eq(len(ir.theme_patches), 4, "主题补丁四处（浅/深/remove/add）")
    assert not ir.errors, f"不应有 error：{[n.code for n in ir.notes]}"
    return f"toc={len(ir.toc)} chapters={len(ir.chapters)} patches={len(ir.theme_patches)}"


@check("mdbook.alignment_synthetic")
def _mdbook_align():
    """目录 2 条 vs level-1 的 h1 3 个 → 必须跳过那个未收录页。

    这条判据对应真实书的情形（toc 105 条 / h1 106 个），
    是 v1 单点插入 K 的泛化版本。
    """
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "print.html"), "w", encoding="utf-8").write(SYNTH_PRINT)
        open(os.path.join(d, "toc-abc123.js"), "w", encoding="utf-8").write(SYNTH_TOC)
        ir = MdBookAdapter().build(d, "https://s/book/")
        r = Report()
        idx = RW.align_ir(ir, r)
    eq(idx.get(0), 0, "第一章 → 第 1 个 h1")
    eq(idx.get(1), 2, "第二章 → 第 3 个 h1（跳过未收录页）")
    eq(r.metrics["align_inserted_pages"], 1, "识别出 1 个未收录页")
    assert r.metrics["align_quality"] > 0.9, r.metrics["align_quality"]
    return f"quality={r.metrics['align_quality']} 插入页={r.metrics['align_inserted_pages']}"


@check("mdbook.resolver_rewrites_cross_chapter_link")
def _mdbook_resolve():
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "print.html"), "w", encoding="utf-8").write(SYNTH_PRINT)
        open(os.path.join(d, "toc-abc123.js"), "w", encoding="utf-8").write(SYNTH_TOC)
        ir = MdBookAdapter().build(d, "https://s/book/")
        r = Report()
        idx = RW.align_ir(ir, r)
        ids = {"ch-one", "sec-one", "ch-two"}
        body, finalize = RW.make_resolver(ir, idx, ids, "https://s/book/", None, Report())
        out = body('<p><a href="a/two.html">二</a></p>')
        finalize(1)
    eq(out, '<p><a href="#ch-two">二</a></p>', "跨章链接 → 页内锚点")
    return "a/two.html → #ch-two"


@check("mdbook.break_divs_removed")
def _mdbook_breaks():
    r = Report()
    out = RW.remove_page_break_divs(SYNTH_PRINT, r)
    eq(r.metrics["page_break_divs_removed"], 4, "移除的分页占位 div 数")
    assert "break-before: page" not in out, "不应残留"
    return "4 个占位 div 已移除"


# --------------------------------------------------------------------------- #
# 多页适配器（合成站点）
# --------------------------------------------------------------------------- #

@check("multipage.merge_and_cross_page_anchor")
def _multipage():
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "index.html"), "w", encoding="utf-8").write(
            '<html><head><meta name="generator" content="mkdocs-1.5"></head><body>'
            '<nav><a href="guide/one.html">第一章</a><a href="guide/two.html">第二章</a></nav>'
            '<main><p>首页正文</p></main></body></html>')
        os.makedirs(os.path.join(d, "guide"), exist_ok=True)
        open(os.path.join(d, "guide", "one.html"), "w", encoding="utf-8").write(
            '<html><body><main><h1>第一章</h1><p>看 <a href="two.html">第二章</a> 与 '
            '<img src="img/a.png"></p></main></body></html>')
        open(os.path.join(d, "guide", "two.html"), "w", encoding="utf-8").write(
            "<html><body><main><h1>第二章</h1><p>第二章正文</p></main></body></html>")

        ad = MkDocsAdapter()
        ir = ad.build(d, "https://s/book/", fetcher=None)
        merged = open(os.path.join(d, "merged.html"), encoding="utf-8").read()

    assert 'id="pg-guide-one-html"' in merged, "每页注入稳定锚点"
    assert 'href="#pg-guide-two-html"' in merged, "跨页链接 → 页内锚点"
    assert 'src="guide/img/a.png"' in merged, "资源路径重写为站点根相对"
    eq(ir.print_path, "merged.html", "合并页路径")
    eq(ir.theme_required, False, "多页站点不走 JS 主题机制")
    eq(len(ir.toc), 2, "目录条数")
    return f"目录 {len(ir.toc)} 条 / 章节 {len(ir.chapters)} 个"


@check("multipage.longest_first_wins_title")
def _multipage_title():
    import re
    merged = '<h1 id="pg-a">第一章</h1><p>x</p>'
    from book2pdf.adapters.multipage import MultiPageAdapter
    out = MultiPageAdapter._ensure_h1(merged, "pg-a", "IGNORED")
    assert '<h1 id="pg-a">第一章</h1>' in out, out
    assert not re.search(r'id="pg-a"[^>]*id="', out), "不应出现两个 id"
    return "已有 h1 时保留原文并替换 id"


# --------------------------------------------------------------------------- #
# 站点根归一化（--base 给目录 / 给页面两种写法必须等价）
# --------------------------------------------------------------------------- #

@check("base.site_prefix_normalizes_page_url")
def _site_prefix():
    from book2pdf.adapters.base import site_prefix, site_root
    eq(site_prefix("/book/"), "/book/", "目录形态")
    eq(site_prefix("/book/index.html"), "/book/", "页面形态 → 裁掉文件名")
    eq(site_prefix("/book"), "/book/", "无斜杠目录 → 补斜杠")
    eq(site_prefix("/index.html"), "/", "根下的首页")
    eq(site_prefix(""), "/", "空路径")
    eq(site_root("https://h/book/index.html"), "https://h/book/", "site_root 保留 scheme/netloc")
    return "5 种 path 形态"


@check("base.url_to_local_page_url_base")
def _url_to_local():
    from book2pdf.adapters.base import url_to_local
    # 两种 base 写法必须落到**同一个**本地路径（这就是真书崩溃的那条路径）
    a = url_to_local("https://h/book/print.html", "https://h/book/", "/root")
    b = url_to_local("https://h/book/print.html", "https://h/book/index.html", "/root")
    eq(os.path.basename(a), "print.html", "目录型 base")
    eq(a, b, "页面型 base 必须与目录型等价（不得多出一层 book/）")
    # 无扩展名目录链接仍落成 index.html
    c = url_to_local("https://h/book/00-guide", "https://h/book/index.html", "/root")
    eq(os.path.basename(c), "index.html", "目录链接落成 index.html")
    eq(os.path.basename(os.path.dirname(c)), "00-guide", "目录段保留")
    return "两种 base 写法等价"


@check("base.same_site_page_page_url_base")
def _same_site():
    from book2pdf.adapters.multipage import _same_site_page
    base = "https://h/book/index.html"
    eq(_same_site_page("https://h/book/a/one.html", base), "a/one.html", "页面型 base 下同站页")
    eq(_same_site_page("https://h/book/", base), "index.html", "站点根")
    eq(_same_site_page("https://other/x.html", base), None, "跨站必须拒绝")
    return "同站判定不受 base 写法影响"


# --------------------------------------------------------------------------- #

def main() -> int:
    bad = [r for r in RESULTS if not r[1]]
    for name, ok, detail in RESULTS:
        if VERBOSE or not ok:
            print(f"[{'PASS' if ok else 'FAIL'}] {name:46s} {detail}")
    print("-" * 78)
    print(f"{len(RESULTS) - len(bad)}/{len(RESULTS)} 通过")
    for name, _ok, detail in bad:
        print(f"  FAIL {name}: {detail}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
