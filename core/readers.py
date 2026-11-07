"""各格式读取器：文件 → Document 模型。

保真纪律
--------
1. **编码**：保留 1.0 的探测链条（BOM → UTF-8 → 中文编码），并解决其简繁歧义——
   GBK 与 Big5 的高位区重叠，1.0 按顺序先命中 GBK，会把繁体文件解成乱码。
   现在对候选结果做「像不像正常文本」的打分择优，1.0 已正确解出的文件结果不变。
2. **不丢内容**：无法归类的段落一律保留；空段落用于维持段落分隔。
3. **损坏文件补救**：xlsx 的 `showZeroes` 是非标准属性（真实存在，微软自己的
   Excel 也写过），会让 openpyxl 直接抛错。读之前做一次 XML 修补再重试。
4. **读不动就明说**：扫描版 PDF（无文本层）不容忍返回空串假装成功。
"""

from __future__ import annotations

import csv
import io
import re
import sys
import tempfile
import zipfile
from pathlib import Path

from .errors import DependencyMissing, SourceError
from .mdparse import parse_markdown
from .model import (Block, Document, ImageRef, Sheet, TextDoc, Workbook,
                    looks_like_prose_sheet, normalize_newlines, prose_sheet_to_doc)
from .registry import format_of_path, resolve_format

# ---------------------------------------------------------------- 编码探测
# 顺序即优先级：确定性编码在前，可猜测编码在后
_BOM_TABLE = [
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe\x00\x00", "utf-32-le"),
    (b"\x00\x00\xfe\xff", "utf-32-be"),
    (b"\xff\xfe", "utf-16-le"),
    (b"\xfe\xff", "utf-16-be"),
]

# 中文 Windows 实际是 GB18030（GBK 的超集），用它替代 GBK 不会改变纯 GBK 文件的解码结果
_CJK_CANDIDATES = ("gb18030", "big5", "shift_jis", "euc-kr")

_COMMON_HAN = set(
    "的一是在不了有和人这中大为上个国我以要他时来用们生到作地于出就分对成会可主发年动"
    "同工也能下过子说产种面而方后多定行学法所民得经十三之进着等部度家电力里如水化高自"
    "二理起小物现实加量都两体制机当使点从业本去把性好应开它合还因由其些然前外天政四日"
    "那社义事平形相全表间样与关各重新线内数正心反你明看原又么利比或但质气第向道命此变"
    "条只没结解问意建月公无系军很情者最立代想已通并提直题党程展五果料象员革位入常文总"
    "次品式活设及管特件長發學會體實對開關點時個們來後應國產業問題經濟這樣經過發現"
)


def _score_text(text: str) -> float:
    """给解码结果打分：越像正常文本分越高。用于消解编码歧义。"""
    if not text:
        return 0.0
    n = len(text)
    bad = text.count("\ufffd") + sum(
        1 for c in text if ord(c) < 32 and c not in "\n\r\t")
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    common = sum(1 for c in text if c in _COMMON_HAN)
    # 控制字符与替换符是强负信号；常见汉字是强正信号
    return (common * 3 + cjk) / n - (bad * 40 / n)


def decode_bytes(raw: bytes, hint: str = "") -> tuple[str, str]:
    """解码字节串，返回 (文本, 编码名)。

    不做「静默替换」：所有候选都失败才退回 replace，并在编码名标注 ``*``。
    """
    if not raw:
        return "", "utf-8"

    for bom, enc in _BOM_TABLE:
        if raw.startswith(bom):
            # utf-16-le/be 解码不会自动吃掉 BOM，会留下 \ufeff 字符
            return raw.decode(enc, "replace").lstrip("\ufeff"), enc

    # 严格 UTF-8 最可靠，先试
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass

    # 用户/调用方指定的编码优先
    if hint:
        try:
            return raw.decode(hint), hint
        except (UnicodeDecodeError, LookupError):
            pass

    # 中文编码逐个试解码 + 打分择优（解决 GBK/Big5 歧义）
    best: tuple[float, str, str] | None = None
    for enc in _CJK_CANDIDATES:
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        score = _score_text(text)
        if best is None or score > best[0]:
            best = (score, text, enc)

    if best is not None and best[0] > -0.05:
        return best[1], best[2]

    # 交给 charset_normalizer 再判一次（对 Windows-1252 等西文编码有效）
    try:
        from charset_normalizer import from_bytes
        match = from_bytes(raw).best()
        if match is not None:
            text = str(match)
            if _score_text(text) >= (best[0] if best else -1):
                return text, match.encoding or "unknown"
    except Exception:
        pass

    if best is not None:
        return best[1], best[2]
    return raw.decode("utf-8", "replace"), "utf-8*"


def looks_binary(raw: bytes) -> bool:
    """粗判是否为二进制文件（防止把 exe 当 txt 转出乱码）。"""
    if not raw:
        return False
    sample = raw[:4096]
    if b"\x00" in sample:
        return True
    ctrl = sum(1 for b in sample if b < 9 or 13 < b < 32)
    return ctrl / len(sample) > 0.10


# ---------------------------------------------------------------- 各格式读取
def read_text_file(path: Path, *, start_heading: bool = False,
                   verbatim: bool = False, encoding: str = "") -> Document:
    """读取纯文本。

    ``verbatim=True`` 时整篇作为 raw 块，写出可逐字还原（对齐 1.0 的 --plain）。
    ``start_heading=True`` 时首段升为一级标题（对齐 1.0 的 --smart）。
    """
    raw = path.read_bytes()
    if looks_binary(raw):
        raise SourceError(f"{path.name} 看起来是二进制文件，不是文本",
                          hint="请确认文件类型；如确实是文本，可能编码异常")
    text, enc = decode_bytes(raw, encoding)
    doc = Document(kind="text", name=path.stem, source_format="txt",
                   source_encoding=enc)
    if enc.endswith("*"):
        doc.warnings.append(f"编码无法可靠识别，已按 {enc.rstrip('*')} 容错解码，"
                            f"个别字符可能不正确")

    text = normalize_newlines(text)
    if verbatim:
        doc.verbatim = True
        doc.text = TextDoc([Block(kind="raw", text=text)])
        return doc

    from .model import blocks_of_text
    doc.text = blocks_of_text(text, start_heading=start_heading)
    return doc


