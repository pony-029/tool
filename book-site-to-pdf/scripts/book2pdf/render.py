# -*- coding: utf-8 -*-
"""渲染层：本地起服务 → 无头 Edge 渲染 → 渲染前断言 → 导出 PDF。

两个必须守住的点：
- **渲染前断言**（主题已锁定 / 正文段落数 / 正文颜色）比生成完再看图便宜得多
- **Mermaid 未渲染率超阈值必须 FAIL**（v1 是超时后静默 break，可能整批图空白）
"""
from __future__ import annotations

import functools
import http.server
import os
import socket
import socketserver
import threading
import time

MERMAID_WAIT_CODE = """
() => {
  // 只数**图容器**，不数容器里的 svg —— 两者是父子关系，并集会把一张图算两次。
  // 旧写法 `.mermaid[data-processed="true"], .mermaid svg` 正是父子并集：
  // 2 张图只渲染 1 张时会得到 done=2=total，判成 "ok"，**静默放过一张空白图**。
  const uniq = Array.from(new Set(document.querySelectorAll('pre.mermaid, .mermaid')));
  const done = uniq.filter(el => el.getAttribute('data-processed') === 'true'
                                 || el.querySelector('svg'));
  return {total: uniq.length, done: done.length,
          svgs: document.querySelectorAll('.mermaid svg').length,
          lib: !!(window.mermaid || window.mermaidAPI)};
}
"""

DIAG_CODE = """
(sel) => {
  const p = document.querySelector(sel + ' p') || document.querySelector('main p') || document.querySelector('p');
  const h1 = document.querySelector('h1');
  const cs = p ? getComputedStyle(p) : null;
  return {
    htmlClass: document.documentElement.className,
    bodyBg: getComputedStyle(document.body).backgroundColor,
    pColor: cs ? cs.color : null,
    h1Color: h1 ? getComputedStyle(h1).color : null,
    paras: document.querySelectorAll(sel + ' p, main p').length,
    mermaid: document.querySelectorAll('pre.mermaid').length,
    images: document.querySelectorAll('img').length,
    tables: document.querySelectorAll('table').length,
    codeBlocks: document.querySelectorAll('pre code').length,
  };
}
"""

MERMAID_FAIL_RATIO = 0.05

# 无进展多久算「卡住」。v1 只有总超时（900s）——若站点压根没加载 mermaid 库，
# 会整整空等 15 分钟才报错。这里改成「停滞检测」，把等待时间与真实进度绑定。
MERMAID_STALL_SEC = 90
# 连 mermaid 运行时都不存在时的宽限期：给动态 import 一点时间再下结论。
MERMAID_LIB_GRACE_SEC = 30


def mermaid_verdict(total: int, done: int, fail_ratio: float = MERMAID_FAIL_RATIO) -> tuple[str, str]:
    """把「未渲染率」变成判据，便于单测与对偶测试。

    返回 (severity, message)：'ok' | 'warn' | 'error'。
    v1 在这里是超时后静默 break —— 图可能整批空白却报成功。
    """
    unrendered = max(0, total - done)
    if total == 0:
        return "ok", "无 Mermaid 图"
    ratio = unrendered / total
    if ratio > fail_ratio:
        return "error", (f"Mermaid 未渲染率 {ratio:.1%} > {fail_ratio:.0%}"
                         f"（{unrendered}/{total}）—— 图可能整批空白")
    if unrendered:
        return "warn", f"{unrendered}/{total} 张 Mermaid 未渲染完成"
    return "ok", f"{done}/{total} 张 Mermaid 全部渲染"


def mermaid_counts_consistent(total: int, done: int, svgs: int) -> tuple[bool, str]:
    """三条计数路径必须自洽：`容器内 svg 数 == 已渲染容器数 ≤ 容器总数`。

    旧实现把「图容器」与「容器内的 svg」**并集求和**，而两者是父子关系：
    2 张图只渲染 1 张时得到 `done = 1(容器) + 1(svg) = 2 = total`，于是判为 "ok"，
    **静默放过一张空白图**。这就是"同一个量有多条计算路径却不互相校验"的典型，
    所以这里把不变式显式化成判据，而不是"相信"那句 JS。

    为什么用等式而不是不等式：只用 `svgs <= done` 时，(2, 2, 1) 这个**正是本 bug
    的签名**是"自洽"的，判据就没有区分力。结构性事实是「一个已渲染的容器恰好装一个 svg」，
    必须写成等式才拦得住。（这条判据本身也是被对偶测试检验出来的：第一版写成不等式，
    对偶测试立刻 FAIL。）
    """
    if total < 0 or done < 0 or svgs < 0:
        return False, f"计数为负：total={total} done={done} svg={svgs}"
    if done > total:
        return False, (f"已渲染 {done} > 容器总数 {total} —— 计数口径重叠"
                       f"（容器与容器内 svg 被并集求和）")
    if svgs != done:
        return False, (f"容器内 svg 数 {svgs} ≠ 已渲染容器数 {done} —— 两条计数路径不自洽"
                       f"（旧实现正是靠这个把「2 张图只渲染 1 张」判成完成）")
    return True, ""


