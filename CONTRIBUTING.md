# 贡献指南

感谢你愿意改进 AI eye。以下约定能让合并过程更顺畅。

## 开发环境

```bash
git clone https://github.com/Xiaolinkemengwei/txt2md.git
cd txt2md   # 仓库目录名沿用旧名，项目名是 AI eye

python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate

pip install -r requirements.txt   # 只需要图形界面的 PySide6
```

> 只改命令行部分时无需安装任何依赖 —— `aieye.py` 是纯标准库实现。

## 本地校验

提交前请确保以下命令全部通过：

```bash
python aieye.py --version                       # 能正常输出版本
python aieye.py tests/sample -o tests/out --overwrite   # 命令行回归
python tests/test_gui_flow.py                  # 界面层功能测试
python tests/shot_gui.py                       # 视觉回归（生成多尺寸截图）
```

`tests/shot_gui.py` 生成的截图在 `tests/preview/`，改动界面时请附上对比图。

## 代码约定

- **语言**：注释、文档、界面文案使用中文；标识符用英文。
- **风格**：PEP 8，4 空格缩进，行宽 ≤ 100；类型标注尽量补全。
- **分层**：转换逻辑只能写在 `aieye.py`；`gui/` 不得实现业务规则，只调用核心函数。
- **设计令牌**：颜色、圆角、字号一律走 `gui/theme.py`，禁止在控件里写死色值。
- **兼容性**：Python ≥ 3.9；命令行部分不得引入第三方依赖。

## 新增一条「智能排版」规则的流程

1. 在 `core/legacy.py` 的 `smart_line()` 里加规则，并保持**不删改正文内容**这一底线。
2. 在 `tests/sample/` 补一个能区分新规则与旧行为的样例。
3. 在 `tests/out/` 核对产物，并在 `research/` 或 CHANGELOG 说明动机。
4. 中文场景务必验证：中文无空格，任何依据「空格数量」的规则都会误伤。

## 提交信息

采用 Conventional Commits：

```
feat: 支持为标题指定自定义级别
fix: 修复单文件模式忽略 -o 参数
docs: 补充打包步骤
refactor: 抽离卡片阴影参数
test: 增加 GBK 大文件用例
chore: 升级 PySide6 到 6.12
```

## 提 PR 前的检查清单

- [ ] 本地校验的四条命令全部通过
- [ ] 界面改动附最新截图（`tests/preview/gui_default.png`）
- [ ] 新增/变更行为已写入 `CHANGELOG.md` 的 Unreleased 段
- [ ] 未引入与现有能力重叠的新依赖
- [ ] 未提交 `build/`、`dist/`、`__pycache__` 等产物

## 报告问题

提 Issue 时请附上：

- 操作系统版本与 `python --version`
- `python aieye.py --version` 输出
- 可复现的最小样例（脱敏后的 txt 片段或文件编码说明）
- 完整报错信息或界面截图

## 行为准则

保持友善、就事论事。针对代码，不针对人；对新手问题保持耐心。