def read_markdown_file(path: Path, *, encoding: str = "",
                       verbatim: bool = False) -> Document:
    raw = path.read_bytes()
    text, enc = decode_bytes(raw, encoding)
    doc = Document(kind="text", name=path.stem, source_format="md",
                   source_encoding=enc)
    if verbatim:
        # --plain 语义：内容原样保留，仅改扩展名（对齐 1.0）
        doc.verbatim = True
        doc.text = TextDoc([Block(kind="raw", text=normalize_newlines(text))])
        return doc
    doc.text = parse_markdown(text, path.stem)
    return doc


def read_html_file(path: Path, *, encoding: str = "") -> Document:
    """HTML → 块模型。用标准库解析，不引入 bs4。

    只取可见文本与表格；脚本/样式丢弃（这是唯一允许「丢内容」的地方，
    因为它们不属于文档内容）。
    """
    from .htmlio import html_to_doc
    raw = path.read_bytes()
    text, enc = decode_bytes(raw, encoding)
    doc = Document(kind="text", name=path.stem, source_format="html",
                   source_encoding=enc)
    doc.text = html_to_doc(text)
    if doc.text.is_empty():
        raise SourceError(f"{path.name} 中没有可提取的正文",
                          hint="页面可能完全由脚本渲染")
    return doc


def read_csv_file(path: Path, *, encoding: str = "", delimiter: str = "",
                  has_header: bool = True) -> Document:
    """读取 CSV/TSV 为工作表。

    分隔符自动判定：``.tsv`` 用制表符，其余在 ``, ; \\t |`` 中按首行出现次数择优。
    """
    raw = path.read_bytes()
    if looks_binary(raw):
        raise SourceError(f"{path.name} 不是文本表格")
    text, enc = decode_bytes(raw, encoding)
    text = normalize_newlines(text)
    if not text.strip():
        raise SourceError(f"{path.name} 是空文件")

    if not delimiter:
        suffix = path.suffix.lower()
        if suffix == ".tsv":
            delimiter = "\t"
        else:
            first = text.split("\n", 1)[0]
            counts = {d: first.count(d) for d in (",", ";", "\t", "|")}
            delimiter = max(counts, key=counts.get) if max(counts.values()) else ","

    rows: list[list[str]] = []
    try:
        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        for row in reader:
            rows.append([c for c in row])
    except csv.Error as exc:
        raise SourceError(f"CSV 解析失败：{exc}", hint="请检查引号是否配对") from exc

    # 去掉完全空白的尾部行
    while rows and not any(c.strip() for c in rows[-1]):
        rows.pop()
    if not rows:
        raise SourceError(f"{path.name} 没有数据行")

    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]

    doc = Document(kind="table", name=path.stem, source_format="csv",
                   source_encoding=enc)
    doc.book = Workbook([Sheet(name=path.stem or "Sheet1", rows=rows,
                               header=has_header)])
    return doc


# -- xlsx --------------------------------------------------------
_SHEETVIEW = re.compile(rb"<sheetView(?=[\s/>])[^>]*>")
_SHOW_ZEROES = re.compile(rb"(?<=[\s])showZeroes(\s*=)")


def _repair_xlsx(path: Path) -> io.BytesIO | None:
    """修补非标准 showZeroes 属性。返回修补后的内存流，无需修补则 None。

    该属性不在 OOXML schema 内，但真实文件里存在（部分导出工具与旧版 Excel 会写），
    openpyxl 会拒绝。修法借鉴 markitdown（MIT）的同名处理：只改 <sheetView> 起始标签。
    """
    try:
        out = io.BytesIO()
        changed = False
        with zipfile.ZipFile(path) as src, \
                zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
            for item in src.infolist():
                data = src.read(item.filename)
                if (item.filename.startswith("xl/worksheets/")
                        and item.filename.endswith(".xml")
                        and b"showZeroes" in data):
                    data = _SHEETVIEW.sub(
                        lambda m: _SHOW_ZEROES.sub(rb"showZeros\1", m.group(0)), data)
                    changed = True
                dst.writestr(item, data)
        if not changed:
            return None
        out.seek(0)
        return out
    except (zipfile.BadZipFile, OSError):
        return None


def read_xlsx_file(path: Path, *, sheets: list[str] | None = None) -> Document:
    try:
        import openpyxl
    except ImportError as exc:
        raise DependencyMissing.for_import(exc, "读取 xlsx", "openpyxl") from exc

    def _load(source):
        return openpyxl.load_workbook(source, data_only=True, read_only=False)

    try:
        try:
            wb = _load(path)
        except Exception as first:
            repaired = _repair_xlsx(path)
            if repaired is None:
                raise first
            wb = _load(repaired)
    except zipfile.BadZipFile as exc:
        raise SourceError(f"{path.name} 不是有效的 xlsx（可能其实是旧的 .xls）",
                          hint="请在 Excel 中另存为 .xlsx 后重试") from exc
    except Exception as exc:
        raise SourceError(f"无法打开 {path.name}：{exc}") from exc

    doc = Document(kind="table", name=path.stem, source_format="xlsx")
    book = Workbook()
    try:
        for ws in wb.worksheets:
            if sheets and ws.title not in sheets:
                continue
            rows: list[list[str]] = []
            # 用单元格对象而非 values_only：需要 number_format 才能还原
            # 「12.50」这种带尾随零的显示精度（数值本身 12.50 与 12.5 相同）
            for row in ws.iter_rows():
                rows.append([_cell_value(c) for c in row])
            while rows and not any(c.strip() for c in rows[-1]):
                rows.pop()
            if not rows:
                doc.warnings.append(f"工作表「{ws.title}」为空，已跳过")
                continue
            # 统一列宽
            width = max(len(r) for r in rows)
            rows = [r + [""] * (width - len(r)) for r in rows]
            book.sheets.append(Sheet(name=ws.title, rows=rows, header=True))
    finally:
        wb.close()

    if not book.sheets:
        raise SourceError(f"{path.name} 中没有非空工作表")

    # 若含结构表，还原为文本文档（保住标题级别、列表层级、代码语言）
    prose = next((s for s in book.sheets if looks_like_prose_sheet(s)), None)
    if prose is not None:
        doc.kind = "text"
        doc.book = book
        doc.text = prose_sheet_to_doc(prose, book)
        return doc

    doc.book = book
    return doc


