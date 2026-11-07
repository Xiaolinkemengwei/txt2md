# txt2md 2.0 · 文档格式互转

> 把文档从一种格式换成另一种 —— 双向、批量、不丢内容。

<p>
  <img alt="version" src="https://img.shields.io/badge/version-2.0.0-0F6CBD">
  <img alt="license" src="https://img.shields.io/badge/license-MIT-0F6CBD">
  <img alt="python" src="https://img.shields.io/badge/python-%E2%89%A53.9-0F6CBD">
  <img alt="platform" src="https://img.shields.io/badge/platform-Windows%2010%2F11-0F6CBD">
</p>

![界面预览](tests/preview/gui_default.png)

txt / md / html / csv / xlsx / docx / pdf 共 7 种格式，任意两种之间可互转。中文编码（GBK / GB18030 / Big5 / UTF-8 / UTF-16）自动识别，Windows 下的记事本老文件不会变乱码。

- **开箱即用**：不需要装 pandoc、Office 或 LaTeX，PDF 直接用系统自带的 Edge 打印
- **保真优先**：往返转换后内容零丢失，结构（标题层级、列表嵌套、表格、代码块）完整保留
- **防呆**：会破坏数据的操作一律拒绝，失败绝不留半个文件

```
Windows 用户：下载 Releases 里的 txt2md.exe，双击即用
命令行用户：  pip install txt2md && txt2md report.docx --to md
```

## 支持的格式

| 格式 | 读 | 写 | 实现 | 保真 |
|---|:--:|:--:|---|---|
| `txt` 纯文本 | ✓ | ✓ | 内置 | 逐字 |
| `md` Markdown | ✓ | ✓ | 内置 | 结构完整 |
| `html` | ✓ | ✓ | 内置 | 内容完整 |
| `csv` | ✓ | ✓ | 内置 | 逐格 |
| `xlsx` Excel | ✓ | ✓ | openpyxl | 逐格 |
| `docx` Word | ✓ | ✓ | python-docx | 结构完整 |
| `pdf` | ✓ | ✓ | pdfplumber 读 / 浏览器打印写 | 文本层重排 |

`python txt2md.py --formats` 会按你机器上的实际环境打印完整矩阵。缺组件的格式自动标灰，不会让你点了才失败。

## 保真到什么程度

这是选型时最该看的一栏。**格式能力有边界，诚实之处在于提前讲清边界**：

| 转换 | 保留 | 不保留 |
|---|---|---|
| txt ⇄ md | 内容逐字节（`--plain`）／首行标题与列表识别（默认） | — |
| md ⇄ txt | 全部文字、段落、列表、表格（制表符分隔） | Markdown 标记本身 |
| md ⇄ xlsx | 标题级别、列表层级、代码语言、表格 | 段落间空行数量、表格列对齐 |
| md ⇄ docx | 标题、粗体/斜体、嵌套列表、引用、代码块、表格 | 行内代码标记、代码块语言、表格列对齐 |
| csv ⇄ xlsx | 每个单元格、前导零、小数位精度（`12.50` 不会变成 `12.5`） | — |
| * → pdf | 全部内容与基础排版（A4、分页避断行） | 原文档版式 |

xlsx 中转时，正文会落在一张 `_txt2md_body` 表里（仅在文档含非表格内容时生成），带类型列，读回时能还原成原来的标题 / 列表 / 代码结构。

## 用法

### 图形界面

双击 `txt2md.exe`，或 `python txt2md.py --gui`。

选文件 / 文件夹 → 选目标格式 → 转换。格式按钮直接反映本机能力，不可用的置灰并说明原因；转换前就会列出哪些文件会被跳过、为什么。

### 命令行

```bash
# 单文件
txt2md note.txt                  # txt → md（默认）
txt2md report.docx --to txt      # Word → 纯文本
txt2md 数据.xlsx --to md          # Excel → Markdown 表格
txt2md 手册.md --to pdf           # Markdown → PDF

# 批量（目录递归收集所有支持的格式）
txt2md ./docs --to pdf -o ./out  # 保留相对目录结构
txt2md ./docs --to xlsx --force  # 覆盖已存在的目标

# 只预检不执行：先看会发生什么
txt2md ./docs --to pdf --dry-run

# 查看能力矩阵与本机引擎
txt2md --formats
txt2md --engines
```

### 参数

| 参数 | 说明 |
|---|---|
| `-t, --to 格式` | 目标格式，默认 `md`。也接受别名：`word` / `excel` / `markdown` |
| `-f, --from 格式` | 强制指定源格式（默认按扩展名判断） |
| `-o, --out 目录` | 输出目录，批量时保留相对结构；缺省输出到源文件旁边 |
| `--force` / `--overwrite` | 覆盖已存在的目标（默认跳过） |
| `--plain` | 逐字保真：不加任何 Markdown 标记，内容原样保留 |
| `--encoding 编码` | 强制指定源编码（默认自动探测） |
| `--sheets 名称` | 只转换 xlsx 中指定的工作表，逗号分隔 |
| `--dry-run` | 只预检并列出计划，不实际转换 |
| `--no-verify` | 跳过产物校验（不建议） |
| `--formats` / `--engines` | 显示格式矩阵 / 本机引擎后退出 |
| `-V, --version` | 版本号 |

