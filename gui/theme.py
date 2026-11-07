"""Fluent Design 设计令牌与全局样式。

配色取自 Fluent 2 (Microsoft Design) 亮色主题：
- 层级底色 #F3F3F3，卡片 #FFFFFF + 1px 描边 + 8px 圆角 + 低透明度阴影
- 强调色 #0F6CBD（含 hover/pressed/tint 三档）
- 语义色遵循 Fluent 状态色（成功/警告/危险）
字体优先 Segoe UI Variable（Win11），回退 Segoe UI / 微软雅黑。
"""

from PySide6.QtGui import QFontDatabase

# ---------------------------------------------------------------- 颜色令牌
BG = "#F3F3F3"
CARD = "#FFFFFF"
STROKE = "#E5E5E5"
STROKE_STRONG = "#D1D1D1"
STROKE_FIELD = "#8A8A8A"

TEXT = "#1B1B1B"
TEXT_SECOND = "#616161"
TEXT_TERTIARY = "#8A8A8A"
TEXT_DISABLED = "#BDBDBD"

ACCENT = "#0F6CBD"
ACCENT_HOVER = "#115EA3"
ACCENT_PRESSED = "#0F548C"
ACCENT_TINT = "#EBF3FC"

SUCCESS = "#0F7B0F"
SUCCESS_BG = "#DFF6DD"
WARN = "#9D5D00"
WARN_BG = "#FFF4CE"
DANGER = "#C42B1C"
DANGER_BG = "#FDE7E9"

HOVER_BG = "#F5F5F5"
PRESSED_BG = "#EDEDED"


# ---------------------------------------------------------------- 字体
# 注意：QFontDatabase 必须在 QApplication 创建之后才可访问，
# 因此这里只给默认值，实际族名由 init_fonts() 在启动时探测后覆盖。
UI_FAMILY = "Segoe UI"
DISPLAY_FAMILY = "Segoe UI"
ICON_FAMILY = "Segoe MDL2 Assets"
MONO_FAMILY = "Consolas"


def _pick_family(candidates, fallback="sans-serif") -> str:
    fams = set(QFontDatabase.families())
    for c in candidates:
        if c in fams:
            return c
    return fallback


def init_fonts():
    """在 QApplication 之后调用：按可用性挑选 Segoe UI 体系字体。"""
    global UI_FAMILY, DISPLAY_FAMILY, ICON_FAMILY, MONO_FAMILY
    UI_FAMILY = _pick_family(
        ["Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei"])
    DISPLAY_FAMILY = _pick_family(
        ["Segoe UI Variable Display", "Segoe UI Semibold", "Segoe UI", "Microsoft YaHei UI"])
    ICON_FAMILY = _pick_family(["Segoe Fluent Icons", "Segoe MDL2 Assets"], "Segoe UI")
    MONO_FAMILY = _pick_family(["Cascadia Mono", "Consolas", "Courier New"], "monospace")
    return UI_FAMILY


