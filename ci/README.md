# CI 工作流（待启用）

这里放的是 GitHub Actions 工作流定义，但**没有**放在 `.github/workflows/` 下。

## 为什么

GitHub 规定：用 Personal Access Token 推送 `.github/workflows/` 下的文件，token 必须额外具备
`workflow` 权限。本项目首发时使用的 token 只有 `repo` / `read:org` / `write:packages`，
推送被拒：

```
! [remote rejected] main -> main (refusing to allow a Personal Access Token
  to create or update workflow `.github/workflows/ci.yml` without `workflow` scope)
```

为了让仓库能正常发布，工作流先存放在这里，等 token 具备权限后再一键搬回去。

## 怎么启用

```bash
# 方式一：使用 tools/enable_ci.py（推荐）
python tools/enable_ci.py            # 复制文件并提交
git push origin main

# 方式二：手动
mkdir -p .github/workflows
cp ci/*.yml .github/workflows/
git add .github/workflows && git commit -m "ci: 启用 GitHub Actions"
git push origin main
```

> token 需要勾选 `workflow` 权限：GitHub → Settings → Developer settings →
> Personal access tokens → 编辑该 token → 勾选 `workflow` → 保存。

## 两个工作流

| 文件 | 触发条件 | 作用 |
|---|---|---|
| `ci.yml` | push 到 main / PR / 手动 | 三个 Python 版本（3.9/3.11/3.13）的零依赖命令行校验、界面层测试、视觉回归截图 |
| `release.yml` | 推送 `v*` 标签 / 手动 | 在 Windows runner 上构建 exe、wheel、sdist、发布包，自动创建 Release 并附上全部产物 |
