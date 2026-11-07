"""Fluent 2（Windows 11 / WinUI 3）设计令牌与全局样式。

为什么单独抽一层
----------------
界面里出现的每一个颜色、字号、圆角都必须有名字。写死 ``"#0F6CBD"`` 的地方
一旦要调，就得全文件搜索替换，最后必然漏掉几处，界面出现「两种蓝」。

令牌来源
--------
- 中性色取自 Fluent 2 亮色主题：底 #F3F3F3 / 卡 #FFFFFF / 描边 #E5E5E5
- 强调色 #0F6CBD（Fluent 2 accent），按下态 #0F548C，浅底 #EBF3FC
- 语义色 success / caution / critical 用的是 Fluent 的「前景-背景」成对色，
  徽标只在前景-背景成对使用时才满足对比度要求，不要混搭
- 圆角分三档：卡片 8 / 控件 4 / 胶囊 999。WinUI 3 的按钮就是 4px，
  用 8px 会显得像网页而不是系统应用

字号阶
------
28/20/14/13/12/11 六档。低于 11px 在 125% 缩放下会糊，不再往下取。
"""

from PySide6.QtGui import QFontDatabase

# ---------------------------------------------------------------- 中性色
BG = "#F3F3F3"              # 窗口底：Win11 设置页的同款灰
BG_TOP = "#FBFBFB"          # 顶部渐亮的起点，模拟 Mica 的受光感
CARD = "#FFFFFF"
SURFACE_ALT = "#FAFAFA"     # 输入框 / 次级容器
STROKE = "#E5E5E5"          # 卡片描边
STROKE_STRONG = "#D6D6D6"   # 控件描边
STROKE_FIELD = "#8A8A8A"    # 输入框底线（Fluent 输入框默认较深）
DIVIDER = "#EDEDED"

TEXT = "#1B1B1B"
TEXT_SECOND = "#5C5C5C"
TEXT_TERTIARY = "#8A8A8A"
TEXT_DISABLED = "#BDBDBD"

# ---------------------------------------------------------------- 强调色
ACCENT = "#0F6CBD"
ACCENT_HOVER = "#115EA3"
ACCENT_PRESSED = "#0F548C"
ACCENT_TINT = "#EBF3FC"
ACCENT_TINT_HOVER = "#DCE9F7"
ACCENT_DISABLED = "#C7DCF0"

# ---------------------------------------------------------------- 语义色
SUCCESS = "#0F7B0F"
SUCCESS_BG = "#DFF6DD"
WARN = "#9D5D00"
WARN_BG = "#FFF4CE"
DANGER = "#C42B1C"
DANGER_BG = "#FDE7E9"
INFO = ACCENT
INFO_BG = ACCENT_TINT

HOVER_BG = "#F5F5F5"
PRESSED_BG = "#EDEDED"
ROW_HOVER = "#F7F7F7"
ROW_LINE = "#F1F1F1"

# ---------------------------------------------------------------- 字体
# QFontDatabase 必须在 QApplication 之后才能访问，这里只放默认值，
# 实际族名由 init_fonts() 在启动时探测后覆盖。
UI_FAMILY = "Segoe UI"
DISPLAY_FAMILY = "Segoe UI"
ICON_FAMILY = "Segoe MDL2 Assets"
MONO_FAMILY = "Consolas"

# 字号阶（px）
FS_DISPLAY = 26
FS_TITLE = 20
FS_HEADING = 14
FS_BODY = 13
FS_CAPTION = 12
FS_MICRO = 11

# 圆角阶（px）
R_CARD = 8
R_CONTROL = 4
R_PILL = 999


def _pick_family(candidates, fallback="sans-serif") -> str:
    fams = set(QFontDatabase.families())
    for c in candidates:
        if c in fams:
            return c
    return fallback


def init_fonts():
    """在 QApplication 之后调用：按可用性挑选 Segoe UI 体系字体。"""
    global UI_FAMILY, DISPLAY_FAMILY, ICON_FAMILY, MONO_FAMILY
    # Segoe UI Variable 是 Win11 的默认字体；Win10 上退回 Segoe UI，
    # 再退到中文字体（避免中文被替换成宋体）
    UI_FAMILY = _pick_family(
        ["Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei"])
    DISPLAY_FAMILY = _pick_family(
        ["Segoe UI Variable Display", "Segoe UI Semibold", "Segoe UI", "Microsoft YaHei UI"])
    ICON_FAMILY = _pick_family(["Segoe Fluent Icons", "Segoe MDL2 Assets"], "Segoe UI")
    MONO_FAMILY = _pick_family(["Cascadia Mono", "Consolas", "Courier New"], "monospace")
    return UI_FAMILY


