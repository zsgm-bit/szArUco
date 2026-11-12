# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 zsgm-bit
# 本项目采用 MIT 许可证，详见 LICENSE。
"""扫码识别 / 校验对话框。

两种取图方式：
- **图片 / 文件夹**：批量识别已有照片、截图、打印验收照。
- **摄像头**：实时取帧识别，画面里画框提示，结果自动累积去重。

识别结果可与当前码列表比对（匹配 / 缺失 / 多余），也可一键把新编号加进码列表。
ArUco 码号会按码表自动翻译成业务编号，便于现场核对。
"""
import csv
import os
from datetime import datetime

import cv2
import numpy as np
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QImage, QPixmap
from PyQt5.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QFileDialog,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from code_reader import (COMMON_ARUCO_DICTS, compare_codes, imread_unicode,
                         list_cameras, open_camera, scan_folder, scan_frame,
                         scan_image)
from textutil import csv_cell


def _bgr_to_qimage(bgr):
    """OpenCV BGR 帧 → QImage（copy 脱离 numpy 缓冲区，避免悬空指针）。"""
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    h, w, ch = rgb.shape
    return QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888).copy()


class FrameLabel(QLabel):
    """摄像头预览：等比缩放显示，保留最后一帧以便窗口缩放时重绘。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._image = None
        self.setMinimumSize(560, 320)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("background:#111; color:#888; border:1px solid #333;")
        self.setText("未开启摄像头")

    def set_frame(self, bgr):
        self._image = _bgr_to_qimage(bgr)
        self._repaint()

    def clear_frame(self):
        self._image = None
        self.clear()
        self.setText("未开启摄像头")

    def _repaint(self):
        if self._image is None:
            return
        pm = QPixmap.fromImage(self._image).scaled(
            self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.setPixmap(pm)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._repaint()


class ScanDialog(QDialog):
    """扫码识别 / 校验窗口。"""

    def __init__(self, parent=None, markers=None, dict_name="", on_add_codes=None):
        super().__init__(parent)
        self.setWindowTitle("扫码识别 / 校验 — QR 与 ArUco")
        self.resize(940, 760)

        self.markers = list(markers or [])
        self.dict_name = dict_name or ""
        self.on_add_codes = on_add_codes
        self.hits = {}          # 去重键 -> ScanHit

        self._cap = None        # cv2.VideoCapture
        self._frame_no = 0
        self._timer = QTimer(self)
        self._timer.setInterval(60)      # ≈16 fps，足够跟手又不烧 CPU
        self._timer.timeout.connect(self._tick)

        self._build_ui()
        self._reload_table_codes()
        self._update_stats()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setSpacing(8)

        tip = QLabel("识别图片或摄像头画面中的 QR / ArUco 码，用于打印验收与现场核对。\n"
                     "「黑底反色」样式会自动按反色识别；ArUco 码号会按当前码表翻译成业务编号。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#444; background:#EFF6FF; border:1px solid #CFE0F5; "
                          "border-radius:4px; padding:6px;")
        outer.addWidget(tip)

        # -- 识别类型（两种取图方式共用）
        opt = QHBoxLayout()
        self.chk_qr = QCheckBox("识别 QR 二维码")
        self.chk_qr.setChecked(True)
        self.chk_aruco = QCheckBox("识别 ArUco 码（较慢，摄像头下每 5 帧检测一次）")
        opt.addWidget(self.chk_qr)
        opt.addWidget(self.chk_aruco)
        opt.addStretch(1)
        outer.addLayout(opt)

        # -- 取图方式
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_image_tab(), "图片 / 文件夹")
        self.tabs.addTab(self._build_camera_tab(), "摄像头")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        outer.addWidget(self.tabs)

        # -- 结果表
        grp = QGroupBox("识别结果")
        gv = QVBoxLayout(grp)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["来源", "类型", "内容", "备注"])
        head = self.table.horizontalHeader()
        head.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        head.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        head.setSectionResizeMode(2, QHeaderView.Stretch)
        head.setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        gv.addWidget(self.table)
        outer.addWidget(grp, 1)

        # -- 底部操作
        bottom = QHBoxLayout()
        self.stats = QLabel()
        self.stats.setStyleSheet("color:#666;")
        bottom.addWidget(self.stats)
        bottom.addStretch(1)
        btn_cmp = QPushButton("与码表比对")
        btn_add = QPushButton("加入码列表")
        btn_csv = QPushButton("导出结果 CSV…")
        btn_close = QPushButton("关闭")
        btn_cmp.clicked.connect(self.compare_with_table)
        btn_add.clicked.connect(self.add_to_table)
        btn_csv.clicked.connect(self.export_csv)
        btn_close.clicked.connect(self.accept)
        for b in (btn_cmp, btn_add, btn_csv, btn_close):
            bottom.addWidget(b)
        outer.addLayout(bottom)

    def _build_image_tab(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        row = QHBoxLayout()
        btn_files = QPushButton("🖼 选择图片…")
        btn_folder = QPushButton("📁 扫描文件夹…")
        btn_clear = QPushButton("清空结果")
        btn_files.clicked.connect(self.pick_images)
        btn_folder.clicked.connect(self.pick_folder)
        btn_clear.clicked.connect(self.clear_results)
        row.addWidget(btn_files)
        row.addWidget(btn_folder)
        row.addWidget(btn_clear)
        row.addStretch(1)
        lay.addLayout(row)
        hint = QLabel("支持 PNG / JPG / BMP / WEBP / TIF，可多选；扫描文件夹会递归子目录（最多 500 张）。\n"
                      "手机拍的大图若首次未识别，会自动缩小一半再试一次。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#777; font-size:11px;")
        lay.addWidget(hint)
        lay.addStretch(1)
        return page

    def _build_camera_tab(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        row = QHBoxLayout()
        row.addWidget(QLabel("摄像头:"))
        self.cam_combo = QComboBox()
        self.cam_combo.addItems([f"摄像头 {i}" for i in range(4)])
        row.addWidget(self.cam_combo)
        btn_probe = QPushButton("探测设备")
        btn_probe.clicked.connect(self.probe_cameras)
        row.addWidget(btn_probe)
        self.btn_cam = QPushButton("▶ 开始扫描")
        self.btn_cam.setObjectName("accent")
        self.btn_cam.clicked.connect(self.toggle_camera)
        row.addWidget(self.btn_cam)
        row.addStretch(1)
        lay.addLayout(row)

        self.cam_label = FrameLabel()
        lay.addWidget(self.cam_label, 1)
        hint = QLabel("对准标签后自动识别，结果会累积去重；同一编号只会记录一次。")
        hint.setStyleSheet("color:#777; font-size:11px;")
        lay.addWidget(hint)
        return page

    # ------------------------------------------------------------- 码表缓存
    def _reload_table_codes(self):
        """从主窗口同步码表：编号集合 + 码号→编号映射。"""
        self.markers = list(getattr(self.parent(), "markers", self.markers) or [])
        self.code_list = [m.display_code() for m in self.markers]
        self.code_set = {c for c in self.code_list if c}
        self.code_by_id = {m.marker_id: m.display_code()
                           for m in self.markers if m.marker_id is not None}

    def _dicts(self):
        names = []
        if self.dict_name and self.dict_name != "QR":
            names.append(self.dict_name)
        names.extend(COMMON_ARUCO_DICTS)
        return tuple(dict.fromkeys(names))

    def _code_of(self, hit):
        """把识别结果翻译成业务编号（ArUco 码号查码表映射）。"""
        if hit.kind == "qr":
            return hit.text
        mapped = self.code_by_id.get(hit.marker_id if hit.marker_id is not None else -1)
        return mapped if mapped else str(hit.marker_id)

    # ------------------------------------------------------------- 结果累积
    def _note_for(self, hit):
        bits = []
        if hit.kind == "qr":
            if hit.text in self.code_set:
                spec = next((m for m in self.markers
                             if m.display_code() == hit.text), None)
                bits.append("✓ 在码表中" + (f"（{spec.location}）" if spec and spec.location else ""))
            elif self.markers:
                bits.append("码表外")
        else:
            mapped = self.code_by_id.get(hit.marker_id if hit.marker_id is not None else -1)
            if mapped:
                bits.append(f"码号→编号 {mapped}")
            elif self.markers:
                bits.append("码表外")
            else:
                bits.append(f"字典 {hit.dict_name}")
        if hit.inverted:
            bits.append("反色读取")
        return "；".join(bits)

    def _add_hit(self, hit, source):
        """写入结果表并去重，返回是否为新命中。"""
        hit.source = source
        if hit.key in self.hits:
            return False
        self.hits[hit.key] = hit
        row = self.table.rowCount()
        self.table.insertRow(row)
        for col, text in enumerate((source, hit.kind_text(), hit.text,
                                    self._note_for(hit))):
            item = QTableWidgetItem(text)
            if hit.kind == "aruco":
                item.setForeground(QColor("#B26A00"))
            if hit.inverted:
                item.setToolTip("该码为白码黑底（反色），已按反色识别")
            self.table.setItem(row, col, item)
        self.table.scrollToBottom()
        return True

    def _update_stats(self):
        qr = sum(1 for h in self.hits.values() if h.kind == "qr")
        ar = len(self.hits) - qr
        src = len({h.source for h in self.hits.values()})
        self.stats.setText(
            f"共 {len(self.hits)} 个码（QR {qr} / ArUco {ar}）｜ 来源 {src} 处"
            f"｜ 码表 {len(self.code_set)} 个编号")

    def clear_results(self):
        self.hits.clear()
        self.table.setRowCount(0)
        self._update_stats()

    # ------------------------------------------------------------- 图片扫描
    def pick_images(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择图片（可多选）", "",
            "图片 (*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff);;所有文件 (*.*)")
        if not files:
            return
        self._scan_paths(files)

    def _scan_paths(self, files):
        want_qr, want_aruco = self.chk_qr.isChecked(), self.chk_aruco.isChecked()
        if not (want_qr or want_aruco):
            QMessageBox.information(self, "提示", "请至少勾选一种识别类型。")
            return
        added, unreadable, nohit = 0, [], []
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            for path in files:
                if imread_unicode(path) is None:
                    unreadable.append(os.path.basename(path))
                    continue
                hits = scan_image(path, want_qr, want_aruco, self._dicts())
                if not hits:
                    nohit.append(os.path.basename(path))
                    continue
                name = os.path.basename(path)
                for hit in hits:
                    if self._add_hit(hit, name):
                        added += 1
                QApplication.processEvents()      # 大批量时保持界面响应
        finally:
            QApplication.restoreOverrideCursor()
        self._update_stats()

        msg = f"新增识别 {added} 个码，共扫描 {len(files)} 张图片。"
        if nohit:
            msg += f"\n\n{len(nohit)} 张未识别到码: {nohit[:8]}"
        if unreadable:
            msg += f"\n\n{len(unreadable)} 个文件无法读取: {unreadable[:8]}"
        if not added and want_qr and not want_aruco:
            msg += "\n\n提示：若标签是 ArUco 码，请勾选「识别 ArUco 码」。"
        QMessageBox.information(self, "扫描完成", msg)

    def pick_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择要扫描的文件夹")
        if not folder:
            return
        want_qr, want_aruco = self.chk_qr.isChecked(), self.chk_aruco.isChecked()
        if not (want_qr or want_aruco):
            QMessageBox.information(self, "提示", "请至少勾选一种识别类型。")
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            found, failed = scan_folder(folder, want_qr, want_aruco, self._dicts())
        finally:
            QApplication.restoreOverrideCursor()
        added = 0
        for hit in found:
            if self._add_hit(hit, hit.source or os.path.basename(folder)):
                added += 1
        self._update_stats()

        msg = f"扫描完成：新增 {added} 个码。"
        if not found:
            msg += "\n\n未识别到任何码。请确认照片里码足够清晰、完整且未被裁掉。"
        if failed:
            msg += f"\n\n{len(failed)} 个文件无法读取: {failed[:8]}"
        QMessageBox.information(self, "扫描结果", msg)

    # ------------------------------------------------------------- 摄像头
    def probe_cameras(self):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            found = list_cameras(4)
        finally:
            QApplication.restoreOverrideCursor()
        if not found:
            QMessageBox.warning(
                self, "未找到摄像头",
                "未探测到可用摄像头。\n\n请检查设备是否已连接、是否被其他程序占用，"
                "以及系统「隐私和安全性 → 摄像头」是否允许桌面应用访问。")
            return
        self.cam_combo.clear()
        self.cam_combo.addItems([f"摄像头 {i}" for i in found])
        QMessageBox.information(self, "探测完成",
                                "可用摄像头编号: " + "、".join(str(i) for i in found))

    def toggle_camera(self):
        if self._cap is not None:
            self.stop_camera()
        else:
            self.start_camera()

    def start_camera(self):
        if not (self.chk_qr.isChecked() or self.chk_aruco.isChecked()):
            QMessageBox.information(self, "提示", "请至少勾选一种识别类型。")
            return
        index = self.cam_combo.currentIndex()
        cap = open_camera(index)
        if cap is None:
            QMessageBox.warning(
                self, "摄像头不可用",
                f"无法打开「摄像头 {index}」。\n\n可能原因：被其他程序（会议软件、"
                "浏览器）占用，或系统隐私设置禁止访问。\n可点「探测设备」换一个编号再试。")
            return
        self._cap = cap
        self._frame_no = 0
        self.btn_cam.setText("⏹ 停止扫描")
        self._timer.start()

    def stop_camera(self):
        self._timer.stop()
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:      # noqa: BLE001 —— 释放失败不影响界面状态
                pass
            self._cap = None
        self.btn_cam.setText("▶ 开始扫描")
        self.cam_label.clear_frame()

    def _tick(self):
        if self._cap is None:
            return
        try:
            ok, frame = self._cap.read()
        except cv2.error:
            ok, frame = False, None
        if not ok or frame is None:
            self.stop_camera()
            QMessageBox.warning(self, "摄像头中断", "读取画面失败，已停止扫描。")
            return
        self._frame_no += 1
        # ArUco 多字典检测较慢 → 摄像头下每 5 帧才跑一次；QR 每帧都跑（很快）
        want_aruco = self.chk_aruco.isChecked() and self._frame_no % 5 == 0
        hits = scan_frame(frame, self.chk_qr.isChecked(), want_aruco, self._dicts())
        added = 0
        for hit in hits:
            if self._add_hit(hit, "摄像头"):
                added += 1
        if added:
            self._update_stats()
        self._draw_boxes(frame, hits)
        self.cam_label.set_frame(frame)

    @staticmethod
    def _draw_boxes(frame, hits):
        """在预览帧上画识别框。用 #序号 而非中文——cv2.putText 不支持中文。"""
        for i, hit in enumerate(hits, 1):
            if len(hit.corners) < 4:
                continue
            pts = np.array(hit.corners, dtype=np.int32).reshape(-1, 1, 2)
            color = (60, 200, 60) if hit.kind == "qr" else (0, 170, 255)
            cv2.polylines(frame, [pts], True, color, 2)
            x = int(pts[:, 0, 0].min())
            y = int(pts[:, 0, 1].min())
            cv2.putText(frame, f"#{i}", (x, max(14, y - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)

    def _on_tab_changed(self, index):
        # 离开摄像头页就停掉，避免设备被占着
        if index != 1 and self._cap is not None:
            self.stop_camera()

    def closeEvent(self, event):
        self.stop_camera()
        super().closeEvent(event)

    # ------------------------------------------------------------- 比对 / 导出
    def compare_with_table(self):
        if not self.markers:
            QMessageBox.information(
                self, "提示", "当前码列表为空，无法比对。\n请先在主界面导入 CSV 或批量编号。")
            return
        if not self.hits:
            QMessageBox.information(self, "提示", "还没有识别到任何码。")
            return
        scanned = [self._code_of(h) for h in self.hits.values()]
        res = compare_codes(scanned, self.code_list)

        def _fmt(items, n=12):
            if not items:
                return "—"
            return "、".join(items[:n]) + (f" …（共 {len(items)} 个）" if len(items) > n else "")

        msg = (f"扫描到 {len(res['scanned'])} 个编号，码表共 {len(self.code_set)} 个。\n\n"
               f"✅ 匹配：{len(res['matched'])} 个\n"
               f"❌ 缺失（码表有、未扫到）：{len(res['missing'])} 个\n"
               f"⚠ 多余（扫到但码表没有）：{len(res['extra'])} 个\n\n"
               f"缺失: {_fmt(res['missing'])}\n"
               f"多余: {_fmt(res['extra'])}")
        QMessageBox.information(self, "比对结果", msg)

    def add_to_table(self):
        if not self.hits:
            QMessageBox.information(self, "提示", "还没有识别到任何码。")
            return
        if self.on_add_codes is None:
            QMessageBox.information(self, "提示", "当前窗口不支持加入码列表。")
            return
        new = []
        for hit in self.hits.values():
            if hit.kind != "qr":
                continue          # ArUco 码号无法反推业务编号，除非码表里已有
            code = hit.text.strip()
            if code and code not in self.code_set and code not in new:
                new.append(code)
        if not new:
            QMessageBox.information(
                self, "提示",
                f"没有需要新增的编号：识别到的 {len(self.hits)} 个码都已在码表中。")
            return
        added = self.on_add_codes(new)
        self._reload_table_codes()
        self._update_stats()
        QMessageBox.information(
            self, "已加入码列表",
            f"新增 {added} 条记录（共提交 {len(new)} 个编号，重复的已跳过）。\n"
            "主界面列表已同步更新。")

    def export_csv(self):
        if not self.hits:
            QMessageBox.information(self, "提示", "还没有识别结果可导出。")
            return
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        path, _ = QFileDialog.getSaveFileName(
            self, "保存扫描结果",
            os.path.join(os.path.expanduser("~"), "Desktop", f"扫码结果_v{stamp}.csv"),
            "CSV 文件 (*.csv)")
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["来源", "类型", "内容", "字典", "ArUco码号", "极性", "备注"])
            for hit in self.hits.values():
                w.writerow([
                    csv_cell(hit.source),
                    "QR" if hit.kind == "qr" else "ArUco",
                    csv_cell(hit.text),
                    csv_cell(hit.dict_name),
                    "" if hit.marker_id is None else hit.marker_id,
                    "反色" if hit.inverted else "正常",
                    csv_cell(self._note_for(hit)),
                ])
        QMessageBox.information(
            self, "导出完成",
            f"扫描结果已保存:\n{path}\n\n共 {len(self.hits)} 条记录。")
