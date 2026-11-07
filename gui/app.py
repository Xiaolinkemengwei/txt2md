"""Fluent Design 主窗口（2.0）。

交互主线：选文件/文件夹 → 选目标格式 → 一键转换 → 逐条日志 + 汇总。
转换执行交给 core.pipeline，界面只负责「让用户在动手前就知道会发生什么」：
格式选项按引擎能力置灰、被拦下的任务当场说明原因、失败的绝不留下半成品。
"""

import sys
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QThread, Signal
from PySide6.QtGui import QFont, QIcon, QPixmap
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
                               QMessageBox, QProgressBar, QSizePolicy, QVBoxLayout,
                               QWidget)

# 复用核心转换管线
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.pipeline import collect_inputs, convert_one, plan_jobs, precheck  # noqa: E402
from core.registry import (ALL_EXTENSIONS, FORMATS, capability,  # noqa: E402
                           describe_environment, resolve_format)

from . import theme as T
from .widgets import (Card, FluentSwitch, FormatChips, HoverRow, IconTile, LogRow,
                      make_button)


# ---------------------------------------------------------------- 转换线程
class ConvertWorker(QThread):
    """在后台线程逐条执行转换。

    单个任务失败只记录不中断——批量转换里最怕「一个坏文件让后面全不跑」。
    """

    item_ready = Signal(str, str, str, str)   # status, src, dst, reason
    finished_all = Signal(int, int, int, int)  # ok, skipped, error, total

    def __init__(self, jobs: list, force: bool, parent=None):
        super().__init__(parent)
        self.jobs = jobs
        self.force = force

    def run(self):
        ok = skipped = error = 0
        for job in self.jobs:
            result = convert_one(job, force=self.force)
            if result.status == "ok":
                ok += 1
            elif result.status == "skipped":
                skipped += 1
            else:
                error += 1
            reason = result.reason
            if not reason and result.warnings:
                reason = "；".join(result.warnings)
            self.item_ready.emit(result.status, str(job.src), str(job.dst), reason)
        self.finished_all.emit(ok, skipped, error, len(self.jobs))


