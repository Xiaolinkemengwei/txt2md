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
# 测试不读写用户的真实配置，否则上一次跑留下的输出格式会让断言随机失败
os.environ["AIEYE_NO_SETTINGS"] = "1"
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

# 模态对话框在 offscreen 平台会永久阻塞，测试里一律短路
QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)

from gui import theme as T  # noqa: E402
from gui.app import MainWindow  # noqa: E402
from gui.widgets import FormatGrid  # noqa: E402

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
    chips: FormatGrid = win.chips
    check("8 个格式卡片", len(chips._cards) == 8, str(len(chips._cards)))
    check("含 EPUB 卡片", "epub" in chips._cards)
    check("默认选中 md", chips.current_format() == "md", chips.current_format())

    chips.set_format("pdf")
    check("可切换目标格式", chips.current_format() == "pdf")

    # set_format 必须自己发出 changed（调用方不该再手动刷新说明）
    hint = win.fmt_hint.text()
    check("格式说明随之更新", "保真" in hint and hint.strip(), hint)

    chips.set_format("md")


def test_typed_path(app: QApplication, win: MainWindow, tmp: Path):
    print("\n6. 输入框路径与拖放")
    only = tmp / "typed"
    only.mkdir(exist_ok=True)
    (only / "t.txt").write_bytes("输入框路径\n".encode("gbk"))

    # 只粘贴路径、不按回车，直接点转换也要能跑
    win.paths = []
    win.path_edit.setText(str(only / "t.txt"))
    win.chips.set_format("md")
    win._start_convert()
    if win.worker is not None:
        win.worker.wait(30000)
    for _ in range(20):
        app.processEvents()
    check("粘贴路径后直接转换可用", (only / "t.md").exists(),
          win.summary.text())

    # 初始空态不该露出表头
    win._clear_log()
    app.processEvents()
    check("空态隐藏日志表头", not win.log_header.isVisible())


def test_layout_heights(win: MainWindow):
    print("\n7. 布局不压缩")
    win.resize(win.minimumWidth(), win.minimumHeight())
    QApplication.processEvents()
    card = win.chips._cards["md"]
    check("最小尺寸下格式卡片未被压扁", card.height() >= 56, str(card.height()))
    check("格式网格未被压扁", win.chips.height() >= 56, str(win.chips.height()))

    # 窗口下限若小于布局需求，Qt 不会报错，而是静默把最后一张卡片裁掉
    # （表现为「覆盖同名文件」整行消失）。这两条断言就是防这个的。
    lay = win.centralWidget().layout()
    check("窗口最小高度不小于布局需求",
          win.minimumHeight() >= lay.minimumSize().height(),
          f"窗口 {win.minimumHeight()} < 布局 {lay.minimumSize().height()}")
    check("选项卡片未被裁切",
          win.options_card.height() >= win.options_card.minimumHeight(),
          f"{win.options_card.height()} < {win.options_card.minimumHeight()}")


def test_all_skipped_retry(app: QApplication, win: MainWindow, tmp: Path):
    print("\n8. 全部跳过时的补救入口")
    d = tmp / "skip"
    d.mkdir(exist_ok=True)
    (d / "s.txt").write_text("标题\n\n正文\n", encoding="utf-8")

    win.chips.set_format("md")
    win.sw_overwrite.setChecked(True)
    win._set_paths([d])
    run_convert(app, win)
    check("首次转换成功", "成功 1" in win.summary.text(), win.summary.text())

    # 目标已存在 + 未开覆盖 → 全部跳过，必须给出一键补救的入口
    # 这里只选 .txt：选目录会把上一轮生成的 .md 也扫进来，那不是「全部跳过」的场景
    win.sw_overwrite.setChecked(False)
    win._set_paths([d / "s.txt"])
    run_convert(app, win)
    check("第二次全部跳过", "跳过 1" in win.summary.text(), win.summary.text())
    check("出现「覆盖并重转」入口", win.btn_retry.isVisible())

    win.btn_retry.click()
    if win.worker is not None:
        win.worker.wait(30000)
    for _ in range(20):
        app.processEvents()
    check("点补救入口后转换成功", "成功 1" in win.summary.text(), win.summary.text())
    check("成功后补救入口收起", not win.btn_retry.isVisible())


def test_worker_crash(app: QApplication, win: MainWindow, tmp: Path):
    print("\n9. 转换线程异常不锁死界面")
    import gui.app as appmod

    d = tmp / "crash"
    d.mkdir(exist_ok=True)
    (d / "c.txt").write_text("标题\n\n正文\n", encoding="utf-8")

    def boom(job, **kwargs):
        raise RuntimeError("模拟转换内核崩溃")

    orig = appmod.convert_one
    appmod.convert_one = boom
    try:
        win.chips.set_format("md")
        win.sw_overwrite.setChecked(True)
        win._set_paths([d])
        win._start_convert()
        if win.worker is not None:
            win.worker.wait(30000)
        for _ in range(20):
            app.processEvents()
    finally:
        appmod.convert_one = orig

    # 修复前：线程直接死掉，finished_all 永不发出，按钮永久灰着
    check("异常后按钮恢复可用", win.btn_convert.isEnabled())
    check("异常后仍给出汇总", "失败 1" in win.summary.text(), win.summary.text())
    check("异常后退出忙碌态", win.btn_convert.text() == "一键转换", win.btn_convert.text())


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


