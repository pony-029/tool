# -*- coding: utf-8 -*-
"""段切分：把正文 HTML 切成"可翻文本节点"，绝不碰代码与结构。

**由内向外只取最内层块级元素**，并整块跳过 pre/code/script/style/svg/math。
这样代码块、代码注释、行内标识符天然 0 次进入翻译队列。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from . import placeholders as PH

TAG_RE = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9]*)((?:\"[^\"]*\"|'[^']*'|[^>\"'])*?)(/?)>")

BLOCK_TAGS = {"p", "li", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6",
              "blockquote", "dd", "dt", "figcaption", "caption", "summary"}
SKIP_TAGS = {"pre", "code", "script", "style", "svg", "math", "textarea"}
VOID_TAGS = {"br", "img", "hr", "input", "meta", "link", "col", "source", "area", "base", "wbr"}
MERMAID_RE = re.compile(r'<pre[^>]*class="[^"]*\bmermaid\b[^"]*"[^>]*>(.*?)</pre>', re.S)

HAS_LETTER = re.compile(r"[A-Za-z\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")
WS = re.compile(r"\s+")


@dataclass
class Segment:
    sid: str
    kind: str
    start: int
    end: int
    inner: str                      # 原始内层 HTML
    text: str                       # 送翻文本（已占位）
    slots: list[str] = field(default_factory=list)
    key: str = ""
    translated: str = ""
    status: str = "pending"         # pending | ok | tm | fallback | broken

    def as_dict(self) -> dict:
        return {
            "sid": self.sid, "kind": self.kind, "status": self.status,
            "chars": len(self.text), "placeholders": len(self.slots),
            "source": self.text,
            "translated": PH.restore(self.translated, self.slots) if self.translated else "",
        }


def _sha(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()


def _key(text: str) -> str:
    return _sha(WS.sub("", text).strip().lower())


def _text_of(inner: str) -> str:
    """取块内纯文本，用于「是否有可翻字符」的判定。"""
    return re.sub(r"<[^>]+>", "", inner)


def _collect_spans(html: str) -> list[tuple[str, str, int, int, str]]:
    """单遍扫描，返回 (tag, attrs, inner_start, inner_end, inner_html)。

    只保留**最内层**块级元素：若某块内有块已被产出，则跳过外层，避免重复计数。
    """
    out: list[tuple[str, str, int, int, str]] = []
    stack: list[list] = []           # [tag, inner_start, is_block, has_emitted_child]
    skip_depth = 0

    for m in TAG_RE.finditer(html):
        closing, tag, attrs, selfclose = m.group(1), m.group(2).lower(), m.group(3), m.group(4)
        if tag in SKIP_TAGS:
            skip_depth = max(0, skip_depth - 1) if closing else skip_depth + 1
            continue
        if skip_depth:
            continue
        if selfclose or tag in VOID_TAGS:
            continue

        if not closing:
            stack.append([tag, m.end(), tag in BLOCK_TAGS, False])
            continue

        idx = None
        for k in range(len(stack) - 1, -1, -1):
            if stack[k][0] == tag:
                idx = k
                break
        if idx is None:
            continue
        entry = stack[idx]
        del stack[idx + 1:]
        stack.pop(idx)
        if entry[2] and not entry[3]:
            out.append((tag, attrs, entry[1], m.start(), html[entry[1]:m.start()]))
            if stack:
                stack[-1][3] = True
    return out


def _mermaid_labels(src: str) -> tuple[str, list[str]]:
    """从 Mermaid 源码里抽出节点/边标签，替换成占位符，语法保持不变。"""
    slots: list[str] = []

    def slot(literal: str) -> str:
        slots.append(literal)
        return PH.make("", len(slots) - 1)

    out = src
    for pat in (r'\["([^"]{1,60})"\]', r'\[([^\[\]<>{}|]{1,60})\]',
                r'\{"([^"]{1,60})"\}', r'\{([^{}<>\[\]|]{1,60})\}',
                r'\|"([^"]{1,60})"\|', r'\|([^|{}]{1,60})\|'):
        out = re.sub(pat, lambda m: slot(m.group(0)), out)
    return out, slots


def segment(container_html: str, *, translate_mermaid: bool = False) -> list[Segment]:
    """切分正文，产出段清单。**顺序即文档顺序**。"""
    segs: list[Segment] = []
    seen: dict[str, str] = {}

    for tag, _attrs, start, end, inner in _collect_spans(container_html):
        if not HAS_LETTER.search(_text_of(inner)):
            continue
        text, slots = PH.protect(inner)
        if not HAS_LETTER.search(re.sub(r"\u27e6\d+\u27e7", "", text)):
            continue
        key = _key(text)
        sid = seen.get(key)
        if sid is None:
            sid = hashlib.sha1(key.encode()).hexdigest()[:12]
            seen[key] = sid
        segs.append(Segment(sid=sid, kind=tag, start=start, end=end,
                            inner=inner, text=text, slots=slots, key=key))

    if translate_mermaid:
        for m in MERMAID_RE.finditer(container_html):
            # 只处理未落在已切分块内的 mermaid 源码
            src = m.group(1)
            if not HAS_LETTER.search(re.sub(r"<[^>]+>", "", src)):
                continue
            text, slots = _mermaid_labels(src)
            if not slots:
                continue
            key = _key(text)
            sid = seen.get(key)
            if sid is None:
                sid = hashlib.sha1(key.encode()).hexdigest()[:12]
                seen[key] = sid
            segs.append(Segment(sid=sid, kind="mermaid", start=m.start(1), end=m.end(1),
                                inner=src, text=text, slots=slots, key=key))

    segs.sort(key=lambda s: s.start)
    return segs


def apply(container_html: str, segs: list[Segment]) -> str:
    """把译文回填到 HTML（按 start 倒序替换，避免偏移漂移）。"""
    out = container_html
    for s in sorted(segs, key=lambda x: x.start, reverse=True):
        if s.status not in ("ok", "tm"):
            continue
        new_inner = PH.restore(s.translated, s.slots)
        out = out[: s.start] + new_inner + out[s.end:]
    return out


def apply_bilingual(container_html: str, segs: list[Segment], *,
                    inline_tags: tuple[str, ...] = ("td", "th", "li", "dt", "dd")) -> str:
    """双语对照：块后插译文 div；表格/列表用行内 span（否则会撑破表格）。"""
    out = container_html
    for s in sorted(segs, key=lambda x: x.start, reverse=True):
        if s.status not in ("ok", "tm") or not s.translated:
            continue
        tr = PH.restore(s.translated, s.slots)
        if s.kind in inline_tags:
            block = f'<span class="tr">{tr}</span>'
        elif s.kind == "mermaid":
            continue
        else:
            block = f'<div class="tr">{tr}</div>'
        out = out[: s.end] + block + out[s.end:]
    return out


def stats(segs: list[Segment]) -> dict:
    """去重口径必须显式声明：段命中率与字符节省率是两个不同的量。

    实测教训：技术书段命中率可达 ~30%，但字符只省 ~10%
    （重复的多是短标题，长正文几乎不重复）。只报段命中率会严重高估收益。
    """
    per_kind: dict[str, int] = {}
    first: dict[str, Segment] = {}
    for s in segs:
        per_kind[s.kind] = per_kind.get(s.kind, 0) + 1
        first.setdefault(s.key, s)
    chars = sum(len(s.text) for s in segs)
    chars_u = sum(len(s.text) for s in first.values())
    cjk = sum(len(re.findall(r"[\u4e00-\u9fff]", s.text)) for s in segs)
    cjk_u = sum(len(re.findall(r"[\u4e00-\u9fff]", s.text)) for s in first.values())
    lo, hi = estimate_tokens(chars, cjk)
    lo_u, hi_u = estimate_tokens(chars_u, cjk_u)
    return {
        "segments": len(segs),
        "unique_segments": len(first),
        "chars": chars,
        "unique_chars": chars_u,
        "cjk_chars": cjk,
        "by_kind": per_kind,
        "dedup_saving_segment_pct": round((1 - len(first) / max(1, len(segs))) * 100, 1),
        "dedup_saving_char_pct": round((1 - chars_u / max(1, chars)) * 100, 1),
        "tokens_est_low": lo,
        "tokens_est_high": hi,
        "tokens_est_low_unique": lo_u,
        "tokens_est_high_unique": hi_u,
    }


def estimate_tokens(chars: int, cjk: int) -> tuple[int, int]:
    """两个口径都报：中文按 1.3~1.7 字符/token，其它按 3.2~4.5 字符/token。"""
    latin = max(0, chars - cjk)
    lo = cjk / 1.7 + latin / 4.5
    hi = cjk / 1.3 + latin / 3.2
    return int(lo), int(hi)
