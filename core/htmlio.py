"""HTML 读取与写出（标准库实现，无 bs4 依赖）。

双重用途
--------
1. HTML 作为独立目标格式（md ⇄ html）。
2. **HTML 是 PDF 生成的中转**：浏览器打印 PDF 需要一份排版良好的 HTML，
   所以这里的 ``render_html`` 同时承担打印样式，中文字体与分页规则都在里面。

读取只取可见正文；``script`` / ``style`` / ``head`` 的内容不属于文档内容，
是唯一允许丢弃的部分。
"""

from __future__ import annotations

import html as html_mod
import re
from html.parser import HTMLParser

from .mdparse import strip_inline
from .model import Block, TextDoc

_HEADINGS = {f"h{i}": i for i in range(1, 7)}
# 只放「有结束标签的容器」：跳过深度靠 start/end 配对加减，把 meta、link
# 这类自闭合标签放进来会让深度只增不减——</head> 只抵消一层，剩下的深度
# 会把整个 <body> 吞掉，表现就是「任何标准网页都读不出正文」。
_SKIP_TAGS = {"script", "style", "head", "noscript", "template"}
_BLOCK_TAGS = {"p", "div", "section", "article", "header", "footer", "main",
               "aside", "figure", "figcaption", "dd", "dt", "pre", "blockquote",
               "table", "ul", "ol", "li", "hr", "br", "tr"}


