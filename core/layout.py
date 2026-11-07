"""版面切分：在扫描页上划出插图与表格区域。

为什么需要
----------
扫描件没有文字层，整页 OCR 会把图里的坐标轴刻度、气泡标注一并识别成
正文行。实测第 41 页 27 行识别结果里有 8 行是 K 线图的纵轴数字
（3.60 / 2.80 …），第 61 页 43 行里有 25 行在图内（低位孕线 / 48000 …）。
这些行拼进正文就是用户看到的「乱码」。

做法
----
OCR 每行都带四点框，这本身就是一份免费的文字掩膜。把掩膜从页面暗像素
里减掉，剩下的连通结构就是「图」。再按页面比例过滤掉装饰线，就得到图区。

不引入版面检测模型的原因：docling / layoutparser 走 torch，模型加运行时
800MB~1.5GB，会毁掉单文件 exe 的体积；DocLayout-YOLO 的 ONNX 版 25~40MB
且是 AGPL。本项目已有 opencv + numpy，300 行像素统计就能覆盖这本书的
版面，故零新增依赖。

零新增依赖：opencv-python-headless 与 numpy 已随 OCR 一起安装。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

# 图注：以「图 / Figure / Fig.」开头并紧跟编号。正文里的「图表说明」不会
# 命中，因为「表」后面不是数字。
_CAPTION_RE = re.compile(r"^\s*(?:图|Figure|Fig\.?)\s*[\d一二三四五六七八九十]")
_CAPTION_MAX_LEN = 48

# 暗像素阈值：扫描件的纸是浅灰（200~250），墨迹在 60~180。190 能同时抓住
# 铅笔线（K 线影线）和印刷字，又不至于把纸纹算进去。
_DARK_LEVEL = 190

# 图区尺寸下限（相对页面）：宽 30%、高 10%、暗像素占比 0.6%。
# 低于此的多是页眉横线、正文里的强调下划线、单个公式分式线。
_MIN_W_RATIO = 0.30
_MIN_H_RATIO = 0.10
_MIN_INK_RATIO = 0.006


@dataclass
class Region:
    """一个版面区域。"""

    kind: Literal["figure", "table"]
    bbox: tuple[int, int, int, int]      # x0, y0, x1, y1（像素，已收紧）
    caption: str = ""
    index: int = 0                       # 页内序号，从 1 开始

    def width(self) -> int:
        return self.bbox[2] - self.bbox[0]

    def height(self) -> int:
        return self.bbox[3] - self.bbox[1]

    def area(self) -> int:
        return max(0, self.width()) * max(0, self.height())

    def contains_line(self, line: dict, pad: int = 4) -> bool:
        """判断一个 OCR 行是否落在本区域内（按行中心点）。

        用中心点而不是整框包含：紧贴图边界的刻度文字常有一两个像素溢出，
        整框判断会漏掉它们，而漏掉的那几行恰恰是乱码的来源。
        """
        x0, top, x1, bottom = self.bbox
        cx = (float(line.get("x0", 0)) + float(line.get("right", line.get("x0", 0)))) / 2
        cy = (float(line.get("top", 0)) + float(line.get("bottom", 0))) / 2
        return (x0 - pad <= cx <= x1 + pad) and (top - pad <= cy <= bottom + pad)


@dataclass
class PageLayout:
    """一页的版面分析结果。"""

    regions: list[Region] = field(default_factory=list)
    kept: list[dict] = field(default_factory=list)      # 保留为正文的行
    dropped: list[dict] = field(default_factory=list)   # 落在图区、被剔除的行
    note: str = ""

    def figures(self) -> list[Region]:
        return [r for r in self.regions if r.kind == "figure"]

    def tables(self) -> list[Region]:
        return [r for r in self.regions if r.kind == "table"]


# ---------------------------------------------------------------- 依赖
def _cv2():
    """惰性导入 OpenCV。缺失时返回 None，调用方降级为「不检测图区」。"""
    try:
        import cv2
        return cv2
    except BaseException:
        return None


def _np():
    try:
        import numpy
        return numpy
    except BaseException:
        return None


def available() -> bool:
    """版面分析是否可用（需要 numpy + opencv）。"""
    return _np() is not None and _cv2() is not None


# ---------------------------------------------------------------- 检测
def _text_mask(shape: tuple[int, int], lines: list[dict], cv2, np) -> "np.ndarray":
    """把 OCR 行框涂成实心块，得到「已知文字」掩膜。"""
    mask = np.zeros(shape, np.uint8)
    h, w = shape
    for ln in lines:
        x0 = int(max(0, float(ln.get("x0", 0))))
        x1 = int(min(w - 1, float(ln.get("right", ln.get("x0", 0)))))
        y0 = int(max(0, float(ln.get("top", 0))))
        y1 = int(min(h - 1, float(ln.get("bottom", 0))))
        if x1 > x0 and y1 > y0:
            mask[y0:y1 + 1, x0:x1 + 1] = 255
    # 外扩 2px：OCR 框贴字很紧，不加余量会在字脚留一圈残墨，闭运算时
    # 这些残墨会把相邻文字连成假图区。
    return cv2.dilate(mask, np.ones((5, 5), np.uint8), iterations=1)


def _tighten(residual, bbox: tuple[int, int, int, int],
             np, pad: int = 6) -> tuple[int, int, int, int]:
    """把形态学外扩过的框收回到实际墨迹范围。

    闭运算会让包围盒向外膨胀半个核宽；直接用会让裁出来的图带一圈白边，
    严重时把下方的图注也吃进去。
    """
    x0, y0, x1, y1 = bbox
    h, w = residual.shape
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    if x1 <= x0 or y1 <= y0:
        return bbox
    sub = residual[y0:y1, x0:x1]
    ys, xs = np.nonzero(sub)
    if len(xs) == 0:
        return bbox
    nx0 = int(x0 + xs.min()) - pad
    nx1 = int(x0 + xs.max()) + 1 + pad
    ny0 = int(y0 + ys.min()) - pad
    ny1 = int(y0 + ys.max()) + 1 + pad
    return (max(0, nx0), max(0, ny0), min(w, nx1), min(h, ny1))


def _looks_like_table(gray, bbox: tuple[int, int, int, int], cv2, np) -> bool:
    """在区域内找规则网格线，判断是不是真表格。

    判据比「有线」严格得多：K 线图也有大量水平价格网格线，若只看横线会
    全判成表格。真表格的竖线至少两条（左右边框或列分隔），且要贯穿区域。
    """
    x0, y0, x1, y1 = bbox
    sub = gray[y0:y1, x0:x1]
    if sub.size == 0:
        return False
    bw = cv2.threshold(sub, _DARK_LEVEL, 255, cv2.THRESH_BINARY_INV)[1]
    h, w = bw.shape

    # 横线：开运算只留「又长又薄」的结构
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(10, int(w * 0.5)), 1))
    horiz = cv2.morphologyEx(bw, cv2.MORPH_OPEN, hk)
    # 竖线同理
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(10, int(h * 0.5))))
    vert = cv2.morphologyEx(bw, cv2.MORPH_OPEN, vk)

    def _count(mask, axis: int) -> int:
        n, _lab, stats, _c = cv2.connectedComponentsWithStats(mask, 8)
        cnt = 0
        for i in range(1, n):
            bw_, bh_ = stats[i][2], stats[i][3]
            # 线要细：横线高 ≤ 4px，竖线宽 ≤ 4px（200dpi 下真实表格线 2~3px）
            if axis == 0 and bh_ <= max(4, h * 0.01) and bw_ >= w * 0.5:
                cnt += 1
            elif axis == 1 and bw_ <= max(4, w * 0.01) and bh_ >= h * 0.4:
                cnt += 1
        return cnt

    return _count(horiz, 0) >= 3 and _count(vert, 1) >= 2


def _caption_below(lines: list[dict], region: Region, line_h: float) -> str:
    """取图区下方紧邻的一行作为图注。

    扫描书的图注恒在图下方、字号与正文相同、以「图 2-13」这类编号开头。
    三条件同时满足才认，避免把正文首行当成图注吞掉。
    """
    gap = max(36.0, line_h * 2.4)
    best: tuple[float, str] | None = None
    for ln in lines:
        text = str(ln.get("text") or "").strip()
        if not text or len(text) > _CAPTION_MAX_LEN:
            continue
        if not _CAPTION_RE.match(text):
            continue
        top = float(ln.get("top", 0))
        dy = top - region.bbox[3]
        # 允许 -10：收紧时 pad 会把图区底边压到图注上沿，dy 变成小负数
        if -10 <= dy <= gap and (best is None or abs(dy) < abs(best[0])):
            best = (dy, text)
    return best[1] if best else ""


def detect_regions(image, lines: list[dict], *,
                   min_ink_ratio: float = _MIN_INK_RATIO) -> list[Region]:
    """从页面图与 OCR 行检测版面区域，按纵坐标排序。

    image: PIL.Image（灰度或彩色均可）
    lines: OCR 行，需含 x0/right/top/bottom
    """
    cv2, np = _cv2(), _np()
    if cv2 is None or np is None:
        return []

    gray = _to_gray(image, np)
    h, w = gray.shape
    if h < 40 or w < 40:
        return []

    mask = _text_mask((h, w), lines, cv2, np)
    dark = ((gray < _DARK_LEVEL).astype(np.uint8) * 255)
    # 减掉文字后剩下的才是图形
    residual = cv2.subtract(dark, mask)

    # 核按页面比例取，不写死像素：110dpi 与 200dpi 渲染出的页面宽度差一倍，
    # 固定核在低分辨率下会把整个正文连成一块。
    kw = max(9, int(w * 0.045))
    kh = max(5, int(h * 0.016))
    closed = cv2.morphologyEx(residual, cv2.MORPH_CLOSE,
                              cv2.getStructuringElement(cv2.MORPH_RECT, (kw, kh)))

    n, _lab, stats, _cent = cv2.connectedComponentsWithStats(closed, 8)
    page_ink = max(1, int((residual > 0).sum()))
    line_h = _median_line_height(lines)

    cands: list[tuple[int, int, int, int]] = []
    for i in range(1, n):
        x, y, cw, ch, area = (int(v) for v in stats[i][:5])
        if cw < w * _MIN_W_RATIO or ch < h * _MIN_H_RATIO:
            continue
        if area / float(w * h) < min_ink_ratio:
            continue
        # 连通域里的实际墨量也要够：纯白框（阴影、扫描脏点围出的空腔）不算
        ink = int((residual[y:y + ch, x:x + cw] > 0).sum())
        if ink / float(page_ink) < 0.05:
            continue
        cands.append((x, y, x + cw, y + ch))

    # 相交的候选合并：一张图常被图例、刻度线切成两三块
    merged = _merge_boxes(cands)
    regions: list[Region] = []
    for box in merged:
        tight = _tighten(residual, box, np)
        kind = "table" if _looks_like_table(gray, tight, cv2, np) else "figure"
        regions.append(Region(kind=kind, bbox=tight))

    regions.sort(key=lambda r: (r.bbox[1], r.bbox[0]))
    for i, r in enumerate(regions, 1):
        r.index = i
        r.caption = _caption_below(lines, r, line_h)
    return regions


def _to_gray(image, np):
    if image.mode != "L":
        return np.array(image.convert("L"))
    return np.array(image)


def _median_line_height(lines: list[dict]) -> float:
    hs = sorted(float(ln.get("bottom", 0)) - float(ln.get("top", 0))
                for ln in lines if ln.get("bottom") and ln.get("top"))
    if not hs:
        return 20.0
    return hs[len(hs) // 2]


def _merge_boxes(boxes: list[tuple[int, int, int, int]],
                 gap: int = 12) -> list[tuple[int, int, int, int]]:
    """合并相互靠近或重叠的框。"""
    out: list[list[int]] = []
    for b in sorted(boxes, key=lambda t: (t[1], t[0])):
        x0, y0, x1, y1 = b
        for o in out:
            if (x0 <= o[2] + gap and x1 >= o[0] - gap
                    and y0 <= o[3] + gap and y1 >= o[1] - gap):
                o[0], o[1] = min(o[0], x0), min(o[1], y0)
                o[2], o[3] = max(o[2], x1), max(o[3], y1)
                break
        else:
            out.append([x0, y0, x1, y1])
    return [tuple(o) for o in out]


# ---------------------------------------------------------------- 行归属
def split_lines(lines: list[dict], regions: list[Region],
                image_size: tuple[int, int]) -> tuple[list[dict], list[dict]]:
    """把 OCR 行分成「正文」与「图内碎片」。

    图注行也算碎片：它的文字已经挂到 ``Region.caption``，若再当正文输出一次，
    图上方会同时出现「图 2-13 …」和下面的图片题注，读起来是重复的。

    整页图保护：若剔除后剩下的行不足三成，说明这页本身就是一张大图
    （封面、整版图录），此时**不剔除任何行**——宁可带点乱码，也不能让
    一页内容凭空消失。
    """
    if not regions:
        return lines, []

    page_h = float(image_size[1])

    def _is_page_number(ln: dict) -> bool:
        """孤立页码：纯数字且贴在页面上下边缘。

        正文里不会出现「单独一行的 31」，扫描书却几乎每页都有。不剔掉的话
        它会作为独立段落插在正文中间，读起来像内容被截断了。
        """
        text = str(ln.get("text") or "").strip()
        if not text.isdigit() or len(text) > 4:
            return False
        cy = (float(ln.get("top", 0)) + float(ln.get("bottom", 0))) / 2
        return cy < page_h * 0.10 or cy > page_h * 0.90

    if not regions:
        # 没有插图也要剔页码：它的危害与有没有图无关
        dropped = [ln for ln in lines if _is_page_number(ln)]
        return [ln for ln in lines if ln not in dropped], dropped

    captions = {(r.caption, r.bbox[3]) for r in regions if r.caption}

    def _is_caption(ln: dict) -> bool:
        text = str(ln.get("text") or "").strip()
        if not text:
            return False
        top = float(ln.get("top", 0))
        # 与 _caption_below 保持同样的 −10 容差：图区底边被 pad 压到图注上沿时
        # top - bottom 会是小负数，这里卡 0 就会漏掉、图注又当正文输出一遍。
        return any(text == cap and -10 <= top - bottom <= 60
                   for cap, bottom in captions)

    dropped = [ln for ln in lines
               if any(r.contains_line(ln) for r in regions)
               or _is_caption(ln) or _is_page_number(ln)]
    kept = [ln for ln in lines if ln not in dropped]

    w, h = image_size
    covered = sum(r.area() for r in regions) / float(max(1, w * h))
    if covered > 0.70 and len(kept) < len(lines) * 0.30:
        return lines, []
    return kept, dropped


# ---------------------------------------------------------------- 裁切
def crop_region(image, region: Region, *, max_width: int = 1400,
                margin: int = 4) -> tuple[bytes, int, int]:
    """裁出区域并编码为 PNG，返回 (字节, 宽, 高)。

    max_width 默认 1400：整本书几百张图，原分辨率下每张 300KB+，全部落盘
    会让输出目录膨胀到几百 MB；1400px 宽在 Word 里按 15cm 排版仍清晰。
    margin 默认 4：形态学收紧时容易把贴边的成交量柱子切掉半个像素，
    留一点余量比事后补救便宜。
    """
    from io import BytesIO

    x0, y0, x1, y1 = region.bbox
    x0 = max(0, x0 - margin)
    y0 = max(0, y0 - margin)
    x1 = min(image.width, x1 + margin)
    y1 = min(image.height, y1 + margin)
    tile = image.crop((x0, y0, x1, y1))

    if tile.width > max_width > 0:
        ratio = max_width / float(tile.width)
        tile = tile.resize((max_width, max(1, int(tile.height * ratio))),
                           _resample())

    buf = BytesIO()
    if tile.mode not in ("L", "RGB"):
        tile = tile.convert("RGB")
    tile.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), tile.width, tile.height


def _resample():
    try:
        from PIL import Image
        return Image.LANCZOS
    except BaseException:                     # 老版本 Pillow
        from PIL import Image
        return Image.BICUBIC


# ---------------------------------------------------------------- 主入口
def analyze_page(image, lines: list[dict], *, page_no: int = 0) -> PageLayout:
    """一页的完整版面分析：区域 + 行归属。"""
    if not lines:
        return PageLayout(kept=list(lines))

    regions = detect_regions(image, lines)
    if not regions:
        return PageLayout(kept=list(lines))

    kept, dropped = split_lines(lines, regions, (image.width, image.height))
    note = ""
    if dropped:
        note = f"第 {page_no} 页：{len(regions)} 处图表，剔除 {len(dropped)} 行图内文字"
    return PageLayout(regions=regions, kept=kept, dropped=dropped, note=note)
