"""Fluent 2 风格自绘控件。

设计取舍
--------
- 格式选择用**卡片**而不是下拉框或胶囊：7 个格式固定不变，用户需要一眼看到
  哪些可写、保真到什么程度；下拉框会把这些信息全藏起来。
- 日志用自绘行而不是 QTableWidget：行内要放状态徽标 + 两段省略文本，
  表格的委托反而更啰嗦，且难以做到「悬停变色 + 双击打开输出位置」。
- 所有卡片的层次都靠 ``CARD_SHADOW``：Fluent 2 的层次是「极淡阴影 + 1px 描边」
  叠出来的，阴影一重就发脏、像 2010 年的拟物界面。
- 全部圆角、间距、字号都取自 ``theme``，不允许在这里写死颜色值。

坐标系统一用 ``QRectF(x+0.5, y+0.5, w-1, h-1)``：1px 描边落在像素中心才不会
被抗锯齿糊成 2px 灰边——这是自绘控件最常见的「描边发虚」来源。
"""

from PySide6.QtCore import (Property, QEasingCurve, QPointF, QPropertyAnimation,
                            QRectF, QSize, Qt, Signal)
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QGuiApplication,
                           QLinearGradient, QPainter, QPainterPath, QPen)
from PySide6.QtWidgets import (QFrame, QGraphicsDropShadowEffect, QHBoxLayout,
                               QLabel, QMenu, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from . import theme as T

# ---------------------------------------------------------------- 图标码位
# Segoe Fluent Icons 与 Segoe MDL2 Assets 共用这套私用区码位，两者都能显示。
GLYPH = {
    "doc": "\ue8a5",
    "folder": "\ue8b7",
    "folder_open": "\ue838",
    "convert": "\ue8ab",
    "settings": "\ue713",
    "info": "\ue946",
    "eye": "\ue7b3",
    "delete": "\ue74d",
    "check": "\ue73e",
    "open": "\ue8e5",
    "chevron": "\ue76c",
    "sparkle": "\ue9d9",
    "warning": "\ue7ba",
    "sun": "\ue706",      # Brightness
    "moon": "\ue708",     # QuietHours
    "guide": "\ue8a9",    # ViewAll：AI eye 导读
}


def icon_font(pixel: int, bold: bool = False) -> QFont:
    f = QFont(T.ICON_FAMILY)
    f.setPixelSize(pixel)
    f.setBold(bold)
    return f


# ---------------------------------------------------------------- 卡片
def card_shadow(widget: QWidget) -> QGraphicsDropShadowEffect:
    """统一卡片阴影：大模糊 + 小偏移 + 极低透明度，靠近但不发灰。"""
    shadow = QGraphicsDropShadowEffect(widget)
    shadow.setBlurRadius(18)
    shadow.setOffset(0, 2)
    shadow.setColor(QColor(0, 0, 0, 18))
    widget.setGraphicsEffect(shadow)
    return shadow


class Card(QFrame):
    """白底圆角卡片。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setAttribute(Qt.WA_StyledBackground, True)
        card_shadow(self)


class SectionHeader(QWidget):
    """卡片标题行：小图标 + 标题 + 可选说明，右侧留一个插槽放操作。"""

    def __init__(self, glyph: str, title: str, desc: str = "", parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        icon = QLabel(GLYPH.get(glyph, glyph))
        icon.setFont(icon_font(15))
        icon.setStyleSheet(f"color: {T.ACCENT}; background: transparent;")
        icon.setFixedWidth(18)
        icon.setAlignment(Qt.AlignCenter)
        lay.addWidget(icon)

        self.title = QLabel(title)
        self.title.setObjectName("cardTitle")
        lay.addWidget(self.title)

        if desc:
            self.desc = QLabel(desc)
            self.desc.setObjectName("cardDesc")
            lay.addWidget(self.desc)
        lay.addStretch(1)


# ---------------------------------------------------------------- 按钮
def make_button(text: str, variant: str = "secondary", parent=None) -> QPushButton:
    btn = QPushButton(text, parent)
    btn.setProperty("variant", variant)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFocusPolicy(Qt.StrongFocus)
    return btn


class IconButton(QPushButton):
    """图标按钮：Win11 应用栏里的圆形/方形操作。"""

    def __init__(self, glyph: str, tooltip: str = "", size: int = 30, parent=None):
        super().__init__(GLYPH.get(glyph, glyph), parent)
        self.setProperty("variant", "icon")
        self.setFont(icon_font(15))
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        if tooltip:
            self.setToolTip(tooltip)


# ---------------------------------------------------------------- 图标块
class IconTile(QWidget):
    """应用图标：强调色渐变圆角方块 + 白色「眼睛」图形（AI eye 的视觉母题）。

    眼睛完全用几何图形绘制，不依赖图标字体——打包环境里字体缺失时也不会
    变成豆腐块。
    """

    def __init__(self, size: int = 44, parent=None):
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = self._size
        rect = QRectF(0.5, 0.5, s - 1, s - 1)

        grad = QLinearGradient(0, 0, s, s)
        grad.setColorAt(0.0, "#2B88D8")
        grad.setColorAt(1.0, T.ACCENT_PRESSED)
        p.setPen(Qt.NoPen)
        p.setBrush(grad)
        p.drawRoundedRect(rect, s * 0.24, s * 0.24)

        paint_eye(p, QRectF(s * 0.16, s * 0.16, s * 0.68, s * 0.68),
                  QColor("#FFFFFF"), max(1.4, s * 0.062))
        p.end()


def paint_eye(p: QPainter, box: QRectF, color: QColor, stroke: float,
              filled_pupil: bool = True) -> None:
    """在 box 内画一只眼睛：上下两段弧 + 瞳孔。

    上下弧是单段三次贝塞尔拼成的杏仁形，比 drawEllipse 更接近人造「视觉」符号。
    ``filled_pupil=False`` 时瞳孔画成空心圆——用于空状态那种浅色线稿，
    实心点在浅灰描边里会糊成一团墨点。
    """
    cx, cy = box.center().x(), box.center().y()
    half_w = box.width() / 2.0
    half_h = box.height() * 0.26

    # 用单段三次贝塞尔画半只眼：两段二次贝塞尔会在正中央留一个尖点，
    # 放大看像「鱼嘴」。三次曲线的两个控制点让中点的切线保持水平。
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
    if filled_pupil:
        p.setPen(Qt.NoPen)
        p.setBrush(color)
    else:
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(color, max(1.2, stroke * 0.8), Qt.SolidLine))
    p.drawEllipse(QPointF(cx, cy), r, r)


# ---------------------------------------------------------------- 悬停行
class HoverRow(QWidget):
    """Fluent 设置行：悬停时出现浅灰底。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hover = False

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _e):
        if not self._hover:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(T.ROW_HOVER))
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 4, 4)
        p.end()


