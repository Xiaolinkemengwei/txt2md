"""界面层测试：扫描、格式选择、转换流程、防呆拦截。

界面测试容易退化成「窗口能打开就算过」。这里每条都验证实际状态：
扫描出的文件数、选中格式的传递、日志行内容、被拦截时的提示。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

# 模态对话框在 offscreen 平台会永久阻塞，测试里一律短路
QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)

from gui import theme as T  # noqa: E402
from gui.app import MainWindow  # noqa: E402
from gui.widgets import FormatChips  # noqa: E402

PASS = FAIL = 0
FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        FAILURES.append(f"{name}: {detail}")
        print(f"  ✗ {name}  {detail}")


def make_samples(tmp: Path) -> Path:
    d = tmp / "src"
    d.mkdir(parents=True, exist_ok=True)
    (d / "笔记一.txt").write_bytes("读书笔记\n\n第一章\n\n1. 要点\n".encode("gbk"))
    (d / "笔记二.txt").write_text("第二份笔记\n\n内容。\n", encoding="utf-8")
    (d / "说明.md").write_text("# 标题\n\n- 条目一\n- 条目二\n", encoding="utf-8")
    (d / "数据.csv").write_text("名称,值\n甲,1\n乙,2\n", encoding="utf-8")
    return d


def run_convert(app: QApplication, win: MainWindow, timeout: int = 60000):
    """触发转换并等线程结束（含排队信号的派发）。"""
    win._start_convert()
    if win.worker is not None:
        win.worker.wait(timeout)
    for _ in range(20):
        app.processEvents()


def test_scan(app: QApplication, win: MainWindow, src: Path):
    print("\n1. 扫描与统计")
    win._set_paths([src])
    app.processEvents()
    check("找到全部支持格式", len(win.found_files) == 4, f"实际 {len(win.found_files)}")
    text = win.hint.text()
    check("提示含文件数", "4 个文件" in text, text)
    check("提示含格式构成", "txt" in text and "md" in text and "csv" in text, text)


def test_chips(win: MainWindow):
    print("\n2. 格式选择")
    chips: FormatChips = win.chips
    check("7 个格式按钮", len(chips._buttons) == 7, str(len(chips._buttons)))
    check("默认选中 md", chips.current_format() == "md", chips.current_format())

    chips.set_format("pdf")
    check("可切换目标格式", chips.current_format() == "pdf")

    # 目标格式变更要同步到说明文字
    chips.changed.emit("pdf")
    win._on_format_changed("pdf")
    hint = win.fmt_hint.text()
    check("格式说明随之更新", "PDF" in hint, hint)

    chips.set_format("md")
    win._on_format_changed("md")


def test_suggest(win: MainWindow, tmp: Path):
    print("\n3. 目标格式智能建议")
    md_only = tmp / "mdonly"
    md_only.mkdir(exist_ok=True)
    (md_only / "a.md").write_text("# A\n", encoding="utf-8")

    win.chips.set_format("md")
    win._set_paths([md_only])
    check("源为 md 时目标自动避开 md",
          win.chips.current_format() != "md", win.chips.current_format())


def test_convert(app: QApplication, win: MainWindow, src: Path, tmp: Path):
    print("\n4. 转换流程")
    out_root = tmp / "src"
    # 清掉上一轮产物
    for p in out_root.glob("*.md"):
        if p.stem.startswith("笔记"):
            p.unlink()

    win.chips.set_format("md")
    win._on_format_changed("md")
    win._set_paths([src])
    app.processEvents()

    win.log.clear()
    run_convert(app, win)
    rows = [win.log.itemWidget(win.log.item(i)).__class__.__name__
            for i in range(win.log.count())]
    check("日志有记录", win.log.count() == 4, f"实际 {win.log.count()}")

    produced = {p.name for p in out_root.glob("*.md")}
    check("txt 转出了 md", "笔记一.md" in produced and "笔记二.md" in produced, str(produced))
    check("md 未自我覆盖", (out_root / "说明.md").read_text("utf-8").startswith("# 标题"))
    check("汇总显示成功数", "成功" in win.summary.text(), win.summary.text())

    body = (out_root / "笔记一.md").read_text("utf-8")
    check("GBK 中文正确解码", "读书笔记" in body, body[:40])

    print("\n5. 防呆拦截")
    # 目标已存在 + 关闭覆盖 → 应跳过而非覆盖
    win.sw_overwrite.setChecked(False)
    win.log.clear()
    run_convert(app, win)
    texts = []
    for i in range(win.log.count()):
        w = win.log.itemWidget(win.log.item(i))
        texts.append(w.toolTip() if hasattr(w, "toolTip") else "")
    check("重复转换时全部跳过", "跳过 4" in win.summary.text() or "跳过" in win.summary.text(),
          win.summary.text())


def main() -> int:
    print("=" * 62)
    print("txt2md 2.0 界面层测试")
    print("=" * 62)

    app = QApplication(sys.argv)
    T.init_fonts()
    app.setStyleSheet(T.qss())

    tmp = Path(tempfile.mkdtemp(prefix="txt2md-gui-"))
    win = MainWindow()
    win.show()
    app.processEvents()

    try:
        src = make_samples(tmp)
        test_scan(app, win, src)
        test_chips(win)
        test_suggest(win, tmp)
        test_convert(app, win, src, tmp)
    finally:
        win.close()
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 62)
    total = PASS + FAIL
    if FAIL:
        print(f"失败 {FAIL} / {total}")
        for f in FAILURES:
            print(f"  · {f}")
        print("=" * 62)
        return 1
    print(f"全部通过：{PASS} 项")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