def _cell_value(cell) -> str:
    """取单元格显示值：优先按 number_format 还原小数位数。"""
    v = cell.value
    if v is None:
        return ""
    if isinstance(v, float):
        fmt = (cell.number_format or "").strip()
        if fmt.startswith("0.") and set(fmt[2:]) == {"0"}:
            decimals = len(fmt) - 2
            if decimals <= 10:
                return f"{v:.{decimals}f}"
    return _cell_text(v)


def _cell_text(value) -> str:
    """单元格值转字符串，尽量不改写用户数据。

    数字去掉 .0 尾巴，日期用 ISO，布尔用 TRUE/FALSE（与 Excel 显示一致）。
    """
    import datetime as _dt
    import decimal

    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)[:15].rstrip("0").rstrip(".")
    if isinstance(value, decimal.Decimal):
        return format(value, "f")
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat(sep=" ") if isinstance(value, _dt.datetime) else value.isoformat()
    return str(value)


# -- docx --------------------------------------------------------
_MONO_HINTS = ("consolas", "cascadia", "courier", "mono", "menlo", "source code")


def _run_markdown(p) -> str:
    """把段落的 run 级格式还原成 Markdown 行内标记。

    只读 ``p.text`` 会把粗体/斜体全部抹平，docx → md 后就再也回不去了。
    这里按 run 的 bold/italic 反向补标记，保证 docx → md → docx 格式可往返。
    """
    parts: list[str] = []
    for run in getattr(p, "runs", []):
        t = run.text
        if not t:
            continue
        if run.bold and run.italic:
            t = f"***{t}***"
        elif run.bold:
            t = f"**{t}**"
        elif run.italic:
            t = f"*{t}*"
        parts.append(t)
    text = "".join(parts)
    # runs 可能漏掉超链接等元素，用 p.text 兜底避免丢字
    return text if text.strip() else (p.text or "")


def _has_bottom_border(p) -> bool:
    """段落是否有下边框（写出时用边框表示分隔线）。"""
    try:
        from docx.oxml.ns import qn
        pPr = p._p.pPr
        if pPr is None:
            return False
        bdr = pPr.find(qn("w:pBdr"))
        return bdr is not None and bdr.find(qn("w:bottom")) is not None
    except Exception:
        return False


def _is_code_para(p) -> bool:
    """整段使用等宽字体 → 判定为代码块（写出时用 Consolas 表示代码）。"""
    runs = [r for r in getattr(p, "runs", []) if r.text.strip()]
    if not runs:
        return False
    for r in runs:
        name = (r.font.name or "").lower()
        if not name:
            # 未显式设置时看样式
            name = ((p.style.name if p.style is not None else "") or "").lower()
        if not any(h in name for h in _MONO_HINTS):
            return False
    return True


def _indent_level(p) -> int:
    """按左缩进推算列表层级（写出时每级 18pt）。"""
    try:
        li = p.paragraph_format.left_indent
        if not li:
            return 0
        return max(0, min(5, round(li.pt / 18) - 1))
    except Exception:
        return 0


def read_docx_file(path: Path) -> Document:
    try:
        import docx
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError as exc:
        raise DependencyMissing.for_import(exc, "读取 docx", "python-docx") from exc

    try:
        d = docx.Document(str(path))
    except Exception as exc:
        raise SourceError(f"无法打开 {path.name}：{exc}",
                          hint="文件可能损坏，或其实是旧版 .doc") from exc

    doc = Document(kind="text", name=path.stem, source_format="docx")
    body = d.element.body

    def para_to_block(p: Paragraph) -> Block | None:
        text = p.text
        style = (p.style.name if p.style is not None else "") or ""
        low = style.lower()

        # 分隔线：无文字但有下边框
        if not text.strip() and _has_bottom_border(p):
            return Block(kind="divider")
        if not text.strip():
            return None

        # 代码块：等宽字体段落（优先于标题判断，避免代码里的 # 被当标题）
        if _is_code_para(p):
            return Block(kind="code", text=text)

        m = re.match(r"heading\s*(\d)", low) or re.match(r"标题\s*(\d)", style)
        if m:
            return Block.head(_run_markdown(p).strip(), int(m.group(1)))
        if low in ("title", "标题"):
            return Block.head(_run_markdown(p).strip(), 1)
        if low == "subtitle":
            return Block.head(_run_markdown(p).strip(), 2)

        if low in ("quote", "intense quote", "引用", "明显引用"):
            return Block(kind="quote", text=_run_markdown(p))

        if "list bullet" in low or "list paragraph" in low:
            return Block(kind="list", items=[_run_markdown(p).strip()],
                         ordered=False, level=_indent_level(p))
        if "list number" in low:
            return Block(kind="list", items=[_run_markdown(p).strip()],
                         ordered=True, level=_indent_level(p))

        return Block.para(_run_markdown(p))

    raw_blocks: list[Block | None] = []
    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            raw_blocks.append(para_to_block(Paragraph(child, d)))
        elif tag == "tbl":
            table = Table(child, d)
            rows = []
            for r in table.rows:
                cells = []
                seen_cells = set()
                for c in r.cells:
                    # 合并单元格会重复返回同一对象，去重避免列数虚高
                    if id(c._tc) in seen_cells:
                        continue
                    seen_cells.add(id(c._tc))
                    cells.append(c.text.strip())
                rows.append(cells)
            if rows:
                raw_blocks.append(Block(kind="table", rows=rows, header=True))
            raw_blocks.append(None)

    # 合并相邻同类块：代码/引用逐段写出，读回时要重新拼成一个块
    doc.text = TextDoc()
    for b in raw_blocks:
        if b is None:
            continue
        last = doc.text.blocks[-1] if doc.text.blocks else None
        if (b.kind == "code" and last is not None and last.kind == "code"):
            last.text += "\n" + b.text
            continue
        if (b.kind == "quote" and last is not None and last.kind == "quote"):
            last.text += "\n" + b.text
            continue
        if (b.kind == "list" and last is not None and last.kind == "list"
                and last.ordered == b.ordered and last.level == b.level):
            last.items.extend(b.items)
            continue
        doc.text.add(b)

    if doc.text.is_empty():
        raise SourceError(f"{path.name} 中没有可提取的文字",
                          hint="文档可能是空的，或内容全在图片里")
    return doc


