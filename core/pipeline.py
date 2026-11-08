"""转换编排：任务规划、预检、执行、产物校验。

防呆设计（本模块的主要职责）
--------------------------
转换工具最常见的事故不是「转不出来」，而是**悄悄把东西弄坏**。所以这里宁可
拒绝也不将就：

| 风险 | 处理 |
|---|---|
| 源与目标是同一文件（如 a.md → a.md 就地覆盖） | **直接拒绝**，不询问、不备份 |
| 目标已存在 | 默认跳过，需显式 ``force`` 才覆盖 |
| 批量中两个源产出同名目标（a.txt 与 a.md 都转 a.pdf） | 自动加 `` (2)`` 后缀，不互相覆盖 |
| 目标路径是个目录 | 拒绝并说明 |
| 格式不支持 / 当前环境缺引擎 | **转换前**就拦下，不浪费用户时间 |
| 转换中途失败 | 原子写入，不留半个文件 |
| 产出为空（源有内容而结果空白） | 判为失败，不写空文件糊弄过去 |
| 目录递归时混入二进制或非目标格式 | 按扩展名白名单过滤，不盲目尝试 |
| 批量中单个文件失败 | 不中断其余任务，最后汇总 |

``precheck()`` 与 ``run()`` 分开，是为了让 GUI 能在真正动手前就展示
「哪些会成功、哪些会被跳过、为什么」。
"""

from __future__ import annotations

import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from .errors import (ConvertError, DestinationError, EngineMissing, SelfOverwrite,
                     SourceError, UnsupportedFormat)
from .readers import read_any
from .registry import (ALL_EXTENSIONS, FORMATS, capability, detect_engines,
                       format_of_path, resolve_format)
from .writers import write_any


@dataclass
class Job:
    src: Path
    dst: Path
    src_format: str
    dst_format: str

    # 执行期选项
    options: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        return f"{self.src.name} → {self.dst.name}"


@dataclass
class Result:
    status: str            # ok / skipped / error
    src: str
    dst: str
    reason: str = ""       # error/skipped 的原因
    engine: str = ""       # 实际使用的引擎
    warnings: list[str] = field(default_factory=list)
    seconds: float = 0.0
    # 生成内容的 Markdown 表示，供界面「查看结果 / 复制」用。不落盘、不进产物，
    # 任何目标格式都能预览——docx/pdf 的产物本身没法直接读成文本。
    preview: str = ""

    # 本次任务顺带生成的 AI eye 导读路径（未开启时为空）
    guide: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"


