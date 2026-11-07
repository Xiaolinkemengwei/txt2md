"""Markdown（GFM）块级解析与行内降级。

为什么自己写而不引 markdown-it-py
--------------------------------
1. 本项目对 Markdown 的用法是**往返双向**的，不是单向渲染。解析器必须让
   ``md -> 模型 -> md`` 尽量恒等，这要求对空行、围栏、列表缩进的处置完全可控。
   通用渲染器面向「HTML 输出正确」，不承诺往返稳定。
2. 0 依赖，符合本项目「开箱即用」的定位。
3. 体积：本模块约 300 行，而完整 CommonMark 实现需要数千行且带插件体系。

支持的块类型：ATX/Setext 标题、段落、有序/无序列表（含嵌套）、围栏与缩进代码、
引用、GFM 表格、分隔线、HTML 块（原样保留）。

未识别的行不会丢失：一律并入段落或 ``raw`` 块原样保留。
"""

from __future__ import annotations

import re

from .model import Block, TextDoc, normalize_newlines

# ---------------------------------------------------------------- 行级正则
RE_ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
RE_SETEXT = re.compile(r"^ {0,3}(=+|-{2,})[ \t]*$")
RE_FENCE = re.compile(r"^( {0,3})(`{3,}|~{3,})[ \t]*([^`\s]*)[ \t]*$")
RE_HR = re.compile(r"^ {0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$")
RE_QUOTE = re.compile(r"^ {0,3}>[ \t]?(.*)$")
RE_UL = re.compile(r"^( *)[-*+][ \t]+(.*)$")
RE_OL = re.compile(r"^( *)(\d{1,9})[.)][ \t]+(.*)$")
RE_TABLE_SEP = re.compile(
    r"^ {0,3}\|?[ \t]*:?-{1,}:?[ \t]*(?:\|[ \t]*:?-{1,}:?[ \t]*)*\|?[ \t]*$")
RE_HTML_BLOCK = re.compile(r"^ {0,3}<(?:[a-zA-Z][\w-]*|/[a-zA-Z][\w-]*|!--)")


def _split_row(line: str) -> list[str]:
    """按 GFM 规则切分表格行，尊重 \\| 转义。"""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    cells: list[str] = []
    buf: list[str] = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s) and s[i + 1] == "|":
            buf.append("|")
            i += 2
            continue
        if ch == "|":
            cells.append("".join(buf).strip())
            buf.clear()
        else:
            buf.append(ch)
        i += 1
    cells.append("".join(buf).strip())
    return cells


def _is_table_start(lines: list[str], i: int) -> bool:
    """判断第 i 行是否为表格起始（本行含 | 且下一行为分隔行）。"""
    if i + 1 >= len(lines):
        return False
    head, sep = lines[i], lines[i + 1]
    if "|" not in head:
        return False
    if not RE_TABLE_SEP.match(sep):
        return False
    return len(_split_row(head)) == len(_split_row(sep))


def _aligns(sep_line: str) -> list[str]:
    out = []
    for cell in _split_row(sep_line):
        left, right = cell.startswith(":"), cell.endswith(":")
        out.append("center" if left and right else "right" if right else "left" if left else "")
    return out


