"""转换引擎探测原型（2.0 P0 的最小验证脚本）。

只做探测，不做转换：确认「本机有哪些引擎、各自能读写什么」，
用于验证 docs/2.0-设计与实现大纲.md 里「启动时按实际引擎裁剪能力矩阵」的可行性。

依赖：仅标准库（pandoc / openpyxl / pdfplumber 的可用性是探测结果，不是前提）。

用法：
    python research/exp/probe_conversion.py
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

EXE_NAMES = {
    "pandoc": ["pandoc", "pandoc.exe"],
    "soffice": ["soffice", "soffice.exe"],
    "browser": [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ],
}


def find_pandoc() -> str | None:
    """PATH 优先；其次 pypandoc_binary 自带的二进制。"""
    for name in EXE_NAMES["pandoc"]:
        hit = shutil.which(name)
        if hit:
            return hit
    try:  # pypandoc_binary 把 pandoc 放在包目录下（非 PATH）
        import pypandoc  # noqa: WPS433

        return pypandoc.get_pandoc_path()
    except Exception:
        return None


def find_soffice() -> str | None:
    for name in EXE_NAMES["soffice"]:
        hit = shutil.which(name)
        if hit:
            return hit
    for cand in (
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    ):
        if Path(cand).exists():
            return cand
    return None


def find_browser() -> str | None:
    for cand in EXE_NAMES["browser"]:
        if Path(cand).exists():
            return cand
    return None


def _run(cmd: list[str], timeout: int = 15) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (out.stdout or out.stderr).strip()
    except Exception as e:  # 探测失败不应中断整体流程
        return f"<probe failed: {e}>"


def probe() -> dict:
    result: dict = {}

    pandoc = find_pandoc()
    if pandoc:
        ver = _run([pandoc, "--version"]).splitlines()[0]
        ins = _run([pandoc, "--list-input-formats"]).split()
        outs = _run([pandoc, "--list-output-formats"]).split()
        result["pandoc"] = {"path": pandoc, "version": ver,
                            "in": len(ins), "out": len(outs),
                            "xlsx_in": "xlsx" in ins, "xlsx_out": "xlsx" in outs,
                            "pdf_out": "pdf" in outs}
    else:
        result["pandoc"] = None

    soffice = find_soffice()
    result["soffice"] = {"path": soffice} if soffice else None

    browser = find_browser()
    result["browser"] = {"path": browser} if browser else None

    mods = {}
    for name in ("openpyxl", "pdfplumber", "docx"):
        try:
            __import__(name)
            mods[name] = True
        except Exception:
            mods[name] = False
    result["python_modules"] = mods
    return result


def main() -> int:
    r = probe()
    print("=" * 66)
    print("转换引擎探测")
    print("=" * 66)

    pd = r["pandoc"]
    print(f"pandoc      : {pd['version']}" if pd else "pandoc      : 未找到")
    if pd:
        print(f"              {pd['path']}")
        print(f"              输入 {pd['in']} 种 / 输出 {pd['out']} 种 "
              f"| xlsx 输入={pd['xlsx_in']} xlsx 输出={pd['xlsx_out']} pdf 输出={pd['pdf_out']}")

    print(f"LibreOffice : {r['soffice']['path']}" if r["soffice"] else "LibreOffice : 未找到（office→pdf 将不可用）")
    print(f"浏览器      : {r['browser']['path']}" if r["browser"] else "浏览器      : 未找到（文本→pdf 将不可用）")

    mods = r["python_modules"]
    print("Python 模块 : " + "  ".join(f"{k}={'✓' if v else '✗'}" for k, v in mods.items()))

    # 能力摘要：按「本机实际可用引擎」推导，而不是写死
    can = {
        "txt/md/html/csv 互转": True,
        "xlsx ↔ md/csv": mods["openpyxl"],
        "→ docx/odt/pptx/epub": bool(pd),
        "→ pdf（文本类）": bool(r["browser"]),
        "→ pdf（office 类，保版式）": bool(r["soffice"]),
        "pdf → 文本": mods["pdfplumber"],
    }
    print("\n可达能力（按探测结果推导）：")
    for k, v in can.items():
        print(f"  {'✓' if v else '✗'} {k}")

    print("\nJSON:")
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
