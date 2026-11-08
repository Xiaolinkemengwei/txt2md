"""结果查看器：在软件里直接看识别/转换出来的文字，并复制或另存。

为什么要单独一个窗口而不是主界面内嵌：整本扫描书的识别结果几万字，内嵌
会把日志区挤没；而且用户常要「开两个对比」。窗口非模态，可以并排开着。

数据来源是 ``Result.preview``（管线在转换时顺带渲染的 Markdown），
所以 docx/pdf 这类二进制产物也有可复制的文字。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QGuiApplication, QTextCursor
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QHBoxLayout,
                               QLabel, QMenu, QPlainTextEdit, QVBoxLayout, QWidget)

from . import theme as T
from .widgets import make_button
from .theme import repolish

# 可另存的目标格式（图片只能作为源，不在此列）
EXPORT_FORMATS = [
    ("md", "Markdown 文件 (*.md)"),
    ("txt", "纯文本 (*.txt)"),
    ("html", "网页 (*.html)"),
    ("docx", "Word 文档 (*.docx)"),
    ("pdf", "PDF 文档 (*.pdf)"),
    ("xlsx", "Excel 工作簿 (*.xlsx)"),
    ("csv", "CSV 表格 (*.csv)"),
]


class ResultViewer(QDialog):
    """转换结果查看器（非模态）。"""

    exported = Signal(str)          # 另存成功后传出文件路径

    def __init__(self, src_path: str, dst_path: str, content: str,
                 parent=None):
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setWindowTitle(f"查看结果 · {Path(dst_path or src_path).name}")
        self.resize(1000, 720)
        self.setMinimumSize(640, 460)
        self.setModal(False)

        self.src_path = src_path
        self.dst_path = dst_path
        self._markdown = content or ""
        self._plain = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        root.addLayout(self._build_head())
        root.addWidget(self._build_body(), 1)
        root.addLayout(self._build_footer())
        self._apply_mode("md")

    # -------------------------------------------------- 顶部
    def _build_head(self) -> QVBoxLayout:
        box = QVBoxLayout()
        box.setSpacing(2)

        # 样式一律走 QSS 的 objectName 规则：内联样式在切换主题时会留在旧颜色上
        title = QLabel(Path(self.dst_path or self.src_path).name)
        title.setObjectName("viewerTitle")
        box.addWidget(title)

        sub = QLabel(self._subtitle())
        sub.setObjectName("viewerSub")
        box.addWidget(sub)

        tabs = QHBoxLayout()
        tabs.setSpacing(6)
        tabs.setContentsMargins(0, 8, 0, 0)

        self.btn_md = make_button("Markdown 原文", "ghost")
        self.btn_plain = make_button("纯文本", "ghost")
        for btn, mode in ((self.btn_md, "md"), (self.btn_plain, "plain")):
            btn.setCheckable(True)
            btn.setProperty("tab", True)   # 选中态由动态属性 + QSS 表达
            btn.setFixedHeight(28)
            btn.clicked.connect(lambda _=False, m=mode: self._apply_mode(m))
            tabs.addWidget(btn)
        tabs.addStretch(1)

        hint = QLabel("可直接框选复制，或点右下角「复制全部」")
        hint.setObjectName("viewerHint")
        tabs.addWidget(hint)
        box.addLayout(tabs)
        return box

    def _subtitle(self) -> str:
        chars = len(self._markdown)
        figures = self._markdown.count("![")
        parts = [self.dst_path or self.src_path]
        parts.append(f"{chars:,} 字符")
        if figures:
            parts.append(f"{figures} 张图")
        return "　·　".join(parts)

    # -------------------------------------------------- 正文
    def _build_body(self) -> QWidget:
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.text.setObjectName("viewerText")
        font = QFont(T.MONO_FAMILY)
        font.setPixelSize(T.FS_BODY)
        self.text.setFont(font)
        return self.text

    # 页签的选中态用「动态属性 + QSS」表达（QSS 的 [tab="true"][active="true"]）。
    # 不要用内联 setStyleSheet：那样颜色在调用那一刻就固化了，切到深色主题后
    # 页签仍会停在浅色底上——这是本项目踩过的坑，所有随主题变的样式都别写内联。
    def _apply_mode(self, mode: str) -> None:
        self.btn_md.setChecked(mode == "md")
        self.btn_plain.setChecked(mode == "plain")
        for btn, m in ((self.btn_md, "md"), (self.btn_plain, "plain")):
            btn.setProperty("active", m == mode)
            repolish(btn)
        if mode == "md":
            body = self._markdown
        else:
            if not self._plain:
                self._plain = self._to_plain(self._markdown)
            body = self._plain
        self.text.setPlainText(body)
        self.text.moveCursor(QTextCursor.MoveOperation.Start)

    @staticmethod
    def _to_plain(markdown: str) -> str:
        """Markdown → 纯文本：复用内核的渲染器，不另写一套正则。"""
        try:
            import re as _re
            from core.htmlio import render_plain
            from core.mdparse import parse_markdown
            # 图片语法先摘掉：mdparse 不认 ![](…)，会把 alt 当普通段落输出，
            # 纯文本里就多出一行莫名其妙的「原图」。
            body = _re.sub(r"!\[[^\]]*\]\([^)]*\)", "", markdown)
            return render_plain(parse_markdown(body))
        except Exception:
            return markdown

    # -------------------------------------------------- 底部
    def _build_footer(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)

        self.status = QLabel("")
        self.status.setObjectName("viewerStatus")
        row.addWidget(self.status)
        row.addStretch(1)

        self.btn_copy_sel = make_button("复制选中", "secondary")
        self.btn_copy_sel.clicked.connect(self._copy_selection)
        row.addWidget(self.btn_copy_sel)

        self.btn_copy = make_button("复制全部", "secondary")
        self.btn_copy.clicked.connect(self._copy_all)
        row.addWidget(self.btn_copy)

        self.btn_open = make_button("打开文件", "secondary")
        self.btn_open.clicked.connect(self._open_file)
        row.addWidget(self.btn_open)

        self.btn_export = make_button("另存为…", "primary")
        self.btn_export.clicked.connect(self._export_menu)
        row.addWidget(self.btn_export)
        return row

    # -------------------------------------------------- 动作
    def _flash(self, text: str) -> None:
        self.status.setText(text)

    def _copy_all(self) -> None:
        QApplication.clipboard().setText(self.text.toPlainText())
        self._flash(f"已复制 {len(self.text.toPlainText()):,} 字符")

    def _copy_selection(self) -> None:
        cursor = self.text.textCursor()
        if not cursor.hasSelection():
            self._copy_all()
            return
        # selectedText 用 U+2029 分段，直接进剪贴板会粘成乱码
        text = cursor.selectedText().replace("\u2029", "\n")
        QApplication.clipboard().setText(text)
        self._flash(f"已复制选中 {len(text):,} 字符")

    def _open_file(self) -> None:
        path = Path(self.dst_path or self.src_path)
        if not path.exists():
            self._flash("文件不存在，可能已被移动")
            return
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        self._flash(f"已打开 {path.name}")

    def _export_menu(self) -> None:
        menu = QMenu(self)
        for code, label in EXPORT_FORMATS:
            act = menu.addAction(label)
            act.triggered.connect(lambda _=False, c=code: self._export_as(c))
        menu.exec(self.btn_export.mapToGlobal(self.btn_export.rect().bottomLeft()))

    def _export_as(self, fmt: str) -> None:
        stem = Path(self.dst_path or self.src_path).stem
        filters = dict(EXPORT_FORMATS)
        path, _ = QFileDialog.getSaveFileName(
            self, "另存为",
            str(Path(self.dst_path or self.src_path).with_suffix(f".{fmt}")),
            filters.get(fmt, f"*.{fmt}"))
        if path:
            self.export_to(path, fmt)

    def export_to(self, path: str, fmt: str) -> bool:
        """把当前内容另存为指定格式。返回是否成功。

        走「Markdown → 临时文件 → 内核读取 → 内核写出」，而不是自己拼格式：
        这样 7 种目标格式的规则只有一处实现，另存出来的结果与主流程一致。
        """
        import shutil

        from core.readers import read_markdown_file
        from core.writers import write_any

        out = Path(path)
        tmpdir = Path(tempfile.mkdtemp(prefix="aieye-export-"))
        try:
            md_file = tmpdir / f"{out.stem}.md"
            md_file.write_text(self._markdown, encoding="utf-8")
            doc = read_markdown_file(md_file)
            doc.name = out.stem
            write_any(doc, out, fmt)
        except Exception as exc:
            self._flash(f"另存失败：{type(exc).__name__}: {exc}")
            return False
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
        self._flash(f"已另存为 {out.name}")
        self.exported.emit(str(out))
        return True
