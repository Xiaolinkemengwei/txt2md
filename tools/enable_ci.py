"""把 ci/ 下准备好的工作流搬回 .github/workflows/ 并提交。

为什么需要这一步：GitHub 要求推送 `.github/workflows/` 的 PAT 必须带 `workflow` 权限，
否则会以 "refusing to allow a Personal Access Token to create or update workflow" 拒绝。
首发时 token 权限不足，工作流暂存于 ci/，权限补齐后用本脚本一键启用。

用法：
    python tools/enable_ci.py            # 复制并提交
    python tools/enable_ci.py --dry-run  # 只看会做什么
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "ci"
DST = ROOT / ".github" / "workflows"


def main():
    parser = argparse.ArgumentParser(description="启用 ci/ 下的 GitHub Actions 工作流")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不落盘")
    args = parser.parse_args()

    files = sorted(SRC.glob("*.yml"))
    if not files:
        raise SystemExit(f"没有在 {SRC} 下找到工作流文件")

    print(f"将把 {len(files)} 个工作流复制到 {DST.relative_to(ROOT)}：")
    for f in files:
        print(f"  · {f.name}")
    if args.dry_run:
        print("\n（--dry-run：未做任何改动）")
        return 0

    DST.mkdir(parents=True, exist_ok=True)
    for f in files:
        shutil.copy2(f, DST / f.name)

    subprocess.run(["git", "add", str(DST)], cwd=ROOT, check=True)
    proc = subprocess.run(
        ["git", "commit", "-m", "ci: 启用 GitHub Actions 工作流"],
        cwd=ROOT, text=True, capture_output=True,
    )
    if proc.returncode != 0:
        print(proc.stdout or proc.stderr)
        print("提交未完成（可能没有改动或缺少 git 身份配置）")
    else:
        print(proc.stdout.strip())

    print("\n下一步：git push origin main")
    print("如果仍然被拒绝，说明 token 还没有勾选 workflow 权限。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
