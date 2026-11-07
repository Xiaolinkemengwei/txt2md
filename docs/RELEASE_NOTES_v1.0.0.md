把 `.txt` 拖进窗口，点一下，得到干净的 `.md`。零配置、离线、不联网、不上传任何文件。

## 下载

| 选项 | 适合谁 |
|---|---|
| **`txt2md-1.0.0-win64.zip`** | Windows 用户，解压双击 `txt2md.exe` 即可，**不需要安装 Python** |
| `txt2md-1.0.0-py3-none-any.whl` | 已有 Python 环境，`pip install` 后可用 `txt2md` 命令 |
| `txt2md-1.0.0.tar.gz` | 想读源码或二次开发 |

下载后可用随包发布的 `SHA256SUMS.txt` 核验完整性：

```powershell
Get-FileHash .\txt2md-1.0.0-win64.zip -Algorithm SHA256
```

## 这个版本做了什么

- **图形界面**：Fluent 2 亮色设计系统，圆角卡片 + 柔和投影 + `#0F6CBD` 强调色 + Segoe UI 字体链；支持拖拽文件、`Ctrl+Enter` 快捷转换、逐条状态日志与进度条
- **编码自动识别**：`BOM → UTF-8 → Big5 → GBK`，中文 Windows 记事本存出的 GBK 文件不再报错，输出统一 UTF-8
- **智能排版**：首行作 `# 标题`、识别已有列表与 Markdown 语法、全大写英文短行作 `## 标题`；正文内容不删改。另有 `--plain` 纯改扩展名模式
- **批量与命令行**：整目录递归转换并保留目录结构；`txt2md ./notes -o ./out` 一行搞定
- **失败不中断**：单个文件出错只记一条日志，批量任务继续跑完

## 已知限制

- 界面只有亮色主题，未跟随系统深色模式
- 仅提供 Windows 打包产物；macOS / Linux 需从源码运行
- `Big5` 解码路径已实现但未在真实语料上验证

## 校验信息

本版本已在 Windows 11 + Python 3.13 环境完成构建与自测：命令行零依赖校验、界面层功能测试（扫描统计 / 转换线程 / 覆盖跳过）全部通过，exe 启动实测正常。

完整变更见 [CHANGELOG.md](https://github.com/Xiaolinkemengwei/txt2md/blob/main/CHANGELOG.md)。

---

**如果这个工具帮你省了点时间，给个 Star 就是最好的支持。**