# ---------------------------------------------------------------- 开关
class FluentSwitch(QWidget):
    """WinUI 3 ToggleSwitch：40x20 轨道 + 14px 圆钮，120ms 缓动。"""

    toggled = Signal(bool)

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self._checked = checked
        self._pos = 1.0 if checked else 0.0
        self._hover = False
        self.setFixedSize(40, 20)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self._anim = QPropertyAnimation(self, b"knobPos", self)
        self._anim.setDuration(120)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

    # -- 动画属性（避免覆盖 QWidget.pos）
    def get_knob_pos(self) -> float:
        return self._pos

    def set_knob_pos(self, v: float):
        self._pos = v
        self.update()

    knobPos = Property(float, get_knob_pos, set_knob_pos)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool, animate: bool = True):
        if checked == self._checked:
            return
        self._checked = checked
        target = 1.0 if checked else 0.0
        if animate:
            self._anim.stop()
            self._anim.setStartValue(self._pos)
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self.set_knob_pos(target)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.isEnabled():
            self.setChecked(not self._checked)
            self.toggled.emit(self._checked)
            e.accept()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            self.setChecked(not self._checked)
            self.toggled.emit(self._checked)
            e.accept()
        else:
            super().keyPressEvent(e)

    def enterEvent(self, _e):
        self._hover = True
        self.update()

    def leaveEvent(self, _e):
        self._hover = False
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        track = QRectF(0.5, 0.5, 39, 19)

        on = self.isEnabled()
        if not on:
            fill = QColor(T.SURFACE_ALT)
            p.setPen(QPen(QColor(T.STROKE), 1))
        elif self._checked:
            fill = QColor(T.ACCENT_HOVER if self._hover else T.ACCENT)
            p.setPen(Qt.NoPen)
        else:
            fill = QColor("#FDFDFD" if self._hover else T.CARD)
            p.setPen(QPen(QColor(T.STROKE_FIELD), 1))
        p.setBrush(fill)
        p.drawRoundedRect(track, 9.5, 9.5)

        # 关：16px 深灰钮；开：14px 白钮（Win11 的原样）
        r = 8.0 if not self._checked else 7.0
        cx = 11.0 + self._pos * 18.0
        p.setPen(Qt.NoPen)
        if not on:
            knob = QColor(T.TEXT_DISABLED)
        else:
            knob = QColor("#FFFFFF" if self._checked else T.TEXT_SECOND)
        p.setBrush(knob)
        p.drawEllipse(QRectF(cx - r, 10 - r, r * 2, r * 2))

        if self.hasFocus():
            p.setPen(QPen(QColor(T.TEXT), 1.4))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(-2.5, -2.5, 45, 25), 12.5, 12.5)
        p.end()


