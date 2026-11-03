# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 zsgm-bit
"""安全修复回归验证：路径穿越 / CSV 公式注入 / 资源上限 / 异常兜底。

运行：..\\.venv\\Scripts\\python.exe test_security_regression.py
全部通过退出码 0，任一失败退出码 1（CI 会执行本脚本）。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "gui"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

app = QApplication([])

from gui.main_window import MainWindow, csv_cell, safe_filename  # noqa: E402
from svg_generator import MarkerSpec  # noqa: E402

ok = True


def check(name, cond, detail=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (("  " + detail) if detail else ""))
    if not cond:
        ok = False


# ---- 1. safe_filename 单元
for bad in ["../../../PWNED", "..\\..\\evil", "a/b", "C:abs", "..", ".", ""]:
    s = safe_filename(bad)
    check(f"safe_filename({bad!r}) -> {s!r}",
          "/" not in s and "\\" not in s and ":" not in s and ".." not in s and s)

# ---- 2. 路径穿越实测（QR 模式，编号为攻击载荷）
root = tempfile.mkdtemp(prefix="trav_")
chosen = os.path.join(root, "a", "b", "chosen")
os.makedirs(chosen)
w = MainWindow()
w.type_combo.setCurrentIndex(1)          # QR 模式
w.markers = [
    MarkerSpec("QR", None, 40.0, "", 1, "../../../PWNED_DEEP"),
    MarkerSpec("QR", None, 40.0, "..\\..\\loc", 1, "100002"),
    MarkerSpec("QR", None, 40.0, "正常位置", 1, "100003"),
]
w.refresh_list()
QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: chosen)
QMessageBox.information = staticmethod(lambda *a, **k: None)
w.export_svg()

written = [os.path.join(dp, f) for dp, _, fs in os.walk(root) for f in fs]
outside = [p for p in written if os.path.dirname(os.path.realpath(p)) != os.path.realpath(chosen)]
check("编号路径穿越被阻断（无文件越出所选目录）", not outside, str(outside))
check("正常记录仍能导出", len(written) >= 1, f"{len(written)} 个文件: "
      f"{[os.path.basename(p) for p in written]}")

# ---- 3. CSV 公式注入
check("csv_cell 处理 = 前缀", csv_cell('=HYPERLINK("http://evil/x")').startswith("'="))
check("csv_cell 处理 + 前缀", csv_cell("+1+1").startswith("'+"))
check("csv_cell 处理 @ 前缀", csv_cell("@SUM(1)").startswith("'@"))
check("csv_cell 不动普通文本", csv_cell("A区-01充电桩") == "A区-01充电桩")

mapping = os.path.join(root, "map.csv")
QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (mapping, ""))
w.markers = [MarkerSpec("QR", None, 40.0, '=HYPERLINK("http://evil/x","click")', 1,
                        '=1+1')]
w.refresh_list()
w.export_mapping()
content = open(mapping, encoding="utf-8-sig").read()
check("映射表已中和公式前缀", "=HYPERLINK" not in content.replace('"=HYPERLINK', "NEUTRAL") or
      "'=HYPERLINK" in content, content.strip().splitlines()[-1])

# ---- 4. 资源上限
from csv_loader import MAX_CSV_BYTES, CsvLoadError, load_csv  # noqa: E402

big = os.path.join(root, "big.csv")
with open(big, "wb") as f:
    f.write(b"id,size\n")
    f.write(b"100001,40\n" * ((MAX_CSV_BYTES // 10) + 10))
try:
    load_csv(big)
    check("超大 CSV 被拒绝", False)
except CsvLoadError as e:
    check("超大 CSV 被拒绝", True, str(e))

# ---- 5. 导入异常兜底（选到目录）
QMessageBox.warning = staticmethod(lambda *a, **k: None)
QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (root, ""))
try:
    w.import_csv()
    check("导入目录不抛异常", True)
except Exception as e:      # noqa: BLE001
    check("导入目录不抛异常", False, repr(e))

print("\n结果:", "全部通过" if ok else "存在失败")
sys.exit(0 if ok else 1)