# -- pdf ---------------------------------------------------------
def _pdf_real_tables(page) -> list[tuple[tuple[float, float, float, float], list[list[str]]]]:
    """挑出真正像表格的区域，返回 [(bbox, rows)]，按纵坐标从上到下。

    PDF 没有「表格」这个概念，全靠画线推断。从 HTML 打印出来的 PDF 里，正文
    文字块之间的框线会让 pdfplumber 把**整页**识别成一张只有一列或两列的
    「表格」——放任不管的话，整篇文章会塌成一张表，而且表格区和正文区各被
    提取一次，内容直接翻倍。所以这里先把不像表格的候选丢掉。
    """
    page_area = max(1.0, float(page.width) * float(page.height))

    def _not_page_frame(obj) -> bool:
        # 整页边框（HTML 打印版式的 body 外框）会把真表格的网格、引用竖线、
        # 标题下划线全部串成一张覆盖整页的假表——真表反而找不到了。
        # 先把这种巨型矩形剔除，剩下的边线才构成真的表格网格。
        if obj.get("object_type") != "rect":
            return True
        w = float(obj.get("x1") or 0) - float(obj.get("x0") or 0)
        h = float(obj.get("bottom") or 0) - float(obj.get("top") or 0)
        return w * h <= page_area * 0.4

    try:
        finder = page
        if hasattr(page, "filter"):
            try:
                finder = page.filter(_not_page_frame)
            except Exception:
                finder = page
        found = finder.find_tables()
    except Exception:
        return []

    # pdfplumber 0.11 起 find_tables() 直接返回 list；旧版本返回带 .tables 的对象。
    # 只认 .tables 会让新版本上永远找不到表格（静默丢结构），两种都要接。
    items = getattr(found, "tables", None)
    if items is None:
        items = list(found) if isinstance(found, (list, tuple)) else []

    page_area = max(1.0, float(page.width) * float(page.height))
    out: list[tuple[tuple[float, float, float, float], list[list[str]]]] = []
    for t in items:
        try:
            raw = t.extract() or []
        except Exception:
            continue
        rows = [["" if c is None else str(c).replace("\n", " ").strip() for c in r]
                for r in raw]
        rows = [r for r in rows if any(c for c in r)]
        if not rows:
            continue

        # 先裁掉整列为空的列。整页被误判成表格时，右边那列是所有行都为空
        # 的假列——真实表格不会有「一个字都没有」的列。裁完只剩一列，
        # 就说明这压根不是表格。
        width = max(len(r) for r in rows)
        keep = [i for i in range(width) if any(i < len(r) and r[i] for r in rows)]
        if len(keep) < 2:
            continue
        rows = [[r[i] if i < len(r) else "" for i in keep] for r in rows]

        # 至少两行两列才叫表格：单列是正文，单行是分隔条
        if len(rows) < 2:
            continue
        x0, top, x1, bottom = (float(v) for v in t.bbox)
        # 表格框占了大半页高，说明它把正文段落也圈了进去（HTML 里 h1 的下划线、
        # 代码块和表格的边框会连成一片网格）。真表格极少有这种比例；宁可退回
        # 「整页当正文」——文字一个不少，只是少了表格结构——也不能让整篇文章
        # 塌成一张表。
        if (bottom - top) / max(1.0, float(page.height)) > 0.75:
            continue
        area = max(0.0, x1 - x0) * max(0.0, bottom - top)
        if area / page_area > 0.85 and len(keep) <= 2:
            continue                     # 铺满整页的两列表格 = 正文误判
        out.append(((x0, top, x1, bottom), rows))

    out.sort(key=lambda item: item[0][1])
    return out


# 行首的列表标记：无序符号、1. / 1) / （1） 等
_PDF_LIST_RE = re.compile(r"^\s*(?:[-*•·▪◦‣]|\d{1,2}[.)、]|[（(]\d{1,2}[)）])\s+")
# 出现在行尾就认为一句话说完了，下一行另起段落
_PDF_SENT_END = ("。", "！", "？", "；", "：", ".", "!", "?", ";", ":", "”", "）", "】")
# 句中标点：标题里极少出现逗号分号，出现就说明这是一句话
_SENT_PUNCT = re.compile(r"[，。；！？,;]")


