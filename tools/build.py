"""一键构建发布产物。

产物（输出到 dist/）：
    txt2md.exe                    单文件可执行程序（GUI，含图标与版本资源）
    txt2md-<ver>-win64.zip        发布压缩包（exe + 文档 + 许可）
    txt2md-<ver>-py3-none-any.whl Python 安装包
    txt2md-<ver>.tar.gz           源码分发包

用法：
    python tools/build.py              # 全部构建
    python tools/build.py --no-wheel   # 只构建 exe
    python tools/build.py --onedir     # 目录版（启动更快，体积解压后略大）
"""

import argparse
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
                    (ROOT / "txt2md.py").read_text("utf-8")).group(1)

DOC_FILES = ["README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "CHANGELOG.md"]


def run(cmd: list[str], env: dict | None = None):
    print(f"\n$ {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=ROOT, env=env)
    if proc.returncode != 0:
        raise SystemExit(f"命令失败（退出码 {proc.returncode}）：{' '.join(cmd)}")


def step_icon():
    print("\n[1/4] 生成应用图标")
    run([sys.executable, str(ROOT / "tools" / "make_icon.py")])


def step_exe(onedir: bool):
    print(f"\n[2/4] 打包可执行文件（{'目录版' if onedir else '单文件'}）")
    env = dict(os.environ)
    env["TXT2MD_ONEFILE"] = "0" if onedir else "1"
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         str(ROOT / "txt2md.spec")], env=env)


def step_python_packages():
    print("\n[3/4] 构建 Python 安装包（wheel）")
    try:
        run([sys.executable, "-m", "pip", "wheel", ".", "-w", str(DIST), "--no-deps"])
    except SystemExit as e:
        print(f"跳过 wheel：{e}")


def step_zip(onedir: bool):
    print("\n[4/4] 生成发布压缩包")
    name = f"txt2md-{VERSION}-win64.zip"
    zip_path = DIST / name

    if onedir:
        src_dir = DIST / "txt2md"
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
        exe = DIST / "txt2md.exe"
        if not exe.exists():
            raise SystemExit(f"未找到可执行文件：{exe}")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(exe, "txt2md.exe")
            for doc in DOC_FILES:
                p = ROOT / doc
                if p.exists():
                    z.write(p, doc)
        exe_size = exe.stat().st_size

    print(f"  {zip_path.name}  (解压后 {exe_size / 1024 / 1024:.1f} MB)")


def _human(size: int) -> str:
    """按量级选择单位，避免小文件显示成 0.0 MB。"""
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} MB"
    return f"{size / 1024:.0f} KB"


def summarize():
    print("\n" + "=" * 62)
    print(f"构建完成  txt2md v{VERSION}")
    print("=" * 62)
    if DIST.exists():
        for f in sorted(DIST.iterdir()):
            if f.is_file():
                print(f"  {f.name:<40} {_human(f.stat().st_size):>9}")
            elif f.name == "txt2md":
                size = sum(p.stat().st_size for p in f.rglob("*") if p.is_file())
                print(f"  {'txt2md/ (目录版)':<40} {_human(size):>9}")
    print("\n提示：发布前请确认 dist/ 中的 exe 能正常启动，并以该版本号打 Git tag。")


def main():
    parser = argparse.ArgumentParser(description="构建 txt2md 发布产物")
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
    step_icon()
    step_exe(args.onedir)
    if not args.no_wheel:
        step_python_packages()
    step_zip(args.onedir)
    summarize()


if __name__ == "__main__":
    main()
