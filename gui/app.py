"""AI eye 主窗口（Fluent 2）。

交互主线：选文件/文件夹 → 选输出格式 → 一键转换 → 逐条记录 + 汇总。

界面只负责「让用户在动手前就知道会发生什么」：格式按引擎能力置灰、
被拦下的任务当场说明原因、失败的绝不留下半成品。所有转换逻辑仍在
``core.pipeline``，这里不重复实现任何业务规则。
"""

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QSettings, QSize, Qt, QThread, QUrl, Signal
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QIcon, QKeySequence,
                           QPainter, QPixmap, QShortcut)
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMainWindow, QMessageBox, QProgressBar, QSizePolicy,
                               QVBoxLayout, QWidget)

# 复用核心转换管线
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.pipeline import (Result, collect_inputs, convert_one,  # noqa: E402
                           plan_jobs, precheck)
from core.registry import (ALL_EXTENSIONS, FORMATS, capability,  # noqa: E402
                           describe_environment, resolve_format)

from . import theme as T
from .theme import repolish
from .widgets import (AppRoot, Card, DropOverlay, EmptyState, FluentSwitch,
                      FormatGrid, HoverRow, IconButton, IconTile, LogRow,
                      SectionHeader, make_button, paint_eye)

APP_NAME = "AI eye"
APP_TAGLINE = "文档格式转换"
SHORTCUT_HINT = "Ctrl+Enter 转换"
# 版本号的唯一来源是 aieye.py 的 __version__；这里只是兜底（单独跑本文件时用）。
APP_VERSION = "2.2.0"

# 设置行的图标：智能排版=字体大小、OCR=眼睛、覆盖=文档
_OPTION_GLYPH = {"smart": "\ue8e9", "eye": "\ue7b3", "doc": "\ue8a5"}


# ---------------------------------------------------------------- 转换线程
class ConvertWorker(QThread):
    """在后台线程逐条执行转换。

    单个任务失败只记录不中断——批量转换里最怕「一个坏文件让后面全不跑」。

    兜底逻辑是这段代码的重点：转换线程一旦异常退出而不发 ``finished_all``，
    界面会永久停在「转换中…」——按钮灰着、进度条不动，用户只能杀进程。
    所以循环外面必须 try/finally，任何情况下都要把结束信号发出去。
    """

    item_ready = Signal(str, str, str, str, str)   # status, src, dst, reason, preview
    finished_all = Signal(int, int, int, int)   # ok, skipped, error, total

    def __init__(self, jobs: list, force: bool, parent=None):
        super().__init__(parent)
        self.jobs = jobs
        self.force = force

    def run(self):
        ok = skipped = error = 0
        try:
            for job in self.jobs:
                try:
                    result = convert_one(job, force=self.force)
                except BaseException as exc:                      # 单任务兜底
                    result = Result(status="error", src=str(job.src),
                                    dst=str(job.dst),
                                    reason=f"内部错误：{type(exc).__name__}: {exc}")
                if result.status == "ok":
                    ok += 1
                elif result.status == "skipped":
                    skipped += 1
                else:
                    error += 1
                reason = result.reason
                if not reason and result.warnings:
                    reason = "；".join(result.warnings)
                self.item_ready.emit(result.status, str(job.src), str(job.dst),
                                     reason, result.preview)
        finally:
            # 即使上面抛了 BaseException，也要让界面恢复可用
            self.finished_all.emit(ok, skipped, error, len(self.jobs))


