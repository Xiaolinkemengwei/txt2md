"""扫描件 OCR：基于 RapidOCR（onnxruntime 版）。

选型理由：模型随包分发（pip 装完即用，不需要 Tesseract 这样的外部程序）、
Apache-2.0 许可、离线运行、中英文识别质量足够。OCR 实例的初始化要加载
模型（约 1 秒），所以做成进程级单例，只加载一次。
"""
from __future__ import annotations

from .errors import DependencyMissing

_INSTANCE = None


def get_ocr():
    """返回 RapidOCR 单例；未安装时抛带安装指引的 DependencyMissing。"""
    global _INSTANCE
    if _INSTANCE is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except BaseException as exc:
            raise DependencyMissing.for_import(
                exc, "识别扫描件（OCR）", "rapidocr-onnxruntime") from exc
        _INSTANCE = RapidOCR()
    return _INSTANCE


def ocr_page_lines(pil_image) -> list[dict]:
    """识别一张页面图，返回与 _blocks_from_lines 兼容的行信息列表。

    RapidOCR 的每个结果是一个文本行：[四点框, 文字, 置信度]。框的坐标
    正好用来还原结构——x0 推列表缩进、框高当字号推标题，与文字层路径
    共用同一套块重建逻辑。
    """
    import re

    import numpy as np

    ocr = get_ocr()
    result, _elapse = ocr(np.asarray(pil_image))
    lines: list[dict] = []
    for box, text, _score in (result or []):
        text = str(text).strip()
        if not text:
            continue
        # OCR 常把 "- 列表项" 的空格吞掉（"-列表项"），导致行首列表标记
        # 正则不命中、整行掉回普通段落。识别行里补回这个空格——只在
        # OCR 路径做，文字层路径保持原样。
        text = re.sub(r"^([-*•·▪◦‣])(?=[^\s])", r"\1 ", text)
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        # right 是给版面切分用的：它要靠完整矩形涂掉文字、留下图形。
        # _blocks_from_lines 只关心 x0，多一个键不影响。
        lines.append({"text": text,
                      "size": max(4.0, max(ys) - min(ys)),   # 框高 ≈ 字高
                      "bold": False,
                      "x0": min(xs), "right": max(xs),
                      "top": min(ys), "bottom": max(ys)})
    lines.sort(key=lambda d: (d["top"], d["x0"]))
    return lines
