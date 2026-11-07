"""Fluent 风格自绘控件：阴影卡片、开关、状态胶囊、省略文本、图标块。"""

from PySide6.QtCore import (Property, QEasingCurve, QPropertyAnimation, QRectF,
                            Qt, Signal)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (QButtonGroup, QFrame, QGraphicsDropShadowEffect,
                               QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget)

from . import theme as T


# ---------------------------------------------------------------- 卡片
class Card(QFrame):
    """白底圆角卡片 + Fluent 二级阴影（elevation 4）。"""

    def __init__(self, parent=None, radius: int = 8):
        super().__init__(parent)
        self.setObjectName("card")
        self.setAttribute(Qt.WA_StyledBackground, True)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setOffset(0, 5)
        shadow.setColor(QColor(0, 0, 0, 85))
        self.setGraphicsEffect(shadow)


# ---------------------------------------------------------------- 按钮
def make_button(text: str, variant: str = "secondary", parent=None) -> QPushButton:
    btn = QPushButton(text, parent)
    btn.setProperty("variant", variant)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFocusPolicy(Qt.StrongFocus)
    return btn


# ---------------------------------------------------------------- 图标块
class IconTile(QLabel):
    """应用图标：强调色渐变圆角方块 + 白色 Fluent 图标字形。"""

    GLYPHS = {"doc": "\ue8a5", "text": "\ue8d2", "convert": "\ue8ab"}

    def __init__(self, glyph: str = "doc", size: int = 44, parent=None):
        super().__init__(parent)
        self._glyph = glyph
        self._size = size
        self.setFixedSize(size, size)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(0.5, 0.5, self._size - 1, self._size - 1)

        grad = QLinearGradient(0, 0, self._size, self._size)
        grad.setColorAt(0.0, QColor("#2B88D8"))
        grad.setColorAt(1.0, QColor(T.ACCENT_PRESSED))
        p.setPen(Qt.NoPen)
        p.setBrush(grad)
        p.drawRoundedRect(rect, 10, 10)

        f = QFont(T.ICON_FAMILY)
        f.setPixelSize(int(self._size * 0.5))
        p.setFont(f)
        p.setPen(QColor("#FFFFFF"))
        p.drawText(rect, Qt.AlignCenter, self.GLYPHS.get(self._glyph, self._glyph))
        p.end()


# ---------------------------------------------------------------- 悬停行
class HoverRow(QWidget):
    """Fluent 设置行：鼠标悬停时出现 6px 圆角的浅灰底。"""

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
        p.setBrush(QColor("#F7F7F7"))
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 6, 6)
        p.end()


# ---------------------------------------------------------------- 开关
class FluentSwitch(QWidget):
    """Fluent ToggleSwitch：40x20 胶囊轨道 + 12px 圆钮，带 120ms 缓动。"""

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

    # -- 交互
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
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

        if self._checked:
            fill = QColor(T.ACCENT_HOVER if self._hover else T.ACCENT)
            p.setPen(Qt.NoPen)
        else:
            fill = QColor("#FAFAFA" if self._hover else T.CARD)
            p.setPen(QPen(QColor(T.STROKE_FIELD), 1))
        p.setBrush(fill)
        p.drawRoundedRect(track, 9.5, 9.5)

        # 圆钮：直径 12，圆心从 x=10(关) 移动到 x=30(开)
        r = 6.0
        cx = 10.0 + self._pos * 20.0
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#FFFFFF" if self._checked else T.TEXT_SECOND))
        p.drawEllipse(QRectF(cx - r, 10 - r, r * 2, r * 2))

        if self.hasFocus():
            p.setPen(QPen(QColor(T.ACCENT), 1.4))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(-2.0, -2.0, 44, 24), 12, 12)
        p.end()