# ---------------------------------------------------------------- 状态徽标
class StatusPill(QLabel):
    """Fluent Badge：前台色 + 同色系浅底，只在这对组合下满足对比度。"""

    KIND = {
        "ok": (T.SUCCESS_BG, T.SUCCESS, "完成"),
        "skipped": (T.WARN_BG, T.WARN, "跳过"),
        "error": (T.DANGER_BG, T.DANGER, "失败"),
        "info": (T.INFO_BG, T.INFO, "提示"),
    }

    def __init__(self, kind: str, parent=None):
        bg, fg, text = self.KIND.get(kind, self.KIND["ok"])
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignCenter)
        self.setFixedSize(42, 18)
        self.setStyleSheet(
            f"background: {bg}; color: {fg}; border-radius: 4px;"
            f"font-size: {T.FS_MICRO}px; font-weight: 600;"
        )


# ---------------------------------------------------------------- 省略文本
class ElidedLabel(QLabel):
    """超宽时省略，保留文件名两端，避免撑破卡片宽度。"""

    def __init__(self, text: str = "", mode=Qt.ElideMiddle, parent=None):
        super().__init__(parent)
        self._full = text
        self._mode = mode
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(0)
        self.setText(text)

    def setText(self, text: str):  # noqa: N802 (Qt 命名)
        self._full = text
        self._apply()

    def fullText(self) -> str:
        return self._full

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._apply()

    def _apply(self):
        fm = QFontMetrics(self.font())
        avail = max(0, self.width() - 2)
        super().setText(fm.elidedText(self._full, self._mode, avail) if avail else self._full)