# ---------------------------------------------------------------- 主解析
def parse_markdown(text: str, name: str = "document") -> TextDoc:
    """把 Markdown 文本解析为块序列。"""
    lines = normalize_newlines(text).split("\n")
    doc = TextDoc()
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]

        # 空行
        if not line.strip():
            i += 1
            continue

        # 围栏代码块
        m = RE_FENCE.match(line)
        if m:
            indent, fence, lang = m.group(1), m.group(2), m.group(3)
            close = re.compile(r"^ {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}[ \t]*$")
            body: list[str] = []
            i += 1
            while i < n and not close.match(lines[i]):
                body.append(lines[i][len(indent):] if lines[i].startswith(indent) else lines[i])
                i += 1
            i += 1  # 跳过结束围栏
            doc.add(Block(kind="code", text="\n".join(body), lang=lang))
            continue

        # ATX 标题
        m = RE_ATX.match(line)
        if m:
            doc.add(Block.head((m.group(2) or "").strip(), len(m.group(1))))
            i += 1
            continue

        # 分隔线（必须早于 setext 判断，避免 --- 被当作标题下划线）
        if RE_HR.match(line):
            nxt = lines[i + 1] if i + 1 < n else ""
            # 上一行如果是文本，则本行属于 setext 二级标题；交给下面的分支处理
            if not doc.blocks or doc.blocks[-1].kind != "paragraph":
                doc.add(Block(kind="divider"))
                i += 1
                continue

        # 表格
        if _is_table_start(lines, i):
            header_cells = _split_row(lines[i])
            aligns = _aligns(lines[i + 1])
            i += 2
            rows = [header_cells]
            while i < n and lines[i].strip() and "|" in lines[i]:
                rows.append(_split_row(lines[i]))
                i += 1
            doc.add(Block(kind="table", rows=rows, header=True, align=aligns))
            continue

        # 引用
        if RE_QUOTE.match(line):
            buf: list[str] = []
            while i < n and lines[i].strip():
                mq = RE_QUOTE.match(lines[i])
                if mq:
                    buf.append(mq.group(1))
                elif buf and not RE_ATX.match(lines[i]):
                    buf.append(lines[i])  # 惰性续行
                else:
                    break
                i += 1
            doc.add(Block(kind="quote", text="\n".join(buf)))
            continue

        # 列表（含嵌套）
        ml_ul, ml_ol = RE_UL.match(line), RE_OL.match(line)
        if ml_ul or ml_ol:
            while i < n:
                lu, lo = RE_UL.match(lines[i]), RE_OL.match(lines[i])
                m2 = lu or lo
                if not m2 or not lines[i].strip():
                    break
                indent = len(m2.group(1))
                level = max(0, indent // 2)
                ordered = lo is not None
                start_no = int(lo.group(2)) if lo else 1
                item_text = (lo.group(3) if lo else lu.group(2)).strip()

                # 续行（缩进比标记更深的非列表行）
                i += 1
                while i < n and lines[i].strip():
                    if RE_UL.match(lines[i]) or RE_OL.match(lines[i]):
                        break
                    if RE_ATX.match(lines[i]) or RE_FENCE.match(lines[i]):
                        break
                    if len(lines[i]) - len(lines[i].lstrip()) > indent:
                        item_text += "\n" + lines[i].strip()
                        i += 1
                    else:
                        break

                last = doc.blocks[-1] if doc.blocks else None
                if (last is not None and last.kind == "list"
                        and last.level == level and last.ordered == ordered):
                    last.items.append(item_text)
                else:
                    doc.add(Block(kind="list", items=[item_text], ordered=ordered,
                                  level=level, start=start_no))
            continue

        # 缩进代码块（4 空格 / 制表符）
        if line.startswith("    ") or line.startswith("\t"):
            body = []
            while i < n and (lines[i].startswith("    ") or lines[i].startswith("\t")
                             or not lines[i].strip()):
                body.append(lines[i][4:] if lines[i].startswith("    ") else lines[i].lstrip("\t"))
                i += 1
            while body and not body[-1].strip():
                body.pop()
            doc.add(Block(kind="code", text="\n".join(body)))
            continue

        # HTML 块：原样保留，不试图解析
        if RE_HTML_BLOCK.match(line):
            buf = []
            while i < n and lines[i].strip():
                buf.append(lines[i])
                i += 1
            doc.add(Block(kind="raw", text="\n".join(buf)))
            continue

        # 段落（可跨行；遇到空行/块起始即止）
        buf = [line]
        i += 1
        while i < n and lines[i].strip():
            nxt = lines[i]
            if (RE_ATX.match(nxt) or RE_FENCE.match(nxt) or RE_QUOTE.match(nxt)
                    or RE_UL.match(nxt) or RE_OL.match(nxt) or RE_HR.match(nxt)
                    or _is_table_start(lines, i)):
                break
            # setext 标题：当前段落只有一行时升为标题
            ms = RE_SETEXT.match(nxt)
            if ms and len(buf) == 1:
                level = 1 if ms.group(1).startswith("=") else 2
                doc.add(Block.head(buf[0].strip(), level))
                buf.clear()
                i += 1
                break
            buf.append(nxt)
            i += 1
        if buf:
            doc.add(Block.para("\n".join(buf).strip("\n")))
        continue

    return doc


# ---------------------------------------------------------------- 行内降级
# 边界必须用 ASCII 字符集，不能用 \w —— Python 的 \w 默认包含中文，
# 会让「这是**粗体**」这种紧贴汉字的标记全部漏掉。
# 保守策略：只处理成对且明确的标记，避免误伤 2 * 3、snake_case 这类正文。
RE_INLINE_PATTERNS = [
    (re.compile(r"!\[([^\]]*)\]\([^)]*\)"), r"\1"),                  # 图片 -> alt
    (re.compile(r"\[([^\]]+)\]\([^)]*\)"), r"\1"),                  # 链接 -> 文字
    (re.compile(r"\[([^\]]+)\]\[[^\]]*\]"), r"\1"),                 # 引用式链接
    (re.compile(r"`{1,2}([^`\n]+?)`{1,2}"), r"\1"),                # 行内代码
    (re.compile(r"\*\*\*(?=\S)(.+?)(?<=\S)\*\*\*"), r"\1"),         # 粗斜体
    (re.compile(r"(?<![A-Za-z0-9_*])\*\*(?=\S)(.+?)(?<=\S)\*\*(?![A-Za-z0-9_*])"), r"\1"),
    (re.compile(r"(?<![A-Za-z0-9_])__(?=\S)(.+?)(?<=\S)__(?![A-Za-z0-9_])"), r"\1"),
    (re.compile(r"(?<![A-Za-z0-9_*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![A-Za-z0-9_*])"), r"\1"),
    (re.compile(r"(?<![A-Za-z0-9_])_(?=[^\s_])(.+?)(?<=[^\s_])_(?![A-Za-z0-9_])"), r"\1"),
    (re.compile(r"~~(?=\S)(.+?)(?<=\S)~~"), r"\1"),                 # 删除线
    (re.compile(r"<br\s*/?>", re.I), ""),                           # 硬换行标签
]


def strip_inline(text: str) -> str:
    """去掉行内 Markdown 标记，保留文字。用于 md → 纯文本。"""
    out = text
    for _ in range(3):  # 处理嵌套（如 [**粗**](url)）
        prev = out
        for pat, rep in RE_INLINE_PATTERNS:
            out = pat.sub(rep, out)
        if out == prev:
            break
    # 处理不了的残留反斜杠转义：去掉反斜杠保留字符
    out = re.sub(r"\\([\\`*_{}\[\]()#+\-.!>~|])", r"\1", out)
    return out
