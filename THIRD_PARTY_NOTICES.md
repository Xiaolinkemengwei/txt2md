# 第三方组件与许可声明

本项目自身以 **MIT** 许可发布（见 `LICENSE`）。分发的可执行文件包含以下第三方组件，
其许可条款独立适用于对应组件。

## Qt / PySide6（LGPL-3.0）

- 组件：Qt 6（Copyright © The Qt Company Ltd.）、PySide6（Copyright © The Qt Company Ltd.）
- 许可：GNU Lesser General Public License v3（LGPL-3.0）
- 用途：图形界面框架
- 许可全文：https://www.gnu.org/licenses/lgpl-3.0.html
- Qt 许可说明：https://www.qt.io/licensing/open-source-lgpl-obligations

**分发说明**：打包的 `txt2md.exe` 通过 PyInstaller 将 Qt 动态库一并封装。LGPL-3.0 允许以
动态链接方式分发，前提是保留本声明、提供对应许可文本，并允许使用者替换 Qt 库。
本项目全部源码公开，任何人均可用 `python tools/build.py` 重新构建并替换其中的 Qt 组件，
满足重新链接的要求。若你需要完全规避 LGPL 义务，可仅使用本项目的命令行部分
（`txt2md.py`，纯标准库，不依赖 Qt）。

## Segoe UI / Segoe Fluent Icons（微软系统字体）

界面优先使用 Windows 自带字体。字体文件不随本项目分发，仅在运行时按名称引用；
若目标系统缺少这些字体，会自动回退到 Segoe UI / 微软雅黑等可用字体。

## Santt997/txt2md（MIT）

智能排版的「轻量启发式」思路参考自该开源项目（MIT 许可）。
本项目未复制其代码，为独立实现，并修复了其 UTF-8 硬编码与中文段落误加反引号的问题。

- 项目地址：https://github.com/Santt997/txt2md