退出码：`0` 全部成功，`1` 有失败，`2` 参数或路径错误。适合放进脚本与 CI。

## 防呆设计

转换工具最危险的不是转不出来，是**悄悄把东西弄坏**。以下情况一律拒绝执行：

| 情况 | 行为 |
|---|---|
| 源与目标是同一文件 | 拒绝，不询问、不备份、不尝试 |
| 目标已存在 | 默认跳过，需 `--force` 才覆盖 |
| 批量中两个源产出同名目标 | 自动加 ` (2)` 后缀，不互相覆盖 |
| 文件是二进制伪装成 .txt | 拒绝，不产出乱码 |
| 源文件为空（0 字节） | 拒绝 |
| 目标格式在本机不可用 | 转换前拦下，说明缺什么 |
| 转换中途失败 | 原子写入，不留半个文件 |
| 产物为空但源有内容 | 判定失败，不写空文件糊弄过去 |
| 批量中单个文件失败 | 不中断其余任务，最后汇总 |
| 源格式与目标格式相同 | 逐字节复制，绝不重新排版 |

`--dry-run` 可以在动手前看到完整的「会成功 / 会被跳过 / 为什么」清单。

## 安装

### 直接用（Windows）

下载 Releases 里的 `txt2md.exe`（单文件，免安装），双击运行图形界面。
命令行同样可用：`txt2md.exe report.docx --to md`。

### pip 安装

```bash
pip install txt2md            # 核心转换
pip install txt2md[gui]       # 带图形界面
```

### 从源码

```bash
git clone https://github.com/Xiaolinkemengwei/txt2md.git
cd txt2md
pip install -r requirements.txt
python txt2md.py --gui
```

### 可选组件

以下几个**不是必需**，装了只提升特定能力：

| 组件 | 何时需要 |
|---|---|
| Microsoft Edge / Google Chrome | 生成 PDF。Windows 自带 Edge，通常无需额外安装 |
| `pip install pypandoc-binary` | 内置实现处理某些特殊文档失败时的兜底（含 231 MB pandoc 二进制） |

## 目录结构

```
txt2md/
├─ txt2md.py              # CLI 入口与 1.0 兼容 API
├─ core/                  # 转换内核
│  ├─ model.py            #   统一文档模型（块序列 / 工作表）
│  ├─ mdparse.py          #   Markdown 解析与行内降级
│  ├─ htmlio.py           #   HTML 解析与渲染（也用于 PDF 打印排版）
│  ├─ readers.py          #   各格式 → 模型
│  ├─ writers.py          #   模型 → 各格式
│  ├─ registry.py         #   格式表、引擎探测、能力矩阵
│  ├─ pipeline.py         #   任务规划、防呆预检、执行与产物校验
│  ├─ legacy.py           #   1.0 排版算法（保证旧输出不变）
│  ├─ pandoc_fallback.py  #   pandoc 兜底通道
│  └─ errors.py           #   分层异常
├─ gui/                   # Fluent Design 桌面界面
├─ tests/                 # 内核测试 + 界面测试 + 截图
├─ docs/                  # 项目介绍、2.0 设计大纲
├─ research/              # 开源方案调研报告
└─ tools/                 # 构建、图标生成、CI 启用
```

## 开发

```bash
python tests/test_convert.py    # 内核测试（87 项：编码 / 往返 / 防呆 / 全矩阵）
python tests/test_gui_flow.py   # 界面测试（14 项）
python tools/build.py           # 打包
```

内核测试里的「42 个格式组合无崩溃」会真的把每种组合跑一遍，不含糊。

## 已知限制

- **PDF 只取文本层**：扫描版 PDF（无文本层）需先做 OCR，本工具不做 OCR
- **PDF 输出是文本重排**：不是原文档的版式复刻
- **docx 不保留**：分栏、页眉页脚、脚注、批注、内嵌图片（文字内容不受影响）
- **xlsx 中转会丢列对齐**：Excel 里没有与 Markdown 表格对齐对应的概念
- **不支持**：`.doc`（旧版）、`.ppt`、`.epub`、图片文字识别

## 从 1.0 升级

命令行用法完全兼容：`txt2md note.txt` 依旧输出 `note.md`，`--plain` 依旧表示逐字保留。1.0 的排版算法原样保留在 `core/legacy.py` 并作为 txt → md 的直通通道，输出与 1.0 字节级一致。

新增的是 `--to` 参数与其余 6 种格式。

## 许可证

[MIT](LICENSE)。第三方组件许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 更多文档

- [项目介绍（完整版）](docs/项目介绍.md)
- [2.0 设计与实现大纲](docs/2.0-设计与实现大纲.md)
- [2.0 开源方案调研](research/2026-11-07-2.0-多格式互转调研.md)
- [贡献指南](CONTRIBUTING.md) · [变更日志](CHANGELOG.md)
