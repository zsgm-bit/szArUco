# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 zsgm-bit
# 本项目采用 AGPL-3.0 + 商业许可 双轨制：开源许可见 LICENSE，商业许可见 LICENSE-COMMERCIAL.md。
r"""一键打包 Windows exe（PyInstaller 单文件）。

用法：
    ..\.venv\Scripts\python.exe build_exe.py          # 单文件 exe（默认）
    ..\.venv\Scripts\python.exe build_exe.py onedir   # 目录版（启动更快）

产出：dist/<名称>_v<时间戳>.exe（单文件）或 dist/<名称>_v<时间戳>/ 目录。
依赖：pip install pyinstaller
"""
import os
import shutil
import subprocess
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
BASE_NAME = "szArUco"
# 打包时剔除的无用大模块
EXCLUDES = ["matplotlib", "pandas", "scipy", "tkinter", "PIL.ImageQt",
            "PyQt5.QtWebEngineWidgets", "PyQt5.QtQml", "PyQt5.QtQuick",
            "PyQt5.QtBluetooth", "PyQt5.QtMultimedia", "PyQt5.QtNetworkAuth"]


def _redact(text: str) -> str:
    """日志脱敏：把绝对路径收敛成相对/中文占位，避免把本机用户名目录结构
    泄露到公开仓库的 CI 日志（CWE-532）。"""
    text = text.replace(HERE, "<proj>")
    text = text.replace(sys.executable, "<python>")
    home = os.path.expanduser("~")
    if home and home != "~":
        text = text.replace(home, "~")
    return text


def main():
    onedir = len(sys.argv) > 1 and sys.argv[1].lower() in ("onedir", "dir")
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    name = f"{BASE_NAME}_v{stamp}"

    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
           "--onedir" if onedir else "--onefile",
           "--windowed", "--name", name]
    icon = os.path.join(HERE, "app.ico")
    if os.path.exists(icon):
        cmd += ["--icon", icon]
    for mod in EXCLUDES:
        cmd += ["--exclude-module", mod]
    cmd.append(os.path.join(HERE, "main.py"))

    print("[build] " + _redact(" ".join(cmd)))
    proc = subprocess.run(cmd, cwd=HERE)
    if proc.returncode != 0:
        print("[build] 失败，退出码", proc.returncode)
        return proc.returncode

    dist = os.path.join(HERE, "dist")
    # 把示例 CSV 一起放到 dist，方便双击 exe 直接试用
    for csv_name in os.listdir(HERE):
        if csv_name.startswith("sample_") and csv_name.endswith(".csv"):
            target = dist if not onedir else os.path.join(dist, name)
            try:
                shutil.copy2(os.path.join(HERE, csv_name), os.path.join(target, csv_name))
            except OSError:
                pass
    out = os.path.join(dist, name + ("" if onedir else ".exe"))
    size = os.path.getsize(out) / (1024 * 1024) if os.path.exists(out) else 0
    print(_redact(f"[build] 完成：{out}（{size:.1f} MB）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
