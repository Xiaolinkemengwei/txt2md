"""EPUB 读写。

为什么单独一个模块而不塞进 readers/writers
-------------------------------------------
EPUB 不是一个「文件格式」而是一个**约定好的 zip 包**：里面装着 OPF 清单、
XHTML 正文、图片和目录。读和写都要先解开/组装这层容器，逻辑量比其它格式的
读写器大一个量级；放在 readers.py 里会把那个文件的主线（文本解码 → 块模型）
淹掉。

读取策略
--------
**按 spine 顺序读**，不按文件名排序。spine 是出版方定义的阅读顺序，文件名
（`ch01.xhtml` / `part-a.xhtml`）不保证顺序，按文件名排会把书读乱。

**宁可多留不可少留**：OPF 缺失、目录损坏、某章解码失败，都只降级不整体失败。
一本有 30 章的书，第 17 章坏了，应该输出其余 29 章并给一条警告，而不是让
用户看到「转换失败」。

写出策略
--------
按**一级标题分章**。EPUB 阅读器的目录来自章文件，整本书堆进一个 xhtml 会
让目录只剩一条、翻页卡顿。没有标题的文档退化为单章，不影响可用性。

只依赖标准库（zipfile / xml.etree）——EPUB 的容器与元数据都是 XML，
没有引入第三方 ebook 库的必要，也避免给单文件 exe 增重。
"""

from __future__ import annotations

import html as html_mod
import io
import posixpath
import re
import uuid
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from .errors import SourceError
from .model import Block, Document, ImageRef, TextDoc

# ---------------------------------------------------------------- 命名空间
NS_CONTAINER = "{urn:oasis:names:tc:opendocument:xmlns:container}"
NS_OPF = "{http://www.idpf.org/2007/opf}"
NS_DC = "{http://purl.org/dc/elements/1.1/}"
NS_NCX = "{http://www.daisy.org/z3986/2005/ncx/}"

# EPUB 里正文的媒体类型（读的时候只认这几种）
_TEXT_TYPES = {
    "application/xhtml+xml", "text/html", "application/x-dtbook+xml",
}
_IMAGE_TYPES = {
    "image/jpeg", "image/png", "image/gif", "image/svg+xml",
    "image/webp", "image/bmp", "image/tiff",
}
_MAX_CHAPTERS = 2000      # 防呆：异常 EPUB 里可能塞进上万个 spine 项


# ---------------------------------------------------------------- 容器解析
def _safe_name(name: str) -> str:
    """把 zip 内路径规范化成 posix 形式，供 zip.read 使用。

    EPUB 里出现过反斜杠路径（Windows 工具打包的），也有 ``./OEBPS/`` 这种
    前缀，统一抹平后再查，否则明明存在却报「找不到」。
    """
    n = name.replace("\\", "/").lstrip("/")
    while n.startswith("./"):
        n = n[2:]
    return posixpath.normpath(n) if n else n


def _resolve(base_file: str, href: str) -> str:
    """把相对 href 解析成 zip 内绝对路径。``base_file`` 是引用方自己的路径。"""
    href = href.split("#", 1)[0].strip()
    if not href:
        return ""
    if href.startswith("/"):
        return _safe_name(href)
    base_dir = posixpath.dirname(_safe_name(base_file))
    return _safe_name(posixpath.join(base_dir, href))


def _read_bytes(z: zipfile.ZipFile, name: str) -> bytes | None:
    try:
        return z.read(name)
    except KeyError:
        return None
    except Exception:
        return None