def _looks_like_heading(text: str, ocr: bool) -> bool:
    """字号明显偏大的行，是否够格当标题。

    文字层里字号是排版属性，很可靠；OCR 里它是外接框高度，长句带括号时
    会被撑高。所以 OCR 路径额外要求「长句不得含句中标点」。
    """
    if len(text) > (60 if ocr else 80):
        return False
    # 纯数字行不是标题：页码（31）、图表编号（2-13）都会因为短而被判大字号
    if text.strip().isdigit():
        return False
    if ocr and len(text) > 20 and _SENT_PUNCT.search(text):
        return False
    return True


def _pdf_page_blocks(page) -> list[Block]:
    """把一页的文字行还原成块序列（文字层路径）。"""
    try:
        lines = page.extract_text_lines()
    except Exception:
        lines = []

    info: list[dict] = []
    for ln in lines or []:
        text = (ln.get("text") or "").strip()
        if not text:
            continue
        chars = ln.get("chars") or []
        size = max((float(c.get("size") or 0) for c in chars), default=0.0)
        bold = any("bold" in str(c.get("fontname") or "").lower() for c in chars)
        info.append({"text": text, "size": size, "bold": bold,
                     "x0": float(ln.get("x0") or 0.0),
                     "top": float(ln.get("top") or 0.0),
                     "bottom": float(ln.get("bottom") or 0.0)})

    if not info:
        # 老版本 pdfplumber 没有 extract_text_lines，退回整段文本
        text = (page.extract_text() or "").strip()
        if not text:
            return []
        from .model import blocks_of_text
        return [b for b in blocks_of_text(text, start_heading=False).blocks
                if b.kind != "table"]

    # 引用竖线只存在于矢量矩形里（文字层没有），在这里取出来交给重建逻辑
    rects = []
    try:
        rects = [r for r in page.rects
                 if float(r.get("x1") or 0) - float(r.get("x0") or 0) <= 6
                 and float(r.get("bottom") or 0) - float(r.get("top") or 0) >= 8]
    except Exception:
        rects = []
    return _blocks_from_lines(info, rects)


def _blocks_from_lines(info: list[dict], rects: list, *, ocr: bool = False) -> list[Block]:
    """把行信息（text/size/bold/x0/top/bottom）还原成块序列。

    文字层路径与 OCR 路径共用：OCR 结果的框坐标正好提供 x0 与框高，
    走的完全是同一套弱信号推断。PDF 只记录「哪几个字画在哪个坐标」，
    没有段落和标题的概念，这里靠几条弱信号猜：字号比正文明显大的行当
    标题，行尾标点决定段落在哪断，左侧的细长竖条是引用边线，剩余的
    缩进档位是列表层级。猜错的最坏结果是段落划分不理想，文字本身不会
    丢——这符合 PDF 的 ○ 级保真定位。

    ``ocr=True`` 时收紧标题判定：OCR 的「字号」其实是外接框高度，行内
    出现括号、数字、下标就会把框撑高，一个普通长句因此被判成标题（实测
    「如图2-14所示，是美尔雅（600107）的日K线走势图…」整行变成了 h3）。
    """
    # 引用识别：HTML 打印版式里 blockquote 画一条细长竖线（宽几磅、高度
    # 覆盖整段），文字从竖线右侧缩进一点开始。竖线不在文字层，只能看 rect。
    # 注意引用的悬挂缩进会污染列表的缩进档位，必须先把引用行挑出来排除掉。
    quotes: list[bool] = []
    for it in info:
        hit = False
        for r in rects:
            rx0 = float(r.get("x0") or 0)
            rx1 = float(r.get("x1") or 0)
            rtop = float(r.get("top") or 0)
            rbot = float(r.get("bottom") or 0)
            if (rx1 <= it["x0"] + 2 and rx0 >= it["x0"] - 24
                    and rtop <= it["bottom"] - 2 and rbot >= it["top"] + 2):
                hit = True
                break
        quotes.append(hit)

    # 基准字号：按字符数加权取众数，比平均数更抗「少数大标题」的影响
    weight: dict[float, int] = {}
    for it in info:
        key = round(it["size"], 1)
        weight[key] = weight.get(key, 0) + len(it["text"])
    base = max(weight.items(), key=lambda kv: kv[1])[0] if weight else 0.0

    # 比正文大 15% 以上的字号，从大到小映射到 h1 / h2 / h3 …
    head_sizes = sorted({round(it["size"], 1) for it in info
                         if base and it["size"] >= base * 1.15}, reverse=True)
    level_of = {s: min(6, i + 1) for i, s in enumerate(head_sizes)}

    # 列表靠左缩进认：PDF 里项目符号常是画出来的图形，文字层没有 "- "，
    # 但缩进会如实保留。把出现过的缩进量分成档位，每一档就是一层列表——
    # 不能用「缩进 ÷ 最小步长」取整：列表在 17.6、嵌套在 27.6 时，
    # 17.6/17.6 和 27.6/17.6 取整都是 1，嵌套就被拍平了。
    # 下限取一个正文字符宽——表格单元格之间常有几磅的位置差，那种不算缩进。
    # 引用行不参与：引用的悬挂缩进（如 58.2）会挤掉列表的顶层档位。
    left = min((it["x0"] for it, q in zip(info, quotes) if not q), default=0.0)
    min_gap = max(6.0, base)
    if ocr:
        # 扫描件放弃「按缩进认列表」：中文正文段首缩进两个字符，与列表缩进
        # 在几何上完全同构，而 OCR 的 x0 还有几像素抖动。实测一本书几乎每个
        # 段落首行都会被判成列表项（「在使用上升旗形买入方法时…」变成 L2 的
        # 单条列表）。扫描件只认显式项目符号：- • 1. 这类。
        ranks: dict[float, int] = {}
    else:
        ranks = {ind: i for i, ind in enumerate(sorted(
            {round(it["x0"] - left, 1) for it, q in zip(info, quotes)
             if not q and it["x0"] - left >= min_gap}))}

    # 层级用 None 表示「这一行不是列表项」——0 是合法层级（顶层列表），
    # 两者不能用同一个值表示，否则顶层列表整段会被当成普通段落。
    levels: list[int | None] = []
    for it, q in zip(info, quotes):
        if q:
            levels.append(None)
            continue
        lvl: int | None = None
        if len(it["text"]) <= 24 and round(it["x0"] - left, 1) in ranks:
            lvl = ranks[round(it["x0"] - left, 1)]
        levels.append(lvl)

    blocks: list[Block] = []
    para: list[str] = []
    items: list[str] = []
    item_level = 0
    quote_text: list[str] = []

    def flush_para() -> None:
        if para:
            blocks.append(Block(kind="paragraph", text="".join(para)))
            para.clear()

    def flush_list() -> None:
        if items:
            blocks.append(Block(kind="list", items=list(items), level=item_level))
            items.clear()

    def flush_quote() -> None:
        if quote_text:
            blocks.append(Block(kind="quote", text="\n".join(quote_text)))
            quote_text.clear()

    for idx, it in enumerate(info):
        text = it["text"]
        key = round(it["size"], 1)
        if key in level_of and _looks_like_heading(text, ocr):
            flush_para()
            flush_list()
            flush_quote()
            blocks.append(Block(kind="heading", text=text, level=level_of[key]))
            continue

        if quotes[idx]:
            flush_para()
            flush_list()
            quote_text.append(text)
            continue
        flush_quote()

        lvl = levels[idx]
        m = _PDF_LIST_RE.match(text)
        if m:
            if lvl is None:
                lvl = 0                 # 文字层带着 "- " 或 "1." 就是明确的列表项
            text = text[m.end():].strip()

        if lvl is not None:
            flush_para()
            if items and lvl != item_level:
                flush_list()              # 层级变了，另起一个列表块
            item_level = lvl
            items.append(text)
            continue

        flush_list()
        # 短行（表格单元格、独立短语）不并入上一段，否则会粘成一长串
        if len(text) <= 12 and not text.rstrip().endswith(_PDF_SENT_END):
            flush_para()
            blocks.append(Block(kind="paragraph", text=text))
            continue
        if not para:
            para.append(text)
            continue
        prev = para[-1]
        if prev.rstrip().endswith(_PDF_SENT_END):
            flush_para()
            para.append(text)
            continue
        # 中文直接拼接；两边都是英文/数字时才补一个空格，避免单词粘在一起
        need_space = (prev and prev[-1].isascii() and prev[-1].isalnum()
                      and text and text[0].isascii() and text[0].isalnum())
        para.append((" " if need_space else "") + text)

    flush_para()
    flush_list()
    flush_quote()
    return blocks


