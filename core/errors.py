"""异常类型。

分层原则（借鉴 markitdown 的异常设计）：把「环境缺东西」和「文件有问题」
和「程序内部出错」分开，让上层能给出不同的处置——前两者是用户可修复的，
应打印可操作提示；第三者才是 bug，应带堆栈。
"""

from __future__ import annotations


class ConvertError(Exception):
    """转换失败的基类。message 面向用户，应可直接展示。"""

    def __init__(self, message: str, *, hint: str = ""):
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:
        return f"{self.message}（{self.hint}）" if self.hint else self.message


class EngineMissing(ConvertError):
    """缺少外部引擎（pandoc / LibreOffice / 浏览器）。

    可修复：提示安装什么。
    """


class DependencyMissing(ConvertError):
    """缺少 Python 包。可修复：提示 pip install 什么。"""


class UnsupportedFormat(ConvertError):
    """格式不在支持范围，或该方向当前环境不支持。不可修复（除非换格式）。"""


class SourceError(ConvertError):
    """源文件本身有问题：不存在、为空、损坏、编码无法识别、像二进制。"""


class DestinationError(ConvertError):
    """目标位置不可写：同名已存在且未允许覆盖、路径是目录、磁盘满等。"""


class SelfOverwrite(ConvertError):
    """源与目标是同一个文件。红线：直接拒绝，不询问。"""