# ---------------------------------------------------------------- 主窗口
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("文档格式转换")
        self.resize(1060, 880)
        self.setMinimumSize(720, 660)
        self.setAcceptDrops(True)

        self.paths: list[Path] = []
        self.worker: ConvertWorker | None = None
        self.found_files: list[Path] = []
        self.scan_notes: list[str] = []

        root = QWidget()
        root.setObjectName("appRoot")
        root.setAttribute(Qt.WA_StyledBackground, True)
        self.setCentralWidget(root)

        outer = QVBoxLayout(root)
        outer.setContentsMargins(26, 22, 26, 20)
        outer.setSpacing(16)

        outer.addLayout(self._build_header())
        outer.addWidget(self._build_source_card())
        outer.addWidget(self._build_options_card())
        outer.addWidget(self._build_log_card(), 1)
        outer.addLayout(self._build_footer())

        self._update_hint()
        self._set_busy(False)

    # -------------------------------------------------- 头部
    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(16)
        row.addWidget(IconTile("doc", 46))

        col = QVBoxLayout()
        col.setSpacing(3)
        title = QLabel("文档格式转换")
        title.setObjectName("appTitle")
        sub = QLabel(f"txt · md · html · csv · xlsx · docx · pdf 互转　|　{describe_environment()}")
        sub.setObjectName("subtitle")
        col.addWidget(title)
        col.addWidget(sub)
        row.addLayout(col)
        row.addStretch(1)
        return row

    # -------------------------------------------------- 卡片：源文件
    def _build_source_card(self) -> Card:
        card = Card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 14, 18, 16)
        lay.setSpacing(10)

        head = QHBoxLayout()
        t = QLabel("源文件")
        t.setObjectName("cardTitle")
        self.hint = QLabel()
        self.hint.setObjectName("cardHint")
        head.addWidget(t)
        head.addStretch(1)
        head.addWidget(self.hint)
        lay.addLayout(head)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("粘贴文件或文件夹路径，也可直接拖拽到窗口")
        self.path_edit.setClearButtonEnabled(True)
        self.path_edit.returnPressed.connect(self._apply_typed_path)

        self.btn_files = make_button("选择文件", "secondary")
        self.btn_folder = make_button("选择文件夹", "secondary")
        self.btn_files.clicked.connect(self._pick_files)
        self.btn_folder.clicked.connect(self._pick_folder)

        row.addWidget(self.path_edit, 1)
        row.addWidget(self.btn_files)
        row.addWidget(self.btn_folder)
        lay.addLayout(row)
        return card

    # -------------------------------------------------- 卡片：选项
    def _build_options_card(self) -> Card:
        card = Card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(20, 16, 20, 14)
        lay.setSpacing(4)

        t = QLabel("转换选项")
        t.setObjectName("cardTitle")
        lay.addWidget(t)
        lay.addSpacing(6)

        # -- 目标格式
        fmt_row = QWidget()
        fmt_lay = QVBoxLayout(fmt_row)
        fmt_lay.setContentsMargins(10, 4, 10, 6)
        fmt_lay.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(10)
        lab = QLabel("目标格式")
        lab.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {T.TEXT};")
        self.fmt_hint = QLabel()
        self.fmt_hint.setStyleSheet(f"font-size: 12px; color: {T.TEXT_SECOND};")
        top.addWidget(lab)
        top.addStretch(1)
        top.addWidget(self.fmt_hint)
        fmt_lay.addLayout(top)

        self.chips = FormatChips()
        self.chips.changed.connect(self._on_format_changed)
        fmt_lay.addWidget(self.chips)
        lay.addWidget(fmt_row)
        self._on_format_changed(self.chips.current_format())

        lay.addWidget(self._divider())

        self.sw_smart = FluentSwitch(checked=True)
        lay.addWidget(self._option_row(
            "智能排版", "纯文本输入时首行作标题、识别列表；关闭则逐字保留", self.sw_smart))

        lay.addWidget(self._divider())

        self.sw_overwrite = FluentSwitch(checked=False)
        lay.addWidget(self._option_row(
            "覆盖同名文件", "关闭时目标已存在则自动跳过，不会覆盖已有内容", self.sw_overwrite))
        return card

    def _on_format_changed(self, code: str):
        fmt = FORMATS.get(code)
        if fmt is None:
            return
        cap = capability(code)
        # 保真度用文字而非 ●◐○：符号在界面字体下辨识度差
        level = {"●": "高", "◐": "中", "○": "低"}.get(cap.fidelity_write, "")
        note = f"　{cap.note}" if cap.note else ""
        self.fmt_hint.setText(
            f"{fmt.label}　{cap.engine_write}　保真{level}{note}")

    @staticmethod
    def _divider() -> QFrame:
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background: #F0F0F0; border: none;")
        return line

    @staticmethod
    def _option_row(title: str, desc: str, switch: FluentSwitch) -> HoverRow:
        w = HoverRow()
        w.setMinimumHeight(48)  # 防止窗口压窄时标题与描述重叠
        row = QHBoxLayout(w)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(12)

        col = QVBoxLayout()
        col.setSpacing(2)
        lab = QLabel(title)
        lab.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {T.TEXT};")
        desc_lab = QLabel(desc)
        desc_lab.setStyleSheet(f"font-size: 12px; color: {T.TEXT_SECOND};")
        col.addWidget(lab)
        col.addWidget(desc_lab)

        row.addLayout(col)
        row.addStretch(1)
        row.addWidget(switch, 0, Qt.AlignVCenter)
        return w

    # -------------------------------------------------- 卡片：日志
    def _build_log_card(self) -> Card:
        card = Card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 14, 14, 12)
        lay.setSpacing(8)

        head = QHBoxLayout()
        t = QLabel("转换日志")
        t.setObjectName("cardTitle")
        self.summary = QLabel("就绪")
        self.summary.setObjectName("cardHint")
        self.btn_clear = make_button("清空", "ghost")
        self.btn_clear.setFixedHeight(26)
        self.btn_clear.clicked.connect(self._clear_log)
        head.addWidget(t)
        head.addSpacing(8)
        head.addWidget(self.summary)
        head.addStretch(1)
        head.addWidget(self.btn_clear)
        lay.addLayout(head)

        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)

        self.log = QListWidget()
        self.log.setSelectionMode(QListWidget.NoSelection)
        self.log.setFocusPolicy(Qt.NoFocus)
        self.log.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.log.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.log.setSpacing(0)
        self.log.setUniformItemSizes(True)
        self.log.setVisible(False)  # 无记录时让空状态文案占满区域
        body_lay.addWidget(self.log, 1)

        self.empty = QLabel("转换记录会显示在这里")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.empty.setStyleSheet(f"color: {T.TEXT_TERTIARY}; font-size: 12px;")
        body_lay.addWidget(self.empty, 1)

        lay.addWidget(body, 1)
        return card

    # -------------------------------------------------- 底部操作区
    def _build_footer(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(14)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(False)  # 空闲时不占用视觉重量

        self.btn_convert = make_button("一键转换", "primary")
        self.btn_convert.setFixedWidth(150)
        self.btn_convert.setFixedHeight(38)
        self.btn_convert.setShortcut("Ctrl+Return")
        self.btn_convert.clicked.connect(self._start_convert)

        row.addWidget(self.progress, 1)
        row.addStretch(1)
        row.addWidget(self.btn_convert)
        return row

    # -------------------------------------------------- 选择来源
    def _pick_files(self):
        exts = " ".join(f"*{e}" for e in ALL_EXTENSIONS)
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择要转换的文件", "",
            f"所有支持的格式 ({exts});;"
            "文本与标记 (*.txt *.md *.html);;"
            "办公文档 (*.docx *.xlsx);;"
            "表格 (*.csv *.xlsx);;"
            "所有文件 (*.*)")
        if files:
            self._set_paths([Path(f) for f in files])

    def _pick_folder(self):
        d = QFileDialog.getExistingDirectory(self, "选择包含待转换文件的文件夹")
        if d:
            self._set_paths([Path(d)])

    def _apply_typed_path(self):
        raw = self.path_edit.text().strip().strip('"')
        if raw:
            self._set_paths([Path(raw)])

    def _set_paths(self, paths: list[Path]):
        self.paths = paths
        if len(paths) == 1:
            self.path_edit.setText(str(paths[0]))
        else:
            self.path_edit.setText(f"{paths[0].parent} 等 {len(paths)} 项")
        self._rescan(pick_default_target=True)

    def _rescan(self, pick_default_target: bool = False):
        """扫描待转换文件并刷新提示。"""
        self.found_files: list[Path] = []
        if self.paths:
            self.found_files, _notes = collect_inputs(self.paths)
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
            self.chips.set_format("txt" if src.code != "txt" else "md")
            self._on_format_changed(self.chips.current_format())

    def _update_hint(self):
        if not self.paths:
            self.hint.setText("尚未选择")
            self.hint.setStyleSheet(f"color: {T.TEXT_SECOND}; font-size: 12px;")
            return
        if not self.found_files:
            self.hint.setText("未找到支持的文件")
            self.hint.setStyleSheet(f"color: {T.DANGER}; font-size: 12px;")
            return

        counter: dict[str, int] = {}
        for p in self.found_files:
            fmt = resolve_format(p.suffix.lstrip("."))
            key = fmt.code if fmt else "其他"
            counter[key] = counter.get(key, 0) + 1
        detail = " · ".join(f"{k} {v}" for k, v in
                            sorted(counter.items(), key=lambda kv: -kv[1]))
        self.hint.setText(f"找到 {len(self.found_files)} 个文件（{detail}）")
        self.hint.setStyleSheet(f"color: {T.ACCENT}; font-size: 12px;")

    def _check_target_supported(self) -> bool:
        """目标格式在当前环境不可写时，提前拦下并说明原因。"""
        code = self.chips.current_format()
        cap = capability(code)
        if cap.can_write:
            return True
        QMessageBox.warning(
            self, "当前环境不支持",
            f"无法生成 {FORMATS[code].label}：{cap.note or '缺少必要组件'}。\n\n"
            "请换一个目标格式，或按 README 的说明安装所需组件。")
        return False

    # -------------------------------------------------- 拖拽支持
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self._set_paths(paths)
            e.acceptProposedAction()

    # -------------------------------------------------- 转换
    def _show_empty(self, empty: bool):
        self.log.setVisible(not empty)
        self.empty.setVisible(empty)

    def _clear_log(self):
        self.log.clear()
        self._show_empty(True)
        self.summary.setText("就绪")
        self.summary.setStyleSheet(f"color: {T.TEXT_SECOND}; font-size: 12px;")
        self.progress.setVisible(False)

    def _set_busy(self, busy: bool):
        for w in (self.btn_files, self.btn_folder, self.btn_convert, self.path_edit,
                  self.chips, self.sw_smart, self.sw_overwrite):
            w.setEnabled(not busy)
        self.btn_convert.setText("转换中…" if busy else "一键转换")

    def _start_convert(self):
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

        # 规划与预检：先算出「哪些会失败、为什么」，动手前就告诉用户
        jobs = plan_jobs(self.found_files, dst_code)
        for job in jobs:
            job.options["smart"] = smart
            if not smart:
                job.options["verbatim"] = True
        ready, blocked, _ = precheck(jobs, force=force)

        self.log.clear()
        self._show_empty(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, len(jobs))
        self.progress.setValue(0)
        self.summary.setText(f"共 {len(jobs)} 个文件 → {dst_code}")
        self.summary.setStyleSheet(f"color: {T.TEXT_SECOND}; font-size: 12px;")

        # 被拦下的当场落到日志，不必占用转换线程
        for job in blocked:
            reason = job.options.get("_blocked_by", "")
            status = "skipped" if reason.startswith("目标文件已存在") else "error"
            self._on_item(status, str(job.src), str(job.dst), reason)

        if not ready:
            self._on_finished(0, len(blocked), len(jobs) - len(blocked), len(jobs))
            return

        self._set_busy(True)
        self.worker = ConvertWorker(ready, force)
        self.worker.item_ready.connect(self._on_item)
        self.worker.finished_all.connect(self._on_finished)
        self.worker.start()

    def _on_item(self, kind: str, src: str, dst: str, reason: str):
        item = QListWidgetItem()
        item.setSizeHint(QSize(0, 28))
        row = LogRow(kind, src, dst, reason)
        self.log.addItem(item)
        self.log.setItemWidget(item, row)
        self.log.scrollToBottom()
        self.progress.setValue(min(self.progress.value() + 1, self.progress.maximum()))

    def _on_finished(self, ok: int, skipped: int, error: int, total: int):
        self._set_busy(False)
        self.summary.setText(f"成功 {ok} · 跳过 {skipped} · 失败 {error}")
        if error:
            self.summary.setStyleSheet(f"color: {T.DANGER}; font-size: 12px;")
        elif skipped:
            self.summary.setStyleSheet(f"color: {T.WARN}; font-size: 12px;")
        else:
            self.summary.setStyleSheet(f"color: {T.SUCCESS}; font-size: 12px;")

        # 只有失败才打断用户：全部成功时界面上已有汇总，弹框只会碍事
        if error:
            QMessageBox.warning(self, "部分文件失败",
                                f"成功 {ok}，跳过 {skipped}，失败 {error}。\n"
                                "失败原因已列在转换日志中，原件未被改动。")


# ---------------------------------------------------------------- 入口
def _app_icon() -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    from PySide6.QtGui import QColor, QPainter, QPen
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(T.ACCENT))
    p.drawRoundedRect(4, 4, 56, 56, 12, 12)
    p.setPen(QPen(QColor("#FFFFFF"), 3, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(20, 26, 44, 26)
    p.drawLine(20, 34, 38, 34)
    p.drawLine(20, 42, 30, 42)
    p.end()
    return QIcon(pm)


def run() -> int:
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("文档格式转换")
    T.init_fonts()  # 字体探测需在 QApplication 之后
    app.setFont(QFont(T.UI_FAMILY, 9))
    app.setStyleSheet(T.qss())
    app.setWindowIcon(_app_icon())

    win = MainWindow()
    win.show()
    return app.exec()
