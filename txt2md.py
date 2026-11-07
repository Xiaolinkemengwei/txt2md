#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
txt2md —— TXT 文件一键转 Markdown 工具

用法:
  1) GUI 一键模式(推荐):  python txt2md.py --gui
  2) 命令行转换单个文件:   python txt2md.py note.txt
  3) 命令行批量转换目录:   python txt2md.py ./notes -o ./out --smart

设计借鉴: Santt997/txt2md (MIT) 的轻量启发式思路,
并修复其两个缺陷: 编码只支持 UTF-8(中文 GBK 会崩)、
"长行少空格包反引号"规则会破坏中文段落(中文无空格)。
"""

import argparse
import re
import sys
from pathlib import Path

__version__ = "1.0.0"
__all__ = ["convert_file", "collect_txt_files", "run_convert", "read_text_smart",
           "to_markdown", "__version__"]


# ---------------------------------------------------------------- 编码检测
def read_text_smart(path: Path) -> str:
    """按 BOM -> UTF-8 -> GBK 顺序尝试解码,适配中文 Windows 常见编码。"""
    raw = path.read_bytes()
    # BOM 优先
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig")
    if raw.startswith(b"\xff\xfe"):
        return raw.decode("utf-16-le") if len(raw) % 2 == 0 else raw.decode("utf-16-le", "replace")
    if raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16-be") if len(raw) % 2 == 0 else raw.decode("utf-16-be", "replace")
    # 依次尝试常见编码
    for enc in ("utf-8", "gbk", "big5"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    # 兜底: 不抛异常,保证"一键"体验不中断
    return raw.decode("utf-8", "replace")


# ---------------------------------------------------------------- 智能排版
LIST_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.、)]\s+|[-–—]{3,}\s*$)")  # 已是无序列表/有序列表/分隔线
MD_RE = re.compile(r"^\s*(?:#{1,6}\s+|>\s*)")                        # 已是 Markdown 标题/引用
CAPS_RE = re.compile(r"^[A-Z0-9 \t\-&,'()!?:]+$")                    # 全大写英文短行


def smart_line(line: str, seen_title: list) -> tuple[str, list]:
    """对单行做保守启发式转换。seen_title 用列表实现可变标志。"""
    stripped = line.rstrip("\r\n")
    s = stripped.strip()

    if not s:
        return "", seen_title

    # 已经是 Markdown 语法 -> 原样保留
    if MD_RE.match(s) or LIST_RE.match(s):
        return stripped, seen_title

    # 全大写英文短行(无句点) -> 二级标题 (借鉴上游规则)
    if len(s) <= 60 and CAPS_RE.match(s) and "." not in s and any(c.isalpha() for c in s):
        return f"## {s.title()}", seen_title

    # 第一段非空文本 -> 一级标题(文件标题),只生效一次
    if not seen_title[0]:
        seen_title[0] = True
        return f"# {s}", seen_title

    return s, seen_title


def to_markdown(text: str, smart: bool) -> str:
    # 统一换行符,去掉 BOM 残留
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    if not smart:
        # 纯转换: 内容原样保留,仅规范化末尾换行(空文件除外)
        return text if (not text or text.endswith("\n")) else text + "\n"

    seen_title = [False]
    out_lines = []
    for line in text.split("\n"):
        new_line, _ = smart_line(line, seen_title)
        # 统一规则: 输出以 # 开头(含大写行/首行产生的标题)即占用标题名额,
        # 避免后续普通段落被误判为文件标题
        if new_line.startswith("#"):
            seen_title[0] = True
        out_lines.append(new_line)
    body = "\n".join(out_lines)
    # 末尾保证单个换行(Markdown 规范)
    return body.rstrip("\n") + "\n"


# ---------------------------------------------------------------- 转换核心
def convert_file(src: Path, dst_dir: Path, smart: bool, overwrite: bool,
                 dst_name: str | None = None) -> tuple[str, str]:
    """转换单个文件,返回 (状态, 目标路径)。状态: ok / skipped / error"""
    dst = dst_dir / (dst_name or (src.stem + ".md"))
    if dst.exists() and not overwrite:
        return "skipped", str(dst)

    text = read_text_smart(src)
    md = to_markdown(text, smart)
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst.write_text(md, encoding="utf-8")  # 输出统一 UTF-8
    return "ok", str(dst)


def collect_txt_files(target: Path):
    """收集待转换文件: 单文件,或目录下递归所有 .txt。"""
    if target.is_file():
        return [target]
    return sorted(target.rglob("*.txt"))


def run_convert(targets: list[Path], out_dir: Path | None, smart: bool,
                overwrite: bool) -> list[tuple[str, str, str]]:
    """执行转换,返回 [(状态, 源, 目标)]。"""
    results = []
    for target in targets:
        files = collect_txt_files(target)
        if not files:
            results.append(("error", str(target), "未找到 .txt 文件"))
            continue
        for f in files:
            try:
                # 指定输出目录时: 批量模式保留相对目录结构,单文件直接落入输出目录
                if out_dir is not None:
                    if target.is_dir():
                        rel_parent = f.parent.relative_to(target)
                        dst_dir = out_dir / rel_parent
                    else:
                        dst_dir = out_dir
                else:
                    dst_dir = f.parent
                status, dst = convert_file(f, dst_dir, smart, overwrite)
                results.append((status, str(f), dst))
            except Exception as e:  # 单个文件失败不中断整体
                results.append(("error", str(f), str(e)))
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


# ---------------------------------------------------------------- CLI
def main():
    if len(sys.argv) == 2 and sys.argv[1] == "--gui":
        run_gui()
        return

    parser = argparse.ArgumentParser(
        description="TXT 文件一键转 Markdown(支持批量、GBK/UTF-8 编码自动识别)")
    parser.add_argument("paths", nargs="*", help="TXT 文件或包含 .txt 的目录")
    parser.add_argument("-o", "--out", type=Path, default=None,
                        help="输出目录(批量时保留相对结构);缺省输出到源文件旁边")
    parser.add_argument("--smart", action="store_true", default=True,
                        help="智能排版(默认开启): 首行作标题、识别列表")
    parser.add_argument("--plain", action="store_true",
                        help="纯转换: 内容原样保留,仅改扩展名")
    parser.add_argument("--overwrite", action="store_true", help="覆盖已存在的 .md")
    parser.add_argument("--gui", action="store_true", help="启动图形界面")
    parser.add_argument("-V", "--version", action="version",
                        version=f"txt2md {__version__}", help="显示版本号")
    args = parser.parse_args()

    if args.gui or not args.paths:
        run_gui()
        return

    smart = not args.plain
    results = run_convert([Path(p) for p in args.paths], args.out, smart, args.overwrite)

    ok = sk = er = 0
    for status, src, dst in results:
        mark = {"ok": "✓", "skipped": "-", "error": "✗"}[status]
        print(f"{mark} {src}")
        if status == "error":
            print(f"    原因: {dst}")
            er += 1
        else:
            print(f"    -> {dst}")
            if status == "ok":
                ok += 1
            else:
                sk += 1
    print(f"\n汇总: 成功 {ok} / 跳过 {sk} / 失败 {er}")
    sys.exit(1 if er else 0)


if __name__ == "__main__":
    main()
