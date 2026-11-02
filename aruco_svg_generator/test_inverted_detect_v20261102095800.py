# -*- coding: utf-8 -*-
"""验证反色 ArUco 可识别性：对比当前实现 vs 样例（标准标记整体反色）。"""
import os
import re
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QImage, QPainter
from PyQt5.QtSvg import QSvgRenderer
from PyQt5.QtWidgets import QApplication

app = QApplication([])

import cv2
import numpy as np

from aruco_dict import ArucoDictionary
from svg_generator import MarkerSpec, marker_svg

OUT = os.path.join(BASE, "test_output")
os.makedirs(OUT, exist_ok=True)

det = cv2.aruco.ArucoDetector(
    cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_6X6_250))


def svg_to_png(svg_text, name, px_per_mm=8):
    m = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg_text)
    w, h = float(m.group(1)), float(m.group(2))
    img = QImage(int(w * px_per_mm), int(h * px_per_mm), QImage.Format_RGB32)
    img.fill(Qt.white)
    p = QPainter(img)
    QSvgRenderer(svg_text.encode()).render(p)
    p.end()
    path = os.path.join(OUT, name + ".png")
    img.save(path)
    return path


def detect(png, invert=False):
    img = cv2.imread(png)
    if invert:
        img = cv2.bitwise_not(img)
    corners, ids, _ = det.detectMarkers(img)
    return None if ids is None else ids.flatten().tolist()


# ---- 样例风格：标准 6x6 标记整体反色（无额外外框环），黑底静区 ----
def sample_style_svg(d, mid, size_mm=60.0, quiet_mm=5.0):
    bits = d.bits(mid)
    n = bits.shape[0]
    total = size_mm + 2 * quiet_mm
    cell = size_mm / n
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{total}mm" '
             f'height="{total}mm" viewBox="0 0 {total} {total}">',
             f'<rect width="{total}" height="{total}" fill="#000000"/>']
    for r in range(n):
        for c in range(n):
            # 反色：0(黑) -> 白，1(白) -> 黑(与黑底同色，不画)
            if not bits[r][c]:
                parts.append(f'<rect x="{quiet_mm + c * cell}" '
                             f'y="{quiet_mm + r * cell}" '
                             f'width="{cell}" height="{cell}" fill="#FFFFFF"/>')
    parts.append("</svg>")
    return "\n".join(parts)


def main():
    d = ArucoDictionary("DICT_6X6_250")
    spec = MarkerSpec("DICT_6X6_250", 0, size_mm=60.0)

    results = {}

    # A. 当前 inverted 实现
    png = svg_to_png(marker_svg(spec, d.bits(0), quiet_mm=5.0, show_label=False,
                                style="inverted"), "cur_inverted")
    results["当前 inverted（直测）"] = detect(png)
    results["当前 inverted（取反后测）"] = detect(png, invert=True)

    # B. 样例风格
    png = svg_to_png(sample_style_svg(d, 0), "sample_style")
    results["样例风格（直测）"] = detect(png)
    results["样例风格（取反后测）"] = detect(png, invert=True)

    # C. 对照：light / dark_panel
    png = svg_to_png(marker_svg(spec, d.bits(0), quiet_mm=5.0, show_label=False,
                                style="light"), "light")
    results["light（直测）"] = detect(png)
    png = svg_to_png(marker_svg(spec, d.bits(0), quiet_mm=5.0, show_label=False,
                                style="dark_panel"), "dark_panel")
    results["dark_panel（直测）"] = detect(png)

    for k, v in results.items():
        print(f"{k}: {'识别 ' + str(v) if v else '未识别'}")


if __name__ == "__main__":
    main()
