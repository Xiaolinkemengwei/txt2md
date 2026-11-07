"""界面层功能测试：验证转换线程、扫描统计、开关状态传递，不依赖人工点击。

运行：
    python tests/test_gui_flow.py
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gui import theme as T  # noqa: E402
from gui.app import ConvertWorker, MainWindow  # noqa: E402
from txt2md import collect_txt_files  # noqa: E402


def test_scan_and_options(app):
    """选中目录后应正确统计 .txt 数量，开关默认值与原版一致。"""
    win = MainWindow()
    win._set_paths([ROOT / "tests" / "sample"])
    assert win.txt_total == 3, f"期望 3 个 txt，实际 {win.txt_total}"
    assert win.sw_smart.isChecked() is True, "智能排版应默认开启"
    assert win.sw_overwrite.isChecked() is False, "覆盖应默认关闭"

    # 路径不存在时应提示 0 个
    win._set_paths([Path(r"Z:\not-exist")])
    assert win.txt_total == 0
    win.close()
    print("PASS 扫描统计与默认选项")


def test_worker_converts(app):
    """转换线程应产出 .md，并正确统计成功/跳过。"""
    tmp = Path(tempfile.mkdtemp(prefix="txt2md-gui-"))
    try:
        for f in (ROOT / "tests" / "sample").glob("*.txt"):
            shutil.copy(f, tmp / f.name)
        jobs = collect_txt_files(tmp)
        assert len(jobs) == 3

        results = []
        worker = ConvertWorker(jobs, smart=True, overwrite=False)
        worker.item_ready.connect(lambda k, s, d, r: results.append((k, s, d, r)))

        summary = {}
        loop = QEventLoop()

        def on_done(ok, skipped, error, total):
            summary.update(ok=ok, skipped=skipped, error=error, total=total)
            loop.quit()

        worker.finished_all.connect(on_done)
        QTimer.singleShot(15000, loop.quit)  # 防止卡死
        worker.start()
        loop.exec()
        worker.wait(3000)

        assert summary.get("ok") == 3, f"期望成功 3，实际 {summary}"
        assert len(results) == 3
        mds = list(tmp.glob("*.md"))
        assert len(mds) == 3, f"期望生成 3 个 md，实际 {len(mds)}"
        assert "# 读书笔记" in (tmp / "gbk_note.md").read_text("utf-8"), "GBK 文件未正确转换"
        print("PASS 转换线程产出与统计", summary)

        # 再跑一次不带 overwrite，应全部跳过
        results.clear()
        summary.clear()
        worker2 = ConvertWorker(jobs, smart=True, overwrite=False)
        worker2.finished_all.connect(on_done)
        loop2 = QEventLoop()

        def on_done2(ok, skipped, error, total):
            summary.update(ok=ok, skipped=skipped, error=error, total=total)
            loop2.quit()

        worker2.finished_all.disconnect(on_done)
        worker2.finished_all.connect(on_done2)
        QTimer.singleShot(15000, loop2.quit)
        worker2.start()
        loop2.exec()
        worker2.wait(3000)
        assert summary.get("skipped") == 3, f"期望跳过 3，实际 {summary}"
        print("PASS 覆盖关闭时跳过已存在文件", summary)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    app = QApplication([])
    T.init_fonts()
    app.setStyleSheet(T.qss())
    test_scan_and_options(app)
    test_worker_converts(app)
    print("\n全部界面层测试通过")


if __name__ == "__main__":
    main()