def _decode(raw: bytes) -> str:
    """EPUB 规范要求 UTF-8 / UTF-16，但现实里 GBK 的盗版书不少。"""
    for enc in ("utf-8-sig", "utf-8", "utf-16"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    from .readers import decode_bytes       # 延迟导入，避开循环依赖
    return decode_bytes(raw)[0]


def _find_opf(z: zipfile.ZipFile) -> str:
    """从 META-INF/container.xml 找 OPF；找不到就按常见位置兜底。"""
    raw = _read_bytes(z, "META-INF/container.xml")
    if raw:
        try:
            root = ET.fromstring(raw)
            for rf in root.iter(f"{NS_CONTAINER}rootfile"):
                path = rf.get("full-path") or ""
                if path:
                    return _safe_name(path)
        except ET.ParseError:
            pass
    # 兜底：扫全表找 .opf。有些「自制 EPUB」根本没有 container.xml。
    for name in z.namelist():
        if name.lower().endswith(".opf"):
            return _safe_name(name)
    return ""


# ---------------------------------------------------------------- OPF 解析
class _Manifest:
    """OPF 清单 + spine + 元数据。"""

    def __init__(self) -> None:
        self.items: dict[str, dict] = {}     # id -> {href, type, props}
        self.spine: list[str] = []           # href 列表（已按阅读顺序解析）
        self.title = ""
        self.creator = ""
        self.language = ""
        self.identifier = ""
        self.ncx_href = ""                   # EPUB2 目录
        self.nav_href = ""                   # EPUB3 目录


def _parse_opf(z: zipfile.ZipFile, opf_path: str) -> _Manifest:
    m = _Manifest()
    raw = _read_bytes(z, opf_path)
    if not raw:
        return m
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return m

    # ---- metadata
    for tag, attr in (("title", "title"), ("creator", "creator"),
                      ("language", "language"), ("identifier", "identifier")):
        node = root.find(f".//{NS_DC}{tag}")
        if node is not None and (node.text or "").strip():
            setattr(m, attr, node.text.strip())

    # ---- manifest
    by_id: dict[str, dict] = {}
    for item in root.iter(f"{NS_OPF}item"):
        iid = item.get("id") or ""
        href = item.get("href") or ""
        if not href:
            continue
        by_id[iid] = {
            "href": _resolve(opf_path, href),
            "type": (item.get("media-type") or "").lower(),
            "props": (item.get("properties") or "").lower(),
        }
        if "nav" in by_id[iid]["props"]:
            m.nav_href = by_id[iid]["href"]
    m.items = by_id

    # ---- spine（阅读顺序）
    for ref in root.iter(f"{NS_OPF}itemref"):
        iid = ref.get("idref") or ""
        item = by_id.get(iid)
        if item and item["type"] in _TEXT_TYPES:
            m.spine.append(item["href"])

    # ---- EPUB2 的 NCX（spine 上的 toc 属性指向它）
    spine_node = root.find(f"{NS_OPF}spine")
    if spine_node is not None:
        toc_id = spine_node.get("toc") or ""
        if toc_id in by_id:
            m.ncx_href = by_id[toc_id]["href"]
    if not m.ncx_href:
        for item in by_id.values():
            if item["type"] in ("application/x-dtbncx+xml",) or item["href"].endswith(".ncx"):
                m.ncx_href = item["href"]
                break

    # ---- spine 为空时（OPF 损坏），退化为按 manifest 里的正文顺序
    if not m.spine:
        m.spine = [it["href"] for it in by_id.values() if it["type"] in _TEXT_TYPES]
    return m


# ---------------------------------------------------------------- 目录标题
class _TocParser:
    """从 nav.xhtml 收集「章节文件 → 标题」。

    用 HTMLParser 而不是 ET：EPUB 的 nav 文档常有不闭合标签，
    XML 解析器会直接拒绝，HTML 解析器能容忍。
    """

    def __init__(self) -> None:
        from html.parser import HTMLParser

        outer = self

        class _P(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=True)
                self.href: str = ""
                self.depth = 0
                self.buf: list[str] = []
                self.found: list[tuple[str, str]] = []

            def handle_starttag(self, tag, attrs):
                if tag == "a":
                    self.href = dict(attrs).get("href") or ""
                    self.depth = 1
                    self.buf = []
                elif self.depth and tag in ("span", "b", "i", "em", "strong"):
                    self.depth += 1

            def handle_data(self, data):
                if self.depth:
                    self.buf.append(data)

            def handle_endtag(self, tag):
                if not self.depth:
                    return
                if tag in ("span", "b", "i", "em", "strong"):
                    self.depth = max(1, self.depth - 1)
                elif tag == "a":
                    text = re.sub(r"\s+", " ", "".join(self.buf)).strip()
                    if self.href and text:
                        self.found.append((self.href, text))
                    self.depth = 0

        self._parser = _P()
        self._outer = outer

    def feed(self, text: str) -> list[tuple[str, str]]:
        try:
            self._parser.feed(text)
            self._parser.close()
        except Exception:
            pass
        return self._parser.found


def _toc_titles(z: zipfile.ZipFile, m: _Manifest) -> dict[str, str]:
    """返回 {章节文件绝对路径: 标题}。nav.xhtml 优先，回退 toc.ncx。"""
    titles: dict[str, str] = {}

    if m.nav_href:
        raw = _read_bytes(z, m.nav_href)
        if raw:
            for href, text in _TocParser().feed(_decode(raw)):
                key = _resolve(m.nav_href, href)
                titles.setdefault(key, text)

    if not titles and m.ncx_href:
        raw = _read_bytes(z, m.ncx_href)
        if raw:
            try:
                root = ET.fromstring(raw)
                for point in root.iter(f"{NS_NCX}navPoint"):
                    label = point.find(f"{NS_NCX}navLabel/{NS_NCX}text")
                    content = point.find(f"{NS_NCX}content")
                    if label is None or content is None:
                        continue
                    title = (label.text or "").strip()
                    src = content.get("src") or ""
                    if title and src:
                        titles.setdefault(_resolve(m.ncx_href, src), title)
            except ET.ParseError:
                pass
    return titles


# ---------------------------------------------------------------- 读
def read_epub(path: Path, *, images: bool = True,
              assets_dir: str | Path | None = None,
              progress=None) -> Document:
    """EPUB → 块模型。

    ``progress(done, total, label)`` 会在每章开始时回调，用于界面进度条。
    """
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise SourceError(f"{path.name} 不是有效的 EPUB（压缩包损坏）",
                          hint="EPUB 本质是 zip；可尝试用支持阅读的软件重新导出") from exc
    except OSError as exc:
        raise SourceError(f"无法读取 {path.name}：{exc}") from exc

    with z:
        # 防呆：伪装成 .epub 的普通 zip（没有 container.xml 也没有 .opf）
        opf_path = _find_opf(z)
        if not opf_path:
            raise SourceError(
                f"{path.name} 里找不到 EPUB 清单（OPF）",
                hint="可能是改名的普通压缩包；请确认文件来源")

        m = _parse_opf(z, opf_path)
        if not m.spine:
            raise SourceError(f"{path.name} 的清单里没有可读章节",
                              hint="EPUB 结构不完整，可能生成工具异常")
        titles = _toc_titles(z, m)

        asset_root = Path(assets_dir) if assets_dir else None
        if images and asset_root:
            asset_root.mkdir(parents=True, exist_ok=True)

        blocks: list[Block] = []
        assets: list[ImageRef] = []
        warned: list[str] = []
        spine = m.spine[:_MAX_CHAPTERS]
        total = len(spine)

        for idx, href in enumerate(spine):
            raw = _read_bytes(z, href)
            if raw is None:
                warned.append(f"章节缺失已跳过：{posixpath.basename(href)}")
                if progress:
                    progress(idx + 1, total, posixpath.basename(href))
                continue

            # 图片解析器：src 相对当前章解析，从 zip 里取出落到资产目录
            def _resolve_img(src: str, alt: str, _href=href, _idx=idx):
                if not (images and asset_root):
                    return None
                inner = _resolve(_href, src)
                data = _read_bytes(z, inner)
                if not data:
                    return None
                ref = _save_epub_image(data, inner, alt, asset_root,
                                       chapter=_idx + 1)
                if ref is not None:
                    assets.append(ref)
                return ref

            try:
                from .htmlio import html_to_doc
                text = _decode(raw)
                chapter = html_to_doc(text, image_resolver=_resolve_img)
            except Exception as exc:
                warned.append(f"章节解析失败已跳过：{posixpath.basename(href)}"
                              f"（{type(exc).__name__}）")
                if progress:
                    progress(idx + 1, total, posixpath.basename(href))
                continue

            if chapter.is_empty():
                if progress:
                    progress(idx + 1, total, posixpath.basename(href))
                continue

            # 章节标题：正文没有以标题开头时，用目录里的标题补一个
            heading = titles.get(href) or titles.get(_safe_name(href)) or ""
            first = chapter.blocks[0] if chapter.blocks else None
            if heading and not (first is not None and first.kind == "heading"):
                blocks.append(Block(kind="heading", level=1, text=heading))
            blocks.extend(chapter.blocks)

            if progress:
                progress(idx + 1, total, posixpath.basename(href))

    if not blocks:
        raise SourceError(f"{path.name} 中没有可提取的正文",
                          hint="可能只含图片或目录结构异常")

    doc = Document(kind="text", name=path.stem, source_format="epub",
                   source_encoding="utf-8")
    doc.text = TextDoc(blocks)
    doc.assets = assets
    doc.source_title = m.title or path.stem
    if m.title:
        doc.title = m.title
    if m.creator:
        doc.author = m.creator
    if m.language:
        doc.language = m.language
    for w in warned:
        doc.warnings.append(w)
    return doc


def _save_epub_image(data: bytes, inner: str, alt: str, root: Path,
                     *, chapter: int) -> ImageRef | None:
    """把 zip 里的图片字节写成临时文件，返回 ImageRef。

    文件名用 ``e<章号>_<原名>``：不同章可能有同名图片（``images/cover.jpg``
    在各章重复引用），加章号前缀避免互相覆盖。
    """
    base = posixpath.basename(inner) or "image"
    stem, ext = posixpath.splitext(base)
    ext = ext.lower() or ".png"
    if ext == ".svg":
        # SVG 无法作为位图嵌入 docx/pdf，跳过并说明（读成图片块没有意义）
        return None
    safe_stem = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]", "_", stem)[:40] or "img"
    name = f"e{chapter:03d}_{safe_stem}{ext}"
    target = root / name
    n = 1
    while target.exists():
        name = f"e{chapter:03d}_{safe_stem}_{n}{ext}"
        target = root / name
        n += 1
    try:
        target.write_bytes(data)
    except OSError:
        return None

    width = height = 0
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            width, height = im.size
    except Exception:
        pass
    return ImageRef(name=name, path=target, alt=alt, width=width, height=height)


