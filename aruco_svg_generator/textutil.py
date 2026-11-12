# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 zsgm-bit
# 本项目采用 MIT 许可证，详见 LICENSE。
"""导出场景的文本清洗工具（供 GUI 与扫码导出共用）。

- `safe_filename`：把任意文本收敛为安全的单段文件名，阻断路径穿越（CWE-22）。
- `csv_cell`：给 Excel/WPS 公式前缀加单引号，阻断 CSV 公式注入（CWE-1236）。
"""
import re

# 文件名白名单：保留数字/字母/下划线/中文/点/横线，其余（含路径分隔符、冒号、
# 空白、控制字符）统一替换为下划线。
_UNSAFE_NAME = re.compile(r"[^0-9A-Za-z_\u4e00-\u9fff.\-]+")

# CSV 公式注入前缀：Excel/WPS 会把以这些字符开头的单元格当公式执行。
_FORMULA_PREFIX = ("=", "+", "-", "@", "\t", "\r")


def safe_filename(text, limit: int = 40) -> str:
    """把任意文本收敛为安全的单段文件名（不含目录分隔符、不以点号开头/结尾）。"""
    s = _UNSAFE_NAME.sub("_", str(text or ""))
    s = s.replace("..", "_").strip("._-")[:limit].strip("._-")
    return s or "label"


def csv_cell(value) -> str:
    """CSV 单元格防公式注入：危险前缀前加单引号，使 Excel 按纯文本处理。"""
    s = "" if value is None else str(value)
    return "'" + s if s[:1] in _FORMULA_PREFIX else s
