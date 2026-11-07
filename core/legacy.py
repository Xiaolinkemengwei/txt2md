"""1.0 的排版算法，原样保留。

为什么要单独留一份而不统一到块模型
--------------------------------
1.0 的 txt → md 输出是**已发布行为**：用户的脚本、README、测试都按它预期。
块模型往返虽然语义等价，但会在空行数量、标题后空格等细节上有差异。
这里把它作为「直通通道」原样复用 —— 用同一个函数、同一份正则，保证
1.0 → 2.0 的 txt → md 结果**字节级一致**。

其他转换方向（涉及 docx/xlsx/pdf 等）才走 core.model 的块管线。

对应 1.0 源码：txt2md.py 的 read_text_smart / smart_line / to_markdown。
"""

from __future__ import annotations

import re
from pathlib import Path

LIST_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.、)]\s+|[-–—]{3,}\s*$)")  # 已是无序/有序列表/分隔线
MD_RE = re.compile(r"^\s*(?:#{1,6}\s+|>\s*)")                          # 已是 Markdown 标题/引用
CAPS_RE = re.compile(r"^[A-Z0-9 \t\-&,'()!?:]+$")                      # 全大写英文短行


def read_text_smart(path: Path) -> str:
    """按 BOM -> UTF-8 -> GBK 顺序尝试解码,适配中文 Windows 常见编码。

    2.0 起委托给 core.readers.decode_bytes，其中 GBK 升级为 GB18030（超集，
    对纯 GBK 文件解码结果不变），并增加简繁歧义消解。
    """
    from .readers import decode_bytes
    text, _enc = decode_bytes(path.read_bytes())
    return text


def smart_line(line: str, seen_title: list) -> tuple[str, list]:
    """对单行做保守启发式转换。seen_title 用列表实现可变标志。"""
    stripped = line.rstrip("\r\n")
    s = stripped.strip()

    if not s:
        return "", seen_title

    # 已经是 Markdown 语法 -> 原样保留
    if MD_RE.match(s) or LIST_RE.match(s):
        return stripped, seen_title

    # 全大写英文短行(无句点) -> 二级标题
    if len(s) <= 60 and CAPS_RE.match(s) and "." not in s and any(c.isalpha() for c in s):
        return f"## {s.title()}", seen_title

    # 第一段非空文本 -> 一级标题(文件标题),只生效一次
    if not seen_title[0]:
        seen_title[0] = True
        return f"# {s}", seen_title

    return s, seen_title


def to_markdown(text: str, smart: bool) -> str:
    """1.0 的转换函数，行为与输出保持原样。"""
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