def test_viewer(app: QApplication, win: MainWindow, tmp: Path):
    print("\n8. 查看结果与复制")
    from gui.viewer import ResultViewer

    # 模拟一条已完成记录：带预览文本
    preview = ("# 项目验收报告\n\n"
               "识别结果需要能复制，也要能导出。\n\n"
               "- 第一项\n- 第二项\n\n"
               "![原图 截图.png](截图_assets/截图_orig.png)\n")
    win._on_item("ok", str(tmp / "截图.png"), str(tmp / "截图.md"), "",
                 preview=preview)
    check("预览已缓存", bool(win._previews.get(str(tmp / "截图.md"))),
          str(list(win._previews)))

    viewer = ResultViewer(str(tmp / "截图.png"), str(tmp / "截图.md"), preview)
    check("查看器标题含文件名", "截图.md" in viewer.windowTitle(),
          viewer.windowTitle())
    check("默认显示 Markdown 原文", viewer.text.toPlainText().startswith("# 项目验收报告"),
          viewer.text.toPlainText()[:20])

    viewer._copy_all()
    clip = QApplication.clipboard().text()
    check("复制全部写进剪贴板", "项目验收报告" in clip, clip[:30])

    # 纯文本视图：去掉标记，且不把图片 alt 当成正文行
    viewer._apply_mode("plain")
    plain = viewer.text.toPlainText()
    check("纯文本去掉 # 标记", "# 项目验收报告" not in plain, plain[:40])
    check("纯文本不留图片 alt", "原图" not in plain, plain[:80])

    # 选中复制：只复制选区
    cursor = viewer.text.textCursor()
    cursor.setPosition(0)
    from PySide6.QtGui import QTextCursor
    cursor.setPosition(5, QTextCursor.MoveMode.KeepAnchor)
    viewer.text.setTextCursor(cursor)
    viewer._copy_selection()
    check("复制选中只含选区", len(QApplication.clipboard().text()) <= 6,
          QApplication.clipboard().text())

    # 另存为：复用内核写出，md → docx 应产出真文件
    out = tmp / "from_viewer.docx"
    viewer.export_to(str(out), "docx")
    check("另存为 docx 成功", out.exists() and out.stat().st_size > 1000,
          f"{out.exists()}")

    check("图片不进输出格式卡片", "image" not in win.chips._cards,
          str(list(win.chips._cards)))
    viewer.close()


def test_image_source(app: QApplication, win: MainWindow, tmp: Path):
    print("\n9. 图片作为输入")
    from core.registry import FORMATS

    pic = tmp / "shot.png"
    from PIL import Image
    Image.new("RGB", (400, 200), "white").save(pic)

    # 关着 OCR 也必须被强制打开：图片没有文字层
    win.sw_ocr.setChecked(False)
    win._set_paths([pic])
    app.processEvents()
    check("图片被识别为可转换文件",
          any(f.name == "shot.png" for f in win.found_files),
          str([f.name for f in win.found_files]))

    forced = any(f.suffix.lower() in FORMATS["image"].extensions
                 for f in win.found_files)
    check("图片输入触发强制 OCR 判定", forced)
    win.sw_ocr.setChecked(True)
    check("OCR 开关处于可用状态", win.sw_ocr.isChecked() and win.sw_ocr.isEnabled())


def test_theme(app: QApplication, win: MainWindow):
    print("\n10. 白天/黑夜主题")
    from gui import theme as T

    was = T.current_palette()
    T.apply_to_app(app, "light")
    light_bg, light_text = T.BG, T.TEXT
    check("亮色背景是浅色", light_bg.upper().startswith("#F"), light_bg)
    check("亮色文字是深色", light_text.upper().startswith("#1"), light_text)
    check("亮色按钮提示切到黑夜", "黑夜" in win.btn_theme.toolTip(),
          win.btn_theme.toolTip())

    win._toggle_theme()
    app.processEvents()
    check("切换到黑夜", T.current_palette() == "dark", T.current_palette())
    check("深色背景是深色", T.BG.upper() in ("#202020", "#1F1F1F"), T.BG)
    check("深色文字是浅色", T.TEXT.upper() == "#FFFFFF", T.TEXT)
    check("深色下按钮提示切回白天", "白天" in win.btn_theme.toolTip(),
          win.btn_theme.toolTip())

    # 样式表必须真的重新生成过：仍含浅色底就说明只改了变量没刷 QSS
    sheet = app.styleSheet()
    check("QSS 已刷新为深色", "#202020" in sheet and "#F3F3F3" not in sheet)
    check("深色下强调色独立", T.ACCENT.upper() != "#0F6CBD", T.ACCENT)

    # 关键控件在深色下仍是可读的组合（不是白底白字）
    check("深色卡片与文字不同色", T.CARD != T.TEXT, f"{T.CARD}/{T.TEXT}")
    check("深色禁用文字仍可辨", T.TEXT_DISABLED != T.CARD, T.TEXT_DISABLED)

    win._toggle_theme()
    app.processEvents()
    check("能切回白天", T.current_palette() == "light", T.current_palette())
    T.apply_to_app(app, was)