# ---------------------------------------------------------------- 图片资产
def _asset_dir(explicit: str | Path | None) -> Path:
    """准备图片落地目录。显式传入时用它（pipeline 的临时目录），否则自建。"""
    if explicit:
        p = Path(explicit)
        p.mkdir(parents=True, exist_ok=True)
        return p
    # 单独调用 read_any（不经 pipeline）时没人负责清理；宁可留几十 MB 临时
    # 文件，也不能让整次转换失败。
    return Path(tempfile.mkdtemp(prefix="aieye-assets-"))


def _save_figure(image, region, asset_root: Path | None, stem: str,
                 page_no: int, *, max_width: int = 1400) -> ImageRef | None:
    """裁出一个图区并存盘，返回图片引用。asset_root 为空时不产图。"""
    if asset_root is None:
        return None
    from . import layout as layout_mod
    try:
        data, w, h = layout_mod.crop_region(image, region, max_width=max_width)
    except Exception:
        return None                        # 单张裁切失败不该拖垮整页
    name = f"{stem}_p{page_no:03d}_fig{region.index}.png"
    target = Path(asset_root) / name
    try:
        target.write_bytes(data)
    except OSError as exc:
        raise SourceError(f"无法保存插图 {name}：{exc}") from exc
    return ImageRef(name=name, path=target,
                    alt=region.caption or f"第 {page_no} 页插图",
                    width=w, height=h)


def _blocks_with_figures(lines: list[dict], regions: list, image,
                         asset_root: Path | None, stem: str,
                         page_no: int) -> list[Block]:
    """按纵坐标把「正文段」与「插图」交错排好。

    不能简单地「文字全部在前、图全部在后」：一本书的版面是「一段话、一张图、
    一段话」，顺序错了整章读起来就是脱节的。
    """
    out: list[Block] = []
    idx = 0
    for r in regions:
        seg: list[dict] = []
        while idx < len(lines) and float(lines[idx].get("top", 0)) < r.bbox[1] - 2:
            seg.append(lines[idx])
            idx += 1
        if seg:
            out.extend(_blocks_from_lines(seg, [], ocr=True))
        ref = _save_figure(image, r, asset_root, stem, page_no)
        if ref is not None:
            out.append(Block.figure(ref))
        # 图区范围内的行已在 split_lines 里剔除，这里只推进指针
        while idx < len(lines) and float(lines[idx].get("top", 0)) < r.bbox[3]:
            idx += 1
    tail = lines[idx:]
    if tail:
        out.extend(_blocks_from_lines(tail, [], ocr=True))
    return out


