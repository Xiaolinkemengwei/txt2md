"""各格式写出器：Document 模型 → 文件。

设计要点
--------
1. **写 Markdown 必须能被自己的解析器读回等值结构** —— 所以围栏、缩进、转义
   都按 ``mdparse`` 的识别规则反向生成，而不是按「看起来像就行」。
2. **写 docx 用真样式**（Heading 1-6 / List Bullet / Table Grid），
   这样 Word 里大纲、导航、自动编号都正常，也保证 docx→md→docx 往返可控。
3. **中文必须显式设置东亚字体**。python-docx 只设 ``font.name`` 时，
   中文仍会回落到默认宋体——这是最常见的「导出 Word 字体不对」原因。
4. **PDF 走浏览器打印**：先渲染打印版 HTML，再调 headless 打印。
   输出后校验 PDF 魔数，不信任进程退出码（浏览器常返回 0 却没写文件）。
5. **原子写入**：先写临时文件再改名，转换失败不留下半个文件。
"""

from __future__ import annotations

import csv
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .errors import DependencyMissing, EngineMissing, SourceError
from .htmlio import render_html, render_plain
from .model import Block, Document, TextDoc, ensure_single_trailing_newline
from .registry import detect_engines

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


# ---------------------------------------------------------------- 原子写入
def atomic_write(path: Path, data: bytes) -> None:
    """写临时文件再改名，避免中断留下损坏的半成品。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".aieye-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    atomic_write(path, text.encode(encoding))


# ---------------------------------------------------------------- Markdown
def _md_escape_cell(text: str) -> str:
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


def render_markdown(doc: TextDoc, *, atx: bool = True,
                    assets_prefix: str = "", with_images: bool = True) -> str:
    """块模型 → GFM 文本。

    块之间用空行分隔，但**嵌套列表除外**：模型把嵌套层拆成了独立块，
    若在中间补空行，GFM 会把它判为 loose list，改变列表语义。

    ``assets_prefix`` 是图片目录相对目标文件的路径（如 ``报告_assets``），
    由 write_any 在落盘素材后传入。
    """
    out: list[str] = []
    prev: Block | None = None

    def blank():
        if out and out[-1] != "":
            out.append("")

    for b in doc.blocks:
        # 空行补在块「之前」而不是之后：块之后的写法在「嵌套层回到外层」时
        # 会漏掉分隔（前一层的块尾没加，本层又不属于续接）。
        #
        # 续接判据是「层级发生了变化」而不是「比上一层更深」：从
        # `  - 嵌套项` 回到 `- 项目二` 时 level 是变小的，按「更深」判断会
        # 在这里插入空行，把一个列表劈成两段（GFM 会视为两个 loose list）。
        continuing = (b.kind == "list" and prev is not None
                      and prev.kind == "list" and b.level != prev.level)
        if not continuing:
            blank()

        if b.kind == "heading":
            lvl = max(1, min(6, b.level))
            out.append(f"{'#' * lvl} {b.text.strip()}" if atx else b.text.strip())
        elif b.kind == "paragraph":
            out.append(b.text.strip("\n"))
        elif b.kind == "raw":
            out.append(b.text)
        elif b.kind == "divider":
            out.append("---")
        elif b.kind == "quote":
            for line in b.text.split("\n"):
                out.append(f"> {line}" if line.strip() else ">")
        elif b.kind == "code":
            fence = "```"
            # 内容里已有 ``` 时升到 4 个反引号，保证能正确闭合
            while fence in b.text:
                fence += "`"
            out.append(f"{fence}{b.lang}")
            out.append(b.text)
            out.append(fence)
        elif b.kind == "list":
            pad = "  " * max(0, b.level)
            for i, item in enumerate(b.items, start=b.start if b.ordered else 0):
                marker = f"{i}. " if b.ordered else "- "
                lines = str(item).split("\n")
                out.append(f"{pad}{marker}{lines[0]}")
                # 续行按 markdown 规则缩进到标记之后
                hang = " " * (len(pad) + len(marker))
                for extra in lines[1:]:
                    out.append(f"{hang}{extra}" if extra.strip() else "")
        elif b.kind == "table":
            if not b.rows:
                continue
            width = max(len(r) for r in b.rows)
            rows = [list(r) + [""] * (width - len(r)) for r in b.rows]
            head, body = (rows[0], rows[1:]) if b.header else (None, rows)
            aligns = b.align or []
            def cell_line(cells):
                return "| " + " | ".join(_md_escape_cell(c) for c in cells) + " |"
            if head is not None:
                out.append(cell_line(head))
            else:
                out.append(cell_line([""] * width))
            sep = []
            for i in range(width):
                a = aligns[i] if i < len(aligns) else ""
                sep.append({"left": ":---", "center": ":---:", "right": "---:"}.get(a, "---"))
            out.append("| " + " | ".join(sep) + " |")
            for r in body:
                out.append(cell_line(r))
        elif b.kind == "image":
            ref = b.image
            if ref is None or not with_images:
                continue
            src = f"{assets_prefix}/{ref.name}" if assets_prefix else ref.name
            # alt 承载题注：Markdown 是源格式，图本身可见，再补一行斜体题注
            # 会让一本书几百张图的地方重复一遍同样的文字。题注在 HTML/docx
            # 输出里会显式渲染成题注。
            alt = (ref.caption() or "插图").replace("]", "）").replace("[", "（")
            out.append(f"![{alt}]({src})")
        prev = b
    text = "\n".join(out)
    return ensure_single_trailing_newline(text)


# ---------------------------------------------------------------- 纯文本
def render_text(doc: Document) -> str:
    """块模型 → 纯文本。verbatim 模式下逐字还原。"""
    if doc.verbatim and doc.text is not None:
        raw = "\n".join(b.text for b in doc.text.blocks if b.kind == "raw")
        return ensure_single_trailing_newline(raw)
    return ensure_single_trailing_newline(render_plain(doc.as_text()))


# ---------------------------------------------------------------- CSV
def render_csv(book, *, delimiter: str = ",", one_sheet: bool = True) -> str:
    """工作表 → CSV 文本。多表时以空行与表名注释分隔（保持单文件）。"""
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=delimiter, lineterminator="\n")
    multi = len(book.sheets) > 1 and not one_sheet
    for idx, sheet in enumerate(book.sheets):
        if idx:
            writer.writerow([])
            if multi:
                writer.writerow([f"# {sheet.name}"])
        for row in sheet.rows:
            writer.writerow([_csv_cell(c) for c in row])
    return buf.getvalue()


def _csv_cell(value: str) -> str:
    """单元格值原样输出；不做类型改写，CSV 里全是文本。"""
    return "" if value is None else str(value)


# ---------------------------------------------------------------- xlsx
def _maybe_number(value: str):
    """把看起来是数字的字符串还原为数值，返回 (值, number_format)。

    转成数值而不是留作文本，是为了避免 Excel 显示「以文本形式存储的数字」。
    同时必须带上 number_format，否则 12.50 会变成 12.5 —— 数值相等但显示
    精度丢失，在金额类数据里是实打实的失真。

    前导零（001、身份证号）保持文本，这是最常见的静默破坏点。
    """
    s = value.strip()
    if not s or len(s) > 15:
        return value, None
    if s.startswith("0") and len(s) > 1 and s[1] not in ".eE":
        return value, None  # 保留前导零
    if s.endswith("%"):
        try:
            num = float(s[:-1])
            decimals = len(s[:-1].split(".")[1]) if "." in s[:-1] else 0
            return num / 100, "0" + ("." + "0" * max(decimals + 2, 2) if decimals else ".00%")
        except ValueError:
            return value, None
    try:
        body = s.lstrip("+-")
        if body.isdigit():
            return int(s), None
        if "." in body and body.replace(".", "").isdigit():
            decimals = len(s.split(".")[1])
            return float(s), "0." + "0" * decimals
        return float(s), None
    except ValueError:
        return value, None


def write_xlsx(doc: Document, path: Path, *, numeric: bool = True) -> None:
    try:
        import openpyxl
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError as exc:
        raise DependencyMissing.for_import(exc, "写出 xlsx", "openpyxl") from exc

    book = doc.as_book()
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    head_font = Font(bold=True)
    head_fill = PatternFill("solid", fgColor="F2F2F2")

    for sheet in book.sheets:
        title = (sheet.name or "Sheet1")[:31]
        # 工作表名不允许 : \ / ? * [ ]
        for ch in ':\\/?*[]':
            title = title.replace(ch, "_")
        ws = wb.create_sheet(title=title or "Sheet1")

        for r_idx, row in enumerate(sheet.rows, start=1):
            for c_idx, value in enumerate(row, start=1):
                cell = ws.cell(row=r_idx, column=c_idx)
                if numeric and not (sheet.header and r_idx == 1):
                    num, fmt = _maybe_number(value)
                    cell.value = num
                    if fmt:
                        cell.number_format = fmt
                else:
                    cell.value = value
                cell.alignment = Alignment(vertical="top", wrap_text=False)
            if sheet.header and r_idx == 1:
                for c_idx in range(1, len(row) + 1):
                    c = ws.cell(row=r_idx, column=c_idx)
                    c.font = head_font
                    c.fill = head_fill

        # 列宽按内容估算（中文按 2 个字符宽）
        if sheet.rows:
            width = max(len(r) for r in sheet.rows)
            for c_idx in range(1, width + 1):
                longest = 0
                for row in sheet.rows[:200]:
                    if c_idx <= len(row):
                        val = str(row[c_idx - 1])
                        longest = max(longest, sum(2 if ord(ch) > 127 else 1 for ch in val))
                ws.column_dimensions[get_column_letter(c_idx)].width = min(60, max(8, longest + 2))

        if sheet.header and len(sheet.rows) > 1:
            ws.freeze_panes = "A2"

    buf = io.BytesIO()
    wb.save(buf)
    atomic_write(path, buf.getvalue())


# ---------------------------------------------------------------- docx
_FALLBACK_TABLE_STYLE = "Table Grid"


def write_docx(doc: Document, path: Path, *, latin_font: str = "Segoe UI",
               east_asia: str = "Microsoft YaHei", images: bool = True) -> None:
    try:
        import docx
        from docx.enum.table import WD_TABLE_ALIGNMENT
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
        from docx.shared import Pt, RGBColor
    except ImportError as exc:
        raise DependencyMissing.for_import(exc, "写出 docx", "python-docx") from exc

    d = docx.Document()

    # 全局字体：西文 + 东亚必须分别设置
    style = d.styles["Normal"]
    style.font.name = latin_font
    style.font.size = Pt(11)
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), east_asia)
    rfonts.set(qn("w:ascii"), latin_font)
    rfonts.set(qn("w:hAnsi"), latin_font)
    style.paragraph_format.space_after = Pt(6)
    style.paragraph_format.line_spacing = 1.5

    # 标题样式同步东亚字体，否则 Word 里标题中文仍是宋体
    for lvl in range(1, 7):
        try:
            hs = d.styles[f"Heading {lvl}"]
        except KeyError:
            continue
        hs.font.name = latin_font
        hr = hs.element.get_or_add_rPr()
        hf = hr.find(qn("w:rFonts"))
        if hf is None:
            hf = OxmlElement("w:rFonts")
            hr.append(hf)
        hf.set(qn("w:eastAsia"), east_asia)
        hf.set(qn("w:ascii"), latin_font)
        hf.set(qn("w:hAnsi"), latin_font)

    def add_runs(par, text: str, *, bold: bool = False, mono: bool = False):
        """按行内标记拆 run，保留粗体/斜体/代码。"""
        import re as _re
        tokens = _re.split(r"(\*\*.+?\*\*|~~.+?~~|`.+?`|\*.+?\*)", text)
        for tok in tokens:
            if not tok:
                continue
            b, i, code = bold, False, mono
            body = tok
            if tok.startswith("**") and tok.endswith("**") and len(tok) > 4:
                b, body = True, tok[2:-2]
            elif tok.startswith("~~") and tok.endswith("~~") and len(tok) > 4:
                body = tok[2:-2]
            elif tok.startswith("`") and tok.endswith("`") and len(tok) > 2:
                code, body = True, tok[1:-1]
            elif tok.startswith("*") and tok.endswith("*") and len(tok) > 2:
                i, body = True, tok[1:-1]
            run = par.add_run(body)
            run.bold, run.italic = b, i
            if code:
                run.font.name = "Consolas"
                run.font.size = Pt(10)

    def shade(par, color: str = "F6F6F6"):
        pr = par._p.get_or_add_pPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), color)
        pr.append(shd)

    text_doc = doc.as_text()
    for b in text_doc.blocks:
        if b.kind == "heading":
            lvl = max(1, min(6, b.level))
            p = d.add_paragraph(style=f"Heading {lvl}")
            add_runs(p, b.text.strip())
        elif b.kind == "paragraph":
            p = d.add_paragraph()
            add_runs(p, b.text.strip())
        elif b.kind == "raw":
            for line in b.text.split("\n"):
                d.add_paragraph(line)
        elif b.kind == "quote":
            # 用内置 Quote 样式而非手写缩进：读回时能可靠识别为引用块
            try:
                d.styles["Quote"]
                quote_style = "Quote"
            except KeyError:
                quote_style = None
            for line in b.text.split("\n"):
                p = d.add_paragraph(style=quote_style) if quote_style else d.add_paragraph()
                if not quote_style:
                    p.paragraph_format.left_indent = Pt(24)
                add_runs(p, line)
        elif b.kind == "code":
            for line in b.text.split("\n"):
                p = d.add_paragraph()
                p.paragraph_format.left_indent = Pt(12)
                p.paragraph_format.space_after = Pt(0)
                p.paragraph_format.line_spacing = 1.15
                shade(p)
                run = p.add_run(line if line else " ")
                run.font.name = "Consolas"
                run.font.size = Pt(9.5)
        elif b.kind == "divider":
            p = d.add_paragraph()
            pPr = p._p.get_or_add_pPr()
            bdr = OxmlElement("w:pBdr")
            bottom = OxmlElement("w:bottom")
            bottom.set(qn("w:val"), "single")
            bottom.set(qn("w:sz"), "6")
            bottom.set(qn("w:color"), "E5E5E5")
            bdr.append(bottom)
            pPr.append(bdr)
        elif b.kind == "list":
            style_name = "List Number" if b.ordered else "List Bullet"
            try:
                d.styles[style_name]
            except KeyError:
                style_name = None
            for item in b.items:
                p = d.add_paragraph(style=style_name) if style_name else d.add_paragraph()
                if b.level:
                    p.paragraph_format.left_indent = Pt(18 * (b.level + 1))
                add_runs(p, str(item))
        elif b.kind == "table":
            if not b.rows:
                continue
            width = max(len(r) for r in b.rows)
            rows = [list(r) + [""] * (width - len(r)) for r in b.rows]
            table = d.add_table(rows=len(rows), cols=width)
            try:
                table.style = _FALLBACK_TABLE_STYLE
            except KeyError:
                pass
            table.alignment = WD_TABLE_ALIGNMENT.CENTER
            for r_i, row in enumerate(rows):
                for c_i, val in enumerate(row):
                    cell = table.cell(r_i, c_i)
                    cell.text = ""
                    par = cell.paragraphs[0]
                    par.paragraph_format.space_after = Pt(0)
                    par.paragraph_format.line_spacing = 1.15
                    add_runs(par, str(val), bold=(b.header and r_i == 0))
            d.add_paragraph()
        elif b.kind == "image":
            ref = b.image
            if ref is None:
                continue
            if not images or not Path(ref.path).exists():
                # 不能因为一张图读不到就让整篇 docx 失败——版式文档里保住
                # 文字和其余图片，比整体报错有用得多。
                d.add_paragraph(f"【图】{ref.caption() or ref.name}")
                continue
            try:
                from docx.shared import Cm
                p = d.add_paragraph()
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                p.add_run().add_picture(str(ref.path), width=Cm(15))
                if ref.caption():
                    cap = d.add_paragraph()
                    cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    run = cap.add_run(ref.caption())
                    run.italic = True
                    run.font.size = Pt(9)
                    run.font.color.rgb = RGBColor(0x59, 0x59, 0x59)
            except Exception:
                d.add_paragraph(f"【图】{ref.caption() or ref.name}")

    buf = io.BytesIO()
    d.save(buf)
    atomic_write(path, buf.getvalue())


# ---------------------------------------------------------------- PDF
def _pdf_looks_valid(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            head = fh.read(5)
        return head.startswith(b"%PDF") and path.stat().st_size > 600
    except OSError:
        return False


def html_to_pdf(html_text: str, out_path: Path, *, title: str = "",
                timeout: int = 90, extra_files: dict[str, Path] | None = None) -> str:
    """用无头浏览器把 HTML 打印成 PDF，返回所用引擎名。

    为什么不用 reportlab/fpdf2：它们需要手工嵌入中文字体且不处理分页断行，
    版式质量与浏览器差一个量级。浏览器方案在 Windows 上零安装可用（Edge 是系统组件）。
    """
    engines = detect_engines()
    if not engines.browser:
        raise EngineMissing(
            "没有可用于生成 PDF 的浏览器",
            hint="安装 Microsoft Edge 或 Google Chrome 后重试")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="aieye-pdf-") as tmp:
        tmpdir = Path(tmp)
        html_file = tmpdir / "print.html"
        html_file.write_text(html_text, encoding="utf-8")

        # 插图随 HTML 一起放到临时目录：打印时浏览器只需读相对路径，
        # 不必依赖目标目录里是否已经落盘了图片。
        for rel, src in (extra_files or {}).items():
            dst = tmpdir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copyfile(src, dst)
            except OSError:
                continue

        # 用临时输出名，成功后再原子替换，避免失败留下 0 字节 PDF
        staged = tmpdir / "out.pdf"
        cmd = [
            engines.browser,
            "--headless",
            "--disable-gpu",
            "--no-sandbox",
            "--no-first-run",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-sync",
            "--virtual-time-budget=8000",
            "--run-all-compositor-stages-before-draw",
            "--no-pdf-header-footer",
            f"--print-to-pdf={staged}",
            html_file.resolve().as_uri(),
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=timeout,
                           creationflags=_NO_WINDOW)
        except subprocess.TimeoutExpired as exc:
            raise EngineMissing(f"{engines.browser_name} 打印超时（{timeout}s）",
                                hint="文档过大或浏览器被占用，可改用 LibreOffice 转换") from exc

        # 浏览器可能异步写盘，短暂轮询
        for _ in range(40):
            if staged.exists() and staged.stat().st_size > 600:
                break
            time.sleep(0.15)

        if not _pdf_looks_valid(staged):
            raise EngineMissing(
                f"{engines.browser_name} 未能生成有效 PDF",
                hint="请确认浏览器可正常启动；或改用 docx + LibreOffice 路线")
        atomic_write(out_path, staged.read_bytes())
        return f"{engines.browser_name} 打印"


def write_pdf(doc: Document, path: Path, *, title: str = "",
              images: bool = True) -> str:
    """Document → PDF。走 HTML 中转 + 浏览器打印。"""
    text_doc = doc.as_text()
    extra: dict[str, Path] = {}
    if images:
        for ref in doc.assets:
            if Path(ref.path).exists():
                extra[f"__assets__/{ref.name}"] = Path(ref.path)
    prefix = "__assets__" if extra else ""
    html_text = render_html(text_doc, title=title or doc.name, assets_prefix=prefix)
    return html_to_pdf(html_text, path, title=title or doc.name, extra_files=extra)


def _materialize_assets(doc: Document, path: Path,
                        assets_dir: str | Path | None) -> str:
    """把图片复制到目标文件旁的素材目录，返回 md/html 里使用的相对前缀。

    默认目录名是 ``<目标名>_assets``：和 md 放同一层，用户压缩、拷贝、上传
    时不会漏掉图；外部编辑器（Typora、VS Code）也能直接显示。
    """
    if not doc.assets:
        return ""

    target = Path(assets_dir) if assets_dir else path.parent / f"{path.stem}_assets"
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SourceError(f"无法创建图片目录 {target}：{exc}") from exc

    for ref in doc.assets:
        src = Path(ref.path)
        if not src.exists():
            continue
        try:
            shutil.copyfile(src, target / ref.name)
        except OSError as exc:
            raise SourceError(f"无法保存图片 {ref.name}：{exc}") from exc

    try:
        prefix = os.path.relpath(target, path.parent)
    except ValueError:                       # 跨盘符时 relpath 会失败
        return target.resolve().as_uri()
    return prefix.replace("\\", "/")


# ---------------------------------------------------------------- 分派
def write_any(doc: Document, path: Path, dst_format: str, **opts) -> str:
    """把 Document 写成指定格式，返回所用引擎说明。"""
    fmt = dst_format
    images = opts.pop("images", True)
    assets_dir = opts.pop("assets_dir", None)

    if fmt == "txt":
        atomic_write_text(path, render_text(doc))
        return "内置"
    if fmt in ("md", "html"):
        # md / html 的图必须落盘为独立文件：Markdown 没有「内嵌图片」这回事，
        # HTML 内联成 data URI 会让几百张图的文件大到编辑器打不开。
        prefix = _materialize_assets(doc, path, assets_dir) if images else ""
        if fmt == "md":
            atomic_write_text(path, render_markdown(doc.as_text(),
                                                    assets_prefix=prefix,
                                                    with_images=images))
        else:
            atomic_write_text(path, render_html(doc.as_text(), title=doc.name,
                                                assets_prefix=prefix,
                                                with_images=images))
        return "内置"
    if fmt == "csv":
        delimiter = opts.pop("delimiter", ",")
        atomic_write_text(path, render_csv(doc.as_book(), delimiter=delimiter))
        return "内置"
    if fmt == "xlsx":
        write_xlsx(doc, path, numeric=opts.pop("numeric", True))
        return "openpyxl"
    if fmt == "docx":
        write_docx(doc, path, images=images, **opts)
        return "python-docx"
    if fmt == "pdf":
        return write_pdf(doc, path, title=doc.name, images=images)

    raise SourceError(f"暂不支持写出 {fmt}")