# ---------------------------------------------------------------- 写
_CSS = """
body { margin: 0 auto; padding: 1.2em 1.4em; max-width: 42em;
  font-family: "Segoe UI", "Microsoft YaHei", "PingFang SC", sans-serif;
  font-size: 1em; line-height: 1.75; color: #1b1b1b; }
h1 { font-size: 1.6em; margin: 1.4em 0 .6em; line-height: 1.3; }
h2 { font-size: 1.3em; margin: 1.2em 0 .5em; }
h3 { font-size: 1.12em; margin: 1em 0 .4em; }
p { margin: .7em 0; text-align: justify; }
blockquote { margin: .8em 0; padding: .2em 0 .2em 1em;
  border-left: 3px solid #d0d0d0; color: #444; }
pre { padding: .8em; background: #f6f6f6; border-radius: 4px;
  overflow-x: auto; font-size: .9em; }
code { font-family: Consolas, monospace; font-size: .95em; }
table { border-collapse: collapse; width: 100%; margin: .9em 0; }
th, td { border: 1px solid #dcdcdc; padding: .4em .6em; text-align: left; }
th { background: #f3f3f3; font-weight: 600; }
img { max-width: 100%; height: auto; }
figure { margin: 1em 0; text-align: center; }
figcaption { font-size: .9em; color: #666; margin-top: .4em; }
"""