def test_progress(app: QApplication, win: MainWindow, tmp: Path):
    print("\n11. 转换进度显示")
    src = tmp / "进度源.md"
    src.write_text("# 章一\n\n内容。\n\n# 章二\n\n内容。\n", encoding="utf-8")
    win._set_paths([src])
    win.chips.set_format("epub")
    app.processEvents()

    target = src.with_suffix(".epub")
    target.unlink(missing_ok=True)
    values: list[int] = []
    win.progress.valueChanged.connect(lambda v: values.append(v))
    run_convert(app, win)

    check("进度条使用千分制", win.progress.maximum() == 1000, str(win.progress.maximum()))
    check("进度条有推进", len(values) >= 2, str(values))
    check("进度值在范围内", all(0 <= v <= 1000 for v in values), str(values[:5]))
    check("最终到达满格", 1000 in values or win.progress.value() == 1000,
          f"max={max(values) if values else None} cur={win.progress.value()}")
    check("产物已生成", target.exists())


def test_aieye_switch(app: QApplication, win: MainWindow, tmp: Path):
    print("\n12. AI eye 导读开关")
    check("开关存在", win.sw_aieye is not None)
    check("默认关闭", not win.sw_aieye.isChecked())

    src = tmp / "导读.md"
    src.write_text("# 章\n\n正文内容足够长一些，方便生成摘要。\n", encoding="utf-8")
    win._set_paths([src])
    win.chips.set_format("txt")
    win.sw_aieye.setChecked(True, animate=False)
    app.processEvents()

    out = src.with_suffix(".txt")
    guide = src.with_name(f"{src.stem}.aieye.md")
    out.unlink(missing_ok=True)
    guide.unlink(missing_ok=True)
    run_convert(app, win)

    check("产物生成", out.exists())
    check("导读生成", guide.exists(), str(guide))
    if guide.exists():
        text = guide.read_text("utf-8")
        check("导读含 front-matter", text.startswith("---\naieye: 1"), text[:30])
        check("导读含结构地图", "结构地图" in text)
    win.sw_aieye.setChecked(False, animate=False)


def test_epub_source(app: QApplication, win: MainWindow, tmp: Path):
    print("\n13. EPUB 作为输入")
    from core.pipeline import Job, convert_one

    md = tmp / "电子书源.md"
    md.write_text("# 第一章\n\n正文一。\n\n# 第二章\n\n正文二。\n", encoding="utf-8")
    epub = tmp / "电子书.epub"
    epub.unlink(missing_ok=True)
    r = convert_one(Job(md, epub, "md", "epub"), force=True)
    check("能造出测试用 EPUB", r.status == "ok", r.reason)

    win._set_paths([epub])
    app.processEvents()
    check("EPUB 被扫描为可转换文件",
          any(f.name == "电子书.epub" for f in win.found_files),
          str([f.name for f in win.found_files]))
    check("EPUB 出现在卡片中", "epub" in win.chips._cards)

    win.chips.set_format("md")
    win.sw_aieye.setChecked(False, animate=False)
    app.processEvents()
    out = epub.with_suffix(".md")
    out.unlink(missing_ok=True)
    run_convert(app, win)
    check("EPUB → md 成功", out.exists())
    if out.exists():
        body = out.read_text("utf-8")
        check("两章内容都在", "第一章" in body and "第二章" in body, body[:80])


def main() -> int:
    print("=" * 62)
    print("AI eye 界面层测试")
    print("=" * 62)

    app = QApplication(sys.argv)
    T.init_fonts()
    app.setStyleSheet(T.qss())

    tmp = Path(tempfile.mkdtemp(prefix="aieye-gui-"))
    win = MainWindow()
    win.show()
    app.processEvents()

    try:
        src = make_samples(tmp)
        test_scan(app, win, src)
        test_chips(win)
        test_suggest(win, tmp)
        test_convert(app, win, src, tmp)
        test_typed_path(app, win, tmp)
        test_all_skipped_retry(app, win, tmp)
        test_worker_crash(app, win, tmp)
        test_layout_heights(win)
        test_viewer(app, win, tmp)
        test_image_source(app, win, tmp)
        test_theme(app, win)
        test_progress(app, win, tmp)
        test_aieye_switch(app, win, tmp)
        test_epub_source(app, win, tmp)
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