def read_pdf_file(path: Path, *, max_pages: int = 0, ocr: bool = False,
                  images: bool = True, assets_dir: str | Path | None = None,
                  layout: bool = True) -> Document:
    try:
        import pdfplumber
    except ImportError as exc:
        raise DependencyMissing.for_import(exc, "读取 PDF", "pdfplumber") from exc

    # pdfminer 遇到字体信息不全的 PDF 会往 stderr 刷 FontBBox 警告，内容其实读得出，
    # 但对用户来说像是出错了。压到 ERROR，真正的失败仍会抛异常。
    import logging
    logging.getLogger("pdfminer").setLevel(logging.ERROR)

    # 连续这么多页都没有文字层，就认定整份是扫描件，立刻失败。
    # 扫描书常见 200+ 页，全跑一遍要几分钟才说「没有文本」，用户会以为程序卡死。
    NO_TEXT_STREAK = 3

    doc = Document(kind="text", name=path.stem, source_format="pdf")
    doc.text = TextDoc()
    total_chars = 0
    ocr_pages = 0
    blank_pages: list[int] = []
    streak = 0
    figure_count = 0
    dropped_total = 0
    # 只有真要 OCR 才准备资产目录：纯文字 PDF 不该凭空生成一个空文件夹
    asset_root = _asset_dir(assets_dir) if (ocr and images) else None

    def emit(page_obj, text: str) -> None:
        nonlocal total_chars
        total_chars += len(text.strip())
        for b in _pdf_page_blocks(page_obj):
            doc.text.add(b)

    def emit_ocr(page_obj, idx: int) -> None:
        """页面没有文字层时的兜底：渲染成图后 OCR，再走同一套块重建。

        扫描书的图（K 线、示意图）里带坐标轴刻度和气泡标注，整页 OCR 会把
        它们识别成正文行——第 41 页 27 行里有 8 行是「3.60 / 2.80」这类刻度。
        所以先做版面切分：图内行剔掉，图本身裁出来落成图片块。
        """
        nonlocal total_chars, ocr_pages, figure_count, dropped_total
        from .ocr import ocr_page_lines
        try:
            image = page_obj.to_image(resolution=200).original
        except Exception as exc:
            raise SourceError(f"第 {idx} 页无法渲染成图像：{exc}") from exc
        infos = ocr_page_lines(image)
        total_chars += sum(len(it["text"]) for it in infos)
        ocr_pages += 1

        if not layout or not infos:
            for b in _blocks_from_lines(infos, [], ocr=True):
                doc.text.add(b)
            return

        from . import layout as layout_mod
        page = layout_mod.analyze_page(image, infos, page_no=idx)
        dropped_total += len(page.dropped)
        if page.regions:
            for b in _blocks_with_figures(page.kept, page.regions, image,
                                          asset_root, path.stem, idx):
                if b.kind == "image":
                    figure_count += 1
                    if b.image is not None:
                        doc.assets.append(b.image)
                doc.text.add(b)
        else:
            for b in _blocks_from_lines(infos, [], ocr=True):
                doc.text.add(b)

    try:
        with pdfplumber.open(str(path)) as pdf:
            pages = pdf.pages[:max_pages] if max_pages else pdf.pages
            if not pages:
                raise SourceError(f"{path.name} 没有页面")
            for idx, page in enumerate(pages, 1):
                text = page.extract_text() or ""
                tables = _pdf_real_tables(page)
                if not text.strip() and not tables:
                    if ocr:
                        streak = 0
                        emit_ocr(page, idx)
                        continue
                    streak += 1
                    blank_pages.append(idx)
                    if streak >= NO_TEXT_STREAK and total_chars == 0:
                        raise SourceError(
                            f"{path.name} 是扫描版 PDF：前 {idx} 页都没有文字层",
                            hint="扫描件的内容是图片。命令行加 --ocr、界面勾选"
                                 "「OCR 识别扫描件」即可直接识别；OCR 较慢，每页约几秒")
                    continue
                streak = 0

                if not tables:
                    emit(page, text)
                    continue

                # 按表格把页面切成若干水平带：带内取正文，表格位置放表格。
                # 这样输出顺序与版面一致，且同一段文字不会被提取两次。
                cursor = 0.0
                for (x0, top, x1, bottom), rows in tables:
                    if top - cursor > 2:
                        try:
                            band = page.crop((0, cursor, page.width, top))
                            emit(band, band.extract_text() or "")
                        except Exception:
                            pass
                    doc.text.add(Block(kind="table", rows=rows, header=True))
                    total_chars += sum(len(c) for r in rows for c in r)
                    cursor = max(cursor, bottom)
                if page.height - cursor > 2:
                    try:
                        band = page.crop((0, cursor, page.width, page.height))
                        emit(band, band.extract_text() or "")
                    except Exception:
                        pass
    except SourceError:
        raise
    except Exception as exc:
        raise SourceError(f"无法读取 {path.name}：{exc}") from exc

    # 少量无文字页（插图、扉页）不算失败，合并成一条提示，不逐页刷屏
    if figure_count:
        doc.warnings.append(
            f"识别到 {figure_count} 张插图，已裁出；图内的 {dropped_total} 行刻度与"
            f"标注未计入正文（导出 md/docx 可看到图片）")
    if ocr_pages:
        doc.warnings.append(f"{ocr_pages} 页没有文字层，已用 OCR 识别（文字可能有误，"
                            "请抽查关键字段）")
    elif blank_pages and total_chars:
        doc.warnings.append(f"{len(blank_pages)} 页没有文字层（多为插图页），已跳过")

    if total_chars == 0 and not any(b.kind == "table" for b in doc.text.blocks):
        raise SourceError(f"{path.name} 没有可提取的文本",
                          hint="命令行加 --ocr 可识别扫描件（较慢）")
    return doc


# ---------------------------------------------------------------- 图片
# 尺寸归一区间。低于 1500px 宽时 OCR 对小字几乎无能为力（截图常见 800px）；
# 高于 2400px 只会拖慢识别，精度不再提升。
IMAGE_MIN_WIDTH = 1500
IMAGE_MAX_WIDTH = 2400


