"""Fluent Design 主窗口。

交互流程与原 tkinter 版本完全一致：
选文件/文件夹 → 勾选选项 → 一键转换 → 逐条日志 + 汇总。
转换逻辑复用 txt2md.py 的 collect_txt_files / convert_file，未做任何改动。
"""

import sys
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QThread, Signal
from PySide6.QtGui import QFont, QIcon, QPixmap
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
                               QMessageBox, QProgressBar, QSizePolicy, QVBoxLayout,
                               QWidget)

# 复用核心转换逻辑
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from txt2md import collect_txt_files, convert_file  # noqa: E402

from . import theme as T
from .widgets import (Card, FluentSwitch, HoverRow, IconTile, LogRow, make_button)


# ---------------------------------------------------------------- 转换线程
class ConvertWorker(QThread):
    """在后台线程执行转换，逐条推送结果，避免界面卡顿。"""

    item_ready = Signal(str, str, str, str)   # kind, src, dst, reason
    finished_all = Signal(int, int, int, int)  # ok, skipped, error, total

    def __init__(self, jobs: list[Path], smart: bool, overwrite: bool, parent=None):
        super().__init__(parent)
        self.jobs = jobs
        self.smart = smart
        self.overwrite = overwrite

    def run(self):
        ok = skipped = error = 0
        for src in self.jobs:
            try:
                status, dst = convert_file(src, src.parent, self.smart, self.overwrite)
                if status == "ok":
                    ok += 1
                elif status == "skipped":
                    skipped += 1
                self.item_ready.emit(status, str(src), dst, "")
            except Exception as e:  # 单文件失败不中断
                error += 1
                self.item_ready.emit("error", str(src), "", str(e))
        self.finished_all.emit(ok, skipped, error, len(self.jobs))


# ---------------------------------------------------------------- 主窗口
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("TXT 转 Markdown")
        self.resize(1000, 800)
        self.setMinimumSize(720, 660)
        self.setAcceptDrops(True)

        self.paths: list[Path] = []
        self.worker: ConvertWorker | None = None
        self.txt_total = 0

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
        title = QLabel("TXT 转 Markdown")
        title.setObjectName("appTitle")
        sub = QLabel("批量转换 · GBK/UTF-8 编码自动识别 · 智能排版")
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

        self.sw_smart = FluentSwitch(checked=True)
        lay.addWidget(self._option_row(
            "智能排版", "首行作为标题、识别列表标记，正文内容不做删改", self.sw_smart))

        lay.addWidget(self._divider())

        self.sw_overwrite = FluentSwitch(checked=False)
        lay.addWidget(self._option_row(
            "覆盖已存在的 .md", "关闭时同名文件自动跳过，不会覆盖已有内容", self.sw_overwrite))
        return card

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
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择要转换的 TXT 文件", "", "文本文件 (*.txt);;所有文件 (*.*)")
        if files:
            self._set_paths([Path(f) for f in files])

    def _pick_folder(self):
        d = QFileDialog.getExistingDirectory(self, "选择包含 TXT 文件的文件夹")
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
        self._rescan()

    def _rescan(self):
        """统计待转换文件数量，给用户即时反馈。"""
        self.txt_total = 0
        for p in self.paths:
            if p.exists():
                self.txt_total += len(collect_txt_files(p))
        self._update_hint()

    def _update_hint(self):
        if not self.paths:
            self.hint.setText("尚未选择")
            return
        if self.txt_total:
            self.hint.setText(f"找到 {self.txt_total} 个 .txt 文件")
            self.hint.setStyleSheet(f"color: {T.ACCENT}; font-size: 12px;")
        else:
            self.hint.setText("未找到 .txt 文件")
            self.hint.setStyleSheet(f"color: {T.DANGER}; font-size: 12px;")

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
        for w in (self.btn_files, self.btn_folder, self.btn_convert, self.path_edit):
            w.setEnabled(not busy)
        self.btn_convert.setText("转换中…" if busy else "一键转换")

    def _start_convert(self):
        if not self.paths:
            QMessageBox.warning(self, "提示", "请先选择 TXT 文件或文件夹")
            return
        self._rescan()
        if not self.txt_total:
            QMessageBox.warning(self, "提示", "所选路径中没有找到 .txt 文件")
            return

        jobs: list[Path] = []
        for p in self.paths:
            jobs.extend(collect_txt_files(p))

        # 清空上一轮日志
        self.log.clear()
        self._show_empty(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, len(jobs))
        self.progress.setValue(0)
        self.summary.setText(f"共 {len(jobs)} 个文件")
        self._set_busy(True)

        self.worker = ConvertWorker(jobs, self.sw_smart.isChecked(),
                                    self.sw_overwrite.isChecked())
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

        if error == 0:
            QMessageBox.information(self, "完成", f"转换完成：成功 {ok}，跳过 {skipped}")
        else:
            QMessageBox.warning(self, "部分失败",
                                f"成功 {ok}，跳过 {skipped}，失败 {error}。\n详见转换日志。")


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
    app.setApplicationName("TXT 转 Markdown")
    T.init_fonts()  # 字体探测需在 QApplication 之后
    app.setFont(QFont(T.UI_FAMILY, 9))
    app.setStyleSheet(T.qss())
    app.setWindowIcon(_app_icon())

    win = MainWindow()
    win.show()
    return app.exec()