# ---------------------------------------------------------------- 日志行
class LogRow(QWidget):
    """一条转换记录：状态徽标 + 源文件 + 目标 / 原因。

    悬停高亮 + 双击打开输出位置——批量转了 30 个文件之后，用户最想做的
    就是「打开刚才转好的那个」，不该让他自己去资源管理器里翻。
    """

    activated = Signal(str)     # 双击：传出目标文件路径（无目标时传源路径）
    reveal_requested = Signal(str)   # 右键「在文件夹中显示」

    def __init__(self, kind: str, src: str, dst: str = "", reason: str = "",
                 parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("background: transparent;")
        self.setMouseTracking(True)
        self._hover = False
        self.path = dst or src

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 0, 10, 0)
        lay.setSpacing(10)

        lay.addWidget(StatusPill(kind))

        name = src.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
        self.src = ElidedLabel(name, Qt.ElideMiddle)
        self.src.setStyleSheet(
            f"color: {T.TEXT}; font-size: {T.FS_CAPTION}px; background: transparent;")
        self.src.setToolTip(src)
        lay.addWidget(self.src, 5)

        if kind == "error":
            tail_text = f"原因：{reason}" if reason else "转换失败"
            tail = ElidedLabel(tail_text, Qt.ElideRight)
            tail.setStyleSheet(
                f"color: {T.DANGER}; font-size: {T.FS_CAPTION}px; background: transparent;")
        elif kind == "skipped":
            tail = ElidedLabel("目标已存在，已跳过", Qt.ElideRight)
            tail.setStyleSheet(
                f"color: {T.WARN}; font-size: {T.FS_CAPTION}px; background: transparent;")
        elif kind == "info":
            tail = ElidedLabel(reason, Qt.ElideRight)
            tail.setStyleSheet(
                f"color: {T.TEXT_SECOND}; font-size: {T.FS_CAPTION}px; background: transparent;")
        else:
            tail = ElidedLabel(dst.rsplit("\\", 1)[-1].rsplit("/", 1)[-1], Qt.ElideMiddle)
            tail.setStyleSheet(
                f"color: {T.TEXT_TERTIARY}; font-size: {T.FS_CAPTION}px; background: transparent;")
        tail.setToolTip(reason or dst)
        tail.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        tail.setMinimumWidth(80)
        lay.addWidget(tail, 4)

        self._line = QColor(T.ROW_LINE)
        self.setFixedHeight(32)

    # -- 交互
    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton and self.path:
            self.activated.emit(self.path)
            e.accept()
            return
        super().mouseDoubleClickEvent(e)

    def contextMenuEvent(self, e):
        """右键：在文件夹中显示。

        双击已经改成「查看结果」了，打开输出位置挪到这里——批量转了三十个
        文件之后，这个动作仍然要一步可达。
        """
        menu = QMenu(self)
        act_reveal = menu.addAction("在文件夹中显示")
        act_reveal.triggered.connect(lambda: self.reveal_requested.emit(self.path))
        act_copy = menu.addAction("复制文件路径")
        act_copy.triggered.connect(
            lambda: QGuiApplication.clipboard().setText(self.path))
        if not self.path:
            act_reveal.setEnabled(False)
            act_copy.setEnabled(False)
        menu.exec(e.globalPos())
        e.accept()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self._hover:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(T.ROW_HOVER))
            p.drawRoundedRect(QRectF(0, 1, self.width(), self.height() - 2), 4, 4)
        p.setPen(QPen(self._line, 1))
        p.drawLine(8, self.height() - 1, self.width() - 10, self.height() - 1)
        p.end()
        super().paintEvent(e)


