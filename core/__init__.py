"""AI eye 转换内核。

分层
----
``model``     统一文档模型（Block / TextDoc / Workbook / Document）
``mdparse``   Markdown 解析与行内降级
``htmlio``    HTML 解析与渲染（同时承担 PDF 的打印版排版）
``readers``   各格式 → 模型
``writers``   模型 → 各格式
``registry``  格式表、引擎探测、能力矩阵
``pipeline``  任务规划、防呆预检、执行与产物校验
``errors``    分层异常

对外只需要 ``convert`` 与 ``run_jobs``。
"""

from __future__ import annotations

from pathlib import Path

from .errors import (ConvertError, DependencyMissing, DestinationError,
                     EngineMissing, SelfOverwrite, SourceError,
                     UnsupportedFormat)
from .pipeline import (Job, Result, check, collect_inputs,
                       convert_one, plan_jobs, precheck, run, summarize,
                       verify_output)
from .registry import (ALL_EXTENSIONS, FORMATS, capability, describe_environment,
                       detect_engines, matrix, resolve_format)

__all__ = [
    # 高层入口
    "convert", "run_jobs",
    # 类型
    "Job", "Result",
    # 格式与引擎
    "FORMATS", "resolve_format", "capability", "matrix",
    "detect_engines", "describe_environment", "ALL_EXTENSIONS",
    # 流程
    "collect_inputs", "plan_jobs", "precheck", "check", "run",
    "convert_one", "summarize", "verify_output",
    # 异常
    "ConvertError", "SourceError", "DestinationError", "EngineMissing",
    "DependencyMissing", "UnsupportedFormat", "SelfOverwrite",
]


def convert(src: str | Path, dst: str | Path | None = None, *,
            to: str = "", **opts) -> Result:
    """单文件转换的便捷入口。

    ``dst`` 省略时按 ``to`` 决定扩展名，输出到源文件旁边。
    """
    src = Path(src)
    dst_fmt = resolve_format(to) or (resolve_format(Path(dst).suffix) if dst else None)
    if dst_fmt is None:
        from .registry import FORMATS as _F
        dst_fmt = _F["md"]

    if dst is None:
        dst = src.with_suffix(dst_fmt.extensions[0])
    dst = Path(dst)

    from .registry import format_of_path
    sfmt = format_of_path(src)
    job = Job(src=src, dst=dst,
              src_format=(sfmt.code if sfmt else ""), dst_format=dst_fmt.code)
    job.options.update(opts)
    return convert_one(job, force=bool(opts.get("force")), verify=not opts.get("no_verify"))


def run_jobs(inputs: list[str | Path], to: str, out_dir: str | Path | None = None,
             *, force: bool = False, from_format: str = "", **opts):
    """批量转换：收集 → 规划 → 预检 → 执行。返回 (结果列表, 汇总)。"""
    targets = [Path(p) for p in inputs]
    files, notes = collect_inputs(targets, src_format=from_format)
    root = targets[0] if targets and targets[0].is_dir() else None
    jobs = plan_jobs(files, to, Path(out_dir) if out_dir else None, src_root=root)
    for job in jobs:
        job.options.update(opts)
    ready, blocked, _ = precheck(jobs, force=force)
    results = run(jobs, force=force, verify=not opts.get("no_verify"), prechecked=True)
    out = summarize(results)
    out["notes"] = notes
    return results, out
