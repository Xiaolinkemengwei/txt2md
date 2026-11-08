"""AI eye 导读：给 AI 看的一份文档地图。

为什么需要它
------------
把一本书整篇塞给大模型有两个现实问题：上下文放不下；放得下也容易「中间迷失」
（Lost in the Middle）——模型对文档开头和结尾印象深，中段细节常被忽略。

导读解决的是**定位**问题：先用一份几百字的结构化地图告诉模型「这文档有什么、
大概在哪」，再让它按需去读正文对应部分。人读书先看目录，AI 同理。

设计约束
--------
**只用统计，不做语义推断。**没有引入分词器、embedding 或模型调用：
- 分词要么带词典（体积 + 许可）要么有歧义（「研究生命起源」），
  在导读里给出错的「关键词」比不给更糟。
- 本项目的定位是离线工具，导读必须能在没有网络、没有 GPU 的机器上生成。

因此这里只放**可验证的事实**：结构、位置、字数、图注原文、重复出现的字面串。
凡是需要「理解」才能得出的结论，一律不做。

输出格式
--------
Markdown + YAML front-matter。这是大模型最容易稳定解析的形态：
front-matter 给机器过滤用，正文表格给人/模型快速浏览用。
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .model import Block, Document, TextDoc

GUIDE_VERSION = 1

_STOP = set("的了是在和与及为也就都而及其这那有一个不我你他她它们个上中下"
            "对从到把被给让使可以能够进行以及或者但是因为所以如果虽然")

_CJK = re.compile(r"[\u4e00-\u9fff]")
_WORD = re.compile(r"[A-Za-z][A-Za-z'-]+")


# ---------------------------------------------------------------- 统计
def _texts(doc: Document) -> list[str]:
    """取出所有可读文字的块文本（不含纯图块）。"""
    out: list[str] = []
    for b in doc.as_text().blocks:
        if b.kind in ("heading", "paragraph", "quote"):
            if b.text.strip():
                out.append(b.text.strip())
        elif b.kind == "list":
            out.extend(i.strip() for i in b.items if i.strip())
        elif b.kind == "code":
            out.append(b.text)
        elif b.kind == "table":
            for row in b.rows:
                out.extend(c for c in row if c)
    return out


def _count_units(text: str) -> tuple[int, int]:
    """返回 (中文字数, 英文词数)。中英混排时两个都要，只报一个会误导。"""
    return len(_CJK.findall(text)), len(_WORD.findall(text))


def _headings(blocks: list[Block]) -> list[tuple[int, int, str]]:
    """(块序号, 层级, 标题)。"""
    return [(i, b.level, b.text.strip())
            for i, b in enumerate(blocks)
            if b.kind == "heading" and b.text.strip()]


def _first_para_after(blocks: list[Block], idx: int, limit: int = 72) -> str:
    """某标题之后的第一段正文，截断到 limit 字。用于给每章一句摘要。"""
    for b in blocks[idx + 1:]:
        if b.kind == "heading":
            return ""
        if b.kind == "paragraph" and b.text.strip():
            t = re.sub(r"\s+", " ", b.text.strip())
            return t if len(t) <= limit else t[:limit].rstrip() + "…"
        if b.kind == "list" and b.items:
            t = re.sub(r"\s+", " ", b.items[0].strip())
            return t if len(t) <= limit else t[:limit].rstrip() + "…"
    return ""


def _term_candidates(texts: list[str], top: int = 12) -> list[tuple[str, int]]:
    """字面重复出现的 2~4 字中文串，作为「可能的话题」线索。

    刻意保守：只保留出现 ≥3 次、且不含虚词、且不是纯数字/标点的片段。
    宁可漏掉一些真话题，也不要给出「的了的了」这种垃圾。
    """
    counts: dict[str, int] = {}
    for t in texts:
        for run in re.findall(r"[\u4e00-\u9fff]{2,}", t):
            for n in (4, 3, 2):
                for i in range(len(run) - n + 1):
                    gram = run[i:i + n]
                    if any(ch in _STOP for ch in gram):
                        continue
                    counts[gram] = counts.get(gram, 0) + 1
    ranked = [(g, c) for g, c in counts.items() if c >= 3]
    # 长串优先：若短串是某个高频长串的子串，说明它只是片段，去掉
    ranked.sort(key=lambda x: (-len(x[0]), -x[1]))
    kept: list[tuple[str, int]] = []
    for g, c in ranked:
        if any(g != k and g in k for k, _ in kept):
            continue
        kept.append((g, c))
        if len(kept) >= top:
            break
    return kept


# ---------------------------------------------------------------- 生成
def _yaml_str(s: str) -> str:
    """YAML 标量转义：含特殊字符时加双引号并转义。"""
    if not s:
        return '""'
    if re.search(r'[:#\-\[\]{}"\'*,&?|<>=!%@`\n]', s) or s != s.strip():
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'
    return s


def build_guide(doc: Document, *, source_name: str = "", target_format: str = "",
                app_version: str = "", images_prefix: str = "") -> str:
    """生成导读正文（Markdown 字符串）。"""
    blocks = doc.as_text().blocks
    texts = _texts(doc)
    joined = "\n".join(texts)
    cjk, words = _count_units(joined)
    heads = _headings(blocks)
    figs = [b.image for b in blocks if b.kind == "image" and b.image is not None]
    paragraphs = sum(1 for b in blocks if b.kind == "paragraph" and b.text.strip())
    tables = sum(1 for b in blocks if b.kind == "table" and b.rows)

    title = (doc.title or doc.source_title or doc.name or "未命名文档").strip()
    author = (doc.author or "").strip()
    lang = (doc.language or "zh").strip() or "zh"
    now = datetime.now().strftime("%Y-%m-%dT%H:%M")
    total_blocks = max(1, len(blocks))

    # ---- front-matter：机器可过滤的硬事实
    fm = [
        "---",
        f"aieye: {GUIDE_VERSION}",
        f"title: {_yaml_str(title)}",
    ]
    if author:
        fm.append(f"author: {_yaml_str(author)}")
    fm += [
        f"source: {_yaml_str(source_name or doc.name)}",
        f"source_format: {doc.source_format or 'unknown'}",
        f"target_format: {target_format or ''}",
        f"language: {lang}",
        f"chars_zh: {cjk}",
        f"words_en: {words}",
        f"paragraphs: {paragraphs}",
        f"headings: {len(heads)}",
        f"tables: {tables}",
        f"figures: {len(figs)}",
        f"generated_by: {_yaml_str('AI eye ' + app_version if app_version else 'AI eye')}",
        f"generated_at: {now}",
        "---",
        "",
    ]

    out: list[str] = list(fm)
    out.append("# AI eye 导读")
    out.append("")
    out.append("> 这份文件不是正文，是给 AI 读的**文档地图**：先看结构再定位，"
               "比把全文一次性塞进上下文更省也更准。")
    out.append("")

    # 小节号必须连续：插图/关键词等小节可能整节跳过，写死「三、四、五」
    # 会得到「二、」后面直接「五、」，看起来像文件坏了。
    _CN = "一二三四五六七八九十"
    sec = 0

    def next_sec(text: str) -> str:
        nonlocal sec
        sec += 1
        num = _CN[sec - 1] if sec <= len(_CN) else str(sec)
        return f"## {num}、{text}"

    # ---- 一、这是什么
    out.append(next_sec("文档概况"))
    out.append("")
    lead = ""
    for b in blocks:
        if b.kind == "paragraph" and b.text.strip():
            lead = re.sub(r"\s+", " ", b.text.strip())
            break
    if lead:
        out.append(f"- **开头内容**：{lead[:120]}{'…' if len(lead) > 120 else ''}")
    out.append(f"- **规模**：约 {cjk:,} 个汉字 / {words:,} 个英文词，"
               f"{paragraphs} 段，{len(heads)} 个标题，{tables} 张表，{len(figs)} 幅插图")
    out.append(f"- **来源**：`{source_name or doc.name}`（{doc.source_format or '未知格式'}）")
    if target_format:
        out.append(f"- **本次产物**：`{target_format}` 格式")
    out.append("")

    # ---- 二、结构地图
    out.append(next_sec("结构地图"))
    out.append("")
    if heads:
        out.append("| 层级 | 标题 | 位置 | 摘要 |")
        out.append("| --- | --- | --- | --- |")
        for i, level, text in heads:
            pct = round(i / total_blocks * 100)
            summary = _first_para_after(blocks, i)
            summary = summary.replace("|", "\\|")
            out.append(f"| H{level} | {text.replace('|', chr(92) + '|')} "
                       f"| {pct}% | {summary} |")
    else:
        out.append("> 本文档没有标题层级，正文为一整段连续文本。")
    out.append("")

    # ---- 三、图表清单
    if figs:
        out.append(next_sec("插图清单"))
        out.append("")
        out.append("| 序号 | 图注 | 文件名 |")
        out.append("| --- | --- | --- |")
        for n, ref in enumerate(figs, 1):
            cap = (ref.alt or ref.caption() or "（无图注）").replace("|", "\\|")
            name = f"{images_prefix}/{ref.name}" if images_prefix else ref.name
            out.append(f"| {n} | {cap} | `{name}` |")
        out.append("")

    # ---- 四、关键词线索
    terms = _term_candidates(texts)
    if terms:
        out.append(next_sec("高频片段（线索，非术语表）"))
        out.append("")
        out.append("> 按字面出现次数统计，未做分词与语义判断，仅供快速判断话题分布。")
        out.append("")
        out.append("| 片段 | 出现次数 |")
        out.append("| --- | --- |")
        for g, c in terms:
            out.append(f"| {g} | {c} |")
        out.append("")

    # ---- 五、怎么用
    out.append(next_sec("使用建议"))
    out.append("")
    if heads:
        out.append("1. 先读上面的结构地图，确定目标内容落在哪个标题下。")
        out.append("2. 需要细节时，按「位置」百分比跳到正文对应区间，不必从头读。")
        if figs:
            out.append("3. 涉及图表的问题，先对照插图清单确认图注与文件名。")
        out.append(f"{4 if figs else 3}. 上下文有限时，优先提供「概况 + 目标章节」，"
                   "而不是整篇正文。")
    else:
        out.append("1. 文档无明确结构，建议按段落顺序分段处理。")
        out.append("2. 上下文有限时，可先提供开头与结尾，再按需补充中段。")
    out.append("")
    return "\n".join(out)


def guide_path_for(target: Path) -> Path:
    """导读文件名：与产物同目录、同主名，后缀 ``.aieye.md``。"""
    return target.with_name(f"{target.stem}.aieye.md")


def app_version() -> str:
    """读版本号。放这里而不是模块顶部：aieye.py 会 import core，顶层导入成环。"""
    try:
        from aieye import __version__
        return __version__
    except Exception:
        try:
            from . import __version__ as v      # 少数安装方式会在这里暴露
            return str(v)
        except Exception:
            return ""


def write_guide(doc: Document, target: Path, *, source_name: str = "",
                target_format: str = "", app_version: str = "",
                images_prefix: str = "") -> Path:
    """把导读写到产物旁边，返回文件路径。"""
    path = guide_path_for(target)
    text = build_guide(doc, source_name=source_name,
                       target_format=target_format or target.suffix.lstrip("."),
                       app_version=app_version,
                       images_prefix=images_prefix)
    from .writers import atomic_write_text
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, text)
    return path
