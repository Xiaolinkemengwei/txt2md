"""界面截图工具（开发自测用）：离屏渲染不同尺寸，检查视觉效果与响应式布局。

用法：
    python tests/shot_gui.py            # 生成 5 个典型尺寸的截图 + 1 张空态
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["AIEYE_NO_SETTINGS"] = "1"   # 截图不落盘，避免改到本机配置
# 离屏平台默认不加载系统字体，显式指定字体目录，否则中文全部渲染为豆腐块
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QSize  # noqa: E402
from PySide6.QtWidgets import QApplication, QListWidgetItem  # noqa: E402

from gui import theme as T  # noqa: E402
from gui.app import MainWindow  # noqa: E402
from gui.widgets import LogRow  # noqa: E402

DEMO = [
    ("ok", r"D:\notes\读书笔记.txt", r"D:\notes\读书笔记.md", ""),
    ("ok", r"D:\notes\2026 工作计划.txt", r"D:\notes\2026 工作计划.md", ""),
    ("ok", r"D:\notes\会议纪要.txt", r"D:\notes\会议纪要.md", ""),
    ("skipped", r"D:\notes\需求草案.txt", r"D:\notes\需求草案.md", ""),
    ("error", r"D:\notes\locked.pdf", "", "PermissionError: 文件被占用"),
]


def build(app: QApplication) -> MainWindow:
    w = MainWindow()
    w.path_edit.setText(r"D:\notes")
    w._set_hint("找到 12 个文件（txt 11 · md 1）", "ok")
    w._show_empty(False)
    for kind, src, dst, reason in DEMO:
        item = QListWidgetItem()
        item.setSizeHint(QSize(0, 32))
        w.log.addItem(item)
        w.log.setItemWidget(item, LogRow(kind, src, dst, reason))
    w._set_summary("成功 3 · 跳过 1 · 失败 1", "error")
    w.status.setText("12 个文件待转换 · Ctrl+Enter 转换")
    w.progress.setVisible(True)
    w.progress.setRange(0, 12)
    w.progress.setValue(9)
    w.progress_text.setText("9 / 12")
    return w


def main():
    app = QApplication(sys.argv)
    T.init_fonts()
    app.setStyleSheet(T.qss())
    out_dir = ROOT / "tests" / "preview"
    out_dir.mkdir(parents=True, exist_ok=True)

    for name, size in [("wide", (1200, 860)), ("default", (1080, 880)),
                       ("compact", (860, 700)), ("tall", (900, 1000)),
                       ("min", (820, 678))]:
        w = build(app)
        w.resize(*size)
        w.show()
        app.processEvents()
        for _ in range(3):
            app.processEvents()
        path = out_dir / f"gui_{name}.png"
        w.grab().save(str(path))
        print("saved", path)
        w.close()

    # 空态单独出一张，检查占位文案与留白
    w = MainWindow()
    w.resize(1080, 880)
    w.show()
    app.processEvents()
    w.grab().save(str(out_dir / "gui_empty.png"))
    print("saved", out_dir / "gui_empty.png")
    w.close()


if __name__ == "__main__":
    main()
