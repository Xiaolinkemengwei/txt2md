"""txt2md 2.0 转换内核测试。

覆盖四类断言，不只看「有没有产出文件」：
  1. 保真   —— 往返后内容/结构是否还等价（字节级或元素级）
  2. 兼容   —— 1.0 的 txt → md 行为是否原样不变
  3. 防呆   —— 该拒绝的是否拒绝，且拒绝后不留残骸
  4. 鲁棒   —— 全部格式组合不崩溃、不产生空产物

运行：python tests/test_convert.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import Job, convert_one, plan_jobs, precheck  # noqa: E402
from core.errors import ConvertError  # noqa: E402
from core.mdparse import parse_markdown, strip_inline  # noqa: E402
from core.model import Document, TextDoc  # noqa: E402
from core.readers import decode_bytes, looks_binary, read_any  # noqa: E402
from core.registry import FORMATS  # noqa: E402
from core.writers import render_markdown  # noqa: E402

PASS = FAIL = 0
FAILURES: list[str] = []
BY_SECTION: dict[str, int] = {}
_CURRENT = [""]


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        BY_SECTION[_CURRENT[0]] = BY_SECTION.get(_CURRENT[0], 0) + 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        FAILURES.append(f"{name}: {detail}")
        print(f"  ✗ {name}  {detail}")


def eq(name: str, got, want):
    check(name, got == want, f"\n      got ={got!r}\n      want={want!r}")


def section(title: str):
    _CURRENT[0] = title
    print(f"\n{title}")


# ================================================================ 1. 编码
def test_encoding(tmp: Path):
    section("1. 编码探测")

    gbk = "读书笔记\n第一章 简介\n中文标点，测试。"
    text, enc = decode_bytes(gbk.encode("gbk"))
    eq("GBK 中文解码", text, gbk)
    check("GBK 编码名", enc in ("gb18030", "gbk"), enc)

    big5 = "繁體中文測試，這是臺灣用語。"
    text2, enc2 = decode_bytes(big5.encode("big5"))
    eq("Big5 繁体解码（不被 GBK 误吞）", text2, big5)

    utf8 = "UTF-8 中文 ✓ emoji 🎯"
    eq("UTF-8 解码", decode_bytes(utf8.encode("utf-8"))[0], utf8)

    bom = "带 BOM 的文本"
    text3, enc3 = decode_bytes(b"\xef\xbb\xbf" + bom.encode("utf-8"))
    eq("UTF-8 BOM 去除", text3, bom)
    eq("UTF-8 BOM 编码名", enc3, "utf-8-sig")

    u16 = "UTF-16 内容"
    text4, _ = decode_bytes(u16.encode("utf-16-le") if False else
                            b"\xff\xfe" + u16.encode("utf-16-le"))
    eq("UTF-16LE BOM 解码", text4, u16)

    check("二进制识别（可执行文件头）", looks_binary(b"MZ\x90\x00\x03\x00\x00\x00"))
    check("文本不被误判为二进制", not looks_binary("正常的中文文本\n第二行".encode("utf-8")))


# ================================================================ 2. 行内降级
def test_inline_strip():
    section("2. Markdown 行内降级")

    eq("紧贴中文的粗体", strip_inline("这是**粗体**文字"), "这是粗体文字")
    eq("紧贴中文的斜体", strip_inline("这是*斜体*文字"), "这是斜体文字")
    eq("行内代码", strip_inline("执行 `pip install` 即可"), "执行 pip install 即可")
    eq("链接取文字", strip_inline("见[文档](https://a.b)说明"), "见文档说明")
    eq("图片取 alt", strip_inline("![示意图](x.png)"), "示意图")
    eq("删除线", strip_inline("~~已废弃~~"), "已废弃")
    eq("乘法表达式不误伤", strip_inline("2 * 3 * 4 = 24"), "2 * 3 * 4 = 24")
    eq("下划线变量名不误伤", strip_inline("snake_case_name 变量"), "snake_case_name 变量")
    eq("未闭合星号保留", strip_inline("单个 * 星号"), "单个 * 星号")


# ================================================================ 3. Markdown 往返
RICH_MD = """# 项目标题