# ---------------------------------------------------------------- 格式卡片
class FormatCard(QWidget):
    """单个格式选项：上行代号，下行中文名，选中时蓝框 + 浅蓝底 + 角标。

    自绘而不是用 QRadioButton：需要在卡片里排两行不同字号的文字，
    QSS 对 QRadioButton 的行内排版控制力不够，且无法画角标。
    """

    clicked = Signal(str)

    def __init__(self, code: str, label: str, detail: str = "", parent=None):
        super().__init__(parent)
        self.code = code
        self.label = label
        self.detail = detail
        self._selected = False
        self._hover = False
        self._available = True
        self.setFixedHeight(56)
        self.setMinimumWidth(72)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)

    # -- 状态
    def is_selected(self) -> bool:
        return self._selected

    def set_selected(self, on: bool):
        if on == self._selected:
            return
        self._selected = on
        self.update()

    def set_available(self, ok: bool):
        self._available = ok
        self.setEnabled(ok)
        self.setCursor(Qt.PointingHandCursor if ok else Qt.ForbiddenCursor)
        self.setFocusPolicy(Qt.StrongFocus if ok else Qt.NoFocus)
        self.update()

    # -- 交互
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self._available:
            self.clicked.emit(self.code)
            e.accept()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            self.clicked.emit(self.code)
            e.accept()
        else:
            super().keyPressEvent(e)

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def sizeHint(self):
        return QSize(84, 56)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)

        if not self._available:
            bg, border = "#FBFBFB", "#F0F0F0"
            code_fg, label_fg = T.TEXT_DISABLED, T.TEXT_DISABLED
        elif self._selected:
            bg, border = T.ACCENT_TINT, T.ACCENT
            code_fg, label_fg = T.ACCENT_PRESSED, T.ACCENT
        elif self._hover:
            bg, border = "#F8FBFF", T.STROKE_STRONG
            code_fg, label_fg = T.TEXT, T.TEXT_SECOND
        else:
            bg, border = T.CARD, T.STROKE
            code_fg, label_fg = T.TEXT, T.TEXT_SECOND

        p.setPen(QPen(QColor(border), 1.6 if self._selected else 1))
        p.setBrush(QColor(bg))
        p.drawRoundedRect(rect, T.R_CARD - 2, T.R_CARD - 2)

        f = QFont(T.MONO_FAMILY)
        f.setPixelSize(13)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(code_fg))
        p.drawText(QRectF(0, 9, self.width(), 17), Qt.AlignCenter, self.code)

        f2 = QFont(T.UI_FAMILY)
        f2.setPixelSize(T.FS_MICRO)
        p.setFont(f2)
        p.setPen(QColor(label_fg))
        fm = QFontMetrics(f2)
        p.drawText(QRectF(4, 29, self.width() - 8, 15), Qt.AlignCenter,
                   fm.elidedText(self.label, Qt.ElideRight, self.width() - 10))

        # 选中角标：一个带勾的小圆点，比整块高饱和底色更轻
        if self._selected:
            d = 13
            dot = QRectF(self.width() - d - 4, 4, d, d)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(T.ACCENT))
            p.drawEllipse(dot)
            p.setPen(QPen(QColor("#FFFFFF"), 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawLine(QPointF(dot.left() + 3.4, dot.center().y() + 0.2),
                       QPointF(dot.left() + 5.6, dot.center().y() + 2.4))
            p.drawLine(QPointF(dot.left() + 5.6, dot.center().y() + 2.4),
                       QPointF(dot.left() + 9.6, dot.center().y() - 2.4))

        if self.hasFocus() and self._available:
            p.setPen(QPen(QColor(T.TEXT), 1.2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(-2.5, -2.5, self.width() + 4, self.height() + 4),
                              T.R_CARD, T.R_CARD)
        p.end()


class FormatGrid(QWidget):
    """一行格式卡片，单选。

    对外只暴露 ``current_format()`` / ``set_format()`` / ``changed``。
    ``set_format`` **会**发出 ``changed``：调用方不该再手动刷新提示，
    否则一旦有新的调用点就会漏掉刷新。
    """

    changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(56)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        self._cards: dict[str, FormatCard] = {}
        self._current = "md"

        from core.registry import FORMATS, capability

        # 只列可写的格式：图片（OCR）是输入格式，放进「输出格式」里会让用户
        # 以为能把文字转成一张图。
        for code, fmt in FORMATS.items():
            if not fmt.can_write:
                continue
            cap = capability(code)
            card = FormatCard(code, fmt.label)
            if cap.can_write:
                level = {"●": "高", "◐": "中", "○": "低"}.get(cap.fidelity_write, "中")
                card.setToolTip(f"{fmt.label}\n引擎：{cap.engine_write}\n保真：{level}")
            else:
                card.set_available(False)
                card.setToolTip(f"{fmt.label}：当前环境无法写出（{cap.note or '缺少依赖'}）")
            card.clicked.connect(self._on_click)
            self._cards[code] = card
            lay.addWidget(card, 1)

        self.select("md", emit=False)

    def _on_click(self, code: str):
        self.select(code)

    def select(self, code: str, emit: bool = True):
        card = self._cards.get(code)
        if card is None or not card.isEnabled():
            return
        changed = code != self._current
        for c, item in self._cards.items():
            item.set_selected(c == code)
        self._current = code
        if emit and changed:
            self.changed.emit(code)

    # -- 兼容旧名
    def set_format(self, code: str):
        self.select(code, emit=True)

    def current_format(self) -> str:
        return self._current


# ---------------------------------------------------------------- 空状态
class EyeMark(QWidget):
    """淡色眼睛线稿：空状态与加载态的视觉母题，与应用图标同源。"""

    def __init__(self, size: int = 56, color: str = "#CFCFCF",
                 stroke: float = 2.0, parent=None):
        super().__init__(parent)
        self._size = size
        self._color = color
        self._stroke = stroke
        self.setFixedSize(size, size)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = self._size
        pad = s * 0.1
        paint_eye(p, QRectF(pad, pad + s * 0.12, s - pad * 2, s - pad * 2 - s * 0.2),
                  QColor(self._color), self._stroke, filled_pupil=False)
        p.end()


class EmptyState(QWidget):
    """列表为空时的占位：眼睛线稿 + 一句主文案 + 一句下一步提示。"""

    def __init__(self, glyph: str = "convert", title: str = "",
                 hint: str = "", parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addStretch(1)

        lay.addWidget(EyeMark(58), 0, Qt.AlignHCenter)

        self.title = QLabel(title)
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setStyleSheet(
            f"color: {T.TEXT_SECOND}; font-size: {T.FS_BODY}px; background: transparent;")
        lay.addWidget(self.title)

        self.hint = QLabel(hint)
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setStyleSheet(
            f"color: {T.TEXT_TERTIARY}; font-size: {T.FS_CAPTION}px; background: transparent;")
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)
        lay.addStretch(1)


# ---------------------------------------------------------------- 根容器
class AppRoot(QWidget):
    """窗口根容器：画一层极淡的自上而下渐变，模拟 Win11 的 Mica 受光。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("appRoot")
        self.setAttribute(Qt.WA_StyledBackground, True)

    def paintEvent(self, e):
        p = QPainter(self)
        grad = QLinearGradient(0, 0, 0, self.height() * 0.45)
        grad.setColorAt(0.0, QColor(T.BG_TOP))
        grad.setColorAt(1.0, QColor(T.BG))
        p.fillRect(self.rect(), grad)
        p.end()
        super().paintEvent(e)


# ---------------------------------------------------------------- 拖放提示
class DropOverlay(QWidget):
    """拖拽文件到窗口时盖一层虚线强调框，明确告诉用户「松手就行」。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setVisible(False)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        frame = QRectF(self.rect()).adjusted(7, 7, -7, -7)

        # 半透明遮罩压住底下的内容，但不至于看不见——用户需要确认「拖到了哪个窗口」
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 196))
        p.drawRoundedRect(frame, T.R_CARD + 2, T.R_CARD + 2)

        pen = QPen(QColor(T.ACCENT), 2, Qt.DashLine)
        pen.setDashPattern([5, 4])
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(frame, T.R_CARD + 2, T.R_CARD + 2)

        cy = frame.center().y()
        p.setFont(icon_font(34))
        p.setPen(QColor(T.ACCENT))
        p.drawText(QRectF(0, cy - 56, frame.width(), 44),
                   Qt.AlignHCenter | Qt.AlignVCenter, GLYPH["open"])

        f = QFont(T.UI_FAMILY)
        f.setPixelSize(15)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(T.TEXT))
        p.drawText(QRectF(0, cy + 6, frame.width(), 24),
                   Qt.AlignHCenter | Qt.AlignTop, "松开即可添加文件")

        f2 = QFont(T.UI_FAMILY)
        f2.setPixelSize(T.FS_CAPTION)
        p.setFont(f2)
        p.setPen(QColor(T.TEXT_SECOND))
        p.drawText(QRectF(0, cy + 30, frame.width(), 20),
                   Qt.AlignHCenter | Qt.AlignTop, "支持文件与文件夹，会自动递归收集")
        p.end()
