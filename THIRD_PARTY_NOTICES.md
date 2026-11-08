# 第三方组件与许可声明

本项目自身以 **MIT** 许可发布（见 `LICENSE`）。分发的可执行文件包含以下第三方组件，
其许可条款独立适用于对应组件。

## Qt / PySide6（LGPL-3.0）

- 组件：Qt 6（Copyright © The Qt Company Ltd.）、PySide6（Copyright © The Qt Company Ltd.）
- 许可：GNU Lesser General Public License v3（LGPL-3.0）
- 用途：图形界面框架
- 许可全文：https://www.gnu.org/licenses/lgpl-3.0.html
- Qt 许可说明：https://www.qt.io/licensing/open-source-lgpl-obligations

**分发说明**：打包的 `AIeye.exe` 通过 PyInstaller 将 Qt 动态库一并封装。LGPL-3.0 允许以
动态链接方式分发，前提是保留本声明、提供对应许可文本，并允许使用者替换 Qt 库。
本项目全部源码公开，任何人均可用 `python tools/build.py` 重新构建并替换其中的 Qt 组件，
满足重新链接的要求。若你需要完全规避 LGPL 义务，可仅使用本项目的命令行部分
（`aieye.py`，纯标准库，不依赖 Qt）。

## 文档格式处理库

多格式互转依赖以下库。全部为宽松许可（MIT / BSD / Apache-2.0），
不产生与本项目 MIT 许可的冲突。

| 组件 | 许可 | 用途 | 是否打包进 exe |
| --- | --- | --- | --- |
| [openpyxl](https://openpyxl.readthedocs.io/) | MIT | 读写 `.xlsx` 工作簿 | 是 |
| [python-docx](https://python-docx.readthedocs.io/) | MIT | 读写 `.docx` 文档 | 是 |
| [pdfplumber](https://github.com/jsvine/pdfplumber) | MIT | 提取 PDF 文本层与表格 | 是 |
| [pdfminer.six](https://github.com/pdfminer/pdfminer.six) | MIT | pdfplumber 的底层解析器 | 是 |
| [charset-normalizer](https://github.com/Ousret/charset_normalizer) | MIT | 编码探测兜底 | 是 |
| [lxml](https://lxml.de/) | BSD-3-Clause | python-docx 的 XML 后端 | 是 |
| [Pillow](https://python-pillow.org/) | HPND | pdfplumber 处理图像对象 | 是 |
| [pypandoc](https://github.com/JessicaTegner/pypandoc) | MIT | 可选兜底通道，**不打包** | 否 |

### 扫描件 OCR（2.0 起内置）

| 组件 | 许可 | 用途 | 是否打包进 exe |
| --- | --- | --- | --- |
| [RapidOCR](https://github.com/RapidAI/RapidOCR) | Apache-2.0 | 文字检测与识别，模型随包分发 | 是 |
| [ONNX Runtime](https://github.com/microsoft/onnxruntime) | MIT | 推理运行时 | 是 |
| [OpenCV (headless)](https://opencv.org/) | Apache-2.0 | 图像预处理、版面切分（掩膜与形态学连块） | 是 |
| [NumPy](https://numpy.org/) | BSD-3-Clause | 数组运算、图区暗像素统计 | 是 |
| [pyclipper](https://github.com/fonttools/pyclipper) | MIT | 检测框后处理 | 是 |
| [Shapely](https://github.com/shapely/shapely) | BSD-3-Clause | 几何计算 | 是 |

OCR 模型（`det` / `cls` / `rec` 三个 `.onnx` 文件）来自 RapidOCR 官方发行版，
遵循 PaddleOCR 的 Apache-2.0 许可。全部推理在本机完成，不上传任何文件。

**关于 pypandoc / pandoc**：本项目**不**将 pandoc 作为主引擎，仅在内置实现失败时
作为可选兜底。`pypandoc-binary` 会额外引入约 231 MB 的 pandoc 可执行文件，
因此默认不安装、不打包；需要时由用户自行 `pip install pypandoc-binary`。
pandoc 本体为 GPL-2.0-or-later，与本项目 MIT 代码仅通过子进程调用交互，
不构成衍生作品。

**关于 PDF 生成**：PDF 输出通过调用系统已安装的 Chromium 系浏览器
（Edge / Chrome / Chromium）以 `--headless --print-to-pdf` 完成，
不分发、不修改浏览器二进制，属于正常的进程调用。若系统无此类浏览器，
PDF 输出能力会被自动关闭（见 `--engines` 输出），而不是运行时报错。

**关于 EPUB 与 AI eye 导读**（2.3.0 新增）：两者均为**纯标准库实现**，未引入任何
第三方依赖。EPUB 的容器解析使用 `zipfile` + `xml.etree.ElementTree` + `html.parser`，
写出使用 `zipfile`；AI 导读的字数统计、标题提取、高频词候选全部基于
`re` 与内置字符串操作，不含分词模型、不含词表文件。因此本版本
`requirements.txt` 无变化，离线环境下功能完整可用。

## Segoe UI / Segoe Fluent Icons（微软系统字体）

界面优先使用 Windows 自带字体。字体文件不随本项目分发，仅在运行时按名称引用；
若目标系统缺少这些字体，会自动回退到 Segoe UI / 微软雅黑等可用字体。

## Santt997/txt2md（MIT）

智能排版的「轻量启发式」思路参考自该开源项目（MIT 许可）。
本项目未复制其代码，为独立实现，并修复了其 UTF-8 硬编码与中文段落误加反引号的问题。

- 项目地址：https://github.com/Santt997/txt2md