def _fit_image_size(img):
    from PIL import Image

    w, h = img.size
    if not w or not h:
        return img
    if w < IMAGE_MIN_WIDTH:
        ratio = IMAGE_MIN_WIDTH / float(w)
        return img.resize((IMAGE_MIN_WIDTH, max(1, int(h * ratio))), Image.LANCZOS)
    if w > IMAGE_MAX_WIDTH:
        ratio = IMAGE_MAX_WIDTH / float(w)
        return img.resize((IMAGE_MAX_WIDTH, max(1, int(h * ratio))), Image.LANCZOS)
    return img


def _save_original(path: Path, img, asset_root: Path) -> ImageRef | None:
    """把原图（必要时缩到 1600px）存进资产目录。

    照片用 JPEG（PNG 存照片能大十倍），线稿和截图用 PNG。判据是扩展名——
    用户从手机相册拿的都是 jpg，从截图工具拿的都是 png，这个假设足够稳。
    """
    from PIL import Image

    if img.width > 1600:
        ratio = 1600 / float(img.width)
        img = img.resize((1600, max(1, int(img.height * ratio))), Image.LANCZOS)

    # 按内容而不是扩展名决定编码：颜色数少的是扫描页/截图/线稿，量化成
    # 256 色 PNG 肉眼无差；颜色多的是照片，PNG 会大到十倍，改用 JPEG。
    try:
        if img.getcolors(maxcolors=4096) is not None:
            name = f"{path.stem}_orig.png"
            img.quantize(colors=256).save(asset_root / name, "PNG", optimize=True)
        else:
            name = f"{path.stem}_orig.jpg"
            img.convert("RGB").save(asset_root / name, "JPEG",
                                    quality=85, optimize=True)
    except Exception:
        return None
    return ImageRef(name=name, path=asset_root / name, alt=f"原图 {path.name}",
                    width=img.width, height=img.height)


def read_image_file(path: Path, *, ocr: bool = True, images: bool = True,
                    assets_dir: str | Path | None = None,
                    layout: bool = True) -> Document:
    """图片 → 文本：OCR 识别，并把原图作为插图保留。

    图片没有文字层，OCR 不是「可选增强」而是唯一途径，因此这里不接受
    ocr=False——与其返回一份空文档让用户以为转换成功，不如明确报错。
    """
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise DependencyMissing.for_import(exc, "读取图片", "pillow") from exc

    from .ocr import ocr_page_lines

    if not ocr:
        raise SourceError(f"{path.name} 是图片，必须经过 OCR 才能转成文本",
                          hint="命令行加 --ocr，界面里 OCR 开关会自动开启")

    doc = Document(kind="text", name=path.stem, source_format="image")
    doc.text = TextDoc()
    doc.source_title = path.stem

    try:
        with Image.open(path) as raw:
            fixed = ImageOps.exif_transpose(raw)          # 手机照片带旋转标记
            img = fixed.convert("RGB") if fixed.mode not in ("L", "RGB") else fixed.copy()
    except Exception as exc:
        raise SourceError(f"无法打开图片 {path.name}：{exc}") from exc

    img = _fit_image_size(img)
    infos = ocr_page_lines(img)

    # 原图放最前：用户要的是「图 + 识别文字」一起导出，图是参照物
    asset_root = _asset_dir(assets_dir) if images else None
    if asset_root is not None:
        ref = _save_original(path, img, asset_root)
        if ref is not None:
            doc.assets.append(ref)
            doc.text.add(Block.figure(ref))

    page = None
    if layout and infos:
        from . import layout as layout_mod
        page = layout_mod.analyze_page(img, infos, page_no=1)

    # 图片里如果有图表，图内文字同样要剔除——否则截图里的刻度会混进正文。
    # 但不额外裁子图：原图已经在上面了，再裁一张只是重复。
    kept = page.kept if (page is not None and page.regions) else infos
    for b in _blocks_from_lines(kept, [], ocr=True):
        doc.text.add(b)

    if page is not None and page.dropped:
        doc.warnings.append(
            f"图内有 {len(page.dropped)} 行文字（刻度、标注）已跳过，"
            f"另存为文档可看到原图")

    if not infos:
        raise SourceError(f"{path.name} 没有识别到文字",
                          hint="换一张更清晰的图；截图建议放大到 1500px 宽以上")
    return doc


# ---------------------------------------------------------------- 分派
READERS = {
    "txt": read_text_file,
    "md": read_markdown_file,
    "html": read_html_file,
    "csv": read_csv_file,
    "xlsx": read_xlsx_file,
    "docx": read_docx_file,
    "pdf": read_pdf_file,
    "image": read_image_file,
}


def read_any(path: Path, src_format: str = "", **opts) -> Document:
    """按扩展名（或指定格式）读取文件。

    选项按读取器签名过滤：管线会把统一选项集传给每个读取器，而各读取器只关心
    其中几个。这里做一次签名适配，避免「多传一个无害选项就整体失败」。
    """
    fmt = resolve_format(src_format) if src_format else format_of_path(path)
    if fmt is None:
        raise SourceError(f"无法从扩展名判断 {path.name} 的格式",
                          hint="请用 --from 显式指定源格式")
    if not path.exists():
        raise SourceError(f"文件不存在：{path}")
    if path.is_dir():
        raise SourceError(f"{path} 是目录，不是文件")
    if path.stat().st_size == 0:
        raise SourceError(f"{path.name} 是空文件（0 字节）")

    reader = READERS.get(fmt.code)
    if reader is None:
        raise SourceError(f"暂不支持读取 {fmt.label}")

    import inspect
    params = inspect.signature(reader).parameters
    if not any(p.kind == p.VAR_KEYWORD for p in params.values()):
        opts = {k: v for k, v in opts.items() if k in params}
    return reader(path, **opts)