# ---------------------------------------------------------------- 状态胶囊
class StatusPill(QLabel):
    KIND = {
        "ok": (T.SUCCESS_BG, T.SUCCESS, "完成"),
        "skipped": (T.WARN_BG, T.WARN, "跳过"),
        "error": (T.DANGER_BG, T.DANGER, "失败"),
    }

    def __init__(self, kind: str, parent=None):
        bg, fg, text = self.KIND.get(kind, self.KIND["ok"])
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignCenter)
        self.setFixedSize(48, 20)
        self.setStyleSheet(
            f"background: {bg}; color: {fg}; border-radius: 10px;"
            f"font-size: 11px; font-weight: 600;"
        )


# ---------------------------------------------------------------- 省略文本
class ElidedLabel(QLabel):
    """超宽时中间省略，保留文件名两端，避免撑破卡片宽度。"""

    def __init__(self, text: str = "", mode=Qt.ElideMiddle, parent=None):
        super().__init__(parent)
        self._full = text
        self._mode = mode
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(0)
        self.setText(text)

    def setText(self, text: str):  # noqa: N802 (Qt 命名)
        self._full = text
        self.setToolTip(text)
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
    """一条转换记录：状态胶囊 + 源文件 + 目标/原因。"""

    def __init__(self, kind: str, src: str, dst: str = "", reason: str = "", parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 0, 6, 0)
        lay.setSpacing(10)

        lay.addWidget(StatusPill(kind))

        name = src.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
        self.src = ElidedLabel(name, Qt.ElideMiddle)
        self.src.setStyleSheet(f"color: {T.TEXT}; font-size: 12px;")
        self.src.setToolTip(src)
        lay.addWidget(self.src, 1)

        if kind == "error":
            tail = ElidedLabel(f"原因：{reason}", Qt.ElideRight)
            tail.setStyleSheet(f"color: {T.DANGER}; font-size: 12px;")
        else:
            tail = ElidedLabel(dst.rsplit("\\", 1)[-1].rsplit("/", 1)[-1], Qt.ElideMiddle)
            tail.setStyleSheet(f"color: {T.TEXT_TERTIARY}; font-size: 12px;")
        tail.setToolTip(dst or reason)
        tail.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        tail.setMinimumWidth(90)
        lay.addWidget(tail, 1)

        self.setFixedHeight(28)


# ---------------------------------------------------------------- 格式选择
class FormatChips(QWidget):
    """一排胶囊按钮做格式单选。

    为什么不用下拉框：格式数量固定（7 个）且用户需要一眼看到「哪些格式
    在当前机器上可用」。下拉框把可用性藏起来了，用户选中后才知道不行。
    这里的不可用项直接置灰，悬停给出原因。
    """

    changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        from core.registry import FORMATS, capability

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        self._current = "md"

        for code, fmt in FORMATS.items():
            cap = capability(code)
            btn = QPushButton(code)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor if cap.can_write else Qt.ForbiddenCursor)
            btn.setProperty("chip", True)
            btn.setFixedHeight(30)
            btn.setMinimumWidth(50)
            btn.setFocusPolicy(Qt.NoFocus)
            if cap.can_write:
                level = {"●": "高", "◐": "中", "○": "低"}.get(cap.fidelity_write, "")
                btn.setToolTip(f"{fmt.label} · {cap.engine_write} · 保真{level}")
            else:
                btn.setEnabled(False)
                btn.setToolTip(f"{fmt.label}：当前环境无法写出（{cap.note or '缺少依赖'}）")
            self._group.addButton(btn)
            self._buttons[code] = btn
            lay.addWidget(btn)

        lay.addStretch(1)
        self._group.idClicked.connect(self._on_click)
        self.set_format("md")

    def _on_click(self, _id: int):
        for code, b in self._buttons.items():
            if b.isChecked() and code != self._current:
                self._current = code
                self.changed.emit(code)
                return

    def current_format(self) -> str:
        return self._current

    def set_format(self, code: str):
        btn = self._buttons.get(code)
        if btn is None or not btn.isEnabled():
            return
        btn.setChecked(True)
        self._current = code