def mermaid_give_up(elapsed: float, since_progress: float, lib_present: bool,
                    done: int, *, timeout: int = 900,
                    stall: float = MERMAID_STALL_SEC,
                    lib_grace: float = MERMAID_LIB_GRACE_SEC) -> str:
    """是否该停止等待？返回原因，'' 表示继续等。

    **同一个量必须有多条判据互相校验**（MPSD）：总超时只是其中一条，
    另两条是「进展停滞」与「运行时不存在」。只靠总超时会让失败延迟 15 分钟，
    也会把「站点没加载库」与「库在跑但很慢」混为一谈。
    """
    if elapsed >= timeout:
        return "timeout"
    if done > 0 and since_progress >= stall:
        return "stalled"
    if done == 0 and not lib_present and elapsed >= lib_grace:
        return "lib_missing"
    return ""


def paras_ok(rendered: int, expect: int) -> tuple[bool, str]:
    """正文段落数校验：**相对源 HTML**，不用绝对阈值。

    绝对阈值（旧版写死 `<= 100`）会把合法的短书误判为失败——这是典型的度量口径错误：
    同一个量有两条独立计算路径（源 HTML 的 `<p>` 计数 / 渲染后 DOM 的段落计数），
    **两条路径必须互相校验**，而不是另设一个凭感觉的常量。
    返回 (ok, message)。
    """
    if expect <= 0:
        return (rendered > 0), ("" if rendered > 0 else "渲染后正文段落数为 0")
    floor = max(1, int(expect * 0.5))
    if rendered < floor:
        return False, (f"渲染后正文段落数 {rendered} 远少于源 HTML 的 {expect}"
                       f"（下限 {floor}）")
    return True, ""


