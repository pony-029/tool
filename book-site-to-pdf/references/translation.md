# 译文子系统

## 目标与非目标

**目标**：把书站正文翻译成目标语言，产出译文版（`replace`）或中英对照版（`bilingual`），
并且**保证代码、内联标签、术语不被翻坏**。

**非目标**：不做机器翻译模型本身；不做版式重排（译文沿用同一套排版管线）。

## 流水线

```
container_html
  └ segmenter.segment()      切出待翻片段（只取最内层块级元素）
      └ placeholders.protect()   内联标签 → ⟦n⟧
          └ tm.get()             命中则直接用（断点续跑靠它）
              └ providers.*.translate()   批次翻译
                  └ placeholders.conserved()   守恒硬校验
                      └ tm.put() + apply()     回填
```

实现入口：`translate/__init__.py::run()`。

## 1. 占位符保护协议

内联标签（`<code>`、`<a>`、`<strong>`、`<em>`、`<span class=...>` 等）如果直接送翻译，
模型几乎必然改动它们。做法是**先把它们抽走**：

```
原文：把 <code>allow</code> 改成 <strong>拒绝</strong>
送出：把 ⟦1⟧ 改成 ⟦2⟧
译文：Change ⟦1⟧ to ⟦2⟧.
回填：Change <code>allow</code> to <strong>拒绝</strong>.
```

- `protect()` 由**内向外**编号，保证外层槽位字面量里包含内层占位符。
- `restore()` **必须递归**：还原外层后，槽位里可能又露出内层占位符，
  要循环替换直到不动。v1/早期实现只做一遍替换，嵌套内联标签会残留 `⟦n⟧` 在成品 PDF 里
  （对偶判据 `placeholder_broken` 抓到的就是这个）。
- `normalize()` 抹平全角数字（`１２３` → `123`）等常见漂移，避免守恒校验误报。
- **守恒硬校验**：`signature(原文) == signature(译文)`（占位符集合完全一致）。
  不一致 → 该段**回退原文**、记 `placeholder_broken`、计入失败数；不阻断整本书。

## 2. 切分范围

`segmenter._collect_spans()` 只取**最内层块级元素**（`p`、`li`、`td`、`th`、`blockquote` 的叶子）。

- 不能取外层容器，否则同一段文字会被切多次（父与子都命中）。
- 默认**不翻**：代码块（`pre`）、行内 `code`、URL、公式（MathJax）、Mermaid 图内文字。
- `--translate-mermaid` 可开图内文字，**默认关**：
  实测图内文字仅 2,107 个 / 全量约 1.0 万汉字 = **1.4%**，把 Mermaid 拆开送翻
  还要处理 `graph TD` 语法与节点标签的位置约束，性价比极低，风险却高。

## 3. 翻译记忆 TM

键的组成：

```
key = sha256( source_key ∥ target_lang ∥ glossary_hash ∥ provider_tag )
```

- `source_key`：原文（或原文 + 上下文片段的哈希）。
- `glossary_hash`：术语表内容的哈希——**换术语表自动失效**，不会拿旧译注新书。
- `provider_tag`：供应商 + 模型标识——**换 provider 自动失效**。

存储 `jsonl`，`load()` 时校验头部元信息（目标语言 / 术语表哈希 / provider），
不一致 → 整份标记 `invalidated`，从零开始（`tm_glossary_invalidation` /
`tm_provider_invalidation` 两条对偶判据覆盖）。

**TM 的真实价值是断点续跑与幂等，不是省钱。** 实测量化：

| 口径 | 数值 |
|---|---|
| 正文段总数 | 38,144 |
| 去重后 | 26,839（**段命中率 70.4%**） |
| 字符 720,287 → 649,401 | **只省 9.8%** |

原因：重复段落大多是短句（标题、提示语、导航语），长正文段几乎不重复。
所以**不要用"省了多少钱"来证明 TM 值得做**；它的价值在于：
中途断网/超额/手停之后，重跑时 70% 的段不重新调用，且结果与上次完全一致。

## 4. 术语表

```yaml
# glossary.yaml
terms:
  "harness": "框架"
  "context window": "上下文窗口"
keep:                       # __KEEP__：禁译
  - "WorkBuddy"
  - "Playwright"
```

- `Glossary.load()` 支持 yaml/json；内置极简 YAML 解析器，**免第三方依赖**。
- `prompt_block()` 把术语表渲染成提示词中的约束块。
- `keep_terms()`：命中禁译词 → 该段跳过翻译，原文照搬（避免专有名词被翻坏）。
- `KEEP = "__KEEP__"` 是内部标记值。
- `check()` 用于事后审计：译文里术语表命中项是否都按术语表译法落地。

## 5. Provider

```python
Provider Protocol:
    tag: str
    translate(batch: list[str], system: str) -> list[str]
```

| provider | 用途 |
|---|---|
| `null` | 假翻译（加前缀/直通），用于**端到端连调，不花钱、不需要网络** |
| `openai` | 任意 OpenAI 兼容端点：`POST {base_url}/chat/completions` |
| `anthropic` | Anthropic Messages API |

`OpenAICompatProvider` 的批协议用 `@@n@@` 标记分隔编号回复；
模型没有按格式回 → **单段降级重试**；仍带术语错误 → **术语重试**（把术语表再强调一遍）。
三层重试后仍失败才记失败段。

**接国产模型**：国内主流厂商都提供 `/anthropic` 或 `/v1` 兼容端点，
只需 `--base-url` + 环境变量里的 key，不需要改代码。

## 6. 环境前提（不要假设代理存在）

- 本地代理 `127.0.0.1:15721` **不保证在跑**。使用前必须先探测：
  连不上就退到 `--provider null` 或直接报 ENV 失败，**不要静默产出未翻译的 PDF**。
- 凭据只走环境变量或 CLI。`config.load_file()` **拒绝加载**含
  `api_key` / `token` / `auth_token` / `password` 的配置文件。

## 7. 成本口径（必须双报）

- **字符口径**：中文 UTF-8 下 1 字符 = **3 字节**。
  **`wc -c` 是陷阱**——用字节数会把中文规模算成 3 倍。
- **token 口径**：实测本书 74.3 万字符 ≈ **31.7 万 ~ 42.2 万 token**（取决于分词器与批内开销）。
- 报告里两个口径都给出，并注明估算方式。**只报一个口径的检查不算检查。**

## 8. 失败模式与处置

| 现象 | code | 处置 |
|---|---|---|
| 译文占位符集合与原文不一致 | `placeholder_broken` | 该段回退原文；换 provider / 减小 batch |
| 术语表命中项未按术语译 | 计入失败数 | 加重术语约束块；或把该词加入 `keep` |
| 端点数不上 / 401 | ENV(4) | 检查 `--base-url` 与 key；或先 `--provider null` 连调 |
| 翻译后字数异常膨胀 | 校验告警 | 大概率模型在"解释"而非翻译，收紧 system prompt |

## 9. 相关文件

```
translate/placeholders.py    保护 / 还原 / 守恒
translate/segmenter.py       切分 / 回填 / 统计（双口径）
translate/tm.py              翻译记忆
translate/glossary.py        术语表
translate/providers/         base / null / openai_compat / anthropic
translate/__init__.py        编排：run() / apply_mode()
```
