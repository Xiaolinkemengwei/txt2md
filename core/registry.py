"""格式注册表、引擎探测与能力矩阵。

能力矩阵是**运行时算出来的**，不是写死的常量表。原因：
PDF 输出依赖浏览器或 LaTeX，docx→pdf 的版式保真依赖 LibreOffice。
用户机器上装没装这些，只有探测才知道。把不可用的格式选项提前标灰/拒绝，
比让用户点了等十秒再报「找不到 pandoc」要好。

探测结果做进程内缓存——Edge/LibreOffice 的探测要启动进程，不能每次转换都做。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Fidelity = str  # ● 结构保真 / ◐ 内容保真 / ○ 纯文本近似

# ---------------------------------------------------------------- 格式定义
FormatKind = Literal["text", "table", "layout"]
# 转换方向：source 只能读、sink 只能写、both 双向
FormatRole = Literal["source", "sink", "both"]


@dataclass(frozen=True)
class Format:
    code: str                 # 内部代号，与 CLI/GUI 一致
    label: str                # 展示名
    extensions: tuple[str, ...]
    kind: FormatKind          # 决定读入后落到哪个模型
    binary: bool = False      # 是否为二进制格式（决定能否逐字保真）
    # 方向：图片只能作为输入（OCR 识别），不能作为输出目标。
    # 写死一个「图片」输出格式没有意义——没有任何转换是「生成一张图」。
    role: FormatRole = "both"

    def __str__(self) -> str:
        return self.code

    @property
    def can_read(self) -> bool:
        return self.role in ("source", "both")

    @property
    def can_write(self) -> bool:
        return self.role in ("sink", "both")


FORMATS: dict[str, Format] = {
    "txt": Format("txt", "纯文本", (".txt", ".text", ".log", ".textile"), "text"),
    "md": Format("md", "Markdown", (".md", ".markdown", ".mdown", ".mkd"), "text"),
    "html": Format("html", "HTML", (".html", ".htm", ".xhtml"), "text"),
    "csv": Format("csv", "CSV", (".csv", ".tsv"), "table"),
    "xlsx": Format("xlsx", "Excel 工作簿", (".xlsx", ".xlsm"), "table", binary=True),
    "docx": Format("docx", "Word 文档", (".docx",), "layout", binary=True),
    "pdf": Format("pdf", "PDF", (".pdf",), "layout", binary=True),
    # 图片走 OCR 读成文本；6 种常见位图格式，PDF 里的扫描页也走同一条链
    "image": Format("image", "图片（OCR）",
                    (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"),
                    "text", binary=True, role="source"),
}

# 别名：CLI 里允许写 xls / word / excel 之类的口语
ALIASES = {
    "text": "txt", "plain": "txt",
    "markdown": "md", "gfm": "md",
    "htm": "html",
    "excel": "xlsx", "xls": "xlsx", "workbook": "xlsx",
    "word": "docx", "doc": "docx",
}


# 全部可识别扩展名（由格式表派生，避免两处维护）
ALL_EXTENSIONS: tuple[str, ...] = tuple(
    sorted({ext for f in FORMATS.values() for ext in f.extensions}))


def resolve_format(name: str) -> Format | None:
    """把用户输入（代号/别名/扩展名）解析为 Format。"""
    if not name:
        return None
    key = name.strip().lower().lstrip("*")
    if key.startswith("."):
        key = key[1:]
    if key in FORMATS:
        return FORMATS[key]
    if key in ALIASES:
        return FORMATS[ALIASES[key]]
    for fmt in FORMATS.values():
        if key in (e.lstrip(".") for e in fmt.extensions):
            return fmt
    return None


def format_of_path(path: Path) -> Format | None:
    return resolve_format(path.suffix)


# ---------------------------------------------------------------- 引擎探测
@dataclass
class Engines:
    """本机可用的转换引擎。字段为 None / False 表示不可用。"""

    pandoc: str | None = None          # pandoc 可执行文件路径
    pandoc_version: str = ""
    browser: str | None = None         # Chromium 系浏览器（PDF 打印）
    browser_name: str = ""

    # 探测结束时记录的人类可读说明
    notes: list[str] = field(default_factory=list)

    def has(self, name: str) -> bool:
        return bool(getattr(self, name, None))


_ENGINES: Engines | None = None


def _which(*candidates: str) -> str | None:
    for c in candidates:
        p = shutil.which(c)
        if p:
            return p
    return None


def _find_pandoc() -> tuple[str | None, str]:
    """优先用 pypandoc 自带的二进制（随 pip 安装，无需用户装 pandoc）。

    注意：pypandoc 在 Windows 上 ``get_pandoc_path()`` 返回的路径缺 ``.exe``
    后缀（上游已知问题），必须补后缀再判断存在性，否则会把已安装的 pandoc
    误判为缺失。
    """
    candidates: list[str] = []
    try:
        import pypandoc
        path = pypandoc.get_pandoc_path()
        if path:
            candidates.extend([path, path + ".exe"])
    except Exception:
        pass
    for c in candidates:
        if c and Path(c).exists():
            return c, _pandoc_version(c)

    p = _which("pandoc", "pandoc.exe")
    if p:
        return p, _pandoc_version(p)
    return None, ""


def _pandoc_version(exe: str) -> str:
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True,
                             timeout=15, creationflags=_NO_WINDOW)
        line = (out.stdout or "").splitlines()[0] if out.stdout else ""
        parts = line.split()
        return parts[-1] if parts else "?"
    except Exception:
        return "?"


_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _find_browser() -> tuple[str | None, str]:
    """找可用于 --headless --print-to-pdf 的浏览器。

    Windows 上 Edge 是系统组件，命中率最高；其他平台退到 Chrome/Chromium。
    """
    if sys.platform == "win32":
        cands = [
            (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", "Edge"),
            (r"C:\Program Files\Microsoft\Edge\Application\msedge.exe", "Edge"),
            (r"C:\Program Files\Google\Chrome\Application\chrome.exe", "Chrome"),
            (r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe", "Chrome"),
        ]
        for path, name in cands:
            if Path(path).exists():
                return path, name
    for exe, name in (("msedge", "Edge"), ("google-chrome", "Chrome"),
                      ("chromium", "Chromium"), ("chromium-browser", "Chromium")):
        p = _which(exe)
        if p:
            return p, name
    for mac, name in (("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "Chrome"),
                      ("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge", "Edge")):
        if Path(mac).exists():
            return mac, name
    return None, ""


def detect_engines(refresh: bool = False) -> Engines:
    """探测一次并缓存。refresh=True 强制重探（GUI 里「重新检测」按钮用）。"""
    global _ENGINES
    if _ENGINES is not None and not refresh:
        return _ENGINES

    e = Engines()
    e.pandoc, e.pandoc_version = _find_pandoc()
    e.browser, e.browser_name = _find_browser()

    if e.pandoc:
        e.notes.append(f"pandoc {e.pandoc_version}")
    if e.browser:
        e.notes.append(f"{e.browser_name}（PDF 打印）")
    _ENGINES = e
    return e


# ---------------------------------------------------------------- 依赖探测
_IMPORTABLE: dict[str, bool] = {}


def _importable(module: str) -> bool:
    """真实 import 一次并缓存。

    为什么不用 ``importlib.util.find_spec``：打包版最常见的问题是「模块清单里
    有，但它依赖的二进制或子模块没进去」，此时 find_spec 照样返回 True，只有
    真正 import 才暴露。这直接决定 GUI 会不会「显示可选、点了才失败」。
    """
    if module in _IMPORTABLE:
        return _IMPORTABLE[module]
    try:
        import importlib
        if importlib.util.find_spec(module) is None:
            _IMPORTABLE[module] = False
            return False
        importlib.import_module(module)
        _IMPORTABLE[module] = True
    except Exception:
        _IMPORTABLE[module] = False
    return _IMPORTABLE[module]


# ---------------------------------------------------------------- 能力矩阵
@dataclass
class Capability:
    can_read: bool
    can_write: bool
    engine_read: str = ""
    engine_write: str = ""
    fidelity_read: Fidelity = "●"
    fidelity_write: Fidelity = "●"
    note: str = ""


def capability(fmt: str, engines: Engines | None = None) -> Capability:
    """返回某格式在当前环境下的读写能力（依赖可导入性会真实检测）。"""
    e = engines or detect_engines()

    if fmt in ("txt", "md", "html"):
        return Capability(True, True, "内置", "内置",
                          "●" if fmt != "html" else "◐", "◐")
    if fmt == "csv":
        return Capability(True, True, "内置", "内置", "●", "●")
    if fmt == "xlsx":
        ok = _importable("openpyxl")
        return Capability(ok, ok, "openpyxl" if ok else "", "openpyxl" if ok else "",
                          "●" if ok else "○", "●" if ok else "○",
                          note="" if ok else "缺少 openpyxl（Excel 读写组件）")
    if fmt == "docx":
        ok = _importable("docx")
        # docx 内置往返保结构；复杂版式（页眉页脚、脚注）会降级
        return Capability(ok, ok, "python-docx" if ok else "", "python-docx" if ok else "",
                          "◐" if ok else "○", "◐" if ok else "○",
                          note=("复杂版式（分栏、页眉页脚、脚注）不保留" if ok
                                else "缺少 python-docx（Word 读写组件）"))
    if fmt == "pdf":
        can_read = _importable("pdfplumber")
        can_write = bool(e.browser or e.pandoc)
        return Capability(
            can_read=can_read,
            can_write=can_write,
            engine_read="pdfplumber" if can_read else "",
            engine_write=(f"{e.browser_name} 打印" if e.browser
                          else "pandoc+LaTeX" if e.pandoc else ""),
            fidelity_read="○" if can_read else "○",
            fidelity_write="◐" if e.browser else "○",
            note=("PDF 无文档结构，只能取文本层重排；扫描件可勾选 OCR（较慢）"
                  if can_read and can_write
                  else "缺少 PDF 解析组件（pdfplumber），无法读取 PDF" if not can_read
                  else "缺少打印引擎（Edge/Chrome），无法生成 PDF"),
        )
    if fmt == "image":
        # 图片只有一条路：OCR 读成文本。写不出图片，因此 can_write 恒为 False。
        ok = _importable("rapidocr_onnxruntime")
        return Capability(
            can_read=ok, can_write=False,
            engine_read="RapidOCR" if ok else "",
            fidelity_read="○" if ok else "○",
            note=("图片经 OCR 识别为文字，不保留原版式" if ok
                  else "缺少 OCR 组件（rapidocr-onnxruntime），无法识别图片"),
        )
    return Capability(False, False, note="未支持的格式")


def validate_target(fmt: str) -> str:
    """校验某格式能否作为输出目标；不能则返回原因（空串表示可以）。"""
    f = FORMATS.get(fmt)
    if f is None:
        return f"未知的输出格式：{fmt}"
    if not f.can_write:
            writable = "、".join(k for k, v in FORMATS.items() if v.can_write)
            return (f"{f.label}只能作为输入（通过 OCR 识别），不能作为输出格式。"
                    f"可选目标：{writable}")
    return ""


def matrix(engines: Engines | None = None) -> dict[str, dict[str, str]]:
    """生成「源 → 目标」可转换性表，值为保真等级或空串（不可转）。"""
    e = engines or detect_engines()
    out: dict[str, dict[str, str]] = {}
    for src in FORMATS:
        row: dict[str, str] = {}
        cs = capability(src, e)
        for dst in FORMATS:
            cd = capability(dst, e)
            # 自身对自身的「·」只在双向格式上成立：图片不能转成图片
            if src == dst:
                row[dst] = "·" if (cs.can_read and cs.can_write) else ""
            elif not (cs.can_read and cd.can_write):
                row[dst] = ""
            else:
                # 整体保真取读写两端较弱者
                order = {"●": 3, "◐": 2, "○": 1, "": 0}
                row[dst] = min(cs.fidelity_read, cd.fidelity_write,
                               key=lambda x: order.get(x, 0))
        out[src] = row
    return out


def describe_environment(engines: Engines | None = None) -> str:
    """给 CLI / GUI 显示的一行环境摘要。"""
    e = engines or detect_engines()
    parts = ["内置引擎"]
    if e.pandoc:
        parts.append(f"pandoc {e.pandoc_version}")
    if e.browser:
        parts.append(e.browser_name)
    return " · ".join(parts)


def env_overrides_engine(name: str) -> None:
    """测试用：把某个引擎标记为不可用（模拟未安装环境）。"""
    global _ENGINES
    e = detect_engines()
    if name == "pandoc":
        e.pandoc, e.pandoc_version = None, ""
    elif name == "browser":
        e.browser, e.browser_name = None, ""
    _ENGINES = e


def reset_engine_cache() -> None:
    global _ENGINES
    _ENGINES = None
