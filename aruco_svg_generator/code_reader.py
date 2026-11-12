# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 zsgm-bit
# 本项目采用 MIT 许可证，详见 LICENSE。
"""码读取（解码）模块 —— 与生成端互为逆运算。

生成端把「编号」画成 QR / ArUco 码；这里把图片或摄像头画面里的码读回文本，
用于打印后验收、现场巡场核对、以及「扫到的码是否与码表一致」的比对。

设计要点：
- 不依赖 Qt，可被 GUI / 命令行 / 自检复用。
- **正反两种极性**都尝试：本项目「黑底反色」样式是白色模块画在黑底上，
  取反后才能被标准解码器识别（对应读码器的 Inverse 选项）。
- Windows 中文路径：`cv2.imread` 会静默失败，统一走 `np.fromfile + imdecode`。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import cv2
import numpy as np

# 图片扫描时默认尝试的 ArUco 字典：覆盖本项目下拉框里最常用的几个。
# 顺序按「小字典优先」，小字典误检率低、速度也更快。
COMMON_ARUCO_DICTS = (
    "DICT_4X4_50", "DICT_5X5_100", "DICT_6X6_250",
    "DICT_7X7_100", "DICT_ARUCO_ORIGINAL",
)

# 支持的图片扩展名（扫描文件夹时用）
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff")


@dataclass
class ScanHit:
    """一次成功识别到的码。"""

    kind: str                      # "qr" | "aruco"
    text: str                      # QR 内容原样 / ArUco 码号（字符串）
    corners: list = field(default_factory=list)   # 4 个 (x, y)，用于画框
    dict_name: str = ""            # ArUco 字典名；QR 为空
    marker_id: int = None          # ArUco 码号；QR 为 None
    source: str = ""               # 来源（文件名 / 「摄像头」）
    inverted: bool = False         # 是否从反色图读出（白码黑底）

    @property
    def key(self):
        """去重键：同一张画面里重复检出的同一个码只记一次。"""
        if self.kind == "qr":
            return ("qr", self.text)
        return ("aruco", self.dict_name, self.marker_id)

    def kind_text(self) -> str:
        return "QR" if self.kind == "qr" else "ArUco"


# --------------------------------------------------------------- 基础工具
def imread_unicode(path, flags=cv2.IMREAD_COLOR):
    """按路径读图，支持中文/空格/特殊字符（cv2.imread 在 Windows 下会失败）。"""
    try:
        buf = np.fromfile(path, dtype=np.uint8)
    except OSError:
        return None
    if buf.size == 0:
        return None
    try:
        return cv2.imdecode(buf, flags)
    except cv2.error:
        return None


def _to_gray(image):
    if image is None:
        return None
    if image.ndim == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


# --------------------------------------------------------------- QR 解码
def decode_qr(image):
    """识别画面里的所有 QR 码，返回 [ScanHit]。

    先按正常极性识别；没读到再试整幅取反（本项目「黑底反色」样式需要）。
    """
    gray = _to_gray(image)
    if gray is None:
        return []
    detector = cv2.QRCodeDetector()
    hits = []
    for inverted in (False, True):
        src = cv2.bitwise_not(gray) if inverted else gray
        try:
            ok, texts, points, _ = detector.detectAndDecodeMulti(src)
        except cv2.error:
            continue
        if not ok or points is None:
            continue
        for text, pts in zip(texts, points):
            if not text:
                continue
            hits.append(ScanHit(
                kind="qr", text=str(text),
                corners=[(float(p[0]), float(p[1])) for p in pts],
                inverted=inverted))
        if hits:
            break          # 正向已读到就不必再试反色，避免重复进水印
    return hits


# --------------------------------------------------------------- ArUco 解码
def _detect_aruco(gray, dict_name):
    """在灰度图上用指定字典检测 ArUco，返回 (corners, ids)。"""
    if not hasattr(cv2.aruco, dict_name):
        return None, None
    d = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, dict_name))
    if hasattr(cv2.aruco, "ArucoDetector"):
        detector = cv2.aruco.ArucoDetector(d)
        corners, ids, _ = detector.detectMarkers(gray)
    else:                  # 兼容 OpenCV < 4.7
        corners, ids, _ = cv2.aruco.detectMarkers(gray, d)
    return corners, ids


def decode_aruco(image, dict_names=COMMON_ARUCO_DICTS):
    """识别画面里的 ArUco 码（按字典顺序尝试）。

    识别到任何一种字典的码即停止（同一画面混用多字典极少见）。
    """
    gray = _to_gray(image)
    if gray is None:
        return []
    hits = []
    for inverted in (False, True):
        src = cv2.bitwise_not(gray) if inverted else gray
        for name in dict_names:
            try:
                corners, ids = _detect_aruco(src, name)
            except cv2.error:
                continue
            if ids is None:
                continue
            for quad, mid in zip(corners, ids.flatten()):
                pts = quad.reshape(4, 2)
                hits.append(ScanHit(
                    kind="aruco", text=str(int(mid)),
                    corners=[(float(p[0]), float(p[1])) for p in pts],
                    dict_name=name, marker_id=int(mid), inverted=inverted))
        if hits:
            break
    return hits


# --------------------------------------------------------------- 统一入口
def scan_frame(image, want_qr=True, want_aruco=False,
               dict_names=COMMON_ARUCO_DICTS):
    """对一帧图像（BGR 或灰度）做识别，返回 [ScanHit]。"""
    if image is None:
        return []
    hits = []
    if want_qr:
        hits.extend(decode_qr(image))
    if want_aruco:
        hits.extend(decode_aruco(image, dict_names))
    return hits


def scan_image(path, want_qr=True, want_aruco=False,
               dict_names=COMMON_ARUCO_DICTS):
    """识别单张图片文件。解析失败或读不到码时返回 []。

    大图（手机拍照常见）先按原图试，失败再缩小一半重试 —— 码在图里占比过小
    时，缩小后反而更容易被检测器定位。
    """
    img = imread_unicode(path)
    if img is None:
        return []
    hits = scan_frame(img, want_qr, want_aruco, dict_names)
    if hits:
        return hits
    h, w = img.shape[:2]
    if max(h, w) > 1200:
        small = cv2.resize(img, (w // 2, h // 2), interpolation=cv2.INTER_AREA)
        hits = scan_frame(small, want_qr, want_aruco, dict_names)
    return hits


def scan_folder(folder, want_qr=True, want_aruco=False,
                dict_names=COMMON_ARUCO_DICTS, recursive=True, limit=500):
    """批量扫描文件夹内的图片，返回 (结果列表, 读取失败的文件名列表)。"""
    paths = []
    if recursive:
        for root, _dirs, files in os.walk(folder):
            for name in sorted(files):
                if name.lower().endswith(IMAGE_EXTS):
                    paths.append(os.path.join(root, name))
    else:
        for name in sorted(os.listdir(folder)):
            full = os.path.join(folder, name)
            if os.path.isfile(full) and name.lower().endswith(IMAGE_EXTS):
                paths.append(full)
    paths = paths[:limit]

    found, failed = [], []
    for p in paths:
        if imread_unicode(p) is None:
            failed.append(os.path.basename(p))
            continue
        hits = scan_image(p, want_qr, want_aruco, dict_names)
        for hit in hits:
            hit.source = os.path.basename(p)
            found.append(hit)
    return found, failed


# --------------------------------------------------------------- 摄像头
def _open_camera(index):
    """打开摄像头：Windows 优先 DSHOW 后端，MSMF 首次打开常卡数秒。"""
    if os.name == "nt" and hasattr(cv2, "CAP_DSHOW"):
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if cap is not None and cap.isOpened():
            return cap
        if cap is not None:
            cap.release()
    cap = cv2.VideoCapture(index)
    return cap if (cap is not None and cap.isOpened()) else None


def open_camera(index=0, width=1280, height=720):
    """打开指定摄像头并尽量设置分辨率，失败返回 None。"""
    cap = _open_camera(index)
    if cap is None:
        return None
    try:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    except cv2.error:
        pass
    return cap


def list_cameras(max_index=4):
    """探测可用的摄像头索引列表（逐个尝试打开，较慢，建议在按钮里触发）。"""
    found = []
    for idx in range(max_index):
        try:
            cap = _open_camera(idx)
        except cv2.error:
            cap = None
        if cap is None:
            continue
        ok = False
        try:
            ok, _ = cap.read()
        except cv2.error:
            ok = False
        cap.release()
        if ok:
            found.append(idx)
    return found


# --------------------------------------------------------------- 比对
def compare_codes(scanned, expected):
    """把「扫描到的内容」与「码表里的编号」比对。

    scanned  : 扫描结果文本列表（可含重复，内部去重，保持出现顺序）
    expected : 码表中的编号列表（同上）
    返回 dict: matched 已扫到 / missing 码表有但没扫到 / extra 扫到了但码表没有
    """
    def _uniq(seq):
        out, seen = [], set()
        for item in seq:
            s = "" if item is None else str(item).strip()
            if not s or s in seen:
                continue
            seen.add(s)
            out.append(s)
        return out

    s_list, e_list = _uniq(scanned), _uniq(expected)
    s_set, e_set = set(s_list), set(e_list)
    return {
        "matched": [c for c in e_list if c in s_set],
        "missing": [c for c in e_list if c not in s_set],
        "extra": [c for c in s_list if c not in e_set],
        "scanned": s_list,
    }
