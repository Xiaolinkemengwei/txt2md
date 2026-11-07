"""pandoc 兜底通道。

定位：**不是主路径，是失败时的第二张网。**

为什么不让 pandoc 当主引擎
------------------------
1. 体积：pypandoc-binary 的 pandoc.exe 有 231 MB，内置实现已覆盖 7 种格式，
   为了边际收益把安装包放大 6 倍不划算。
2. 能力边界：pandoc 3.9 能读 xlsx 但不能写，PDF 只能靠外部 LaTeX，实测
   xlsx 读取对生成器敏感（openpyxl 存的文件会报 Entry not found）。
3. 可控性：主路径用自研实现，行为可测、可预期；pandoc 版本差异会引入漂移。

所以策略是：内置实现优先；内置抛错时，若机器上恰好有 pandoc，就用它再试一次，
并把这个事实写进结果提示，让用户知道发生了什么。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from .registry import detect_engines

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

# pandoc 的格式名与本项目代号的映射。None 表示该方向 pandoc 做不了。
FROM_PANDOC = {
    "txt": "markdown",   # pandoc 无纯文本读取器，按 markdown 读（纯文本是其子集）
    "md": "gfm",
    "html": "html",
    "csv": "csv",
    "xlsx": "xlsx",
    "docx": "docx",
    "pdf": None,         # 无 PDF 读取器
}

TO_PANDOC = {
    "txt": "plain",
    "md": "gfm",
    "html": "html",
    "csv": "csv",
    "xlsx": None,        # pandoc 3.9 只能读 xlsx，不能写
    "docx": "docx",
    "pdf": None,         # 需 LaTeX 引擎，不稳妥，交给浏览器打印
}


def available() -> bool:
    return detect_engines().pandoc is not None


def can_handle(src_fmt: str, dst_fmt: str) -> bool:
    return FROM_PANDOC.get(src_fmt) is not None and TO_PANDOC.get(dst_fmt) is not None


def convert(src: Path, dst: Path, src_fmt: str, dst_fmt: str,
            timeout: int = 120) -> str | None:
    """尝试用 pandoc 转换。成功返回引擎名，失败或不可用返回 None。

    不抛异常：它只是兜底，本身失败不应该掩盖原始错误。
    """
    engines = detect_engines()
    if not engines.pandoc or not can_handle(src_fmt, dst_fmt):
        return None

    cmd = [
        engines.pandoc,
        str(src),
        "--from", FROM_PANDOC[src_fmt],
        "--to", TO_PANDOC[dst_fmt],
        "--output", str(dst),
    ]
    # 输出资源（图片等）放到目标目录，避免落到当前工作目录
    if dst_fmt == "docx":
        cmd += ["--resource-path", str(src.parent)]

    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout,
                              creationflags=_NO_WINDOW)
    except (subprocess.TimeoutExpired, OSError):
        return None

    if proc.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
        return None
    return f"pandoc {engines.pandoc_version}"
