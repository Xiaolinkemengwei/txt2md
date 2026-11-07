#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
txt2md 2.0 —— 多格式文档互转工具

支持的格式：txt / md / html / csv / xlsx / docx / pdf，任意两种之间双向转换。

用法:
  1) GUI 一键模式:          python txt2md.py --gui
  2) 单文件转换:            python txt2md.py note.txt              # txt → md
                           python txt2md.py report.docx --to txt   # Word → 纯文本
  3) 批量转换目录:          python txt2md.py ./docs --to pdf -o ./out
  4) 查看支持矩阵:          python txt2md.py --formats
  5) 查看本机可用引擎:      python txt2md.py --engines

1.0 的命令行用法仍然有效（``txt2md note.txt`` 依旧输出 ``note.md``，
``--plain`` 依旧表示逐字保留）。
"""

import argparse
import sys
import unicodedata
from pathlib import Path

__version__ = "2.0.0"
__all__ = ["convert_file", "collect_txt_files", "run_convert", "read_text_smart",
           "to_markdown", "__version__"]

# 1.0 的算法从这里原样复用（保证 txt → md 输出不变）
from core.legacy import read_text_smart, smart_line, to_markdown  # noqa: E402


# ---------------------------------------------------------------- 终端兼容
# Windows 中文控制台是 cp936，◐(U+25D0) / ✓(U+2713) / ✗(U+2717) 都不在码表里。
# 直接 print 会抛 UnicodeEncodeError 让程序整个崩掉——不是显示乱码那么轻；
# 把输出重定向到文件时同样崩。这里给 stdout/stderr 套一层降级：编不出的字符
# 换成语义最接近的替代符号，保证任何终端、任何代码页都能把活干完。
_GLYPH_FALLBACK = {
    "◐": "+", "●": "*", "○": "-", "·": ".",
    "✓": "√", "✗": "×", "⇄": "<->", "→": "->", "←": "<-",
    "↔": "<->", "🎯": "*",
}


def _encodable(text: str, enc: str) -> bool:
    try:
        text.encode(enc)
        return True
    except (UnicodeEncodeError, LookupError):
        return False


class _GlyphStream:
    """代理流：当前编码写不出的符号降级成替代字符，其余原样透传。"""

    def __init__(self, inner, enc: str):
        self._inner = inner
        self._enc = enc

    def write(self, text):
        if text and not _encodable(text, self._enc):
            text = "".join(
                ch if _encodable(ch, self._enc) else _GLYPH_FALLBACK.get(ch, "?")
                for ch in text)
        return self._inner.write(text)

    def __getattr__(self, name):          # encoding / flush / fileno / isatty …
        return getattr(self._inner, name)


def _install_stream_guard() -> None:
    """按终端实际编码决定：能写原字符就不动，汉字都写不了的编码退到 UTF-8。"""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None or isinstance(stream, _GlyphStream):
            continue
        enc = getattr(stream, "encoding", None)
        if not enc:
            import locale
            enc = locale.getpreferredencoding(False) or "utf-8"
        if not _encodable("文档", enc):
            # 该编码连汉字都没有（POSIX 下的 ASCII 之类），改成 UTF-8 更有用
            try:
                stream.reconfigure(encoding="utf-8")
                continue
            except Exception:
                pass
        if not _encodable("◐✓✗●○", enc):
            setattr(sys, name, _GlyphStream(stream, enc))


# ---------------------------------------------------------------- 1.0 兼容 API
def convert_file(src: Path, dst_dir: Path, smart: bool, overwrite: bool,
                 dst_name: str | None = None) -> tuple[str, str]:
    """转换单个 txt 文件，返回 (状态, 目标路径)。状态: ok / skipped / error

    保留 1.0 签名。内部走新的管线，但 txt → md 仍使用 1.0 的排版算法。
    """
    from core import Job
    from core.pipeline import convert_one

    dst = dst_dir / (dst_name or (src.stem + ".md"))
    job = Job(src=src, dst=dst, src_format="txt", dst_format="md")
    job.options["smart"] = smart
    result = convert_one(job, force=overwrite)
    return (result.status if result.status != "error" else "error",
            str(dst) if result.status != "error" else result.reason)


def collect_txt_files(target: Path):
    """收集待转换文件: 单文件,或目录下递归所有 .txt。"""
    if target.is_file():
        return [target]
    return sorted(target.rglob("*.txt"))


def run_convert(targets: list[Path], out_dir: Path | None, smart: bool,
                overwrite: bool) -> list[tuple[str, str, str]]:
    """执行转换,返回 [(状态, 源, 目标)]。保留 1.0 签名。"""
    from core import Job
    from core.pipeline import convert_one, plan_jobs, collect_inputs

    results = []
    for target in targets:
        files = collect_txt_files(target)
        if not files:
            results.append(("error", str(target), "未找到 .txt 文件"))
            continue
        root = target if target.is_dir() else None
        jobs = plan_jobs(files, "md", out_dir, src_root=root)
        for job in jobs:
            job.options["smart"] = smart
            r = convert_one(job, force=overwrite)
            results.append((r.status, str(job.src),
                            r.dst if r.status != "error" else r.reason))
    return results


# ---------------------------------------------------------------- GUI
def run_gui():
    """启动 Fluent Design 图形界面（PySide6 实现，见 gui/app.py）。"""
    try:
        from gui.app import run
    except ImportError as e:
        print("图形界面需要 PySide6，请先安装：")
        print("    python -m pip install -r requirements.txt")
        print(f"    详情: {e}")
        sys.exit(1)
    sys.exit(run())


# ---------------------------------------------------------------- 展示
def _w(s: str) -> int:
    """显示宽度：中文与全角符号按 2 计。

    不能用 ``ord(c) > 0x2E80`` 判断：●(U+25CF) / ·(U+00B7) 这些字符码位很小，
    但在中文终端里占两格，按 1 算会让整个表格错位。east_asian_width 返回
    W(宽) / F(全角) / A(模棱两可) 的字符都应算两格。
    """
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F", "A") else 1
               for c in s)


def _pad(s: str, width: int) -> str:
    return s + " " * max(0, width - _w(s))


def show_formats():
    """打印「源 → 目标」能力矩阵。"""
    from core.registry import FORMATS, capability, describe_environment, matrix

    m = matrix()
    codes = list(FORMATS)
    head = _pad("源 \\ 目标", 10) + "".join(_pad(c, 7) for c in codes)
    print(f"格式转换矩阵（● 结构保真  ◐ 内容保真  ○ 仅文本  · 同格式  - 不可用）")
    print(f"引擎: {describe_environment()}\n")
    print(head)
    print("-" * _w(head))
    for src in codes:
        line = _pad(src, 10)
        for dst in codes:
            v = m[src][dst]
            line += _pad(v or "-", 7)
        print(line)

    print("\n说明:")
    for code, fmt in FORMATS.items():
        cap = capability(code)
        rw = []
        if cap.can_read:
            rw.append(f"读 {cap.engine_read} {cap.fidelity_read}")
        if cap.can_write:
            rw.append(f"写 {cap.engine_write} {cap.fidelity_write}")
        note = f"  — {cap.note}" if cap.note else ""
        print(f"  {_pad(code, 6)} {fmt.label:<10} {' / '.join(rw) or '不可用'}{note}")


def show_engines():
    from core.registry import describe_environment, detect_engines

    e = detect_engines(refresh=True)
    print("内置引擎：总是可用（txt / md / html / csv / xlsx / docx 全部功能）\n")
    rows = [
        ("浏览器", e.browser or "未安装",
         f"PDF 输出{f'（{e.browser_name}）' if e.browser else '：缺少则无法生成 PDF'}"),
        ("pandoc", e.pandoc or "未安装", "内置实现失败时的格式互转兜底（可选）"),
        ("pdfplumber", _has("pdfplumber"), "读取 PDF 文本层"),
        ("openpyxl", _has("openpyxl"), "读写 xlsx"),
        ("python-docx", _has("docx"), "读写 docx"),
        ("charset-normalizer", _has("charset_normalizer"), "编码探测兜底"),
    ]
    print(f"{_pad('引擎', 20)}{_pad('状态', 10)}用途")
    print("-" * 80)
    for name, status, use in rows:
        state = "可用" if status and status != "未安装" else "缺失"
        print(f"{_pad(name, 20)}{_pad(state, 10)}{use}")
        if state == "缺失" and name in ("pdfplumber", "openpyxl", "python-docx"):
            print(f"{'':20}{'':10}安装: python -m pip install -r requirements.txt")
    print(f"\n环境摘要：{describe_environment()}")


def _has(module: str) -> str:
    try:
        __import__(module)
        return "已安装"
    except ImportError:
        return "未安装"


# ---------------------------------------------------------------- CLI
def main():
    _install_stream_guard()

    if len(sys.argv) == 2 and sys.argv[1] == "--gui":
        run_gui()
        return

    parser = argparse.ArgumentParser(
        prog="txt2md",
        description="文档格式互转：txt / md / html / csv / xlsx / docx / pdf 任意双向转换",
        epilog="示例：  txt2md note.txt              # txt → md\n"
               "        txt2md report.docx --to txt  # Word → 纯文本\n"
               "        txt2md ./docs --to pdf -o out # 目录批量转 PDF",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="*", help="文件或目录（目录会递归收集支持的格式）")
    parser.add_argument("-t", "--to", default="", metavar="格式",
                        help="目标格式：txt/md/html/csv/xlsx/docx/pdf（默认 md）")
    parser.add_argument("-f", "--from", dest="from_format", default="", metavar="格式",
                        help="源格式（默认按扩展名自动判断）")
    parser.add_argument("-o", "--out", type=Path, default=None, metavar="目录",
                        help="输出目录（批量时保留相对结构）；缺省输出到源文件旁边")
    parser.add_argument("--force", "--overwrite", dest="force", action="store_true",
                        help="覆盖已存在的目标文件（默认跳过）")
    parser.add_argument("--plain", action="store_true",
                        help="逐字保真：不添加任何 Markdown 标记，内容原样保留")
    parser.add_argument("--encoding", default="", metavar="编码",
                        help="强制指定源文件编码（默认自动探测 UTF-8/GB18030/Big5）")
    parser.add_argument("--sheets", default="", metavar="名称",
                        help="只转换 xlsx 中指定的工作表（逗号分隔）")
    parser.add_argument("--no-verify", action="store_true", help="跳过产物校验（不建议）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只做预检并列出计划，不实际转换")
    parser.add_argument("--formats", action="store_true", help="显示格式转换矩阵后退出")
    parser.add_argument("--engines", action="store_true", help="显示本机可用引擎后退出")
    parser.add_argument("--gui", action="store_true", help="启动图形界面")
    parser.add_argument("-q", "--quiet", action="store_true", help="只输出汇总")
    parser.add_argument("-V", "--version", action="version",
                        version=f"txt2md {__version__}", help="显示版本号")
    args = parser.parse_args()

    if args.formats:
        show_formats()
        return
    if args.engines:
        show_engines()
        return
    if args.gui or not args.paths:
        run_gui()
        return

    from core import collect_inputs, plan_jobs, precheck, run, summarize
    from core.registry import resolve_format

    to = args.to or "md"
    fmt = resolve_format(to)
    if fmt is None:
        print(f"✗ 不认识的目标格式：{to}", file=sys.stderr)
        print("  可用格式：txt / md / html / csv / xlsx / docx / pdf", file=sys.stderr)
        sys.exit(2)

    targets = [Path(p) for p in args.paths]
    for p in targets:
        if not p.exists():
            print(f"✗ 路径不存在：{p}", file=sys.stderr)
            sys.exit(2)

    files, notes = collect_inputs(targets, src_format=args.from_format)
    for n in notes:
        print(f"! {n}", file=sys.stderr)
    if not files:
        print("✗ 没有找到可转换的文件", file=sys.stderr)
        sys.exit(2)

    root = targets[0] if len(targets) == 1 and targets[0].is_dir() else None
    jobs = plan_jobs(files, fmt.code, args.out, src_root=root)

    opts = {
        "smart": not args.plain,
        "verbatim": args.plain,
        "encoding": args.encoding,
        "has_header": True,
    }
    if args.sheets:
        opts["sheets"] = [s.strip() for s in args.sheets.split(",") if s.strip()]
    for job in jobs:
        job.options.update(opts)

    ready, blocked, _ = precheck(jobs, force=args.force)

    if args.dry_run:
        print(f"预检结果：可执行 {len(ready)}，被拦下 {len(blocked)}\n")
        for job in jobs:
            reason = job.options.get("_blocked_by")
            mark = "✗" if reason else "·"
            print(f"{mark} {job.src} → {job.dst}")
            if reason:
                print(f"    原因: {reason}")
        print(f"\n未做任何转换（--dry-run）。去掉该参数即可执行。")
        sys.exit(1 if blocked else 0)

    results = run(jobs, force=args.force, verify=not args.no_verify, prechecked=True)
    stats = summarize(results)

    if not args.quiet:
        for r in results:
            if r.status == "ok":
                print(f"✓ {r.src}")
                print(f"    -> {r.dst}  [{r.engine}]")
                for w in r.warnings:
                    print(f"    ! {w}")
            elif r.status == "skipped":
                print(f"- {r.src}")
                print(f"    {r.reason}")
            else:
                print(f"✗ {r.src}")
                print(f"    原因: {r.reason}")
        print()

    engine_note = f" · 耗时 {stats['seconds']}s" if stats["seconds"] else ""
    warn_note = f" · 提示 {stats['warnings']}" if stats["warnings"] else ""
    print(f"汇总: 成功 {stats['ok']} / 跳过 {stats['skipped']} / "
          f"失败 {stats['error']}{engine_note}{warn_note}")
    sys.exit(1 if stats["error"] else 0)


if __name__ == "__main__":
    main()
