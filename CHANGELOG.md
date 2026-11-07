# 变更日志

本项目的所有重要变更都会记录在此文件。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

（暂无）

## [1.0.0] - 2026-11-07

首个公开版本。

### 新增

- **图形界面（Fluent Design）**：基于 PySide6 的原生桌面应用，包含 Fluent 2 亮色设计系统
  （`#F3F3F3` 底层、白色圆角卡片、柔和投影、`#0F6CBD` 强调色、Segoe UI 字体链）。
- **转换能力**：TXT → Markdown，支持单文件、多文件、整目录递归批量转换。
- **编码自动识别**：按 `BOM → UTF-8 → Big5 → GBK` 顺序探测，中文 Windows 记事本（GBK）
  文件不再报错；输出统一 UTF-8。
- **智能排版**：首行作 `# 标题`、识别已有列表与 Markdown 语法、全大写英文短行作 `## 标题`；
  正文内容不删改，另有 `--plain` 纯转换模式。
- **命令行接口**：`paths` / `-o` / `--smart` / `--plain` / `--overwrite` / `--gui` / `--version`，
  纯标准库实现，零第三方依赖。
- **交互细节**：拖拽文件进窗口、`Ctrl+Enter` 快捷转换、逐条状态日志、进度条、汇总统计、
  单文件失败不中断批量任务。
- **界面层控件**：自绘 Fluent 开关（120ms 缓动）、状态胶囊、省略文本、阴影卡片、图标块。
- **发布工程**：`txt2md.spec`（PyInstaller，单文件/目录两种模式）、`tools/build.py`
  一键构建 exe + wheel + 发布 zip、`tools/make_icon.py` 生成多尺寸 ICO、
  Windows 版本资源自动从 `__version__` 注入。
- **文档与工程规范**：README、项目介绍文档、贡献指南、第三方许可声明、CI 工作流（暂存于 `ci/`，见 `ci/README.md`）。
- **发布流程**：构建产物附 `SHA256SUMS.txt` 校验和；`ci/release.yml` 支持推送标签后自动建 Release。

### 修复

- 首个标题名额未被已有 Markdown 标题或大写标题占用，导致普通段落被误判为 `# 标题`。
- 单文件模式下 `-o` 输出目录参数被忽略，文件落到源文件旁边。
- 末尾换行未规范化（现两种模式均保证单个末尾换行）。
- `QFontDatabase` 在 `QApplication` 之前访问导致进程静默崩溃（改为启动时惰性探测）。
- `pos` 遮蔽 `QWidget.pos` 内置属性（改名 `knobPos`）。
- 进度条隐藏时主按钮被拉满整行；空状态文案下沉到卡片底部。
- 窗口高度压到 560 时选项行标题与描述重叠（行最小高 48，窗口最小 720×660）。

### 已知限制

- 图形界面仅提供亮色主题，未跟随系统深色模式。
- 仅提供 Windows 打包产物；macOS/Linux 需从源码运行。
- `Big5` 解码路径已实现但未在真实语料上验证。

[Unreleased]: https://github.com/Xiaolinkemengwei/txt2md/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/Xiaolinkemengwei/txt2md/releases/tag/v1.0.0