def free_port(preferred: int = 0) -> int:
    """选一个空闲端口。v1 硬编码 8765，被占用即崩。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", preferred or 0))
        return s.getsockname()[1]


def serve(site_dir: str, port: int):
    socketserver.TCPServer.allow_reuse_address = True

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):                               # noqa: D102
            pass

    handler = functools.partial(Quiet, directory=site_dir)
    srv = socketserver.TCPServer(("127.0.0.1", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def render(site_dir: str, page_file: str, out_pdf: str, *, report,
           port: int = 0, browser_channel: str = "msedge", paper: str = "A4",
           margins: dict | None = None, footer: bool = True,
           container_selector: str = "main", mermaid_timeout: int = 900,
           doc_title: str = "", doc_lang: str = "zh", expect_paras: int = 0) -> None:
    from playwright.sync_api import sync_playwright

    port = free_port(port)
    serve(site_dir, port)
    time.sleep(1)
    margins = margins or {"top": "14mm", "bottom": "16mm", "left": "12mm", "right": "12mm"}

    with sync_playwright() as p:
        kw = {"channel": browser_channel} if browser_channel else {}
        br = p.chromium.launch(**kw)
        ctx = br.new_context(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
        ctx.add_init_script(
            "try{localStorage.removeItem('mdbook-theme');"
            "localStorage.setItem('mdbook-sidebar','hidden');}catch(e){}")
        pg = ctx.new_page()
        pg.goto(f"http://127.0.0.1:{port}/{page_file}",
                wait_until="domcontentloaded", timeout=300000)
        pg.wait_for_timeout(3000)

        # ---- 渲染前断言 ----
        diag = pg.evaluate(DIAG_CODE, container_selector)
        report.metric("render_diag", diag)
        report.stage("render_diag", **{k: v for k, v in diag.items() if k != "render_diag"})
        print(f"[自检] html.class={diag['htmlClass']!r} 正文色={diag['pColor']} "
              f"段落={diag['paras']} 图={diag['mermaid']} 表={diag['tables']} "
              f"代码块={diag['codeBlocks']}")

        classes = diag["htmlClass"].split()
        if "light" not in classes:
            report.error("render_theme_not_locked",
                         f"主题未锁定为 light（实际 {diag['htmlClass']!r}），PDF 会是深色", "render")
        if any(t in classes for t in ("navy", "coal", "ayu", "rust")):
            report.error("render_dark_theme", f"仍是深色主题：{diag['htmlClass']!r}", "render")
        # 段落数：**相对源 HTML 校验**，不用绝对阈值（见 paras_ok 的说明）
        ok_p, msg_p = paras_ok(diag["paras"], expect_paras)
        if not ok_p:
            report.error("render_few_paragraphs",
                         f"{msg_p}；容器选择器 {container_selector!r} 可能不对", "render")
        pc = diag["pColor"] or ""
        if not (pc.startswith("rgb(26") or pc.startswith("rgb(17") or pc.startswith("rgb(0")):
            report.warn("render_low_contrast", f"正文颜色可能是浅色：{pc}", "render")

        # ---- Mermaid ----
        total = diag["mermaid"]
        done = 0
        svgs = 0
        give_up = ""
        if total:
            print(f"[渲染] 等待 {total} 张 Mermaid（阻塞 JS 主线程，勿中断）...")
            t0, last = time.time(), -1
            last_progress = t0
            lib = False
            while True:
                st = pg.evaluate(MERMAID_WAIT_CODE)
                done = st["done"]
                svgs = st.get("svgs", 0)
                lib = st.get("lib", lib)
                now = time.time()
                if done != last:
                    print(f"       {done}/{st['total']}")
                    last = done
                    last_progress = now
                if st["total"] and done >= st["total"]:
                    break
                give_up = mermaid_give_up(now - t0, now - last_progress, lib, done,
                                          timeout=mermaid_timeout)
                if give_up:
                    break
                pg.wait_for_timeout(2000)
            if give_up == "stalled":
                report.warn("mermaid_stalled",
                            f"Mermaid 渲染停滞 {MERMAID_STALL_SEC}s（已渲染 {done}/{total}），"
                            f"提前停止等待", "render")
            elif give_up == "lib_missing":
                report.warn("mermaid_lib_missing",
                            f"页面未加载 Mermaid 运行时（{total} 张图无法渲染）；"
                            f"该站点可能把 mermaid 放在被过滤掉的资源里", "render")
            elif give_up == "timeout":
                report.warn("mermaid_timeout",
                            f"Mermaid 等待超过 {mermaid_timeout}s（{done}/{total}）", "render")
            # 跨路径一致性：三条计数必须自洽，否则判据本身就不可信
            ok_c, msg_c = mermaid_counts_consistent(total, done, svgs)
            if not ok_c:
                report.error("mermaid_count_inconsistent", msg_c, "render")
        unrendered = max(0, total - done)
        ratio = unrendered / total if total else 0.0
        report.metrics_update({"mermaid_total": total, "mermaid_rendered": done,
                               "mermaid_svg": svgs,
                               "mermaid_unrendered": unrendered,
                               "mermaid_unrendered_ratio": round(ratio, 4)})
        severity, message = mermaid_verdict(total, done)
        if severity == "error":
            report.error("mermaid_unrendered", message, "render")
        elif severity == "warn":
            report.warn("mermaid_partial", message, "render")

        # ---- MathJax ----
        idle = 0
        for _ in range(180):
            st = pg.evaluate("""() => { const H = window.MathJax && MathJax.Hub;
                return {ready: !!H, pending: H && H.queue ? H.queue.pending : -1,
                        msg: document.querySelectorAll('.MathJax_Message').length}; }""")
            if st["ready"] and st["pending"] == 0 and st["msg"] == 0:
                idle += 1
                if idle >= 3:
                    break
            else:
                idle = 0
            pg.wait_for_timeout(1000)
        pg.wait_for_timeout(2000)

        # ---- 导出 ----
        print(f"[导出] 开始（大书请耐心，2000 页约 4-6 分钟）...")
        t0 = time.time()
        kwargs = dict(path=out_pdf, format=paper, print_background=True,
                      outline=True, tagged=True,       # 坑 4：少了 tagged 就得到 0 条书签
                      margin=margins, display_header_footer=footer,
                      header_template="<div></div>",
                      footer_template=('<div style="font-size:8px;width:100%;text-align:center;'
                                       'color:#888;font-family:sans-serif;">'
                                       '<span class="pageNumber"></span> / '
                                       '<span class="totalPages"></span></div>'))
        try:
            pg.pdf(**kwargs)
        except TypeError:
            kwargs.pop("tagged", None)               # 老版 Chromium 不支持 tagged
            report.warn("tagged_unsupported",
                        "浏览器不支持 tagged 参数，已退回（书签可能为空）", "render")
            pg.pdf(**kwargs)
        report.metric("render_seconds", round(time.time() - t0, 1))
        print(f"[导出] 完成，用时 {time.time() - t0:.1f}s")
        br.close()

    _write_metadata(out_pdf, report, title=doc_title, lang=doc_lang)


def _write_metadata(pdf: str, report, *, title: str = "", lang: str = "zh") -> None:
    """写 PDF 元数据 + 语言标记。

    语言标记很重要：译文版若不改 `<html lang>`，屏幕阅读器会按源语言朗读。
    """
    try:
        import pymupdf
    except ImportError:
        report.warn("metadata_skipped", "未安装 pymupdf，跳过元数据写入", "render")
        return
    try:
        d = pymupdf.open(pdf)
        md = dict(d.metadata or {})
        if title:
            md["title"] = title
        md.setdefault("producer", "book2pdf 2.0")
        md["subject"] = md.get("subject") or f"Exported book (lang={lang})"
        d.set_metadata(md)
        d.saveIncr() if hasattr(d, "saveIncr") else d.save(pdf + ".tmp")
        d.close()
        report.metric("pdf_metadata", {"title": md.get("title"), "subject": md.get("subject")})
    except Exception as e:                                       # noqa: BLE001
        report.warn("metadata_failed", f"写入 PDF 元数据失败：{e}", "render")
