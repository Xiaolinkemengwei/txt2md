"""一键构建 AI eye 发布产物。

产物（输出到 dist/）：
    AIeye.exe                     单文件可执行程序（GUI，含图标与版本资源）
    AIeye-<ver>-win64.zip         发布压缩包（exe + 文档 + 许可）
    aieye-<ver>-py3-none-any.whl  Python 安装包
    aieye-<ver>.tar.gz            源码分发包
    SHA256SUMS.txt                各产物的 SHA-256 校验和（供下载后核验）

用法：
    python tools/build.py              # 全部构建
    python tools/build.py --no-wheel   # 只构建 exe
    python tools/build.py --onedir     # 目录版（启动更快，体积解压后略大）
"""

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"

VERSION = re.search(r'__version__\s*=\s*"([^"]+)"',
                    (ROOT / "aieye.py").read_text("utf-8")).group(1)

DOC_FILES = ["README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "CHANGELOG.md"]


def run(cmd: list[str], env: dict | None = None):
    print(f"\n$ {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=ROOT, env=env)
    if proc.returncode != 0:
        raise SystemExit(f"命令失败（退出码 {proc.returncode}）：{' '.join(cmd)}")


def step_icon():
    print("\n[1/5] 生成应用图标")
    run([sys.executable, str(ROOT / "tools" / "make_icon.py")])


def step_exe(onedir: bool):
    print(f"\n[2/5] 打包可执行文件（{'目录版' if onedir else '单文件'}）")
    env = dict(os.environ)
    env["AIEYE_ONEFILE"] = "0" if onedir else "1"
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         str(ROOT / "aieye.spec")], env=env)


def _sweep_tmp():
    """清掉 dist/.tmp-* 残留。

    setuptools 的 sdist 会在 dist/ 下建临时目录再搬成品，进程被中断时
    临时目录会留下；下次构建若撞上同名残留会直接失败。
    """
    if not DIST.exists():
        return
    for d in DIST.glob(".tmp-*"):
        shutil.rmtree(d, ignore_errors=True)


def step_python_packages(failed: list):
    print("\n[3/5] 构建 Python 安装包（wheel + sdist）")
    _sweep_tmp()
    try:
        run([sys.executable, "-m", "pip", "wheel", ".", "-w", str(DIST), "--no-deps"])
    except SystemExit as e:
        print(f"跳过 wheel：{e}")
        failed.append("wheel")
    try:
        # 不用 `python -m build`：那要额外装 build 包。setuptools 自带 PEP 517
        # 后端接口，直接调即可，少一个构建期依赖。
        from setuptools import build_meta
        made = build_meta.build_sdist(str(DIST))
        print(f"  {made}")
    except Exception as e:
        # 这里不能只 print 后当作无事发生：退出码仍是 0，发布时容易漏掉
        # 一个产物还察觉不到。记入 failed，由 main 汇总成非零退出码。
        print(f"  !! sdist 构建失败：{e}")
        failed.append(f"sdist（{e}）")
    finally:
        _sweep_tmp()


def step_zip(onedir: bool):
    print("\n[4/5] 生成发布压缩包")
    name = f"AIeye-{VERSION}-win64.zip"
    zip_path = DIST / name

    if onedir:
        src_dir = DIST / "AIeye"
        if not src_dir.exists():
            raise SystemExit(f"未找到目录版产物：{src_dir}")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            for f in src_dir.rglob("*"):
                if f.is_file():
                    z.write(f, f.relative_to(DIST))
            for doc in DOC_FILES:
                p = ROOT / doc
                if p.exists():
                    z.write(p, doc)
        exe_size = sum(f.stat().st_size for f in src_dir.rglob("*") if f.is_file())
    else:
        exe = DIST / "AIeye.exe"
        if not exe.exists():
            raise SystemExit(f"未找到可执行文件：{exe}")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(exe, "AIeye.exe")
            for doc in DOC_FILES:
                p = ROOT / doc
                if p.exists():
                    z.write(p, doc)
        exe_size = exe.stat().st_size

    print(f"  {zip_path.name}  (解压后 {_human(exe_size)})")


def step_checksums():
    """为 dist/ 中的发布产物生成 SHA-256 校验和，便于用户下载后核验完整性。"""
    print("\n[5/5] 生成 SHA-256 校验和")
    targets = sorted(
        f for f in DIST.iterdir()
        if f.is_file() and f.name != "SHA256SUMS.txt"
        and f.suffix in {".exe", ".zip", ".whl", ".gz"}
    )
    lines = []
    for f in targets:
        h = hashlib.sha256()
        with f.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        lines.append(f"{h.hexdigest()}  {f.name}")
        print(f"  {h.hexdigest()[:16]}…  {f.name}")
    (DIST / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  已写入 {DIST / 'SHA256SUMS.txt'}")


def _human(size: int) -> str:
    """按量级选择单位，避免小文件显示成 0.0 MB。

    统一用 1024 进制并显式标注 MiB/KiB：Windows 资源管理器是这一套算法，
    而 GitHub 下载页按 1e6 计（同一个 exe 会写成 197.8 MB）。两边都写
    "MB" 会让用户以为其中一处算错了，标清进制才对得上号。
    """
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} MiB"
    if size >= 1024:
        return f"{size / 1024:.0f} KiB"
    return f"{size} B"


def summarize(failed: list):
    print("\n" + "=" * 62)
    print(f"构建完成  AI eye v{VERSION}")
    print("=" * 62)
    if DIST.exists():
        for f in sorted(DIST.iterdir()):
            if f.name.startswith("."):
                continue
            if f.is_file():
                print(f"  {f.name:<40} {_human(f.stat().st_size):>9}")
            elif f.name == "AIeye":
                size = sum(p.stat().st_size for p in f.rglob("*") if p.is_file())
                print(f"  {'AIeye/ (目录版)':<40} {_human(size):>9}")
    if failed:
        print("\n!! 以下产物构建失败，本次发布不完整：")
        for item in failed:
            print(f"   - {item}")
        print("   处理后可单独重跑对应步骤补产。")
        return 1
    print("\n提示：发布前请确认 dist/ 中的 exe 能正常启动，并以该版本号打 Git tag。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="构建 AI eye 发布产物")
    parser.add_argument("--onedir", action="store_true", help="构建目录版而非单文件")
    parser.add_argument("--no-wheel", action="store_true", help="跳过 wheel 构建")
    parser.add_argument("--clean", action="store_true", help="构建前清空 dist/ 与 build/")
    args = parser.parse_args()

    if args.clean:
        for d in (DIST, BUILD):
            if d.exists():
                shutil.rmtree(d)
                print(f"已清空 {d}")

    DIST.mkdir(exist_ok=True)
    failed: list[str] = []
    step_icon()
    step_exe(args.onedir)
    if not args.no_wheel:
        step_python_packages(failed)
    step_zip(args.onedir)
    step_checksums()
    return summarize(failed)


if __name__ == "__main__":
    sys.exit(main())
