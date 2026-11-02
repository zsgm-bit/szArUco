# -*- coding: utf-8 -*-
"""ArUco 字典封装。

位图（比特矩阵）数据来自 OpenCV 预定义字典，仅用于提供字典比特数据，
不做任何像素渲染 —— 矢量 SVG / PDF 由本项目的生成器自行绘制。
"""
import cv2
import numpy as np

_PREFERRED = [
    "DICT_4X4_50", "DICT_4X4_100", "DICT_4X4_250", "DICT_4X4_1000",
    "DICT_5X5_50", "DICT_5X5_100", "DICT_5X5_250", "DICT_5X5_1000",
    "DICT_6X6_50", "DICT_6X6_100", "DICT_6X6_250", "DICT_6X6_1000",
    "DICT_7X7_50", "DICT_7X7_100", "DICT_7X7_250", "DICT_7X7_1000",
    "DICT_APRILTAG_16h5", "DICT_APRILTAG_25h9",
    "DICT_APRILTAG_36h10", "DICT_APRILTAG_36h11",
]


def list_dictionaries():
    """返回当前 OpenCV 版本支持的字典名称列表。"""
    return [n for n in _PREFERRED if hasattr(cv2.aruco, n)]


class ArucoDictionary:
    """单个 ArUco 字典的封装，带比特矩阵缓存。"""

    def __init__(self, name: str):
        if not hasattr(cv2.aruco, name):
            raise ValueError(f"当前 OpenCV 不支持字典: {name}")
        flag = getattr(cv2.aruco, name)
        if hasattr(cv2.aruco, "getPredefinedDictionary"):
            self._cv_dict = cv2.aruco.getPredefinedDictionary(flag)
        else:  # 兼容 OpenCV < 4.7
            self._cv_dict = cv2.aruco.Dictionary_get(flag)
        self.name = name
        self._bits_cache = {}

    @property
    def marker_count(self) -> int:
        """字典内码的总数。"""
        return int(self._cv_dict.bytesList.shape[0])

    @property
    def marker_bits(self) -> int:
        """每边模块数（如 6x6 字典返回 6）。"""
        return int(self._cv_dict.markerSize)

    def bits(self, marker_id: int):
        """返回 (markerBits, markerBits) 的 0/1 矩阵（第 0 行为顶部）。

        无效 ID（超出字典范围）返回 None。
        """
        if not (0 <= marker_id < self.marker_count):
            return None
        if marker_id not in self._bits_cache:
            n = self.marker_bits
            try:
                img = cv2.aruco.generateImageMarker(self._cv_dict, marker_id, n + 2)
            except AttributeError:  # 兼容旧 API
                img = cv2.aruco.drawMarker(self._cv_dict, marker_id, n + 2)
            # generateImageMarker 含 1 比特白边，裁掉中心即纯比特矩阵
            self._bits_cache[marker_id] = (np.asarray(img)[1:-1, 1:-1] > 127).astype(np.uint8)
        return self._bits_cache[marker_id]

    def id_range_text(self) -> str:
        return f"0 ~ {self.marker_count - 1}"
