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


@dataclass(frozen=True)
class Format:
    code: str                 # 内部代号，与 CLI/GUI 一致
    label: str                # 展示名
    extensions: tuple[str, ...]
    kind: FormatKind          # 决定读入后落到哪个模型
    binary: bool = False      # 是否为二进制格式（决定能否逐字保真）

    def __str__(self) -> str:
        return self.code


FORMATS: dict[str, Format] = {
    "txt": Format("txt", "纯文本", (".txt", ".text", ".log", ".textile"), "text"),
    "md": Format("md", "Markdown", (".md", ".markdown", ".mdown", ".mkd"), "text"),
    "html": Format("html", "HTML", (".html", ".htm", ".xhtml"), "text"),
    "csv": Format("csv", "CSV", (".csv", ".tsv"), "table"),
    "xlsx": Format("xlsx", "Excel 工作簿", (".xlsx", ".xlsm"), "table", binary=True),
    "docx": Format("docx", "Word 文档", (".docx",), "layout", binary=True),
    "pdf": Format("pdf", "PDF", (".pdf",), "layout", binary=True),
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
    """返回某格式在当前环境下的读写能力。"""
    e = engines or detect_engines()

    if fmt in ("txt", "md", "html"):
        return Capability(True, True, "内置", "内置",
                          "●" if fmt != "html" else "◐", "◐")
    if fmt == "csv":
        return Capability(True, True, "内置", "内置", "●", "●")
    if fmt == "xlsx":
        return Capability(True, True, "openpyxl", "openpyxl", "●", "●")
    if fmt == "docx":
        # docx 内置往返保结构；复杂版式（页眉页脚、脚注）会降级
        return Capability(True, True, "python-docx", "python-docx", "◐", "◐",
                          note="复杂版式（分栏、页眉页脚、脚注）不保留")
    if fmt == "pdf":
        return Capability(
            can_read=True,
            can_write=bool(e.browser or e.pandoc),
            engine_read="pdfplumber",
            engine_write=(f"{e.browser_name} 打印" if e.browser else "pandoc+LaTeX" if e.pandoc else ""),
            fidelity_read="○",
            fidelity_write="◐" if e.browser else "○",
            note="PDF 无文档结构，只能取文本层重排",
        )
    return Capability(False, False, note="未支持的格式")


def matrix(engines: Engines | None = None) -> dict[str, dict[str, str]]:
    """生成「源 → 目标」可转换性表，值为保真等级或空串（不可转）。"""
    e = engines or detect_engines()
    out: dict[str, dict[str, str]] = {}
    for src in FORMATS:
        row: dict[str, str] = {}
        cs = capability(src, e)
        for dst in FORMATS:
            cd = capability(dst, e)
            if src == dst:
                row[dst] = "·"
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