# ---------------------------------------------------------------- 主窗口
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} · {APP_TAGLINE}")
        self.resize(1080, 880)
        # 这里只是占位：真正的下限在布局搭完后按实际内容算（写死的最小值
        # 一旦小于布局需求，窗口就能被拖到把「覆盖同名文件」整行裁掉）。
        self.setMinimumSize(820, 680)
        self.setAcceptDrops(True)
        self.setWindowIcon(app_icon())

        self.paths: list[Path] = []
        self.worker: ConvertWorker | None = None
        self.found_files: list[Path] = []
        self.scan_notes: list[str] = []
        self._blocked_skip = 0
        self._blocked_error = 0
        # 结果预览缓存：双击记录时不必重读产物，docx/pdf 也能看到文字
        self._previews: dict[str, str] = {}
        self._preview_order: list[str] = []
        # 便携 / 自动化场景不落盘：测试与 CI 里必须关掉，否则上一次跑留下的
        # 格式会污染「默认选中 md」这类断言。
        self._persist = not os.environ.get("AIEYE_NO_SETTINGS")
        self._settings = QSettings("AIeye", "AIeye")

        root = AppRoot()
        self.setCentralWidget(root)

        outer = QVBoxLayout(root)
        outer.setContentsMargins(24, 18, 24, 16)
        outer.setSpacing(12)

        outer.addWidget(self._build_app_bar())

        source_card = self._build_source_card()
        options_card = self._build_options_card()
        log_card = self._build_log_card()
        self.source_card, self.options_card, self.log_card = (
            source_card, options_card, log_card)
        # 上两张卡片高度由内容决定，窗口压小时只让日志区收缩——
        # 否则格式卡片会被挤成一条细线（中文名整行被裁掉）。
        for c in (source_card, options_card):
            c.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        log_card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        log_card.setMinimumHeight(132)   # 表头 + 2 行记录，少于此会露出半行

        outer.addWidget(source_card)
        outer.addWidget(options_card)
        outer.addWidget(log_card, 1)
        outer.addWidget(self._build_footer())

        # 拖放高亮层：覆盖整个窗口，随窗口尺寸变化
        self.drop_overlay = DropOverlay(root)
        root.installEventFilter(self)

        self._min_locked = False

        self._restore_settings()
        self._install_shortcuts()
        self._update_hint()
        self._show_empty(True)   # 初始空态：表头不该单独露在外面
        self._set_busy(False)

    # -------------------------------------------------- 应用栏
    def _build_app_bar(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(2, 2, 2, 2)
        row.setSpacing(12)

        row.addWidget(IconTile(42))

        col = QVBoxLayout()
        col.setSpacing(1)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title = QLabel(APP_NAME)
        title.setObjectName("appTitle")
        title_row.addWidget(title)
        self.version_tag = QLabel(f"v{APP_VERSION}")
        self.version_tag.setObjectName("versionTag")
        title_row.addWidget(self.version_tag, 0, Qt.AlignVCenter)
        title_row.addStretch(1)

        sub = QLabel(f"{APP_TAGLINE}　·　{describe_environment()}")
        sub.setObjectName("appSubtitle")

        col.addLayout(title_row)
        col.addWidget(sub)
        row.addLayout(col, 1)

        self.btn_about = IconButton("info", "关于 AI eye")
        self.btn_about.clicked.connect(self._show_about)
        row.addWidget(self.btn_about, 0, Qt.AlignVCenter)
        return bar

    def _show_about(self):
        from core.registry import detect_engines

        e = detect_engines()
        engines = [f"{k}：{'可用' if v else '缺失'}" for k, v in (
            ("浏览器（PDF 输出）", bool(e.browser)),
            ("pandoc 兜底", bool(e.pandoc)),
        )]
        QMessageBox.about(
            self, f"关于 {APP_NAME}",
            f"<b>{APP_NAME}</b> v{APP_VERSION}<br>"
            f"{APP_TAGLINE}：txt · md · html · csv · xlsx · docx · pdf<br><br>"
            "全部转换在本机完成，文件不会上传到任何服务器。<br><br>"
            f"{'<br>'.join(engines)}<br><br>"
            f"快捷键：Ctrl+O 选文件 · Ctrl+Shift+O 选文件夹 · {SHORTCUT_HINT}")

    # -------------------------------------------------- 卡片：源文件
    def _build_source_card(self) -> Card:
        card = Card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 12, 18, 14)
        lay.setSpacing(8)

        head = QHBoxLayout()
        head.addWidget(SectionHeader("folder_open", "源文件"))
        head.addStretch(1)
        self.hint = QLabel()
        self.hint.setObjectName("hint")
        self.hint.setProperty("state", "idle")
        head.addWidget(self.hint)
        lay.addLayout(head)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("粘贴文件或文件夹路径，也可直接把文件拖进窗口")
        self.path_edit.setClearButtonEnabled(True)
        self.path_edit.returnPressed.connect(self._apply_typed_path)
        # QLineEdit 默认接受拖放，会把文件 URL 变成一段路径文字插进框里，
        # 而不是让窗口收到 drop。这里拦下来自己处理。
        self.path_edit.installEventFilter(self)

        self.btn_files = make_button("选择文件", "secondary")
        self.btn_folder = make_button("选择文件夹", "secondary")
        self.btn_files.clicked.connect(self._pick_files)
        self.btn_folder.clicked.connect(self._pick_folder)

        row.addWidget(self.path_edit, 1)
        row.addWidget(self.btn_files)
        row.addWidget(self.btn_folder)
        lay.addLayout(row)
        return card

    # -------------------------------------------------- 卡片：格式与选项
    def _build_options_card(self) -> Card:
        card = Card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 12, 18, 10)
        lay.setSpacing(4)

        fmt_head = QHBoxLayout()
        fmt_head.addWidget(SectionHeader("convert", "输出格式"))
        fmt_head.addStretch(1)
        self.fmt_hint = QLabel()
        self.fmt_hint.setObjectName("cardHint")
        fmt_head.addWidget(self.fmt_hint)
        lay.addLayout(fmt_head)
        lay.addSpacing(4)

        self.chips = FormatGrid()
        self.chips.changed.connect(self._on_format_changed)
        lay.addWidget(self.chips)

        lay.addSpacing(8)
        lay.addWidget(self._divider())

        self.sw_smart = FluentSwitch(checked=True)
        lay.addWidget(self._option_row(
            "smart", "智能排版", "纯文本输入时首行作标题、识别列表；关闭则逐字保留",
            self.sw_smart))

        lay.addWidget(self._divider())

        self.sw_ocr = FluentSwitch(checked=False)
        lay.addWidget(self._option_row(
            "eye", "OCR 识别扫描件", "PDF 没有文字层（纯图片）时识别图片里的文字；较慢，每页约几秒",
            self.sw_ocr))

        lay.addWidget(self._divider())

        self.sw_overwrite = FluentSwitch(checked=False)
        lay.addWidget(self._option_row(
            "doc", "覆盖同名文件", "关闭时目标已存在则自动跳过，不会覆盖已有内容",
            self.sw_overwrite))

        self._on_format_changed(self.chips.current_format())
        return card

    def _on_format_changed(self, code: str):
        fmt = FORMATS.get(code)
        if fmt is None:
            return
        cap = capability(code)
        level = {"●": "高", "◐": "中", "○": "低"}.get(cap.fidelity_write, "中")
        self.fmt_hint.setText(f"{cap.engine_write} · 保真{level}")
        self.fmt_hint.setToolTip(
            f"{fmt.label} · {cap.engine_write} · 保真{level}"
            + (f"\n{cap.note}" if cap.note else ""))

    @staticmethod
    def _divider() -> QFrame:
        line = QFrame()
        line.setFixedHeight(1)
        line.setObjectName("hairline")
        return line

    @staticmethod
    def _option_row(glyph: str, title: str, desc: str, switch: FluentSwitch) -> HoverRow:
        w = HoverRow()
        w.setMinimumHeight(50)  # 防止窗口压窄时标题与描述重叠
        row = QHBoxLayout(w)
        row.setContentsMargins(8, 5, 8, 5)
        row.setSpacing(12)

        icon = QLabel()
        icon.setFont(QFont(T.ICON_FAMILY))
        icon.setStyleSheet(f"color: {T.TEXT_SECOND}; background: transparent;")
        icon.setText(_OPTION_GLYPH.get(glyph, _OPTION_GLYPH["doc"]))
        icon.setFixedWidth(18)
        icon.setAlignment(Qt.AlignCenter)
        row.addWidget(icon, 0, Qt.AlignVCenter)

        col = QVBoxLayout()
        col.setSpacing(1)
        lab = QLabel(title)
        lab.setObjectName("rowTitle")
        desc_lab = QLabel(desc)
        desc_lab.setObjectName("rowDesc")
        col.addWidget(lab)
        col.addWidget(desc_lab)

        row.addLayout(col, 1)
        row.addWidget(switch, 0, Qt.AlignVCenter)
        return w

    # -------------------------------------------------- 卡片：日志
    def _build_log_card(self) -> Card:
        card = Card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 13, 14, 8)
        lay.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(SectionHeader("doc", "转换记录"))
        self.summary = QLabel("就绪")
        self.summary.setObjectName("summary")
        self.summary.setProperty("state", "idle")
        head.addWidget(self.summary)
        head.addStretch(1)

        # 「全部跳过」时出现的补救入口：比弹框温和，也不用勾开关再点一次
        self.btn_retry = make_button("覆盖并重转", "ghost")
        self.btn_retry.setFixedHeight(24)
        self.btn_retry.setVisible(False)
        self.btn_retry.clicked.connect(self._retry_with_overwrite)
        head.addWidget(self.btn_retry)

        self.btn_clear = make_button("清空", "ghost")
        self.btn_clear.setFixedHeight(24)
        self.btn_clear.clicked.connect(self._clear_log)
        head.addWidget(self.btn_clear)
        lay.addLayout(head)

        # 列名表头：与 LogRow 的边距/伸缩比保持一致，否则文字对不齐
        self.log_header = QWidget()
        hrow = QHBoxLayout(self.log_header)
        hrow.setContentsMargins(8, 0, 10, 2)
        hrow.setSpacing(10)
        spacer = QWidget()
        spacer.setFixedSize(42, 1)
        hrow.addWidget(spacer)
        c1 = QLabel("源文件")
        c1.setObjectName("colHead")
        hrow.addWidget(c1, 5)
        c2 = QLabel("目标 / 说明")
        c2.setObjectName("colHead")
        c2.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        c2.setMinimumWidth(80)
        hrow.addWidget(c2, 4)
        lay.addWidget(self.log_header)

        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(0)

        self.log = QListWidget()
        self.log.setSelectionMode(QListWidget.NoSelection)
        self.log.setFocusPolicy(Qt.NoFocus)
        self.log.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.log.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.log.setSpacing(0)
        self.log.setUniformItemSizes(True)
        self.log.setVisible(False)  # 无记录时让空状态文案占满区域
        body_lay.addWidget(self.log, 1)

        self.empty = EmptyState(
            "convert", "转换记录会显示在这里",
            "选好文件与输出格式，点击右下角「一键转换」；完成后双击记录可查看并复制识别结果")
        body_lay.addWidget(self.empty, 1)

        lay.addWidget(body, 1)
        return card

    # -------------------------------------------------- 底部操作区
    def _build_footer(self) -> QWidget:
        wrap = QWidget()
        v = QVBoxLayout(wrap)
        v.setContentsMargins(2, 4, 2, 0)
        v.setSpacing(8)

        # 进度条独占一行通栏：和状态文字挤在一行时，那条蓝线看起来更像
        # 一根多余的分隔线，用户读不出「进度」的含义。
        # 不放分隔线：空闲时进度条不可见，分隔线会变成一条孤零零的横线。
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(False)  # 空闲时不占用视觉重量
        v.addWidget(self.progress)

        row = QHBoxLayout()
        row.setSpacing(14)

        self.status = QLabel("就绪")
        self.status.setObjectName("cardHint")
        row.addWidget(self.status, 0, Qt.AlignVCenter)
        row.addStretch(1)

        self.progress_text = QLabel("")
        self.progress_text.setObjectName("cardHint")
        self.progress_text.setMinimumWidth(52)
        self.progress_text.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self.progress_text)

        self.btn_convert = make_button("一键转换", "primary")
        self.btn_convert.setFixedWidth(148)
        self.btn_convert.setFixedHeight(38)
        self.btn_convert.setShortcut("Ctrl+Return")
        self.btn_convert.setToolTip(f"开始转换（{SHORTCUT_HINT}）")
        self.btn_convert.clicked.connect(self._start_convert)
        row.addWidget(self.btn_convert)
        v.addLayout(row)
        return wrap

    # -------------------------------------------------- 快捷键
    def _install_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+O"), self, activated=self._pick_files)
        QShortcut(QKeySequence("Ctrl+Shift+O"), self, activated=self._pick_folder)
        QShortcut(QKeySequence("F5"), self, activated=lambda: self._rescan())

    # -------------------------------------------------- 配置记忆
    def _restore_settings(self):
        """恢复上次的格式与开关。用户第二次打开时不该重设一遍。"""
        if not self._persist:
            return
        fmt = str(self._settings.value("format", "md"))
        if fmt in FORMATS:
            self.chips.set_format(fmt)
            self._on_format_changed(fmt)
        for key, switch in (("smart", self.sw_smart), ("ocr", self.sw_ocr),
                            ("overwrite", self.sw_overwrite)):
            val = self._settings.value(key, None)
            if val is not None:
                switch.setChecked(str(val).lower() in ("true", "1"), animate=False)

    def _save_settings(self):
        if not self._persist:
            return
        self._settings.setValue("format", self.chips.current_format())
        self._settings.setValue("smart", self.sw_smart.isChecked())
        self._settings.setValue("ocr", self.sw_ocr.isChecked())
        self._settings.setValue("overwrite", self.sw_overwrite.isChecked())

    def closeEvent(self, e):
        # 线程还在跑时直接关窗，QThread 会在运行中被析构，轻则警告重则崩溃。
        # 转换可能包含 OCR，短等待解决不了问题，干脆先不让关。
        if self.worker is not None and self.worker.isRunning():
            self.status.setText("正在转换，完成后再关闭窗口")
            e.ignore()
            return
        self._save_settings()
        super().closeEvent(e)

    # -------------------------------------------------- 选择来源
    def _pick_files(self):
        exts = " ".join(f"*{e}" for e in ALL_EXTENSIONS)
        start = str(self._settings.value("last_dir", ""))
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择要转换的文件", start,
            f"所有支持的格式 ({exts});;"
            "文本与标记 (*.txt *.md *.html);;"
            "办公文档 (*.docx *.xlsx);;"
            "表格 (*.csv *.xlsx);;"
            "所有文件 (*.*)")
        if files:
            self._settings.setValue("last_dir", str(Path(files[0]).parent))
            self._set_paths([Path(f) for f in files])

    def _pick_folder(self):
        start = str(self._settings.value("last_dir", ""))
        d = QFileDialog.getExistingDirectory(self, "选择包含待转换文件的文件夹", start)
        if d:
            self._settings.setValue("last_dir", d)
            self._set_paths([Path(d)])

    def _apply_typed_path(self):
        raw = self.path_edit.text().strip().strip('"')
        if raw:
            self._set_paths([Path(raw)])

    def _set_paths(self, paths: list[Path]):
        self.paths = paths
        if not paths:
            self.path_edit.clear()
        elif len(paths) == 1:
            self.path_edit.setText(str(paths[0]))
        else:
            self.path_edit.setText(f"{paths[0].parent}（共 {len(paths)} 个文件）")
        self._rescan(pick_default_target=True)

    def _absorb_typed_path(self) -> bool:
        """把输入框里的路径并入待转换列表，返回是否有变化。

        用户很可能粘贴路径后直接点「一键转换」而不按回车——此时 paths
        还是空的，不处理就会被判成「尚未选择文件」，用户看到的就是
        「怎么点都没反应」。
        """
        raw = self.path_edit.text().strip().strip('"').strip()
        if not raw:
            return False
        probe = Path(raw)
        try:
            exists = probe.exists()
        except OSError:
            exists = False
        if not exists:
            return False
        if any(str(p) == str(probe) for p in self.paths):
            return False
        self.paths = [probe]
        self._rescan(pick_default_target=True)
        return True

    def _rescan(self, pick_default_target: bool = False):
        """扫描待转换文件并刷新提示。"""
        self.found_files: list[Path] = []
        self.scan_notes = []
        if self.paths:
            self.found_files, self.scan_notes = collect_inputs(self.paths)
        if pick_default_target and self.found_files:
            self._suggest_target()
        self._update_hint()

    def _suggest_target(self):
        """源与目标撞了的时候自动换一个——避免用户把 a.md 转成 a.md 白忙一场。"""
        suffix = {p.suffix.lower() for p in self.found_files}
        if len(suffix) != 1:
            return
        src = resolve_format(next(iter(suffix)).lstrip("."))
        if src is not None and src.code == self.chips.current_format():
            # set_format 会自己发出 changed，无需再手动刷新提示
            self.chips.set_format("txt" if src.code != "txt" else "md")

    def _set_hint(self, text: str, state: str):
        self.hint.setText(text)
        if self.hint.property("state") != state:
            self.hint.setProperty("state", state)
            repolish(self.hint)

    def _update_hint(self):
        if not self.paths:
            self._set_hint("尚未选择文件", "idle")
            self.status.setText("就绪")
            return
        if not self.found_files:
            self._set_hint("未找到支持的文件", "error")
            self.status.setText(self.scan_notes[0] if self.scan_notes else "未找到支持的文件")
            return

        counter: dict[str, int] = {}
        for p in self.found_files:
            fmt = resolve_format(p.suffix.lstrip("."))
            key = fmt.code if fmt else "其他"
            counter[key] = counter.get(key, 0) + 1
        detail = " · ".join(f"{k} {v}" for k, v in
                            sorted(counter.items(), key=lambda kv: -kv[1]))
        self._set_hint(f"找到 {len(self.found_files)} 个文件（{detail}）", "ok")
        self.status.setText(f"{len(self.found_files)} 个文件待转换 · {SHORTCUT_HINT}")

    def _set_summary(self, text: str, state: str = "idle", tip: str = ""):
        self.summary.setText(text)
        self.summary.setToolTip(tip)
        if self.summary.property("state") != state:
            self.summary.setProperty("state", state)
            repolish(self.summary)

    def _check_target_supported(self) -> bool:
        """目标格式在当前环境不可写时，提前拦下并说明原因。"""
        code = self.chips.current_format()
        cap = capability(code)
        if cap.can_write:
            return True
        QMessageBox.warning(
            self, "当前环境不支持",
            f"无法生成 {FORMATS[code].label}：{cap.note or '缺少必要组件'}。\n\n"
            "请换一个输出格式，或按 README 的说明安装所需组件。")
        return False

    # -------------------------------------------------- 拖拽支持
    def eventFilter(self, obj, e):
        """让文件拖放穿透输入框落到窗口的统一处理里；同时维护拖放高亮层。"""
        if obj is self.path_edit:
            if e.type() == QEvent.DragLeave:
                # 输入框自己接收了拖放事件，窗口的 dragLeaveEvent 就不会触发；
                # 不在这里收掉高亮层，遮罩会一直盖在界面上。
                self._show_drop_overlay(False)
                return False
            if e.type() in (QEvent.DragEnter, QEvent.Drop) and e.mimeData().hasUrls():
                if e.type() == QEvent.DragEnter:
                    e.acceptProposedAction()
                    self._show_drop_overlay(True)
                else:
                    self._show_drop_overlay(False)
                    paths = [Path(u.toLocalFile()) for u in e.mimeData().urls()
                             if u.isLocalFile()]
                    if paths:
                        self._set_paths(paths)
                        e.acceptProposedAction()
                return True
        if obj is self.centralWidget() and e.type() == QEvent.Resize:
            self.drop_overlay.setGeometry(self.centralWidget().rect())
        return super().eventFilter(obj, e)

    def _show_drop_overlay(self, on: bool):
        if not hasattr(self, "drop_overlay"):
            return
        self.drop_overlay.setGeometry(self.centralWidget().rect())
        self.drop_overlay.setVisible(on)
        if on:
            self.drop_overlay.raise_()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._show_drop_overlay(True)

    def dragLeaveEvent(self, e):
        self._show_drop_overlay(False)

    def dropEvent(self, e):
        self._show_drop_overlay(False)
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self._set_paths(paths)
            e.acceptProposedAction()

    # -------------------------------------------------- 日志区
    def _show_empty(self, empty: bool):
        self.log.setVisible(not empty)
        self.empty.setVisible(empty)
        self.log_header.setVisible(not empty)
        if empty:
            self.btn_retry.setVisible(False)

    def _clear_log(self):
        self.log.clear()
        self._show_empty(True)
        self._blocked_skip = self._blocked_error = 0
        self._set_summary("就绪")
        self.progress.setVisible(False)
        self.progress_text.setText("")

    # -------------------------------------------------- 结果查看
    def _remember_preview(self, dst: str, src: str, preview: str) -> None:
        """缓存最近若干条结果文本。

        整本扫描书的预览能到 2MB，批量转几百个文件时全留着会吃掉几百 MB。
        只保留最近 20 条，够用户回看刚才那几次。
        """
        key = dst or src
        self._previews[key] = preview
        self._preview_order.append(key)
        while len(self._preview_order) > 20:
            old = self._preview_order.pop(0)
            self._previews.pop(old, None)

    def _view_result(self, path: str) -> None:
        """双击记录：打开结果查看器。

        没有缓存时（跳过、失败、或超出 20 条）退回「打开输出位置」，
        总比什么都不发生强。
        """
        content = self._previews.get(path, "")
        if not content:
            target = Path(path)
            if target.exists() and target.suffix.lower() in (".md", ".txt", ".html"):
                try:
                    content = target.read_text("utf-8", errors="replace")
                except OSError:
                    content = ""
        if not content:
            self._reveal(path)
            return
        from .viewer import ResultViewer
        viewer = ResultViewer(src_path=path, dst_path=path, content=content,
                              parent=self)
        viewer.setAttribute(Qt.WA_DeleteOnClose, True)
        viewer.exported.connect(lambda p: self._on_item(
            "info", "已另存为", p, "", advance=False))
        viewer.show()
        viewer.raise_()
        viewer.activateWindow()

    def _reveal(self, path: str):
        """双击记录：在资源管理器里定位到该文件（不存在则打开所在目录）。"""
        p = Path(path)
        try:
            if p.exists():
                subprocess.Popen(["explorer", "/select,", str(p)])
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(p.parent)))
        except OSError:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(p.parent)))

    def _retry_with_overwrite(self):
        """全部跳过时的一键补救：打开覆盖开关并重跑同一批文件。"""
        self.sw_overwrite.setChecked(True)
        self._start_convert()

    # -------------------------------------------------- 状态切换
    def _set_busy(self, busy: bool):
        for w in (self.btn_files, self.btn_folder, self.btn_convert, self.path_edit,
                  self.chips, self.sw_smart, self.sw_ocr, self.sw_overwrite):
            w.setEnabled(not busy)
        self.btn_convert.setText("转换中…" if busy else "一键转换")
        self.btn_clear.setEnabled(not busy)
        if not busy:
            self._update_hint()

    # -------------------------------------------------- 转换
    def _start_convert(self):
        # 先吸收输入框内容：粘贴路径后直接点按钮是最常见的操作路径
        self._absorb_typed_path()
        if not self.paths:
            QMessageBox.warning(self, "提示", "请先选择文件或文件夹")
            return
        self._rescan()
        if not self.found_files:
            QMessageBox.warning(
                self, "没有可转换的文件",
                "所选路径中没有找到支持的格式。\n\n"
                f"支持：{'、'.join(f.extensions[0] for f in FORMATS.values())}")
            return
        if not self._check_target_supported():
            return

        dst_code = self.chips.current_format()
        smart = self.sw_smart.isChecked()
        force = self.sw_overwrite.isChecked()
        use_ocr = self.sw_ocr.isChecked()

        # 图片没有文字层，OCR 是唯一途径，不是可选增强：勾没勾都要开
        has_image = any((f.suffix.lower() in FORMATS["image"].extensions)
                        for f in self.found_files)
        if has_image:
            use_ocr = True

        # 规划与预检：先算出「哪些会失败、为什么」，动手前就告诉用户
        jobs = plan_jobs(self.found_files, dst_code)
        for job in jobs:
            job.options["smart"] = smart
            job.options["ocr"] = use_ocr
            if job.src_format == "image":
                job.options["ocr"] = True
            if not smart:
                job.options["verbatim"] = True
        ready, blocked, _ = precheck(jobs, force=force)

        # 被拦下的任务不进线程，但必须计入最终汇总——否则「成功 3」会和
        # 日志里实际写着的 5 条记录对不上，用户会以为漏转了。
        self._blocked_skip = sum(
            1 for j in blocked
            if str(j.options.get("_blocked_by", "")).startswith("目标文件已存在"))
        self._blocked_error = len(blocked) - self._blocked_skip
        self.btn_retry.setVisible(False)

        self.log.clear()
        self._show_empty(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, len(jobs))
        self.progress.setValue(0)
        self.progress_text.setText(f"0 / {len(jobs)}" if jobs else "")
        self._set_summary(f"共 {len(jobs)} 个文件 → {dst_code}")

        # 被拦下的当场落到日志，不必占用转换线程
        for job in blocked:
            reason = job.options.get("_blocked_by", "")
            status = "skipped" if reason.startswith("目标文件已存在") else "error"
            self._on_item(status, str(job.src), str(job.dst), reason)

        if not ready:
            # blocked 已在日志里，这里只补汇总
            self._on_finished(0, 0, 0, len(jobs))
            return

        # OCR 大文件在单个任务里要跑几分钟，进度条按文件计数不会动——
        # 不提前说一句，用户会以为程序卡死了
        if use_ocr and any(j.src_format in ("pdf", "image") for j in ready):
            n_img = sum(1 for j in ready if j.src_format == "image")
            if n_img:
                tip = (f"{n_img} 个图片文件必须经过文字识别（每张约 3～6 秒）；"
                       "识别后可在记录里双击查看并复制文字")
            else:
                tip = "扫描件每页约 1～3 秒，大文件需要几分钟，请耐心等待"
            self._on_item("info", "OCR 已开启", tip, "", advance=False)

        self._set_busy(True)
        self.worker = ConvertWorker(ready, force)
        self.worker.item_ready.connect(self._on_item)
        self.worker.finished_all.connect(self._on_finished)
        self.worker.start()

    def _on_item(self, kind: str, src: str, dst: str, reason: str,
                 preview: str = "", advance: bool = True):
        item = QListWidgetItem()
        item.setSizeHint(QSize(0, 32))
        row = LogRow(kind, src, dst, reason)
        # 双击看结果，右键看在文件夹中显示：两个动作都要一步可达，所以
        # 双击不能再兼任「打开位置」。
        row.activated.connect(self._view_result)
        row.reveal_requested.connect(self._reveal)
        if preview and kind == "ok":
            self._remember_preview(dst, src, preview)
        self.log.addItem(item)
        self.log.setItemWidget(item, row)
        self.log.scrollToBottom()
        if not advance:
            return                    # 提示行不是任务，不动进度条
        value = min(self.progress.value() + 1, self.progress.maximum())
        self.progress.setValue(value)
        if self.progress.maximum():
            self.progress_text.setText(f"{value} / {self.progress.maximum()}")

    def _on_finished(self, ok: int, skipped: int, error: int, total: int):
        self._set_busy(False)
        # 线程只统计「真正送进去的任务」，预检拦下的补在这里
        skipped += getattr(self, "_blocked_skip", 0)
        error += getattr(self, "_blocked_error", 0)

        # 「全部跳过」在用户眼里和「失败」很像，文案必须自己说清楚下一步
        if skipped and not ok and not error:
            self._set_summary(
                f"已跳过 {skipped} 个：目标文件都已存在", "warn",
                "点右侧「覆盖并重转」即可覆盖旧文件重新生成。")
            self.btn_retry.setVisible(True)
        else:
            self._set_summary(
                f"成功 {ok} · 跳过 {skipped} · 失败 {error}",
                "error" if error else ("warn" if skipped else "ok"),
                "部分目标已存在，未覆盖。开启「覆盖同名文件」可重新生成。" if skipped else "")

        # 转换结束：进度条收到满值后再淡出，避免停在半路像是没跑完
        if total:
            self.progress.setValue(self.progress.maximum())
        self.progress_text.setText("")
        self.progress.setVisible(False)

        # QThread 实例在每次转换后都要丢弃，否则反复转换会攒下一堆已结束
        # 的线程对象，长时间使用后析构顺序不可控。
        worker, self.worker = self.worker, None
        if worker is not None:
            worker.deleteLater()

        # 只有失败才打断用户：全部成功时界面上已有汇总，弹框只会碍事
        if error:
            QMessageBox.warning(self, "部分文件失败",
                                f"成功 {ok}，跳过 {skipped}，失败 {error}。\n"
                                "失败原因已列在转换记录中，原件未被改动。")

    # -------------------------------------------------- 布局事件
    def showEvent(self, e):
        """首次显示后锁定最小尺寸。

        构造期间 ``sizeHint()`` 还没算上样式表的字号与边框，量出来的高度偏小；
        一旦据此设成窗口下限，用户把窗口拖到最小时，Qt 会直接把「覆盖同名
        文件」整行裁掉而不报错。这里等第一次真正显示、样式生效后再量一次。
        """
        super().showEvent(e)
        if getattr(self, "_min_locked", True):
            return
        self._min_locked = True
        for c in (self.source_card, self.options_card):
            c.setMinimumHeight(c.sizeHint().height())
        lay = self.centralWidget().layout()
        lay.activate()
        need = lay.minimumSize().height() + 8
        self.setMinimumSize(820, max(660, need))
        if self.height() < self.minimumHeight():
            self.resize(self.width(), self.minimumHeight())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "drop_overlay"):
            self.drop_overlay.setGeometry(self.centralWidget().rect())


# ---------------------------------------------------------------- 入口
def app_icon() -> QIcon:
    """运行时图标：与 IconTile 用的是同一套眼睛图形。"""
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    from PySide6.QtGui import QLinearGradient
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    grad = QLinearGradient(0, 0, 64, 64)
    grad.setColorAt(0.0, QColor("#2B88D8"))
    grad.setColorAt(1.0, QColor(T.ACCENT_PRESSED))
    p.setPen(Qt.NoPen)
    p.setBrush(grad)
    p.drawRoundedRect(2, 2, 60, 60, 15, 15)
    from PySide6.QtCore import QRectF
    paint_eye(p, QRectF(10, 10, 44, 44), QColor("#FFFFFF"), 4.2)
    p.end()
    return QIcon(pm)


def run() -> int:
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName("AIeye")
    T.init_fonts()  # 字体探测需在 QApplication 之后
    app.setFont(QFont(T.UI_FAMILY, 9))
    app.setStyleSheet(T.qss())
    app.setWindowIcon(app_icon())

    win = MainWindow()
    win.show()
    return app.exec()