def repolish(widget) -> None:
    """动态属性改了之后必须重新走一遍样式计算，否则 QSS 不会重新匹配。

    Qt 不会因为 ``setProperty`` 自动重绘样式——这是 QSS 最常见的坑：
    代码看起来改了状态，界面纹丝不动。
    """
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def qss() -> str:
    """全局样式表。控件通过 objectName / dynamic property 区分变体。"""
    return f"""
    /* ================= 基础 ================= */
    QWidget {{
        font-family: "{UI_FAMILY}";
        font-size: {FS_BODY}px;
        color: {TEXT};
    }}
    #appRoot {{ background: {BG}; }}
    QWidget:disabled {{ color: {TEXT_DISABLED}; }}

    /* ================= 应用栏 ================= */
    #appTitle {{
        font-family: "{DISPLAY_FAMILY}";
        font-size: {FS_TITLE}px;
        font-weight: 600;
        color: {TEXT};
    }}
    #appSubtitle {{
        font-size: {FS_CAPTION}px;
        color: {TEXT_TERTIARY};
    }}
    #versionTag {{
        font-size: {FS_MICRO}px;
        color: {TEXT_TERTIARY};
        background: {SURFACE_ALT};
        border: 1px solid {STROKE};
        border-radius: {R_CONTROL}px;
        padding: 1px 7px;
    }}

    /* ================= 卡片 ================= */
    #card {{
        background: {CARD};
        border: 1px solid {STROKE};
        border-radius: {R_CARD}px;
    }}
    #cardTitle {{
        font-size: {FS_HEADING}px;
        font-weight: 600;
        color: {TEXT};
    }}
    #cardDesc {{ font-size: {FS_CAPTION}px; color: {TEXT_SECOND}; }}
    #cardHint {{ font-size: {FS_CAPTION}px; color: {TEXT_TERTIARY}; }}
    #fieldLabel {{ font-size: {FS_BODY}px; font-weight: 600; color: {TEXT}; }}
    #rowTitle {{ font-size: {FS_BODY}px; font-weight: 600; color: {TEXT}; }}
    #rowDesc {{ font-size: {FS_CAPTION}px; color: {TEXT_SECOND}; }}
    #colHead {{ font-size: {FS_MICRO}px; color: {TEXT_TERTIARY}; }}
    #hairline {{ background: {DIVIDER}; border: none; }}

    /* 提示文字按状态换色：不要在 Python 里写内联 color，否则主题一改就漏 */
    QLabel#hint[state="idle"] {{ color: {TEXT_SECOND}; }}
    QLabel#hint[state="ok"] {{ color: {ACCENT}; }}
    QLabel#hint[state="warn"] {{ color: {WARN}; }}
    QLabel#hint[state="error"] {{ color: {DANGER}; }}
    QLabel#summary[state="idle"] {{ color: {TEXT_SECOND}; }}
    QLabel#summary[state="ok"] {{ color: {SUCCESS}; }}
    QLabel#summary[state="warn"] {{ color: {WARN}; }}
    QLabel#summary[state="error"] {{ color: {DANGER}; }}

    /* ================= 文本输入 ================= */
    QLineEdit {{
        background: {SURFACE_ALT};
        border: 1px solid {STROKE};
        border-bottom: 1px solid {STROKE_FIELD};
        border-radius: {R_CONTROL}px;
        padding: 0 10px;
        min-height: 34px;
        selection-background-color: {ACCENT};
        selection-color: #FFFFFF;
    }}
    QLineEdit:hover {{ background: {CARD}; border-color: {STROKE_STRONG}; }}
    QLineEdit:focus {{
        background: {CARD};
        border: 1px solid {STROKE};
        border-bottom: 2px solid {ACCENT};
    }}
    QLineEdit:disabled {{ background: {BG}; color: {TEXT_DISABLED}; }}

    /* ================= 按钮 ================= */
    QPushButton {{
        border-radius: {R_CONTROL}px;
        padding: 0 14px;
        min-height: 32px;
        font-size: {FS_BODY}px;
    }}
    QPushButton[variant="primary"] {{
        background: {ACCENT};
        color: #FFFFFF;
        border: 1px solid {ACCENT};
        font-weight: 600;
        min-height: 38px;
        padding: 0 22px;
    }}
    QPushButton[variant="primary"]:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
    QPushButton[variant="primary"]:pressed {{ background: {ACCENT_PRESSED}; border-color: {ACCENT_PRESSED}; }}
    QPushButton[variant="primary"]:disabled {{
        background: {ACCENT_DISABLED}; border-color: {ACCENT_DISABLED}; color: #FFFFFF;
    }}
    QPushButton[variant="secondary"] {{
        background: {CARD};
        color: {TEXT};
        border: 1px solid {STROKE_STRONG};
    }}
    QPushButton[variant="secondary"]:hover {{ background: {HOVER_BG}; }}
    QPushButton[variant="secondary"]:pressed {{ background: {PRESSED_BG}; color: {TEXT_SECOND}; }}
    QPushButton[variant="secondary"]:disabled {{ background: {SURFACE_ALT}; color: {TEXT_DISABLED}; }}
    QPushButton[variant="ghost"] {{
        background: transparent;
        color: {ACCENT};
        border: 1px solid transparent;
        padding: 0 8px;
        min-height: 26px;
        font-size: {FS_CAPTION}px;
    }}
    QPushButton[variant="ghost"]:hover {{ background: {ACCENT_TINT}; }}
    QPushButton[variant="ghost"]:pressed {{ background: {ACCENT_TINT_HOVER}; }}
    QPushButton[variant="ghost"]:disabled {{ color: {TEXT_DISABLED}; background: transparent; }}
    QPushButton[variant="icon"] {{
        background: transparent;
        border: 1px solid transparent;
        border-radius: {R_CONTROL}px;
        min-height: 30px;
        padding: 0;
    }}
    QPushButton[variant="icon"]:hover {{ background: {HOVER_BG}; }}
    QPushButton[variant="icon"]:pressed {{ background: {PRESSED_BG}; }}
    QPushButton:focus {{ outline: none; }}
    QPushButton[variant="primary"]:focus {{ border: 2px solid {ACCENT_PRESSED}; }}

    /* ================= 列表 / 日志 ================= */
    QListWidget {{
        background: transparent;
        border: none;
        outline: none;
        padding: 0;
    }}
    QListWidget::item {{ border: none; }}
    QListWidget::item:selected {{ background: transparent; }}

    /* ================= 滚动条（Fluent 细条） ================= */
    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: #CFCFCF;
        border-radius: 3px;
        min-height: 28px;
    }}
    QScrollBar::handle:vertical:hover {{ background: #A6A6A6; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    QScrollBar:horizontal {{ height: 0; }}

    /* ================= 进度条 ================= */
    QProgressBar {{
        background: #E9E9E9;
        border: none;
        border-radius: 2px;
        max-height: 4px;
        min-height: 4px;
    }}
    QProgressBar::chunk {{ background: {ACCENT}; border-radius: 2px; }}

    /* ================= 对话框 / 浮层 ================= */
    QMessageBox, QDialog {{ background: {CARD}; }}
    QMessageBox QLabel, QDialog QLabel {{ color: {TEXT}; font-size: {FS_BODY}px; }}
    QMessageBox QPushButton {{
        background: {CARD}; color: {TEXT}; border: 1px solid {STROKE_STRONG};
        border-radius: {R_CONTROL}px; min-width: 78px; min-height: 30px;
    }}
    QMessageBox QPushButton:hover {{ background: {HOVER_BG}; }}
    QMessageBox QPushButton:default {{
        background: {ACCENT}; color: #FFFFFF; border-color: {ACCENT}; font-weight: 600;
    }}
    QMessageBox QPushButton:default:hover {{ background: {ACCENT_HOVER}; }}

    QToolTip {{
        background: {CARD};
        color: {TEXT};
        border: 1px solid {STROKE_STRONG};
        border-radius: {R_CONTROL}px;
        padding: 6px 8px;
        font-size: {FS_CAPTION}px;
    }}
    """