def qss() -> str:
    """全局样式表。控件通过 dynamic property 区分变体，避免内联样式散落。"""
    return f"""
    QWidget {{
        font-family: "{UI_FAMILY}";
        font-size: 13px;
        color: {TEXT};
    }}
    #appRoot {{ background: {BG}; }}

    /* ---------- 卡片 ---------- */
    #card {{
        background: {CARD};
        border: 1px solid {STROKE};
        border-radius: 8px;
    }}
    #cardTitle {{ font-size: 14px; font-weight: 600; color: {TEXT}; }}
    #cardHint {{ font-size: 12px; color: {TEXT_SECOND}; }}
    #subtitle {{ font-size: 12px; color: {TEXT_SECOND}; }}
    #appTitle {{
        font-family: "{DISPLAY_FAMILY}";
        font-size: 24px;
        font-weight: 600;
        color: {TEXT};
        letter-spacing: -0.2px;
    }}

    /* ---------- 文本输入 ---------- */
    QLineEdit {{
        background: {CARD};
        border: 1px solid {STROKE_STRONG};
        border-bottom: 1px solid {STROKE_FIELD};
        border-radius: 4px;
        padding: 0 12px;
        min-height: 32px;
        selection-background-color: {ACCENT};
        selection-color: #FFFFFF;
    }}
    QLineEdit:hover {{ background: #FAFAFA; }}
    QLineEdit:focus {{
        border: 1px solid {ACCENT};
        border-bottom: 2px solid {ACCENT};
        background: {CARD};
    }}

    /* ---------- 按钮 ---------- */
    QPushButton {{
        border-radius: 4px;
        padding: 0 16px;
        min-height: 32px;
        font-size: 13px;
    }}
    QPushButton[variant="primary"] {{
        background: {ACCENT};
        color: #FFFFFF;
        border: 1px solid {ACCENT};
        font-weight: 600;
        min-height: 38px;
        padding: 0 24px;
    }}
    QPushButton[variant="primary"]:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
    QPushButton[variant="primary"]:pressed {{ background: {ACCENT_PRESSED}; border-color: {ACCENT_PRESSED}; }}
    QPushButton[variant="secondary"] {{
        background: {CARD};
        color: {TEXT};
        border: 1px solid {STROKE_STRONG};
    }}
    QPushButton[variant="secondary"]:hover {{ background: {HOVER_BG}; }}
    QPushButton[variant="secondary"]:pressed {{ background: {PRESSED_BG}; color: {TEXT_SECOND}; }}
    QPushButton[variant="ghost"] {{
        background: transparent;
        color: {ACCENT};
        border: 1px solid transparent;
        padding: 0 8px;
        min-height: 26px;
    }}
    QPushButton[chip="true"] {{
        background: {CARD};
        border: 1px solid {STROKE};
        border-radius: 15px;
        padding: 0 12px;
        font-size: 12px;
        font-weight: 600;
        font-family: "Cascadia Mono", Consolas, monospace;
        color: {TEXT_SECOND};
    }}
    QPushButton[chip="true"]:hover {{
        background: {ACCENT_TINT};
        color: {TEXT};
        border-color: {STROKE_STRONG};
    }}
    QPushButton[chip="true"]:checked {{
        background: {ACCENT};
        border-color: {ACCENT};
        color: #FFFFFF;
    }}
    QPushButton[chip="true"]:checked:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
    QPushButton[chip="true"]:disabled {{
        color: {TEXT_DISABLED};
        background: #FAFAFA;
        border-color: {STROKE};
    }}
QPushButton[variant="ghost"]:hover {{ background: {ACCENT_TINT}; }}
    QPushButton[variant="ghost"]:pressed {{ background: #DCE9F7; }}
    QPushButton:disabled {{
        background: #F0F0F0;
        color: {TEXT_DISABLED};
        border: 1px solid #EBEBEB;
    }}

    /* ---------- 列表 / 日志 ---------- */
    QListWidget {{
        background: transparent;
        border: none;
        outline: none;
        padding: 0;
    }}
    QListWidget::item {{ border: none; }}
    QListWidget::item:selected {{ background: {ACCENT_TINT}; }}

    /* ---------- 滚动条（Fluent 细条） ---------- */
    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: #C8C8C8;
        border-radius: 3px;
        min-height: 28px;
    }}
    QScrollBar::handle:vertical:hover {{ background: #A6A6A6; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    QScrollBar:horizontal {{ height: 0; }}

    /* ---------- 进度条 ---------- */
    QProgressBar {{
        background: #E6E6E6;
        border: none;
        border-radius: 2px;
        max-height: 4px;
    }}
    QProgressBar::chunk {{
        background: {ACCENT};
        border-radius: 2px;
    }}

    /* ---------- 对话框 ---------- */
    QMessageBox {{ background: {CARD}; }}
    QMessageBox QLabel {{ color: {TEXT}; font-size: 13px; }}
    QMessageBox QPushButton {{
        background: {ACCENT}; color: #FFFFFF; border: 1px solid {ACCENT};
        border-radius: 4px; min-width: 78px; min-height: 30px; font-weight: 600;
    }}
    QMessageBox QPushButton:hover {{ background: {ACCENT_HOVER}; }}

    QToolTip {{
        background: {CARD};
        color: {TEXT};
        border: 1px solid {STROKE_STRONG};
        border-radius: 4px;
        padding: 6px 8px;
    }}
    """