class _Extractor(HTMLParser):
    """把 HTML 流式转换为块序列。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks: list[Block] = []
        self._buf: list[str] = []
        self._skip_depth = 0
        self._pre_depth = 0
        self._stack: list[str] = []
        # 列表栈：每层记 (是否有序, 当前条目缓冲)
        self._lists: list[dict] = []
        # 表格
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    # -- 缓冲管理
    def _flush(self, kind: str = "paragraph", level: int = 0):
        text = "".join(self._buf).strip()
        self._buf.clear()
        if not text:
            return
        if self._lists:
            self._lists[-1]["items"].append(re.sub(r"[ \t]*\n[ \t]*", " ", text))
            return
        if kind == "heading":
            self.blocks.append(Block.head(text, level or 1))
        elif kind == "quote":
            self.blocks.append(Block(kind="quote", text=text))
        else:
            self.blocks.append(Block.para(text))

    def _close_lists_for(self, tag: str):
        """遇到非列表块级标签时，收尾本次列表。"""
        if self._lists and tag not in ("ul", "ol", "li"):
            self._pop_lists()

    def _pop_lists(self):
        while self._lists:
            info = self._lists.pop()
            if info["items"]:
                b = Block(kind="list", items=info["items"],
                          ordered=info["ordered"], level=len(self._lists))
                # 与上一块同为列表且同层级则合并
                last = self.blocks[-1] if self.blocks else None
                if (last is not None and last.kind == "list"
                        and last.ordered == b.ordered and last.level == b.level):
                    last.items.extend(b.items)
                else:
                    self.blocks.append(b)

    # -- 解析回调
    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return

        self._stack.append(tag)

        if tag in _HEADINGS:
            self._close_lists_for(tag)
            self._flush()
            self._buf.clear()
        elif tag == "pre":
            self._flush()
            self._pre_depth += 1
            self._buf.clear()
        elif tag == "blockquote":
            self._close_lists_for(tag)
            self._flush()
        elif tag in ("ul", "ol"):
            self._flush()
            self._lists.append({"ordered": tag == "ol", "items": []})
        elif tag == "li":
            self._flush()
            if not self._lists:
                self._lists.append({"ordered": False, "items": []})
        elif tag == "table":
            self._close_lists_for(tag)
            self._flush()
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag == "hr":
            self._close_lists_for(tag)
            self._flush()
            self.blocks.append(Block(kind="divider"))
        elif tag == "br":
            self._buf.append("\n")
        elif tag in ("p", "div", "section", "article", "dd", "dt", "figcaption"):
            self._close_lists_for(tag)
            self._flush()
        elif tag == "img":
            alt = dict(attrs).get("alt", "").strip()
            if alt:
                self._buf.append(alt)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if self._stack and self._stack[-1] == tag:
            self._stack.pop()

        if tag in _HEADINGS:
            self._flush("heading", _HEADINGS[tag])
        elif tag == "pre":
            text = "".join(self._buf).strip("\n")
            self._buf.clear()
            self._pre_depth = max(0, self._pre_depth - 1)
            if text.strip():
                self.blocks.append(Block(kind="code", text=text))
        elif tag == "blockquote":
            text = "".join(self._buf).strip()
            self._buf.clear()
            if text:
                self.blocks.append(Block(kind="quote", text=text))
        elif tag == "li":
            self._flush()
        elif tag in ("ul", "ol"):
            self._flush()
            if self._lists:
                info = self._lists.pop()
                if info["items"]:
                    b = Block(kind="list", items=info["items"],
                              ordered=info["ordered"], level=len(self._lists))
                    last = self.blocks[-1] if self.blocks else None
                    if (last is not None and last.kind == "list"
                            and last.ordered == b.ordered and last.level == b.level):
                        last.items.extend(b.items)
                    else:
                        self.blocks.append(b)
        elif tag in ("td", "th"):
            if self._cell is not None:
                txt = re.sub(r"\s+", " ", "".join(self._cell)).strip()
                if self._row is not None:
                    self._row.append(txt)
                self._cell = None
        elif tag == "tr":
            if self._row is not None and any(c for c in self._row):
                self._table = self._table if self._table is not None else []
                self._table.append(self._row)
            self._row = None
        elif tag == "table":
            if self._table:
                width = max(len(r) for r in self._table)
                rows = [r + [""] * (width - len(r)) for r in self._table]
                self.blocks.append(Block(kind="table", rows=rows, header=True))
            self._table = None
        elif tag in ("p", "div", "section", "article", "dd", "dt"):
            self._flush()

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._cell is not None:
            self._cell.append(data)
            return
        if self._pre_depth:
            self._buf.append(data)
            return
        # 折叠 HTML 空白
        self._buf.append(re.sub(r"\s+", " ", data))

    def close(self):
        super().close()
        self._flush()
        self._pop_lists()


def html_to_doc(text: str) -> TextDoc:
    """HTML 文本 → 块模型。"""
    p = _Extractor()
    try:
        p.feed(text)
        p.close()
    except Exception:
        # HTML 不合法时，退化为去标签取文本，绝不返回空
        plain = re.sub(r"<[^>]+>", "", text)
        plain = html_mod.unescape(plain)
        doc = TextDoc()
        from .model import blocks_of_text
        return blocks_of_text(plain)
    return TextDoc(p.blocks)


# ---------------------------------------------------------------- 写出
_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body {
  margin: 0 auto; padding: 32px 40px; max-width: 820px;
  font-family: "Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei",
               "PingFang SC", "Hiragino Sans GB", sans-serif;
  font-size: 15px; line-height: 1.75; color: #1b1b1b; background: #ffffff;
  -webkit-font-smoothing: antialiased;
}
h1,h2,h3,h4,h5,h6 { font-weight: 600; line-height: 1.3; margin: 1.6em 0 .6em; color: #111; }
h1 { font-size: 1.9em; border-bottom: 1px solid #e5e5e5; padding-bottom: .3em; }
h2 { font-size: 1.45em; } h3 { font-size: 1.2em; } h4 { font-size: 1.05em; }
p { margin: .7em 0; }
ul, ol { margin: .7em 0; padding-left: 1.6em; }
li { margin: .25em 0; }
li > ul, li > ol { margin: .2em 0; }
blockquote {
  margin: .9em 0; padding: .5em 1em; color: #4a4a4a;
  border-left: 3px solid #0f6cbd; background: #f7f9fc;
}
pre {
  margin: .9em 0; padding: 14px 16px; overflow-x: auto;
  background: #f6f6f6; border: 1px solid #e5e5e5; border-radius: 6px;
  font-family: "Cascadia Mono", Consolas, "Courier New", monospace; font-size: .88em;
}
code { font-family: "Cascadia Mono", Consolas, monospace; font-size: .9em;
       background: #f2f2f2; padding: .1em .35em; border-radius: 3px; }
pre code { background: none; padding: 0; font-size: 1em; }
table { border-collapse: collapse; width: 100%; margin: 1em 0; font-size: .94em; }
th, td { border: 1px solid #dcdcdc; padding: 7px 11px; text-align: left; vertical-align: top; }
th { background: #f3f5f8; font-weight: 600; }
tr:nth-child(even) td { background: #fafbfc; }
hr { border: none; border-top: 1px solid #e5e5e5; margin: 2em 0; }

@page { size: A4; margin: 18mm 16mm; }
@media print {
  body { padding: 0; max-width: none; font-size: 11pt; }
  h1, h2, h3, h4 { page-break-after: avoid; }
  table, pre, blockquote { page-break-inside: avoid; }
  tr, img { page-break-inside: avoid; }
  a { color: inherit; text-decoration: none; }
}
"""


def _esc(s: str) -> str:
    return html_mod.escape(s, quote=False)


def _inline_md(text: str) -> str:
    """把 Markdown 行内标记转成 HTML（先转义再替换，避免注入）。"""
    out = _esc(text)
    # 代码优先，避免内部字符被后续规则误处理
    out = re.sub(r"`([^`\n]+)`", lambda m: f"<code>{m.group(1)}</code>", out)
    out = re.sub(r"\*\*\*(?=\S)(.+?)(?<=\S)\*\*\*", r"<strong><em>\1</em></strong>", out)
    out = re.sub(r"(?<![A-Za-z0-9_*])\*\*(?=\S)(.+?)(?<=\S)\*\*(?![A-Za-z0-9_*])", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<![A-Za-z0-9_])__(?=\S)(.+?)(?<=\S)__(?![A-Za-z0-9_])", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<![A-Za-z0-9_*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![A-Za-z0-9_*])", r"<em>\1</em>", out)
    out = re.sub(r"(?<![A-Za-z0-9_])_(?=[^\s_])(.+?)(?<=[^\s_])_(?![A-Za-z0-9_])", r"<em>\1</em>", out)
    out = re.sub(r"~~(?=\S)(.+?)(?<=\S)~~", r"<del>\1</del>", out)
    out = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r'<img src="\2" alt="\1">', out)
    out = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', out)
    return out.replace("\n", "<br>")


