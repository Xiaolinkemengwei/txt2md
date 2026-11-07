# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

用法:
    pyinstaller --noconfirm aieye.spec              # 单文件 exe（默认）
    set AIEYE_ONEFILE=0 && pyinstaller --noconfirm aieye.spec     # 目录版（启动更快）

版本号唯一来源是 aieye.py 的 __version__，此处自动读取并写入 Windows 版本资源。
"""

import os
import re

ROOT = os.path.abspath(os.getcwd())
ENTRY = os.path.join(ROOT, "aieye.py")

# ---- 从源码读取版本号，避免多处维护
with open(ENTRY, encoding="utf-8") as f:
    VERSION = re.search(r'__version__\s*=\s*"([^"]+)"', f.read()).group(1)
VER_TUPLE = tuple(int(p) for p in VERSION.split(".")) + (0,)
VER_STR = VERSION

ONEFILE = os.environ.get("AIEYE_ONEFILE", "1") == "1"

# ---- 生成 Windows 版本资源（文件属性里显示的版本/说明）
os.makedirs(os.path.join(ROOT, "build"), exist_ok=True)
VERSION_FILE = os.path.join(ROOT, "build", "version_info.txt")
with open(VERSION_FILE, "w", encoding="utf-8") as f:
    f.write(f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={VER_TUPLE}, prodvers={VER_TUPLE},
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)
  ),
  kids=[
    StringFileInfo([StringTable('080404B0', [
      StringStruct('CompanyName', 'AI eye contributors'),
      StringStruct('FileDescription', 'AI eye 文档格式互转（txt/md/html/csv/xlsx/docx/pdf + OCR）'),
      StringStruct('FileVersion', '{VER_STR}'),
      StringStruct('InternalName', 'AIeye'),
      StringStruct('LegalCopyright', 'MIT License'),
      StringStruct('OriginalFilename', 'AIeye.exe'),
      StringStruct('ProductName', 'AI eye'),
      StringStruct('ProductVersion', '{VER_STR}')
    ])]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
""")

# ---- 无用模块排除，显著减小体积
EXCLUDES = [
    "tkinter", "unittest", "pydoc", "pydoc_data", "doctest", "test",
    "setuptools", "pip", "wheel", "pkg_resources",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebChannel",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtSql",
    "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtUiTools",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    # pandoc 二进制约 231MB，不随包分发；用户自行安装时才作为兜底启用
    "pypandoc", "pypandoc_binary",
]

# 动态 import 的模块（读取器/写出器按需 import）必须显式列出，
# 否则打包后会在运行时才报 ModuleNotFoundError
HIDDEN = [
    "gui", "gui.app", "gui.theme", "gui.widgets",
    "core", "core.model", "core.mdparse", "core.htmlio", "core.readers",
    "core.writers", "core.registry", "core.pipeline", "core.legacy",
    "core.errors", "core.pandoc_fallback", "core.ocr", "core.layout",
    # 格式编解码
    "openpyxl", "openpyxl.styles", "openpyxl.utils",
    "docx", "pdfplumber", "charset_normalizer",
    "pdfminer", "pdfminer.high_level", "pdfminer.pdfinterp",
    "lxml", "lxml.etree", "PIL.Image",
    # 扫描件 OCR（模型随包分发，见下方数据收集）
    "rapidocr_onnxruntime", "onnxruntime", "cv2", "numpy",
    "pyclipper", "shapely",
]

from PyInstaller.utils.hooks import (collect_data_files,  # noqa: E402
                                     collect_dynamic_libs, collect_submodules)

# PDF 读不出来是打包版最容易踩的坑：pdfplumber 只列顶层模块不够，
# 它的 pdfplumber.page / pdfplumber.display 等子模块、以及 pypdfium2 的
# Python 层和 pdfium.dll 都可能在静态分析时漏掉，运行时才抛 ImportError。
# 这里按包名整体收，宁可多打几 MB 也不要「装了依赖却用不了」。
for pkg in ("pdfplumber", "pdfminer", "pypdfium2", "pypdfium2_raw",
            "rapidocr_onnxruntime", "onnxruntime", "cv2"):
    try:
        HIDDEN += collect_submodules(pkg)
    except Exception:
        pass

# pdfminer 依赖 CMap 等数据文件，漏了会导致中文 PDF 解析失败；
# rapidocr 的识别模型（det/rec_cls 的 onnx 文件）也在包数据里，漏了 OCR 直接失效
DATAS = []
for pkg in ("pdfminer", "charset_normalizer", "docx", "pdfplumber",
            "pypdfium2", "pypdfium2_raw",
            "rapidocr_onnxruntime", "onnxruntime"):
    try:
        DATAS += collect_data_files(pkg)
    except Exception:
        pass

BINARIES = []
for pkg in ("pypdfium2_raw", "pypdfium2", "onnxruntime"):
    try:
        BINARIES += collect_dynamic_libs(pkg)
    except Exception:
        pass

a = Analysis(
    [ENTRY],
    pathex=[ROOT],
    binaries=BINARIES,
    datas=DATAS,
    hiddenimports=HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)

pyz = PYZ(a.pure)

if ONEFILE:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="AIeye",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,          # 无控制台黑窗
        disable_windowed_traceback=False,
        icon=os.path.join(ROOT, "assets", "icon.ico"),
        version=VERSION_FILE,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="AIeye",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        icon=os.path.join(ROOT, "assets", "icon.ico"),
        version=VERSION_FILE,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        name="AIeye",
    )