# ---------------------------------------------------------------- 收集输入
def collect_inputs(targets: list[Path], src_format: str = "",
                   recursive: bool = True) -> tuple[list[Path], list[str]]:
    """收集待转换文件。返回 (文件列表, 提示信息)。

    目录按扩展名白名单递归；显式给出的单文件不受白名单限制（用户意图明确）。
    """
    files: list[Path] = []
    notes: list[str] = []
    wanted = resolve_format(src_format) if src_format else None
    exts = wanted.extensions if wanted else ALL_EXTENSIONS

    for t in targets:
        if t.is_dir():
            pattern = "**/*" if recursive else "*"
            found = sorted(p for p in t.glob(pattern)
                           if p.is_file() and p.suffix.lower() in exts)
            if not found:
                notes.append(f"目录 {t} 中没有找到可转换的文件"
                             f"（支持：{'、'.join(e.lstrip('.') for e in exts)}）")
            files.extend(found)
        elif t.is_file():
            files.append(t)
        else:
            notes.append(f"路径不存在：{t}")

    # 去重但保持顺序
    seen: set[str] = set()
    uniq: list[Path] = []
    for f in files:
        key = str(f.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(f)
    return uniq, notes


# ---------------------------------------------------------------- 路径规划
def _same_file(a: Path, b: Path) -> bool:
    try:
        if a.exists() and b.exists():
            return a.samefile(b)
        return a.resolve() == b.resolve()
    except (OSError, ValueError):
        return False


def plan_jobs(files: list[Path], dst_format: str, out_dir: Path | None = None,
              src_root: Path | None = None) -> list[Job]:
    """为每个源文件算目标路径，并就地解决同名冲突。"""
    jobs: list[Job] = []
    used: dict[str, int] = {}
    fmt = resolve_format(dst_format) or FORMATS["md"]
    ext = fmt.extensions[0]

    for src in files:
        sfmt = format_of_path(src)
        if out_dir is not None:
            if src_root is not None and src_root.is_dir():
                try:
                    rel = src.parent.relative_to(src_root)
                except ValueError:
                    rel = Path()
                base = out_dir / rel
            else:
                base = out_dir
        else:
            base = src.parent

        dst = base / (src.stem + ext)

        # 同批次内同名冲突（a.txt 与 a.md 都转 a.pdf）→ 加序号，避免互相覆盖
        key = str(dst.resolve()).lower()
        if key in used or (_same_file(dst, src) and src.suffix.lower() == ext):
            used[key] = used.get(key, 1) + 1
            dst = base / f"{src.stem} ({used[key]}){ext}"
            while str(dst.resolve()).lower() in used:
                used[key] += 1
                dst = base / f"{src.stem} ({used[key]}){ext}"
        used[str(dst.resolve()).lower()] = 1

        jobs.append(Job(src=src, dst=dst,
                        src_format=(sfmt.code if sfmt else ""),
                        dst_format=fmt.code))
    return jobs


# ---------------------------------------------------------------- 预检
def check(job: Job, *, force: bool = False) -> str:
    """返回空串表示可以执行，否则返回拒绝原因（面向用户）。"""
    src, dst = job.src, job.dst

    if not src.exists():
        return "源文件不存在"
    if src.is_dir():
        return "源路径是目录"
    try:
        if src.stat().st_size == 0:
            return "源文件是空文件（0 字节）"
    except OSError as exc:
        return f"无法读取源文件信息：{exc}"

    if not job.src_format:
        return f"无法识别源格式（扩展名 {src.suffix or '空'}）"
    if not job.dst_format:
        return "未指定目标格式"

    # 自毁红线：源与目标是同一个文件
    if _same_file(src, dst):
        return "源与目标是同一个文件，转换会覆盖原件（已拒绝）"

    if dst.is_dir():
        return "目标路径已存在且是目录"

    engines = detect_engines()
    cs = capability(job.src_format, engines)
    cd = capability(job.dst_format, engines)
    if not cs.can_read:
        return f"当前环境无法读取 {job.src_format}"
    if not cd.can_write:
        return f"当前环境无法写出 {job.dst_format}" + (
            f"（缺少渲染引擎：{_missing_hint(job.dst_format, engines)}）"
            if job.dst_format == "pdf" else "")

    if dst.exists() and not force:
        return "目标文件已存在（未开启覆盖）"
    if not _dir_writable(dst.parent):
        return f"输出目录不可写：{dst.parent}"
    return ""


def _missing_hint(fmt: str, engines) -> str:
    if fmt == "pdf":
        return "需要 Microsoft Edge / Google Chrome，或 pandoc + LaTeX"
    return "请安装对应依赖"


def _dir_writable(path: Path) -> bool:
    probe = path
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    if not probe.exists():
        return False
    import os
    return os.access(probe, os.W_OK)


def precheck(jobs: list[Job], *, force: bool = False) -> tuple[list[Job], list[Job], list[str]]:
    """把任务分成 (可执行, 被拦下, 说明)。被拦下的 Job 用 options['_blocked_by'] 记原因。"""
    ready, blocked = [], []
    for job in jobs:
        reason = check(job, force=force)
        if reason:
            job.options["_blocked_by"] = reason
            blocked.append(job)
        else:
            ready.append(job)
    return ready, blocked, []


# ---------------------------------------------------------------- 执行
# 预览文本上限：整本扫描书的 Markdown 可能几十 MB，全塞进内存只为看几行
# 正文不值得。2MB 足够显示几万字，超出部分用户去开产物文件。
PREVIEW_LIMIT = 2_000_000


def convert_one(job: Job, *, force: bool = False, verify: bool = True,
                progress=None) -> Result:
    """执行单个转换任务。所有异常都转成 Result，不向上抛。

    插图会先落在私有临时目录（几百张图不适合常驻内存），任务结束无论成败
    都清掉——md/html 需要的图在这一步已经被复制到目标目录了。

    ``progress(fraction, label)``：当前**文件内**的进度，fraction 为 0~1。
    读取器按页/按章上报（PDF 逐页、EPUB 逐章），其它格式只在首尾各报一次。
    """
    import shutil
    start = time.perf_counter()
    opts = dict(job.options)
    asset_root = Path(tempfile.mkdtemp(prefix="aieye-assets-"))
    try:
        return _convert_one_inner(job, force=force, verify=verify, opts=opts,
                                  asset_root=asset_root, start=start,
                                  progress=progress)
    finally:
        shutil.rmtree(asset_root, ignore_errors=True)


def _wrap_reader_progress(progress):
    """把读取器的 ``(done, total, label)`` 适配成对外的 ``(fraction, label)``。

    读取器不必知道批次里还有多少个文件，只报自己这一份的进度；批次总进度
    由调用方（界面/命令行）用「已完成文件数 + 当前比例」算，两边职责不混。
    """
    if progress is None:
        return None

    def cb(done: int, total: int, label: str = "") -> None:
        frac = (done / total) if total > 0 else 0.0
        progress(max(0.0, min(1.0, frac)), label)
    return cb


def _progress_span(progress, lo: float, hi: float):
    """把一个 0~1 的进度映射到 [lo, hi] 区间。

    读取占总进度的大头（PDF 的 OCR 每页几秒，写出几乎瞬时），所以读取段
    分到 0~0.72，写出与校验分剩下的部分。
    """
    if progress is None:
        return None

    def f(frac: float, label: str = "") -> None:
        progress(lo + (hi - lo) * max(0.0, min(1.0, frac)), label)
    return f


def _progress_kw(cb) -> dict:
    """只在有回调时把 progress 塞进读取器参数。

    read_any 会按读取器签名过滤参数，但直接传 ``progress=None`` 会让
    「有没有进度」这件事变得不可判——读取器内部就不必再判空。
    """
    return {"progress": cb} if cb is not None else {}


def _convert_one_inner(job: Job, *, force: bool, verify: bool, opts: dict,
                       asset_root: Path, start: float, progress=None) -> Result:
    try:
        # 预检必须在 try 内：check() 里要碰文件系统（stat / exists / 目录可写性
        # 探测），网络盘掉线、路径含非法字符、权限被回收都会在这里抛 OSError。
        # 放在 try 外面，异常会穿出 convert_one，批量任务和界面线程一起被拖死。
        reason = check(job, force=force)
        if reason:
            # 区分「跳过」与「失败」：目标已存在属于正常的跳过
            status = "skipped" if reason.startswith("目标文件已存在") else "error"
            return Result(status=status, src=str(job.src), dst=str(job.dst), reason=reason)

        if progress:
            progress(0.0, "准备中")

        # ---- 同格式：只复制，不重组 ----------------------------
        # 用户选错目标格式时，最坏的结果不该是「内容被重新排版了一遍」。
        # 逐字节复制能保证这种情况下数据零损失。
        if job.src_format == job.dst_format:
            import shutil
            job.dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(job.src, job.dst)
            if verify:
                verify_output(job.dst, job.dst_format)
            if progress:
                progress(1.0, "完成")
            return Result(status="ok", src=str(job.src), dst=str(job.dst),
                          engine="直接复制", seconds=time.perf_counter() - start)

        # ---- 1.0 直通通道 --------------------------------------
        # txt → md 是 1.0 的既有行为，用原算法直接产出，保证字节级一致、
        # 不因架构重构产生细微差异（空行、标题后空格等）。
        if job.src_format == "txt" and job.dst_format == "md":
            from .legacy import to_markdown
            from .readers import decode_bytes, looks_binary
            from .writers import atomic_write_text
            raw = job.src.read_bytes()
            # 直通通道也必须过输入校验，否则二进制文件会被当文本解码成乱码
            if looks_binary(raw):
                raise SourceError(f"{job.src.name} 看起来是二进制文件，不是文本",
                                  hint="请确认文件类型；如确实是文本，可能编码异常")
            text, enc = decode_bytes(raw)
            if not text.strip():
                raise SourceError("源文件没有可转换的内容")
            if enc.endswith("*"):
                job.options.setdefault("_encoding_warning",
                                       f"编码无法可靠识别，已按 {enc.rstrip('*')} 容错解码")
            md = to_markdown(text, smart=bool(opts.get("smart", True)))
            atomic_write_text(job.dst, md)
            if verify:
                verify_output(job.dst, "md")
            warnings = [job.options["_encoding_warning"]] if "_encoding_warning" in job.options else []
            if progress:
                progress(1.0, "完成")
            return Result(status="ok", src=str(job.src), dst=str(job.dst),
                          engine="内置", warnings=warnings,
                          preview=md[:PREVIEW_LIMIT],
                          seconds=time.perf_counter() - start)

        doc = read_any(job.src, job.src_format,
                       **_reader_opts(opts, asset_root),
                       **_progress_kw(_wrap_reader_progress(
                           _progress_span(progress, 0.0, 0.72))))
        if doc.is_empty():
            raise SourceError("源文件没有可转换的内容")

        if progress:
            progress(0.74, "写出中")
        engine = write_any(doc, job.dst, job.dst_format, **_writer_opts(opts))
        if progress:
            progress(0.96, "校验中")

        warnings = list(doc.warnings)
        if verify:
            warnings.extend(verify_output(job.dst, job.dst_format, doc))
        if progress:
            progress(1.0, "完成")

        # ---- AI eye 导读（可选）----------------------------------
        # 放在产物校验之后：产物写失败时不该留下一份「指向不存在文件」的导读。
        guide = ""
        if opts.get("aieye"):
            try:
                from .aieye_doc import app_version, write_guide
                gp = write_guide(doc, job.dst,
                                 source_name=job.src.name,
                                 target_format=job.dst_format,
                                 app_version=app_version(),
                                 images_prefix=_guide_images_prefix(job, opts))
                guide = str(gp)
            except Exception as exc:
                # 导读是附加物，失败不能把已经成功的转换判为失败
                warnings.append(f"AI eye 导读未生成：{type(exc).__name__}: {exc}")

        return Result(status="ok", src=str(job.src), dst=str(job.dst),
                      engine=engine, warnings=warnings,
                      preview=_preview_of(doc), guide=guide,
                      seconds=time.perf_counter() - start)

    except ConvertError as exc:
        # 兜底不能凌驾于防呆之上：源文件是二进制/空文件/扫描件时，pandoc 会把它
        # 当文本读成乱码而不是报错，那就等于绕过校验产出垃圾。这类错误直接失败。
        blocked = isinstance(exc, SourceError) and any(
            m in exc.message for m in ("二进制", "空文件", "0 字节", "没有可", "扫描版"))
        alt = None if blocked else _try_pandoc(job)
        if alt:
            return Result(status="ok", src=str(job.src), dst=str(job.dst),
                          engine=alt,
                          warnings=[f"内置引擎未能处理（{exc.message}），已改用 pandoc 完成"],
                          seconds=time.perf_counter() - start)
        return Result(status="error", src=str(job.src), dst=str(job.dst),
                      reason=str(exc), seconds=time.perf_counter() - start)
    except PermissionError as exc:
        return Result(status="error", src=str(job.src), dst=str(job.dst),
                      reason=f"没有写入权限：{exc.filename or job.dst}",
                      seconds=time.perf_counter() - start)
    except OSError as exc:
        # 磁盘满、路径过长、文件被占用等
        return Result(status="error", src=str(job.src), dst=str(job.dst),
                      reason=f"文件操作失败：{exc}", seconds=time.perf_counter() - start)
    except Exception as exc:  # 兜底：单个文件失败不中断批量
        return Result(status="error", src=str(job.src), dst=str(job.dst),
                      reason=f"内部错误：{type(exc).__name__}: {exc}",
                      seconds=time.perf_counter() - start)


def _try_pandoc(job: Job) -> str | None:
    """尝试 pandoc 兜底；失败时清掉可能的半成品，返回 None。"""
    try:
        from . import pandoc_fallback
        if not pandoc_fallback.can_handle(job.src_format, job.dst_format):
            return None
        engine = pandoc_fallback.convert(job.src, job.dst, job.src_format, job.dst_format)
        if engine is None:
            try:
                job.dst.unlink(missing_ok=True)
            except OSError:
                pass
        return engine
    except Exception:
        return None


def _reader_opts(opts: dict, assets_dir: Path | None = None) -> dict:
    keys = {"start_heading", "verbatim", "encoding", "delimiter", "has_header",
            "sheets", "ocr", "images", "layout"}
    out = {k: v for k, v in opts.items() if k in keys}
    # 只有要图时才给目录：不产图的任务不该多建一个空目录
    if assets_dir is not None and out.get("images", True):
        out["assets_dir"] = assets_dir
    return out


def _writer_opts(opts: dict) -> dict:
    keys = {"delimiter", "numeric", "images"}
    return {k: v for k, v in opts.items() if k in keys}


def _guide_images_prefix(job: Job, opts: dict) -> str:
    """导读里引用插图的前缀，必须与写出器实际使用的目录一致。

    md/html 把图落成独立文件，导读里的路径要能点开；docx/pdf/epub 把图内嵌，
    导读里给文件名即可（方便人看图注与序号对应）。
    """
    if job.dst_format not in ("md", "html") or not opts.get("images", True):
        return ""
    custom = opts.get("assets_dir")
    if custom:
        try:
            import os as _os
            rel = _os.path.relpath(str(custom), str(job.dst.parent))
            return rel.replace("\\", "/")
        except ValueError:              # 跨盘符没法算相对路径
            return ""
    return f"{job.dst.stem}_assets"


def _preview_of(doc) -> str:
    """把结果渲染成 Markdown 供界面预览。

    为什么不用产物文件：docx/pdf/xlsx 都是二进制，界面读不出文字；用模型
    渲染一次，任何目标格式都有可复制的内容。这里不写图片目录，路径是图片名。
    """
    try:
        from .writers import render_markdown
        return render_markdown(doc.as_text())[:PREVIEW_LIMIT]
    except Exception:
        return ""


# ---------------------------------------------------------------- 产物校验
def verify_output(path: Path, fmt: str, doc=None) -> list[str]:
    """确认产物真的存在且可读。返回警告（致命问题直接抛 ConvertError）。"""
    if not path.exists():
        raise DestinationError(f"转换结束但没有生成文件：{path}")
    size = path.stat().st_size
    if size == 0:
        raise DestinationError("生成的产物是空文件，已中止")

    if fmt in ("txt", "md", "html", "csv"):
        try:
            head = path.read_bytes()[:4]
        except OSError as exc:
            raise DestinationError(f"产物无法读取：{exc}") from exc
        if fmt == "html" and not head.lstrip().startswith(b"<"):
            raise DestinationError("HTML 产物格式异常")
        if fmt in ("txt", "md") and b"\x00" in path.read_bytes()[:4096]:
            raise DestinationError("文本产物中含二进制空字节")
        # 源有内容而产物近乎空白 → 视为失败，避免「看起来成功了但东西没了」
        if doc is not None and fmt in ("txt", "md"):
            try:
                body = path.read_text("utf-8")
            except UnicodeDecodeError:
                raise DestinationError("文本产物不是有效 UTF-8")
            if not body.strip() and not doc.is_empty():
                raise DestinationError("转换结果为空，源文件却有内容（已中止）")
        return []

    if fmt == "pdf":
        with path.open("rb") as fh:
            if not fh.read(5).startswith(b"%PDF"):
                raise DestinationError("PDF 产物缺少 %PDF 文件头")
        return []

    if fmt == "xlsx":
        import zipfile
        if not zipfile.is_zipfile(path):
            raise DestinationError("xlsx 产物不是有效的压缩包结构")
        return []

    if fmt == "docx":
        import zipfile
        if not zipfile.is_zipfile(path):
            raise DestinationError("docx 产物不是有效的压缩包结构")
        with zipfile.ZipFile(path) as z:
            if "word/document.xml" not in z.namelist():
                raise DestinationError("docx 产物缺少文档主体")
        return []

    return []


# ---------------------------------------------------------------- 批量
def run(jobs: list[Job], *, force: bool = False, verify: bool = True,
        prechecked: bool = False, progress=None) -> list[Result]:
    """按顺序执行任务。返回与 jobs 等长的结果列表。

    ``progress(index, total, fraction, label)``：index 从 0 起，fraction 是
    **当前文件**的比例。界面据此画一条通栏进度条并显示「第几个 / 共几个」。
    """
    results: list[Result] = []
    total = len(jobs)
    for i, job in enumerate(jobs):
        blocked = job.options.get("_blocked_by") if prechecked else None
        if blocked:
            status = "skipped" if blocked.startswith("目标文件已存在") else "error"
            results.append(Result(status=status, src=str(job.src),
                                  dst=str(job.dst), reason=blocked))
            if progress:
                progress(i + 1, total, 1.0, "已跳过")
            continue

        if progress:
            progress(i, total, 0.0, job.src.name)

        def _fp(frac: float, label: str = "", _i=i) -> None:
            if progress:
                progress(_i, total, frac, label)

        results.append(convert_one(job, force=force, verify=verify, progress=_fp))
    return results


def summarize(results: list[Result]) -> dict:
    ok = [r for r in results if r.status == "ok"]
    skip = [r for r in results if r.status == "skipped"]
    err = [r for r in results if r.status == "error"]
    return {
        "ok": len(ok), "skipped": len(skip), "error": len(err),
        "total": len(results),
        "seconds": round(sum(r.seconds for r in results), 2),
        "warnings": sum(len(r.warnings) for r in results),
    }