这是一段**粗体**与*斜体*，还有 `行内代码` 与 [链接](https://example.com)。

## 二级标题

- 无序一
- 无序二
  - 嵌套项

1. 有序一
2. 有序二

> 引用块内容
> 第二行

```python
def hello():
    print("世界")
```

| 名称 | 数量 |
|:---|---:|
| 苹果 | 12 |
| 香蕉 | 7 |

---

结尾段落。
"""


def test_markdown_roundtrip():
    section("3. Markdown 解析 / 写出")

    doc = parse_markdown(RICH_MD)
    kinds = [b.kind for b in doc.blocks]
    eq("块类型序列", kinds, ["heading", "paragraph", "heading", "list", "list",
                             "list", "quote", "code", "table", "divider", "paragraph"])
    eq("标题级别", [b.level for b in doc.blocks if b.kind == "heading"], [1, 2])
    # 连续同层列表合并为一块，嵌套层独立成块：level 应为 [0, 1, 0]
    eq("列表层级切分", [b.level for b in doc.blocks if b.kind == "list"], [0, 1, 0])
    eq("有序/无序标记", [b.ordered for b in doc.blocks if b.kind == "list"],
       [False, False, True])
    eq("代码语言", [b.lang for b in doc.blocks if b.kind == "code"], ["python"])
    tbl = next(b for b in doc.blocks if b.kind == "table")
    eq("表格列数", [len(r) for r in tbl.rows], [2, 2, 2])
    eq("表格对齐", tbl.align, ["left", "right"])

    once = render_markdown(doc)
    twice = render_markdown(parse_markdown(once))
    eq("渲染幂等（二次转换结果一致）", once, twice)

    # 语义等价：解析原文与解析产物得到同一块结构
    a = [(b.kind, b.level, b.text if b.kind != "table" else b.rows,
          tuple(b.items) if b.kind == "list" else None) for b in doc.blocks]
    b_ = [(b.kind, b.level, b.text if b.kind != "table" else b.rows,
           tuple(b.items) if b.kind == "list" else None)
          for b in parse_markdown(once).blocks]
    eq("往返后块结构等价", b_, a)


# ================================================================ 4. 1.0 兼容
def test_v1_compat(tmp: Path):
    section("4. 1.0 兼容（txt → md）")

    from core.legacy import to_markdown

    src = "读书笔记\n\n第一章 简介\n\n1. 记录要点\n2. 整理成文\n\n正文内容。"
    eq("首段升为一级标题",
       to_markdown(src, True),
       "# 读书笔记\n\n第一章 简介\n\n1. 记录要点\n2. 整理成文\n\n正文内容。\n")

    caps = "INTRODUCTION\n\nThis is body text."
    # 1.0 规则：大写判断优先于首段标题，且该行占用标题名额，后续段落不再升级
    eq("全大写行升为二级标题",
       to_markdown(caps, True),
       "## Introduction\n\nThis is body text.\n")

    existing = "# 已有标题\n\n普通段落。"
    eq("已有标题不再重复加",
       to_markdown(existing, True),
       "# 已有标题\n\n普通段落。\n")

    plain = "原样\n\n内容,不改。"
    # 1.0 的 plain 会补一个末尾换行
    eq("--plain 逐字保留", to_markdown(plain, False), plain + "\n")

    # 端到端：走管线也应得到同样结果
    f = tmp / "v1.txt"
    f.write_bytes(src.encode("gbk"))
    job = Job(src=f, dst=tmp / "v1.md", src_format="txt", dst_format="md")
    job.options["smart"] = True
    r = convert_one(job, force=True)
    check("管线 txt→md 成功", r.status == "ok", r.reason)
    eq("管线产物与 1.0 算法一致",
       (tmp / "v1.md").read_text("utf-8"), to_markdown(src, True))


# ================================================================ 5. 跨格式保真
def test_fidelity(tmp: Path):
    section("5. 跨格式保真")

    md = tmp / "rich.md"
    md.write_text(RICH_MD, encoding="utf-8")

    def convert(src: Path, fmt: str, out: Path) -> tuple[bool, str]:
        r = convert_one(Job(src=src, dst=out / (src.stem + "." + fmt),
                            src_format=src.suffix.lstrip("."), dst_format=fmt),
                        force=True)
        return r.status == "ok", r.reason

    # -- CSV / XLSX 数值保真
    csvf = tmp / "num.csv"
    csvf.write_text("名称,编号,金额\n甲,007,12.50\n乙,100,0.5\n", encoding="utf-8")
    ok, err = convert(csvf, "xlsx", tmp)
    check("csv→xlsx", ok, err)
    ok, err = convert(tmp / "num.xlsx", "csv", tmp)
    check("xlsx→csv", ok, err)
    eq("前导零与小数往返不变",
       (tmp / "num.csv").read_text("utf-8").strip(),
       "名称,编号,金额\n甲,007,12.50\n乙,100,0.5")

    # -- MD → CSV（表格）
    ok, err = convert(md, "csv", tmp)
    check("md→csv 导出表格", ok, err)
    csv_body = (tmp / "rich.csv").read_text("utf-8")
    check("csv 含表格数据", "苹果" in csv_body and "12" in csv_body, csv_body[:80])

    # -- MD → XLSX → MD 结构往返
    ok, err = convert(md, "xlsx", tmp)
    check("md→xlsx", ok, err)
    ok, err = convert(tmp / "rich.xlsx", "md", tmp / "back_xlsx")
    check("xlsx→md", ok, err)
    back = (tmp / "back_xlsx" / "rich.md").read_text("utf-8")
    for token in ("# 项目标题", "## 二级标题", "**粗体**", "嵌套项", "> 引用块内容",
                  "```python", "苹果", "结尾段落。", "---"):
        check(f"xlsx 往返保留「{token}」", token in back)

    # -- MD → TXT 去标记
    ok, err = convert(md, "txt", tmp)
    check("md→txt", ok, err)
    txt = (tmp / "rich.txt").read_text("utf-8")
    check("txt 无粗体标记", "**" not in txt, txt[:80])
    check("txt 保留全部文字", all(w in txt for w in
                              ("项目标题", "粗体", "嵌套项", "苹果", "结尾段落")))
    check("txt 表格用制表符", "名称\t数量" in txt)

    # -- --plain 逐字
    r = convert_one(Job(src=md, dst=tmp / "plain.txt", src_format="md", dst_format="txt",
                        options={"verbatim": True}), force=True)
    check("--plain md→txt", r.status == "ok", r.reason)
    eq("--plain 只改扩展名不改内容",
       (tmp / "plain.txt").read_text("utf-8"), RICH_MD)

    # -- 列表结构往返：嵌套层回到外层时不能被插进空行，否则一个列表会被
    #    GFM 判成两个 loose list，语义就变了
    lst = tmp / "list.md"
    lst.write_text("# 标题一\n\n正文段落，一句话说完了。\n\n## 标题二\n\n"
                   "- 甲\n  - 甲一\n- 乙\n", encoding="utf-8")
    ok, err = convert(lst, "xlsx", tmp / "lx")
    check("列表源 md→xlsx", ok, err)
    ok, err = convert(tmp / "lx" / "list.xlsx", "md", tmp / "lx_back")
    check("列表 xlsx→md", ok, err)
    lx = (tmp / "lx_back" / "list.md").read_text("utf-8")
    check("xlsx 往返列表不被空行劈开", "- 甲\n  - 甲一\n- 乙" in lx,
          lx.replace("\n", "\\n"))

    # -- MD → PDF → MD：正文不能被提取两遍，标题与列表要认得出来
    ok, err = convert(lst, "pdf", tmp / "lp")
    check("md→pdf（需要浏览器）", ok, err)
    if ok:
        ok, err = convert(tmp / "lp" / "list.pdf", "md", tmp / "lp_back")
        check("pdf→md", ok, err)
        lp = (tmp / "lp_back" / "list.md").read_text("utf-8")
        check("pdf 读取不重复正文", lp.count("正文段落") == 1, lp)
        check("pdf 识别出标题层级", "# 标题一" in lp and "## 标题二" in lp,
              lp.replace("\n", "\\n"))
        check("pdf 识别出列表与嵌套", "- 甲" in lp and "甲一" in lp,
              lp.replace("\n", "\\n"))


# ================================================================ 6. 防呆
def test_safety(tmp: Path):
    section("6. 防呆")

    # 源 = 目标
    f = tmp / "self.md"
    f.write_text("# 标题\n\n内容\n", encoding="utf-8")
    r = convert_one(Job(src=f, dst=f, src_format="md", dst_format="md"), force=True)
    eq("源与目标同一文件被拒绝", r.status, "error")
    check("拒绝原因明确", "同一个文件" in r.reason, r.reason)
    eq("原件未被破坏", f.read_text("utf-8"), "# 标题\n\n内容\n")

    # 目标已存在
    src = tmp / "dup.md"
    src.write_text("# 新内容\n", encoding="utf-8")
    dst = tmp / "dup.txt"
    dst.write_text("旧内容\n", encoding="utf-8")
    r = convert_one(Job(src=src, dst=dst, src_format="md", dst_format="txt"), force=False)
    eq("目标已存在时跳过", r.status, "skipped")
    eq("旧文件未被覆盖", dst.read_text("utf-8"), "旧内容\n")
    r = convert_one(Job(src=src, dst=dst, src_format="md", dst_format="txt"), force=True)
    eq("force 后覆盖成功", r.status, "ok")

    # 空文件
    empty = tmp / "empty.txt"
    empty.write_bytes(b"")
    r = convert_one(Job(src=empty, dst=tmp / "empty.md", src_format="txt", dst_format="md"))
    eq("空文件被拒绝", r.status, "error")
    check("空文件提示明确", "空文件" in r.reason, r.reason)

    # 二进制伪装成文本
    fake = tmp / "fake.txt"
    fake.write_bytes(b"MZ\x90\x00" + bytes(range(256)) * 4)
    r = convert_one(Job(src=fake, dst=tmp / "fake.md", src_format="txt", dst_format="md"))
    eq("二进制冒充文本被拒绝", r.status, "error")
    check("提示不是文本", "二进制" in r.reason, r.reason)

    # 不存在的路径
    r = convert_one(Job(src=tmp / "nope.txt", dst=tmp / "nope.md",
                        src_format="txt", dst_format="md"))
    eq("不存在的源被拒绝", r.status, "error")

    # 批量同名冲突：a.txt 与 a.md 都转 pdf 时不能互相覆盖
    d = tmp / "collide"
    d.mkdir()
    (d / "a.txt").write_text("文本内容甲\n", encoding="utf-8")
    (d / "a.md").write_text("# 标题乙\n", encoding="utf-8")
    jobs = plan_jobs(sorted(d.iterdir()), "txt", tmp / "collide_out")
    check("批量同名目标自动改名", len({str(j.dst) for j in jobs}) == len(jobs),
          [str(j.dst) for j in jobs])

    # 失败不留残骸
    bad = tmp / "bad.docx"
    bad.write_bytes(b"not a real docx at all")
    target = tmp / "bad.md"
    r = convert_one(Job(src=bad, dst=target, src_format="docx", dst_format="md"))
    eq("损坏文件转换失败", r.status, "error")
    check("失败后不产生残留文件", not target.exists(), "目标不该存在")

    # 预检不执行
    ok_f = tmp / "pre.txt"
    ok_f.write_text("预检用\n", encoding="utf-8")
    jobs = [Job(src=ok_f, dst=tmp / "pre.md", src_format="txt", dst_format="md")]
    ready, blocked, _ = precheck(jobs)
    eq("预检识别可执行任务", len(ready), 1)
    check("预检未真正写文件", not (tmp / "pre.md").exists())


# ================================================================ 9. 命令行输出
def test_cli_output(tmp: Path):
    section("9. 命令行输出兼容")

    # Windows 中文控制台是 cp936，◐ / ✓ / ✗ 都不在码表里。直接 print 会抛
    # UnicodeEncodeError 把程序整个崩掉——重定向输出到文件时同样崩。
    # 这里把输出编码强制成 gbk 来模拟那个环境。
    import os
    import subprocess

    root = Path(__file__).resolve().parent.parent
    env = dict(os.environ, PYTHONIOENCODING="gbk")
    for arg in ("--formats", "--engines", "--version"):
        p = subprocess.run([sys.executable, str(root / "txt2md.py"), arg],
                           capture_output=True, env=env, cwd=str(root))
        check(f"{arg} 在 GBK 输出下不崩溃", p.returncode == 0,
              p.stderr.decode("utf-8", "replace")[-300:])


# ================================================================ 7. 全矩阵
def test_matrix(tmp: Path):
    section("7. 全格式组合不崩溃")

    srcs: dict[str, Path] = {}
    samples = {
        "txt": "纯文本第一行\n\n第二段内容。\n",
        "md": RICH_MD,
        "html": "<html><body><h1>标题</h1><p>段落</p></body></html>",
        "csv": "列A,列B\n1,2\n3,4\n",
    }
    for fmt, body in samples.items():
        p = tmp / f"m_{fmt}.{fmt}"
        p.write_text(body, encoding="utf-8")
        srcs[fmt] = p

    # 由文本类样例派生 docx / xlsx，避免依赖外部样例文件
    for src_fmt, dst_fmt in (("md", "docx"), ("md", "xlsx"), ("md", "pdf")):
        out = tmp / f"m_{dst_fmt}.{dst_fmt}"
        r = convert_one(Job(src=srcs[src_fmt], dst=out, src_format=src_fmt,
                            dst_format=dst_fmt), force=True)
        if r.status == "ok":
            srcs[dst_fmt] = out

    crashes, empties = [], []
    pairs = 0
    for s_fmt, s_path in list(srcs.items()):
        for d_fmt in FORMATS:
            if d_fmt == s_fmt:
                continue
            pairs += 1
            out_dir = tmp / "matrix" / f"{s_fmt}2{d_fmt}"
            out_dir.mkdir(parents=True, exist_ok=True)
            dst = out_dir / f"x.{d_fmt}"
            try:
                r = convert_one(Job(src=s_path, dst=dst, src_format=s_fmt,
                                    dst_format=d_fmt), force=True)
            except Exception as exc:  # 崩溃才是问题，转换失败可以是能力限制
                crashes.append(f"{s_fmt}→{d_fmt}: {type(exc).__name__}: {exc}")
                continue
            if r.status == "ok" and (not dst.exists() or dst.stat().st_size == 0):
                empties.append(f"{s_fmt}→{d_fmt}")

    check(f"{pairs} 个格式组合无崩溃", not crashes, "; ".join(crashes[:3]))
    check("成功的产物都不是空文件", not empties, "; ".join(empties[:5]))


# ================================================================ 8. 产物校验
def test_verify(tmp: Path):
    section("8. 产物校验")

    from core.pipeline import verify_output

    # 空产物必须被发现
    empty = tmp / "zero.md"
    empty.write_bytes(b"")
    try:
        verify_output(empty, "md")
        check("空产物被拦截", False, "未抛异常")
    except ConvertError:
        check("空产物被拦截", True)

    # 内容变空也要被发现（源有内容）
    doc = Document(kind="text", text=TextDoc())
    doc.text.add(__import__("core.model", fromlist=["Block"]).Block.para("原文"))
    blank = tmp / "blank.md"
    blank.write_text("   \n", encoding="utf-8")
    try:
        verify_output(blank, "md", doc)
        check("产物空白但有源内容时拦截", False, "未抛异常")
    except ConvertError:
        check("产物空白但有源内容时拦截", True)

    # 假 PDF
    fake = tmp / "fake.pdf"
    fake.write_bytes(b"not a pdf")
    try:
        verify_output(fake, "pdf")
        check("伪 PDF 被拦截", False, "未抛异常")
    except ConvertError:
        check("伪 PDF 被拦截", True)


# ================================================================ main
def main() -> int:
    print("=" * 66)
    print("txt2md 2.0 转换内核测试")
    print("=" * 66)

    tmp = Path(tempfile.mkdtemp(prefix="txt2md-test-"))
    try:
        test_encoding(tmp)
        test_inline_strip()
        test_markdown_roundtrip()
        test_v1_compat(tmp)
        test_fidelity(tmp)
        test_safety(tmp)
        test_matrix(tmp)
        test_verify(tmp)
        test_cli_output(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 66)
    total = PASS + FAIL
    if FAIL:
        print(f"失败 {FAIL} / {total}")
        for f in FAILURES:
            print(f"  · {f}")
        print("=" * 66)
        return 1
    print(f"全部通过：{PASS} 项")
    for name, n in BY_SECTION.items():
        print(f"  {name}  {n}")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