def _xhtml(title: str, body: str, lang: str = "zh-CN") -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!DOCTYPE html>\n'
        f'<html xmlns="http://www.w3.org/1999/xhtml" '
        f'xmlns:epub="http://www.idpf.org/2007/ops" lang="{lang}" xml:lang="{lang}">\n'
        f'<head><meta charset="utf-8"/><title>{_esc(title)}</title>'
        '<link rel="stylesheet" type="text/css" href="style.css"/></head>\n'
        f'<body>\n{body}\n</body>\n</html>\n'
    )


def _esc(s: str) -> str:
    return html_mod.escape(s, quote=False)


def _split_chapters(blocks: list[Block]) -> list[tuple[str, list[Block]]]:
    """按最高层级标题切章。没有标题就整篇一章。"""
    levels = [b.level for b in blocks if b.kind == "heading" and b.text.strip()]
    if not levels:
        return [("正文", blocks)]
    top = min(levels)

    chapters: list[tuple[str, list[Block]]] = []
    cur_title = ""
    cur: list[Block] = []
    for b in blocks:
        if b.kind == "heading" and b.level == top and b.text.strip():
            if cur:
                chapters.append((cur_title or f"第 {len(chapters) + 1} 节", cur))
            cur_title = b.text.strip()
            cur = [b]
        else:
            cur.append(b)
    if cur:
        chapters.append((cur_title or f"第 {len(chapters) + 1} 节", cur))

    # 章前的空块（如封面留白）并进第一章，避免生成「无标题空章」
    if chapters and not chapters[0][0]:
        chapters[0] = ("正文", chapters[0][1])
    return [(t, bs) for t, bs in chapters if any(not b.is_empty() for b in bs)] or \
           [("正文", blocks)]