def render_html(doc: TextDoc, title: str = "", *, standalone: bool = True,
                assets_prefix: str = "", with_images: bool = True) -> str:
    """块模型 → HTML。``standalone=False`` 只产出 body 片段（嵌入用）。"""
    parts: list[str] = []
    for b in doc.blocks:
        if b.kind == "heading":
            lvl = max(1, min(6, b.level))
            parts.append(f"<h{lvl}>{_inline_md(b.text)}</h{lvl}>")
        elif b.kind == "paragraph":
            text = b.text.replace("\n", "<br>")
            parts.append(f"<p>{_inline_md(text)}</p>")
        elif b.kind == "raw":
            parts.append(b.text)
        elif b.kind == "quote":
            parts.append(f"<blockquote>{_inline_md(b.text)}</blockquote>")
        elif b.kind == "code":
            cls = f' class="language-{_esc(b.lang)}"' if b.lang else ""
            parts.append(f"<pre><code{cls}>{_esc(b.text)}</code></pre>")
        elif b.kind == "divider":
            parts.append("<hr>")
        elif b.kind == "list":
            tag = "ol" if b.ordered else "ul"
            start = f' start="{b.start}"' if b.ordered and b.start != 1 else ""
            items = "".join(f"<li>{_inline_md(it)}</li>" for it in b.items)
            indent = ' style="margin-left:1.6em"' if b.level else ""
            parts.append(f"<{tag}{start}{indent}>{items}</{tag}>")
        elif b.kind == "table":
            if not b.rows:
                continue
            aligns = b.align or []
            def cell(tag: str, txt: str, idx: int) -> str:
                a = aligns[idx] if idx < len(aligns) else ""
                style = f' style="text-align:{a}"' if a in ("left", "center", "right") else ""
                return f"<{tag}{style}>{_inline_md(txt)}</{tag}>"
            head = ""
            body_rows = b.rows
            if b.header and b.rows:
                head = "<thead><tr>" + "".join(
                    cell("th", c, i) for i, c in enumerate(b.rows[0])) + "</tr></thead>"
                body_rows = b.rows[1:]
            body = "".join(
                "<tr>" + "".join(cell("td", c, i) for i, c in enumerate(r)) + "</tr>"
                for r in body_rows)
            parts.append(f"<table>{head}<tbody>{body}</tbody></table>")
        elif b.kind == "image":
            ref = b.image
            if ref is None or not with_images:
                continue
            src = f"{assets_prefix}/{ref.name}" if assets_prefix else ref.name
            cap = _esc(ref.caption())
            fig_cap = f"<figcaption>{cap}</figcaption>" if cap else ""
            parts.append(f'<figure><img src="{_esc(src)}" alt="{cap}">{fig_cap}</figure>')

    content = "\n".join(parts)
    if not standalone:
        return content
    return (
        "<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>{_esc(title or 'document')}</title>\n"
        f"<style>{_CSS}</style>\n</head>\n<body>\n{content}\n</body>\n</html>\n"
    )


def render_plain(doc: TextDoc, *, keep_blank: bool = False) -> str:
    """块模型 → 纯文本（去全部标记，保留文字与段落分隔）。"""
    out: list[str] = []
    for b in doc.blocks:
        if b.kind == "heading":
            out.append(strip_inline(b.text))
        elif b.kind == "paragraph":
            out.append(strip_inline(b.text))
        elif b.kind == "quote":
            out.append(strip_inline(b.text))
        elif b.kind == "code":
            out.append(b.text)
        elif b.kind == "raw":
            out.append(b.text)
        elif b.kind == "divider":
            out.append("")
        elif b.kind == "list":
            for i, it in enumerate(b.items, start=b.start if b.ordered else 0):
                prefix = f"{i}. " if b.ordered else "- "
                out.append("  " * b.level + prefix + strip_inline(it))
        elif b.kind == "table":
            for r in b.rows:
                out.append("\t".join(strip_inline(c) for c in r))
        elif b.kind == "image":
            # 纯文本留占位；图注本身是有用信息（"图 2-13 上升旗形图例"）
            cap = b.image.caption() if b.image else ""
            out.append(f"【图】{cap}" if cap else "【图】")
        out.append("")
    text = "\n".join(out)
    if not keep_blank:
        text = re.sub(r"\n{3,}", "\n\n", text)
    return text.rstrip("\n") + "\n" if text.strip() else ""
