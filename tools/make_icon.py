"""生成应用图标 assets/icon.ico（多尺寸 PNG 压缩 ICO）。

用法：
    python tools/make_icon.py

图标与应用内 IconTile 保持同一视觉语言：强调色渐变圆角方块 + 白色「眼睛」。
眼睛是 AI eye 的视觉母题，也对应 OCR 识别扫描件这项能力。
几何比例与 gui/widgets.py 的 paint_eye() 保持一致，改一处要同步另一处。
ICO 采用 PNG 压缩条目，Windows Vista 及以上原生支持。
"""

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QBuffer, QByteArray, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (QColor, QGuiApplication, QImage, QLinearGradient,  # noqa: E402
                           QPainter, QPainterPath, QPen)

SIZES = [16, 24, 32, 48, 64, 128, 256]


def draw_eye(p: QPainter, box: QRectF, color: QColor, stroke: float):
    """杏仁形眼睛：上下两段三次贝塞尔 + 实心瞳孔（与 gui/widgets.py 同比例）。"""
    cx, cy = box.center().x(), box.center().y()
    half_w = box.width() / 2.0
    half_h = box.height() * 0.26

    upper = QPainterPath(QPointF(cx - half_w, cy))
    upper.cubicTo(QPointF(cx - half_w * 0.5, cy - half_h * 1.45),
                  QPointF(cx + half_w * 0.5, cy - half_h * 1.45),
                  QPointF(cx + half_w, cy))

    lower = QPainterPath(QPointF(cx - half_w, cy))
    lower.cubicTo(QPointF(cx - half_w * 0.5, cy + half_h * 1.45),
                  QPointF(cx + half_w * 0.5, cy + half_h * 1.45),
                  QPointF(cx + half_w, cy))

    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(color, stroke, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.drawPath(upper)
    p.drawPath(lower)

    r = box.height() * 0.135
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawEllipse(QPointF(cx, cy), r, r)


def render(size: int) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)

    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)

    margin = size * 0.04
    rect = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)
    radius = size * 0.23

    grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
    grad.setColorAt(0.0, QColor("#2B88D8"))
    grad.setColorAt(1.0, QColor("#0F548C"))
    p.setPen(Qt.NoPen)
    p.setBrush(grad)
    p.drawRoundedRect(rect, radius, radius)

    # 小尺寸下瞳孔会更小、描边相对更粗，视觉上依然认得出是眼睛
    eye = QRectF(size * 0.17, size * 0.34, size * 0.66, size * 0.32)
    draw_eye(p, eye, QColor("#FFFFFF"), max(1.1, size * 0.058))
    p.end()
    return img


def png_bytes(img: QImage) -> bytes:
    # QBuffer 需要持有底层 QByteArray 的引用，否则临时对象被回收会段错误
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(ba)


def build_ico(out: Path):
    images = [(s, png_bytes(render(s))) for s in SIZES]
    out.parent.mkdir(parents=True, exist_ok=True)

    header = struct.pack("<HHH", 0, 1, len(images))  # reserved, type=icon, count
    entries = b""
    offset = len(header) + 16 * len(images)
    payload = b""
    for size, data in images:
        w = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", w, w, 0, 0, 1, 32, len(data), offset + len(payload))
        payload += data
    out.write_bytes(header + entries + payload)
    print(f"已生成 {out}（{len(images)} 个尺寸，{out.stat().st_size / 1024:.1f} KB）")


def main():
    # 必须持有引用：QGuiApplication 被回收后 QPainter 会段错误
    app = QGuiApplication(sys.argv)
    build_ico(ROOT / "assets" / "icon.ico")
    del app


if __name__ == "__main__":
    main()
