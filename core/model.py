"""统一文档模型：所有格式先读成这里的结构，再从结构写出。

设计来源
--------
借鉴 docling（MIT）的 DoclingDocument 核心模式——**一个文档模型配多个导出器**
（export_to_markdown / export_to_text / export_to_html），把 M×N 的格式转换
降为 M 个读取器 + N 个写出器。但不引入其重型依赖（版面分析模型、Torch），
只保留块级结构，因为本项目的输入是办公文档而非扫描件。

与 markitdown（MIT）的分工：markitdown 是单向汇聚到 Markdown，本模型是双向的，
因此必须保留「块类型 + 层级 + 表格结构」这些回写时需要的语义。

保真原则（贯穿全部读取器）
--------------------------
1. 读取器**不允许因为看不懂而丢弃内容**。任何无法归类的行一律落为
   ``paragraph`` 或 ``raw`` 块，原样保留文本。
2. ``verbatim`` 通道：当用户要求逐字保真（txt --plain）时，整篇文档是一个
   ``raw`` 块，写出时原样吐出，连空行数量都不改变。
3. 模型转换（Workbook ⇄ TextDoc）是显式的、可预期的，不做隐式猜测。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Literal

# 块类型：决定写出时用什么语法
BlockKind = Literal[
    "heading",    # 标题，level 为 1-6
    "paragraph",  # 普通段落
    "list",       # 列表，items 存条目
    "code",       # 代码块，lang 存语言
    "quote",      # 引用块
    "table",      # 表格，rows 二维
    "divider",    # 分隔线
    "image",      # 插图，image 存引用（扫描件的图表还原）
    "raw",        # 未解析原文，逐字保留
]

# 模型种类
DocKind = Literal["text", "table"]   # table 类走 Workbook 载荷


@dataclass
class ImageRef:
    """一张待落盘的图片。

    为什么放临时文件而不是内存字节：整本扫描书可能有几百张图，全驻留内存
    会让几百页的任务吃掉上 GB。裁切时落到临时目录，写出去时再按目标格式
    复制或嵌入，pipeline 在 finally 里统一清理。

    name 是**目标目录内**的文件名（p041_fig1.png），path 是临时文件位置。
    """

    name: str
    path: Path
    alt: str = ""          # 图注，作为 md 的 alt 文本
    width: int = 0         # 像素宽高，写出 docx 时用来限宽
    height: int = 0

    def caption(self) -> str:
        return self.alt.strip()


@dataclass
class Block:
    """文档块。默认 paragraph，其余字段按 kind 取用。"""

    kind: BlockKind = "paragraph"
    text: str = ""

    # heading: 1-6；list: 嵌套层级（0 起）
    level: int = 0

    # list 专用
    ordered: bool = False
    start: int = 1
    items: list[str] = field(default_factory=list)

    # table 专用
    rows: list[list[str]] = field(default_factory=list)
    header: bool = True
    align: list[str] = field(default_factory=list)  # left/center/right/""

    # 行内标记（md → 纯文本降级时使用；为空表示不做处理）
    inline: bool = True

    # code 专用
    lang: str = ""

    # image 专用（扫描件的图表/照片）
    image: ImageRef | None = None

    def is_empty(self) -> bool:
        if self.kind in ("list", "table"):
            return not (self.items or self.rows)
        if self.kind == "image":
            return self.image is None
        return not self.text.strip()

    # -- 便捷构造 -------------------------------------------------
    @staticmethod
    def para(text: str) -> "Block":
        return Block(kind="paragraph", text=text)

    @staticmethod
    def head(text: str, level: int = 1) -> "Block":
        return Block(kind="heading", text=text, level=max(1, min(6, level)))

    @staticmethod
    def bullet(items: Iterable[str], ordered: bool = False,
               level: int = 0, start: int = 1) -> "Block":
        return Block(kind="list", items=list(items), ordered=ordered,
                     level=level, start=start)

    @staticmethod
    def table(rows: list[list[str]], header: bool = True) -> "Block":
        return Block(kind="table", rows=rows, header=header)

    @staticmethod
    def figure(image: ImageRef) -> "Block":
        return Block(kind="image", image=image, text=image.caption())


@dataclass
class Sheet:
    """工作表。rows 为二维字符串矩阵，首行默认是表头。"""

    name: str = "Sheet1"
    rows: list[list[str]] = field(default_factory=list)
    header: bool = True

    def width(self) -> int:
        return max((len(r) for r in self.rows), default=0)

    def is_empty(self) -> bool:
        return not any(any(str(c).strip() for c in r) for r in self.rows)


@dataclass
class TextDoc:
    """文本类文档：块序列。"""

    blocks: list[Block] = field(default_factory=list)

    def add(self, block: Block) -> "TextDoc":
        self.blocks.append(block)
        return self

    def is_empty(self) -> bool:
        return all(b.is_empty() for b in self.blocks)

    def plain_text(self) -> str:
        """降级为纯文本：去掉全部标记，只留内容。用于 txt 输出。"""
        out: list[str] = []
        for b in self.blocks:
            if b.kind == "heading":
                out.append(b.text)
            elif b.kind == "paragraph":
                out.append(b.text)
            elif b.kind == "quote":
                out.append(b.text)
            elif b.kind == "code":
                out.append(b.text)
            elif b.kind == "raw":
                out.append(b.text)
            elif b.kind == "divider":
                out.append("")
            elif b.kind == "list":
                for i, it in enumerate(b.items, start=b.start if b.ordered else 0):
                    prefix = f"{i}. " if b.ordered else "- "
                    out.append("  " * b.level + prefix + it)
            elif b.kind == "table":
                for row in b.rows:
                    out.append("\t".join(row))
                out.append("")
            elif b.kind == "image":
                # 纯文本里图片无法呈现，留一行占位保住题注——用户至少知道
                # 「这里原本有张图，叫什么」，比直接丢掉强。
                cap = b.image.caption() if b.image else ""
                out.append(f"【图】{cap}" if cap else "【图】")
        return "\n".join(out)


@dataclass
class Workbook:
    """表格类文档：多工作表。"""

    sheets: list[Sheet] = field(default_factory=list)

    def is_empty(self) -> bool:
        return all(s.is_empty() for s in self.sheets)

    def to_text_doc(self) -> TextDoc:
        """把工作表渲染成文本块（每个 sheet 一张表）。

        这是 Workbook → TextDoc 的受控转换，用于 xlsx/csv 输出到 md/html/txt。
        """
        doc = TextDoc()
        multi = len(self.sheets) > 1
        for sheet in self.sheets:
            if sheet.is_empty():
                continue
            if multi:
                doc.add(Block.head(sheet.name, 2))
            if sheet.rows:
                doc.add(Block.table([list(r) for r in sheet.rows], header=sheet.header))
        return doc


# ---------------------------------------------------------------- 结构表
# 把文本块落成工作表时用的专用表名。以下划线开头，暗示它是内部结构表。
PROSE_SHEET = "_aieye_body"
# 旧版本用过的表名（改名后仍要能读回自己生成过的文件）
LEGACY_PROSE_SHEETS = ("_txt2md_body",)
PROSE_HEADER = ("类型", "层级", "内容")
# 认识的结构表类型（用于反向识别校验，避免误判用户自己的表格）
PROSE_KINDS = {"heading", "paragraph", "list", "list-ordered", "code",
               "quote", "divider", "raw", "table", "image"}


def _prose_sheet(doc: TextDoc, table_blocks: list[Block]) -> Sheet:
    """把文本块展开成 [类型, 层级, 内容] 三列，一次转换即可完整还原。

    为什么不把正文塞成单列：那样会丢掉标题级别、列表层级、代码语言，
    读回来只能是一堆段落。用类型列显式记录，往返才能保结构。
    """
    rows: list[list[str]] = [list(PROSE_HEADER)]
    table_index = 0

    for b in doc.blocks:
        if b.kind == "table":
            if b.rows:
                table_index += 1
                # 表格本体在独立工作表里，这里只留引用，往返时插回原位
                rows.append(["table", "0", f"表{table_index}"])
            continue
        if b.kind == "divider":
            # 分隔线没有文字，必须排在 is_empty 判断之前，否则会被当成空块跳过
            rows.append(["divider", "0", ""])
            continue
        if b.is_empty():
            continue

        if b.kind == "list":
            kind = "list-ordered" if b.ordered else "list"
            for item in b.items:
                rows.append([kind, str(b.level), item])
        elif b.kind == "heading":
            rows.append(["heading", str(b.level), b.text])
        elif b.kind == "code":
            # 层级列复用为代码语言（code 没有层级概念）
            rows.append(["code", b.lang or "", b.text])
        elif b.kind == "divider":
            rows.append(["divider", "0", ""])
        else:
            rows.append([b.kind, "0", b.text])

    return Sheet(name=PROSE_SHEET, rows=rows, header=True)


def prose_sheet_to_doc(sheet: Sheet, book: Workbook) -> TextDoc:
    """把结构表还原成文本块。仅在确认是结构表后调用。"""
    doc = TextDoc()
    for row in sheet.rows[1:]:
        if not row:
            continue
        kind = (row[0] if len(row) > 0 else "").strip().lower()
        extra = (row[1] if len(row) > 1 else "").strip()
        content = row[2] if len(row) > 2 else ""
        level = int(extra) if extra.isdigit() else 0

        if kind == "heading":
            doc.add(Block.head(content, level or 1))
        elif kind in ("list", "list-ordered"):
            ordered = kind == "list-ordered"
            last = doc.blocks[-1] if doc.blocks else None
            if (last is not None and last.kind == "list"
                    and last.ordered == ordered and last.level == level):
                last.items.append(content)
            else:
                doc.add(Block(kind="list", items=[content], ordered=ordered, level=level))
        elif kind == "code":
            doc.add(Block(kind="code", text=content, lang=extra))
        elif kind == "quote":
            doc.add(Block(kind="quote", text=content))
        elif kind == "divider":
            doc.add(Block(kind="divider"))
        elif kind == "table":
            target = next((s for s in book.sheets if s.name == content.strip()), None)
            if target is not None:
                doc.add(Block(kind="table", rows=[list(r) for r in target.rows],
                              header=target.header))
        elif kind == "raw":
            doc.add(Block(kind="raw", text=content))
        else:
            doc.add(Block.para(content))
    return doc


def looks_like_prose_sheet(sheet: Sheet) -> bool:
    """判断某工作表是否为结构表：表名 + 表头 + 首列取值三重校验。

    表名同时接受旧版 ``_txt2md_body``：2.0 生成的 xlsx 里写的是这个名字，
    改名后如果不认，那些文件再转回 Markdown 时正文会被当成普通表格。
    """
    if sheet.name not in (PROSE_SHEET, *LEGACY_PROSE_SHEETS) or not sheet.rows:
        return False
    head = [str(c).strip() for c in sheet.rows[0][:3]]
    if tuple(head) != PROSE_HEADER:
        return False
    body = [r for r in sheet.rows[1:] if r and str(r[0]).strip()]
    if not body:
        return True
    known = sum(1 for r in body if str(r[0]).strip().lower() in PROSE_KINDS)
    return known / len(body) >= 0.8


@dataclass
class Document:
    """转换管线的统一载体。

    同一份内容可能同时具备文本视图与表格视图（如 csv），
    由 ``kind`` 标明主视图，写出器按需取用另一视图。
    """

    kind: DocKind = "text"
    name: str = "document"

    text: TextDoc | None = None
    book: Workbook | None = None

    # 来源信息（用于日志与防呆提示）
    source_format: str = ""
    source_encoding: str = ""
    source_title: str = ""

    # 文档元信息（EPUB 的 dc:title / dc:creator / dc:language，docx 的核心属性）。
    # 写出 EPUB / 生成 AI 导读时要用；其它格式留空即可，不影响既有行为。
    title: str = ""
    author: str = ""
    language: str = ""

    # 待落盘的图片（扫描件的插图）。写出器负责复制到目标目录或嵌入文档，
    # 临时文件由 pipeline 在任务结束时统一清理。
    assets: list[ImageRef] = field(default_factory=list)

    # 读取过程中的非致命问题（如"第 3 张表为空，已跳过"）
    warnings: list[str] = field(default_factory=list)

    # 逐字保真标记：为真时写出器只做换行规范化，不加任何标记
    verbatim: bool = False

    # -- 视图获取（缺失时自动派生，避免调用方到处判空）----------
    def as_text(self) -> TextDoc:
        if self.text is not None:
            return self.text
        if self.book is not None:
            self.text = self.book.to_text_doc()
            return self.text
        self.text = TextDoc()
        return self.text

    def as_book(self) -> Workbook:
        if self.book is not None:
            return self.book
        # 文本 → 表格：表格块独立成工作表，其余内容进「正文」表（带类型列），
        # 保证 md → xlsx 不丢任何文字，且能被 read_xlsx 还原回原来的结构。
        book = Workbook()
        table_blocks = [b for b in self.as_text().blocks if b.kind == "table" and b.rows]

        if table_blocks:
            for b in table_blocks:
                book.sheets.append(Sheet(name=f"表{len(book.sheets) + 1}",
                                         rows=b.rows, header=b.header))
            if self._has_prose():
                book.sheets.append(_prose_sheet(self.as_text(), table_blocks))
        else:
            book.sheets.append(_prose_sheet(self.as_text(), []))

        self.book = book
        return book

    def _has_prose(self) -> bool:
        return any(b.kind != "table" and not b.is_empty() for b in self.as_text().blocks)

    def is_empty(self) -> bool:
        if self.kind == "table":
            return self.book.is_empty() if self.book else True
        return self.text.is_empty() if self.text else True


# ---------------------------------------------------------------- 文本工具
_BLANKS = re.compile(r"\n{3,}")


def normalize_newlines(text: str) -> str:
    """统一换行为 \\n，并去掉 BOM 残留。"""
    return text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")


def ensure_single_trailing_newline(text: str) -> str:
    """末尾保证单个换行（空文档返回空串）。"""
    if not text:
        return ""
    return text.rstrip("\n") + "\n"


def collapse_blank_runs(text: str, keep: int = 2) -> str:
    """把连续空行压到最多 keep-1 个空行（即 keep 个换行）。"""
    return _BLANKS.sub("\n" * keep, text)


def blocks_of_text(text: str, start_heading: bool = False) -> TextDoc:
    """把纯文本按空行切成段落块。

    ``start_heading`` 为真时把首个非空段落升为一级标题（对齐 1.0 的 smart 规则）。
    """
    doc = TextDoc()
    prose = normalize_newlines(text)
    if not prose.strip():
        if prose:
            doc.add(Block(kind="raw", text=prose))
        return doc

    used_title = False
    chunk: list[str] = []

    def flush():
        nonlocal used_title
        body = "\n".join(chunk).strip("\n")
        if not body.strip():
            return
        if start_heading and not used_title:
            used_title = True
            doc.add(Block.head(body.strip(), 1))
        else:
            doc.add(Block.para(body))
        chunk.clear()

    for line in prose.split("\n"):
        if not line.strip():
            flush()
        else:
            chunk.append(line)
    flush()
    return doc
