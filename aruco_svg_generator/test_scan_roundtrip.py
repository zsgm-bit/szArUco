# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 zsgm-bit
"""扫码功能回归验证：生成 → 渲染成图 → 再扫回来（闭环）。

覆盖：
- QR 三种显示样式（黑底标签 / 黑底反色 / 白底黑码）都能被读回原编号
- ArUco 码按字典读回码号，并能按码表翻译成业务编号
- 比对逻辑（匹配 / 缺失 / 多余）
- 扫描对话框的结果表、加入码列表

运行：..\\.venv\\Scripts\\python.exe test_scan_roundtrip.py
全部通过退出码 0，任一失败退出码 1（CI 会执行本脚本）。
"""
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "gui"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QImage, QPainter
from PyQt5.QtSvg import QSvgRenderer
from PyQt5.QtWidgets import QApplication, QMessageBox  # noqa: E402

app = QApplication([])

from code_reader import (COMMON_ARUCO_DICTS, compare_codes, decode_aruco,  # noqa: E402
                         imread_unicode, scan_image)
from gui.main_window import MainWindow  # noqa: E402
from gui.scan_dialog import ScanDialog  # noqa: E402
from aruco_dict import ArucoDictionary  # noqa: E402
from svg_generator import MarkerSpec, marker_svg, qr_matrix  # noqa: E402

ok = True
tmpdir = tempfile.mkdtemp(prefix="scan_test_")

# 静默掉弹窗，避免无头环境下阻塞
QMessageBox.information = staticmethod(lambda *a, **k: None)
QMessageBox.warning = staticmethod(lambda *a, **k: None)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)


def check(name, cond, detail=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (("  " + detail) if detail else ""))
    if not cond:
        ok = False


def svg_to_png(svg: str, path: str, px_per_mm: float = 12.0) -> str:
    """把生成的 SVG 渲染成 PNG（模拟打印/拍照后再扫的场景）。"""
    m = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg)
    w, h = float(m.group(1)), float(m.group(2))
    img = QImage(max(1, int(w * px_per_mm)), max(1, int(h * px_per_mm)),
                 QImage.Format_RGB32)
    img.fill(Qt.white)
    p = QPainter(img)
    QSvgRenderer(svg.encode("utf-8")).render(p)
    p.end()
    img.save(path)
    return path


# ----------------------------------------------------- 1. QR 三种样式读回
d = ArucoDictionary("DICT_6X6_250")
qr_spec = MarkerSpec("QR", None, size_mm=45.0, location="A区-01", code="100001")
qr_bits = qr_matrix("100001")

for style, label in (("light", "白底黑码"),
                     ("dark_panel", "黑底标签+白码区"),
                     ("inverted", "黑底反色")):
    svg = marker_svg(qr_spec, qr_bits, 5.0, True, style=style)
    png = svg_to_png(svg, os.path.join(tmpdir, f"qr_{style}.png"))
    hits = scan_image(png, want_qr=True)
    texts = [h.text for h in hits]
    check(f"QR {label} 读回编号", "100001" in texts, f"got={texts}")

# ----------------------------------------------------- 2. ArUco 读回码号
ar_spec = MarkerSpec("DICT_6X6_250", 12, size_mm=45.0, location="B区", code="100013")
bits = d.bits(12)
ar_svg = marker_svg(ar_spec, bits, 5.0, True, style="dark_panel")
ar_png = svg_to_png(ar_svg, os.path.join(tmpdir, "aruco_dark_panel.png"))
ar_hits = decode_aruco(imread_unicode(ar_png),
                       ("DICT_6X6_250",))
check("ArUco 读回码号 12",
      any(h.marker_id == 12 and h.dict_name == "DICT_6X6_250" for h in ar_hits),
      f"got={[(h.dict_name, h.marker_id) for h in ar_hits]}")

# 反色样式（白码黑底）同样能读
ar_inv_svg = marker_svg(ar_spec, bits, 5.0, True, style="inverted")
ar_inv_png = svg_to_png(ar_inv_svg, os.path.join(tmpdir, "aruco_inverted.png"))
ar_inv_hits = decode_aruco(imread_unicode(ar_inv_png),
                           ("DICT_6X6_250",))
check("ArUco 反色样式读回码号 12",
      any(h.marker_id == 12 for h in ar_inv_hits),
      f"got={[(h.dict_name, h.marker_id) for h in ar_inv_hits]}")

# 全字典扫描（对话框默认行为）也能命中
ar_auto = decode_aruco(imread_unicode(ar_png),
                       COMMON_ARUCO_DICTS)
check("ArUco 多字典自动识别", any(h.marker_id == 12 for h in ar_auto))

# ----------------------------------------------------- 3. 比对逻辑
cmp_res = compare_codes(["100001", "100002", "999", "100001"],
                        ["100001", "100002", "100003"])
check("比对-匹配", cmp_res["matched"] == ["100001", "100002"], str(cmp_res["matched"]))
check("比对-缺失", cmp_res["missing"] == ["100003"], str(cmp_res["missing"]))
check("比对-多余（含重复去重）", cmp_res["extra"] == ["999"], str(cmp_res["extra"]))

# ----------------------------------------------------- 4. 对话框：扫描 + 入表
win = MainWindow()
win.markers = [MarkerSpec("QR", None, 45.0, "A区-01", 1, "100001")]
win.refresh_list()
dlg = ScanDialog(win, markers=win.markers, dict_name=win.dict_obj.name,
                 on_add_codes=win.add_scanned_codes)
dlg.chk_qr.setChecked(True)
dlg.chk_aruco.setChecked(True)
dlg._scan_paths([os.path.join(tmpdir, "qr_dark_panel.png")])
check("对话框结果表有记录", dlg.table.rowCount() >= 1, f"rows={dlg.table.rowCount()}")
check("对话框识别到 100001", "100001" in [h.text for h in dlg.hits.values()])
check("已存在编号标注为在码表中",
      "在码表中" in (dlg.table.item(0, 3).text() if dlg.table.rowCount() else ""))

# 扫描同一张图不会重复入表（去重）
before = dlg.table.rowCount()
dlg._scan_paths([os.path.join(tmpdir, "qr_dark_panel.png")])
check("重复扫描不新增行", dlg.table.rowCount() == before, f"{before} -> {dlg.table.rowCount()}")

# 新编号加入码列表
dlg.clear_results()
qr2 = marker_svg(MarkerSpec("QR", None, 45.0, "", 1, "100999"),
                 qr_matrix("100999"), 5.0, True,
                 style="dark_panel")
p2 = svg_to_png(qr2, os.path.join(tmpdir, "qr_new.png"))
dlg._scan_paths([p2])
n_before = len(win.markers)
dlg.add_to_table()
check("扫码新编号已加入主列表", len(win.markers) == n_before + 1,
      f"{n_before} -> {len(win.markers)}")
check("加入的编号正确", win.markers[-1].display_code() == "100999",
      win.markers[-1].display_code())

# ----------------------------------------------------- 5. 异常输入不崩
check("扫描不存在的文件返回空", scan_image(os.path.join(tmpdir, "nope.png")) == [])
blank = os.path.join(tmpdir, "blank.png")
QImage(200, 200, QImage.Format_RGB32).save(blank)
check("扫描纯白图不误报", scan_image(blank) == [])

dlg.stop_camera()
dlg.close()

print("\n结果: " + ("全部通过" if ok else "存在失败项"))
sys.exit(0 if ok else 1)