def write_epub(doc: Document, path: Path, *, title: str = "",
               author: str = "", language: str = "",
               images: bool = True) -> str:
    """块模型 → EPUB 3 文件。返回引擎名。"""
    from .htmlio import render_html

    text = doc.text or TextDoc()
    # 优先级：显式传入 > 文档自带元数据 > 文件名。
    # source_title 是「从文件名推出来的」，而 doc.title 是 EPUB/文档内部声明的
    # 真名，后者更可信——一本叫 三体.epub 的书，书名不该被文件名覆盖。
    book_title = (title or doc.title or doc.source_title
                  or path.stem or "document").strip()
    book_author = (author or doc.author or "").strip()
    lang = (language or doc.language or "zh-CN").strip() or "zh-CN"

    chapters = _split_chapters(text.blocks)
    # 图片有两个来源：doc.assets（读取器登记过的）和正文里的图块。
    # 只认前者会漏掉「块里有图但没登记」的情况（手工构造的文档、
    # 或某个读取器忘了同步），成品就会缺图且正文还引用着不存在的路径。
    assets: list[ImageRef] = []
    if images:
        assets = list(doc.assets)
        known = {a.name for a in assets}
        for b in text.blocks:
            if b.kind == "image" and b.image is not None and b.image.name not in known:
                assets.append(b.image)
                known.add(b.image.name)

    # ---- 章文件内容
    files: list[tuple[str, str, str]] = []      # (文件名, 标题, 内容)
    for i, (chap_title, blocks) in enumerate(chapters, start=1):
        body = render_html(TextDoc(blocks), assets_prefix="images",
                           with_images=bool(assets))
        files.append((f"ch{i:03d}.xhtml", chap_title, body))

    uid = f"urn:uuid:{uuid.uuid4()}"
    modified = "2026-01-01T00:00:00Z"

    # ---- nav.xhtml（EPUB3 目录，阅读器的章节列表来自这里）
    items = "\n".join(
        f'      <li><a href="{name}">{_esc(t)}</a></li>' for name, t, _ in files)
    nav = _xhtml("目录", f'<nav epub:type="toc" id="toc">\n'
                         f'    <h1>目录</h1>\n    <ol>\n{items}\n    </ol>\n  </nav>', lang)

    # ---- content.opf
    manifest = [
        '    <item id="nav" href="nav.xhtml" '
        'media-type="application/xhtml+xml" properties="nav"/>',
        '    <item id="css" href="style.css" media-type="text/css"/>',
    ]
    for i, (name, _, _) in enumerate(files, start=1):
        manifest.append(f'    <item id="c{i}" href="{name}" '
                        f'media-type="application/xhtml+xml"/>')
    for i, a in enumerate(assets, start=1):
        media = _media_type(a.name)
        manifest.append(f'    <item id="img{i}" href="images/{a.name}" '
                        f'media-type="{media}"/>')

    spine = "\n".join(
        f'    <itemref idref="c{i}"/>' for i in range(1, len(files) + 1))

    creator_xml = (f'    <dc:creator>{_esc(book_author)}</dc:creator>\n'
                   if book_author else "")
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="bookid" xml:lang="' + lang + '">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        f'    <dc:identifier id="bookid">{uid}</dc:identifier>\n'
        f'    <dc:title>{_esc(book_title)}</dc:title>\n'
        f'    <dc:language>{_esc(lang)}</dc:language>\n'
        f'{creator_xml}'
        f'    <meta property="dcterms:modified">{modified}</meta>\n'
        '  </metadata>\n'
        '  <manifest>\n' + "\n".join(manifest) + '\n  </manifest>\n'
        '  <spine>\n' + spine + '\n  </spine>\n'
        '</package>\n'
    )

    container = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<container version="1.0" '
        'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
        '  <rootfiles>\n'
        '    <rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/>\n'
        '  </rootfiles>\n'
        '</container>\n'
    )

    # ---- 打包
    # mimetype 必须是**第一个**条目且**不压缩**，否则部分阅读器直接判为非法。
    from .writers import atomic_write
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip",
                   compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", container)
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/nav.xhtml", nav)
        z.writestr("OEBPS/style.css", _CSS)
        for name, chap_title, body in files:
            z.writestr(f"OEBPS/{name}", _xhtml(chap_title, body, lang))
        for a in assets:
            if Path(a.path).exists():
                z.writestr(f"OEBPS/images/{a.name}", Path(a.path).read_bytes())

    atomic_write(path, buf.getvalue())
    return "内置"


def _media_type(name: str) -> str:
    ext = posixpath.splitext(name)[1].lower()
    return {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
        ".tif": "image/tiff", ".tiff": "image/tiff",
    }.get(ext, "image/png")
