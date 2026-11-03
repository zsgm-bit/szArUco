# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 zsgm-bit
# 本项目采用 AGPL-3.0 + 商业许可 双轨制：开源许可见 LICENSE，商业许可见 LICENSE-COMMERCIAL.md。
"""ArUco 矢量码生成器 — PyQt5 主界面。

布局：左侧整列码列表 / 右侧单码大图预览，底部参数面板。
交互流程：导入 CSV / 批量编号 / 添加后，「预览」「生成PDF」按钮可用；
点「预览」渲染当前选中码（之后点击列表即切换）；点「生成PDF」弹出整页
预览对话框（可翻页），在对话框内点「导出 PDF…」命名保存。
"""
import csv
import os
import re
from datetime import datetime

from PyQt5.QtCore import Qt, QByteArray, QRectF
from PyQt5.QtGui import QColor, QPainter, QPixmap
from PyQt5.QtSvg import QSvgRenderer
from PyQt5.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPushButton, QSpinBox, QSplitter, QVBoxLayout, QWidget,
)

from aruco_dict import ArucoDictionary, list_dictionaries
from csv_loader import CsvLoadError, load_csv
from pdf_exporter import PAPERS, export_pdf
from svg_generator import (LABEL_AREA_MM, MarkerSpec, assign_aruco_ids,
                           compute_grid, expand_specs, footprint, marker_svg,
                           placeholder_svg, qr_matrix, sheet_svg, total_labels)

# 文件名白名单：保留数字/字母/下划线/中文/点/横线，其余（含路径分隔符、冒号、
# 空白、控制字符）统一替换为下划线。用于阻断「编号/位置 → 文件名」的路径穿越。
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


class SvgLabel(QLabel):
    """自适应 SVG 预览控件：按控件当前大小矢量重渲染，缩放窗口不裁切、不失真。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._svg = ""
        self.setAlignment(Qt.AlignCenter)

    def set_svg(self, svg_text):
        self._svg = svg_text or ""
        self._rerender()

    def _rerender(self):
        if not self._svg:
            self.clear()
            return
        renderer = QSvgRenderer(QByteArray(self._svg.encode("utf-8")))
        if not renderer.isValid():
            self.clear()
            return
        ds = renderer.defaultSize()
        if ds.isEmpty() or ds.width() <= 0 or ds.height() <= 0:
            self.clear()
            return
        avail_w = max(self.width() - 10, 100)
        avail_h = max(self.height() - 10, 100)
        scale = min(avail_w / ds.width(), avail_h / ds.height())
        pw = max(1, int(ds.width() * scale))
        ph = max(1, int(ds.height() * scale))
        pm = QPixmap(pw, ph)
        pm.fill(Qt.white)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        renderer.render(p, QRectF(0, 0, pw, ph))
        p.end()
        self.setPixmap(pm)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rerender()


class BatchNumberDialog(QDialog):
    """按编号区间批量生成记录（如 100001~100101 + 追加特殊编号 200001）。"""

    def __init__(self, parent=None, default_size: float = 60.0):
        super().__init__(parent)
        self.setWindowTitle("批量编号生成")
        self.setMinimumWidth(420)
        form = QFormLayout(self)

        self.start_spin = QSpinBox()
        self.start_spin.setRange(0, 999999999)
        self.start_spin.setValue(100001)
        self.end_spin = QSpinBox()
        self.end_spin.setRange(0, 999999999)
        self.end_spin.setValue(100101)
        self.step_spin = QSpinBox()
        self.step_spin.setRange(1, 10000)
        self.step_spin.setValue(1)
        self.qty_spin = QSpinBox()
        self.qty_spin.setRange(1, 9999)
        self.qty_spin.setValue(1)
        self.size_spin = QDoubleSpinBox()
        self.size_spin.setRange(2.0, 1000.0)
        self.size_spin.setDecimals(1)
        self.size_spin.setValue(default_size)
        self.size_spin.setSuffix(" mm")
        self.loc_edit = QLineEdit()
        self.loc_edit.setPlaceholderText("可留空；支持 {n}=序号、{id}=编号，如 A区-{n}")
        self.extra_edit = QLineEdit()
        self.extra_edit.setPlaceholderText("额外编号，逗号分隔，如 200001")

        form.addRow("起始编号", self.start_spin)
        form.addRow("结束编号", self.end_spin)
        form.addRow("步长", self.step_spin)
        form.addRow("每个数量", self.qty_spin)
        form.addRow("尺寸", self.size_spin)
        form.addRow("位置模板", self.loc_edit)
        form.addRow("额外编号", self.extra_edit)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def numbers(self):
        """返回编号列表（区间 + 额外编号，去重保序）。"""
        out = []
        a, b, s = self.start_spin.value(), self.end_spin.value(), self.step_spin.value()
        if a <= b:
            out.extend(range(a, b + 1, max(1, s)))
        for part in re.split(r"[,，;；\s]+", self.extra_edit.text()):
            part = part.strip()
            if part.isdigit() and int(part) not in out:
                out.append(int(part))
        return out


class PdfPreviewDialog(QDialog):
    """整页 PDF 预览对话框：分页查看拼版效果，「导出 PDF」命名保存。"""

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.page = 0
        self.setWindowTitle("整页 PDF 预览")
        self.resize(780, 920)
        v = QVBoxLayout(self)

        self.info_label = QLabel()
        self.info_label.setWordWrap(True)
        self.info_label.setStyleSheet("color:#555;")
        v.addWidget(self.info_label)

        self.sheet_label = SvgLabel()
        self.sheet_label.setMinimumSize(640, 760)
        self.sheet_label.setStyleSheet("background:#F5F5F5; border:1px solid #DDD;")
        v.addWidget(self.sheet_label, 1)

        btns = QHBoxLayout()
        self.btn_prev = QPushButton("◀ 上一页")
        self.btn_next = QPushButton("下一页 ▶")
        self.btn_prev.clicked.connect(lambda: self._turn(-1))
        self.btn_next.clicked.connect(lambda: self._turn(1))
        btn_export = QPushButton("📄 导出 PDF…")
        btn_export.setObjectName("accent")
        btn_export.clicked.connect(self.export_pdf)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.reject)
        btns.addWidget(self.btn_prev)
        btns.addWidget(self.btn_next)
        btns.addStretch(1)
        btns.addWidget(btn_export)
        btns.addWidget(btn_close)
        v.addLayout(btns)
        self._render()

    def _turn(self, delta):
        self.page = max(0, self.page + delta)
        self._render()

    def _total_pages(self):
        w = self.win
        flat = expand_specs([s for s in w.markers if w._bits_for(s) is not None])
        if not flat:
            return 1
        if w.mode_combo.currentIndex() == 1:      # 单码一页
            return len(flat)
        pw, ph = w._page()
        max_size = max(s.size_mm for s in flat)
        cell_w, cell_h = footprint(max_size, w._quiet(), w.label_chk.isChecked(),
                                   w.pad_spin.value())
        _, _, per_page, pages = compute_grid(
            len(flat), cell_w, cell_h, pw, ph,
            w.margin_spin.value(), w.gap_spin.value())
        return pages

    def _render(self):
        w = self.win
        total = self._total_pages()
        self.page = min(max(0, self.page), max(0, total - 1))
        pw, ph = w._page()
        sheet, cols, rows, per_page = sheet_svg(
            w.markers, w._bits_fn(), pw, ph,
            margin_mm=w.margin_spin.value(), gap_mm=w.gap_spin.value(),
            quiet_mm=w._quiet(), show_label=w.label_chk.isChecked(),
            cut_line=w.cut_chk.isChecked(), padding_mm=w.pad_spin.value(),
            style=w._style(), page=self.page)
        self.sheet_label.set_svg(sheet)
        self.info_label.setText(
            f"纸张 {pw:g}×{ph:g} mm ｜ 第 {self.page + 1} / {total} 页 ｜ "
            f"{cols} 列 × {rows} 行 = 每页 {per_page} 个 ｜ "
            f"标签共 {total_labels(w.markers)} 个\n"
            f"确认排版无误后，点「导出 PDF…」命名保存。")
        self.btn_prev.setEnabled(self.page > 0)
        self.btn_next.setEnabled(self.page < total - 1)

    def export_pdf(self):
        w = self.win
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        path, _ = QFileDialog.getSaveFileName(
            self, "保存 PDF",
            os.path.join(os.path.expanduser("~"), "Desktop",
                         f"aruco_markers_v{stamp}.pdf"),
            "PDF 文件 (*.pdf)")
        if not path:
            return
        pw, ph = w._page()
        info = export_pdf(
            path, w.markers, w._bits_fn(),
            paper=w.paper_combo.currentText(),
            custom_size=(pw, ph),
            mode="single" if w.mode_combo.currentIndex() == 1 else "grid",
            quiet_mm=w._quiet(), margin_mm=w.margin_spin.value(),
            gap_mm=w.gap_spin.value(), show_label=w.label_chk.isChecked(),
            cut_line=w.cut_chk.isChecked(),
            padding_mm=w.pad_spin.value(), style=w._style())
        if info["written"] == 0:
            QMessageBox.warning(self, "导出失败", "所有记录都无法编码，未生成 PDF。")
            return
        msg = (f"PDF 已生成: {path}\n\n"
               f"记录 {info['records']} 条 → 标签共 {info['written']} 个   页数: {info['pages']}\n")
        if w.mode_combo.currentIndex() == 0:
            msg += f"拼版: {info['cols']} 列 × {info['rows']} 行 = 每页 {info['per_page']} 个\n"
        if info["skipped"]:
            codes = sorted({s.display_code() or "（空）" for s in info["skipped"]})
            msg += f"\n⚠ 无法编码被跳过: {codes[:20]}"
        if info["overflow"]:
            msg += ("\n\n⚠ 警告：部分码（含静区）的尺寸超出了页面可打印区域，"
                    "已按原尺寸绘制但可能被裁掉。\n建议减小码尺寸/静区，或换更大纸张（如 A3）。")
        QMessageBox.information(self, "导出完成", msg)
        w.statusBar().showMessage(f"PDF 导出完成: {path}（{info['pages']} 页）")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("szArUco — ArUco/QR 矢量码生成器（SVG / PDF · OHT·AMR 地标地贴）")
        self.resize(1320, 840)

        names = list_dictionaries()
        if not names:
            QMessageBox.critical(None, "错误", "未检测到可用的 ArUco 字典，请安装 opencv-contrib-python")
            raise SystemExit(1)
        default_dict = "DICT_6X6_250" if "DICT_6X6_250" in names else names[0]
        self.dict_obj = ArucoDictionary(default_dict)
        self.markers = []          # [MarkerSpec]
        self._building = False     # 防止初始化期间触发预览刷新
        self._preview_on = False   # 点「预览」后才渲染单码图

        self._build_ui(names, default_dict)
        self.statusBar().showMessage(
            f"就绪 | 字典 {self.dict_obj.name}：{self.dict_obj.marker_count} 个码 "
            f"(ID 范围 {self.dict_obj.id_range_text()})，每边 {self.dict_obj.marker_bits} 模块。"
            "先「导入CSV」/「批量编号」/「添加」，再点「预览」或「生成PDF」。")
        self.update_previews()

    # ------------------------------------------------------------------ UI
    def _build_ui(self, dict_names, default_dict):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # -- 顶部操作条
        top = QHBoxLayout()
        btn_csv = QPushButton("📂 导入CSV")
        btn_batch = QPushButton("🔢 批量编号…")
        btn_add = QPushButton("＋ 添加")
        btn_del = QPushButton("－ 删除选中")
        btn_clear = QPushButton("清空")
        btn_map = QPushButton("导出映射表")
        btn_svg = QPushButton("导出 SVG…")
        btn_preview = QPushButton("👁 预览")
        btn_pdf = QPushButton("🖨 生成PDF…")
        btn_pdf.setObjectName("accent")
        for b in (btn_csv, btn_batch, btn_add, btn_del, btn_clear):
            b.clicked.connect(
                lambda _, f={btn_csv: self.import_csv, btn_batch: self.batch_numbers,
                             btn_add: self.add_marker,
                             btn_del: self.delete_selected, btn_clear: self.clear_all}[b]: f())
        btn_map.clicked.connect(self.export_mapping)
        btn_svg.clicked.connect(self.export_svg)
        btn_preview.clicked.connect(self.toggle_preview)
        btn_pdf.clicked.connect(self.open_pdf_preview)
        # 列表有数据后才可用（导入 / 批量编号 / 添加之后）
        self.btn_preview = btn_preview
        self.btn_pdf = btn_pdf
        self.btn_map = btn_map
        self.btn_svg = btn_svg
        btn_preview.setEnabled(False)
        btn_pdf.setEnabled(False)
        btn_map.setEnabled(False)
        btn_svg.setEnabled(False)
        for b in (btn_csv, btn_batch, btn_add, btn_del, btn_clear):
            top.addWidget(b)
        top.addStretch(1)
        top.addWidget(btn_map)
        top.addWidget(btn_svg)
        top.addWidget(btn_preview)
        top.addWidget(btn_pdf)
        root.addLayout(top)

        # -- 中部两栏：左=码列表整列，右=单码预览
        splitter = QSplitter(Qt.Horizontal)

        # 左：列表 + 字典
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(QLabel("码列表（ID | 位置 | 数量 | 尺寸）"))
        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list_widget.currentRowChanged.connect(lambda _: self._on_selection_changed())
        lv.addWidget(self.list_widget, 1)
        self.count_label = QLabel("记录 0 条 / 标签共 0 个")
        self.count_label.setStyleSheet("color:#666;")
        lv.addWidget(self.count_label)
        dict_row = QHBoxLayout()
        dict_row.addWidget(QLabel("类型:"))
        self.type_combo = QComboBox()
        self.type_combo.addItems(["ArUco 码", "QR 二维码"])
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)
        dict_row.addWidget(self.type_combo)
        dict_row.addWidget(QLabel("字典:"))
        self.dict_combo = QComboBox()
        self.dict_combo.addItems(dict_names)
        self.dict_combo.setCurrentText(default_dict)
        self.dict_combo.currentTextChanged.connect(self.on_dict_changed)
        dict_row.addWidget(self.dict_combo, 1)
        lv.addLayout(dict_row)
        # 字典 / 码类型的支持范围提示（常驻）
        self.dict_info = QLabel()
        self.dict_info.setWordWrap(True)
        self.dict_info.setStyleSheet("color:#555; font-size:11px; background:#F2F7F2; "
                                     "border:1px solid #D8E4D8; border-radius:4px; padding:4px;")
        lv.addWidget(self.dict_info)
        self._update_dict_info()
        splitter.addWidget(left)

        # 右：单码预览 + 生成参数（参数只占中/右列，不占左列表列）
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(8)

        mid = QGroupBox("单码预览（矢量）")
        mv = QVBoxLayout(mid)
        self.preview_label = SvgLabel()
        self.preview_label.setMinimumSize(520, 380)
        self.preview_label.setStyleSheet("background:#F5F5F5; border:1px solid #DDD;")
        mv.addWidget(self.preview_label)
        rv.addWidget(mid, 1)
        self._params_parent_layout = rv   # 生成参数面板稍后加入右列
        splitter.addWidget(right)

        splitter.setSizes([320, 900])
        root.addWidget(splitter, 1)

        # -- 生成参数面板（位于右列，单码预览下方）
        params = QGroupBox("生成参数")
        grid = QGridLayout(params)
        grid.setVerticalSpacing(6)

        grid.addWidget(QLabel("默认边长 (mm)"), 0, 0)
        self.size_spin = QDoubleSpinBox()
        self.size_spin.setRange(2.0, 500.0)
        self.size_spin.setValue(60.0)
        self.size_spin.setDecimals(1)
        self.size_spin.setSuffix(" mm")
        grid.addWidget(self.size_spin, 0, 1)
        btn_apply_sel = QPushButton("尺寸→选中")
        btn_apply_sel.clicked.connect(lambda: self.apply_size(False))
        btn_apply_all = QPushButton("尺寸→全部")
        btn_apply_all.clicked.connect(lambda: self.apply_size(True))
        grid.addWidget(btn_apply_sel, 0, 2)
        grid.addWidget(btn_apply_all, 0, 3)

        grid.addWidget(QLabel("静区宽度 (mm)"), 1, 0)
        self.quiet_spin = QDoubleSpinBox()
        self.quiet_spin.setRange(0.0, 50.0)
        self.quiet_spin.setValue(5.0)
        self.quiet_spin.setDecimals(1)
        self.quiet_spin.setSingleStep(0.5)
        grid.addWidget(self.quiet_spin, 1, 1)
        self.label_chk = QCheckBox("码下方印标签 (ID+备注)")
        self.label_chk.setChecked(True)
        grid.addWidget(self.label_chk, 1, 2, 1, 2)

        grid.addWidget(QLabel("纸张"), 2, 0)
        self.paper_combo = QComboBox()
        self.paper_combo.addItems(list(PAPERS.keys()) + ["自定义"])
        grid.addWidget(self.paper_combo, 2, 1)
        grid.addWidget(QLabel("自定义 宽×高 (mm)"), 2, 2)
        self.page_w_spin = QDoubleSpinBox()
        self.page_w_spin.setRange(50, 2000)
        self.page_w_spin.setValue(210)
        self.page_h_spin = QDoubleSpinBox()
        self.page_h_spin.setRange(50, 2000)
        self.page_h_spin.setValue(297)
        pw_row = QHBoxLayout()
        pw_row.addWidget(self.page_w_spin)
        pw_row.addWidget(QLabel("×"))
        pw_row.addWidget(self.page_h_spin)
        grid.addLayout(pw_row, 2, 3)

        grid.addWidget(QLabel("PDF 排版"), 3, 0)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["网格拼版（一页多码）", "单码一页"])
        grid.addWidget(self.mode_combo, 3, 1)
        grid.addWidget(QLabel("码间距 (mm)"), 3, 2)
        self.gap_spin = QDoubleSpinBox()
        self.gap_spin.setRange(0.0, 50.0)
        self.gap_spin.setValue(5.0)
        grid.addWidget(self.gap_spin, 3, 3)

        grid.addWidget(QLabel("页边距 (mm)"), 4, 0)
        self.margin_spin = QDoubleSpinBox()
        self.margin_spin.setRange(2.0, 60.0)
        self.margin_spin.setValue(10.0)
        grid.addWidget(self.margin_spin, 4, 1)
        self.cut_chk = QCheckBox("打印裁切参考线")
        grid.addWidget(self.cut_chk, 4, 2, 1, 2)

        grid.addWidget(QLabel("黑边宽度 (mm)"), 5, 0)
        self.pad_spin = QDoubleSpinBox()
        self.pad_spin.setRange(0.0, 50.0)
        self.pad_spin.setValue(3.0)
        self.pad_spin.setDecimals(1)
        self.pad_spin.setSingleStep(0.5)
        grid.addWidget(self.pad_spin, 5, 1)
        grid.addWidget(QLabel("显示样式"), 5, 2)
        self.style_combo = QComboBox()
        self.style_combo.addItems(["黑底标签（白色码区，默认）",
                                   "黑底反色（需读码器支持反色）",
                                   "白底黑码（经典）"])
        self.style_combo.setToolTip(
            "黑底标签（默认）：黑标签底 + 白色码区面板 + 标准黑码，\n"
            "普通扫描器 / OpenCV 直接识别，推荐用于 OHT / AMR 地贴。\n\n"
            "黑底反色：标准码整体反色印在黑底上（静区为黑）。\n"
            "识别需扫描端支持反色：OpenCV 检测前先对图像取反，\n"
            "工业读码器需开启 Inverse/反色 选项；不支持反色的设备读不了。")
        grid.addWidget(self.style_combo, 5, 3)

        # 每个码的生成数量
        grid.addWidget(QLabel("数量 (份)"), 6, 0)
        self.qty_spin = QSpinBox()
        self.qty_spin.setRange(1, 9999)
        self.qty_spin.setValue(1)
        grid.addWidget(self.qty_spin, 6, 1)
        btn_qty_sel = QPushButton("数量→选中")
        btn_qty_sel.clicked.connect(lambda: self.apply_qty(False))
        btn_qty_all = QPushButton("数量→全部")
        btn_qty_all.clicked.connect(lambda: self.apply_qty(True))
        grid.addWidget(btn_qty_sel, 6, 2)
        grid.addWidget(btn_qty_all, 6, 3)

        # 编号 / 位置（作用于选中行）
        edit_row = QHBoxLayout()
        edit_row.addWidget(QLabel("编号"))
        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText("如 100001")
        self.code_edit.setFixedWidth(110)
        edit_row.addWidget(self.code_edit)
        edit_row.addWidget(QLabel("位置"))
        self.loc_edit = QLineEdit()
        self.loc_edit.setPlaceholderText("印在码下方，如：A区充电桩")
        edit_row.addWidget(self.loc_edit, 1)
        btn_identity = QPushButton("应用到选中")
        btn_identity.clicked.connect(self.apply_identity)
        edit_row.addWidget(btn_identity)
        grid.addLayout(edit_row, 7, 0, 1, 4)

        self._params_parent_layout.addWidget(params)

        # 参数变化 → 刷新预览
        for w in (self.quiet_spin, self.page_w_spin, self.page_h_spin,
                  self.gap_spin, self.margin_spin, self.pad_spin):
            w.valueChanged.connect(self.update_previews)
        for w in (self.label_chk, self.cut_chk):
            w.stateChanged.connect(self.update_previews)
        self.style_combo.currentIndexChanged.connect(self.update_previews)
        self.mode_combo.currentIndexChanged.connect(self.update_previews)
        self.paper_combo.currentIndexChanged.connect(self._on_paper_changed)

    # ------------------------------------------------------------- 状态辅助
    def _selected_spec(self):
        row = self.list_widget.currentRow()
        if 0 <= row < len(self.markers):
            return self.markers[row]
        return None

    def _quiet(self):
        return self.quiet_spin.value()

    def _style(self):
        """显示样式：0=黑底标签+白码区 1=黑底反色 2=白底黑码。"""
        return ("dark_panel", "inverted", "light")[self.style_combo.currentIndex()]

    def _is_qr(self):
        return self.type_combo.currentIndex() == 1

    def _bits_for(self, spec):
        """取一个记录的模块矩阵：QR 模式直接编码编号；ArUco 模式查字典。"""
        if self._is_qr():
            return qr_matrix(spec.display_code())
        return None if spec.marker_id is None else self.dict_obj.bits(spec.marker_id)

    def _bits_fn(self):
        return self._bits_for

    def _update_dict_info(self):
        """更新「码类型/字典支持范围」常驻提示。"""
        if self._is_qr():
            self.dict_info.setText(
                "QR 二维码：直接编码「编号」本身（如 100001），"
                "数字容量约 7089 位，无码号数量限制，无需映射表反查。\n"
                "提示：默认「黑底标签」样式通用可识别；「黑底反色」样式"
                "需扫描端支持反色识别（部分设备不支持）。")
        else:
            d = self.dict_obj
            self.dict_info.setText(
                f"{d.name}：支持 {d.marker_count} 个码（码号 0~{d.marker_count - 1}），"
                f"{d.marker_bits}×{d.marker_bits} 模块。\n"
                f"编号可任意（如 100001），超容量自动分配码号；码号用尽时该记录标红并跳过。\n"
                f"提示：默认「黑底标签」样式普通扫描器直接识别；「黑底反色」需"
                f"读码器开启 Inverse/反色 选项（OpenCV 检测前先反转图像）。")

    def _next_qr_code(self):
        """QR 模式下「添加」按钮的默认编号：已有数字编号最大值 +1，否则从 100001 起。"""
        nums = [int(m.code) for m in self.markers if m.code.isdigit()]
        return (max(nums) + 1) if nums else 100001

    def _on_type_changed(self):
        qr = self._is_qr()
        self.dict_combo.setEnabled(not qr)
        self._update_dict_info()
        self.refresh_list()
        self.update_previews()
        kind = "QR 二维码" if qr else f"ArUco {self.dict_obj.name}"
        self.statusBar().showMessage(f"码类型切换为 {kind}。")

    def _page(self):
        if self.paper_combo.currentText() == "自定义":
            return self.page_w_spin.value(), self.page_h_spin.value()
        return PAPERS[self.paper_combo.currentText()]

    def refresh_list(self):
        self._building = True
        cur = self.list_widget.currentRow()
        self.list_widget.clear()
        for spec in self.markers:
            invalid = self._bits_for(spec) is None
            kind = "QR" if self._is_qr() else f"码{spec.marker_id}"
            text = (f"{spec.display_code() or '—'}  |  {spec.location or '—'}  "
                    f"|  ×{spec.qty}  |  {spec.size_mm:g} mm  |  {kind}")
            if invalid:
                text += "   ⚠ 内容无法编码（过长或为空）" if self._is_qr() \
                    else f"   ⚠ 无可用码号 (0~{self.dict_obj.marker_count - 1})"
            item = QListWidgetItem(text)
            if invalid:
                item.setForeground(QColor("#CC2222"))
            self.list_widget.addItem(item)
        self._building = False
        if 0 <= cur < self.list_widget.count():
            self.list_widget.setCurrentRow(cur)
        elif self.list_widget.count():
            self.list_widget.setCurrentRow(0)
        self._update_counts()
        self._update_buttons()

    def _update_counts(self):
        if not hasattr(self, "count_label"):
            return
        self.count_label.setText(
            f"记录 {len(self.markers)} 条 / 标签共 {total_labels(self.markers)} 个")

    def _update_buttons(self):
        """列表有数据后才启用「预览 / 生成PDF / 导出映射表 / 导出 SVG」。"""
        if not hasattr(self, "btn_preview"):
            return
        has = bool(self.markers)
        for b in (self.btn_preview, self.btn_pdf, self.btn_map, self.btn_svg):
            b.setEnabled(has)
        if not has:
            self._preview_on = False

    def _on_selection_changed(self):
        spec = self._selected_spec()
        if spec is not None:
            self.qty_spin.setValue(spec.qty)
            self.loc_edit.setText(spec.location)
            self.code_edit.setText(spec.code)
        self.update_previews()

    # ------------------------------------------------------------- 事件
    def on_dict_changed(self, name):
        self.dict_obj = ArucoDictionary(name)
        self.refresh_list()
        self.update_previews()
        self.statusBar().showMessage(
            f"字典切换为 {name}：{self.dict_obj.marker_count} 个码 "
            f"(ID 范围 {self.dict_obj.id_range_text()}), 每边 {self.dict_obj.marker_bits} 模块")

    def _on_paper_changed(self):
        custom = self.paper_combo.currentText() == "自定义"
        self.page_w_spin.setEnabled(custom)
        self.page_h_spin.setEnabled(custom)
        self.update_previews()

    # ------------------------------------------------------------- 预览
    def toggle_preview(self):
        """点「预览」→ 渲染当前选中码；之后点击左侧列表即切换。"""
        if not self.markers:
            QMessageBox.information(self, "提示", "请先导入 CSV、批量编号或添加码。")
            return
        self._preview_on = True
        self.update_previews()
        self.statusBar().showMessage("单码预览已生成：点击左侧列表可切换预览的码。")

    def update_previews(self):
        if getattr(self, "_building", True):
            return
        spec = self._selected_spec()
        if not self.markers:
            self._preview_on = False
            svg = placeholder_svg("请先导入 CSV、批量编号或添加码")
        elif not self._preview_on:
            svg = placeholder_svg(f"已载入 {len(self.markers)} 条记录\n点击「预览」生成码图")
        elif spec is None:
            svg = placeholder_svg("请先在左侧列表选中一个码")
        else:
            bits = self._bits_for(spec)
            if bits is None:
                tip = ("内容无法编码，请检查编号是否过长" if self._is_qr()
                       else f"无可用码号\n(0~{self.dict_obj.marker_count - 1})")
                svg = placeholder_svg(f"编号 {spec.display_code() or '（空）'}: {tip}")
            else:
                svg = marker_svg(spec, bits, self._quiet(), self.label_chk.isChecked(),
                                 cut_line=self.cut_chk.isChecked(),
                                 padding_mm=self.pad_spin.value(),
                                 style=self._style())
        self.preview_label.set_svg(svg)

    def open_pdf_preview(self):
        """点「生成PDF」→ 弹出整页预览对话框（可翻页），在其中导出。"""
        if not self.markers:
            QMessageBox.information(self, "提示", "请先导入 CSV、批量编号或添加码。")
            return
        PdfPreviewDialog(self).exec_()

    # ------------------------------------------------------------- 数据操作
    def import_csv(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 CSV 文件", "", "CSV 文件 (*.csv);;所有文件 (*.*)")
        if not path:
            return
        try:
            records, errors = load_csv(path, default_size=self.size_spin.value())
        except CsvLoadError as e:
            QMessageBox.warning(self, "导入失败", str(e))
            return
        except OSError as e:
            # 文件被占用 / 无权限 / 选到目录等：不把 traceback 和绝对路径抛给用户
            self.statusBar().showMessage(f"导入失败（{type(e).__name__}）: {e}")
            QMessageBox.warning(
                self, "导入失败",
                f"无法读取所选文件：{type(e).__name__}\n"
                f"{os.path.basename(path)}\n\n"
                "请确认文件未被 Excel 等程序占用，且有读取权限。")
            return

        if self.markers:
            ret = QMessageBox.question(
                self, "导入方式", "当前列表已有码。\n「Yes」= 替换现有列表，「No」= 追加到列表末尾。",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if ret == QMessageBox.Cancel:
                return
            if ret == QMessageBox.Yes:
                self.markers = []

        # 分配 ArUco 码号：编号超出字典容量时自动分配未被占用的码号
        # （QR 模式直接编码编号本身，无需分配码号）
        reserved = {m.marker_id for m in self.markers if m.marker_id is not None}
        if not self._is_qr():
            assign_aruco_ids(records, self.dict_obj.marker_count, reserved)

        no_slot = 0
        for r in records:
            if not self._is_qr() and r["aruco"] is None:
                no_slot += 1
                continue
            self.markers.append(MarkerSpec(
                dict_name="QR" if self._is_qr() else self.dict_obj.name,
                marker_id=None if self._is_qr() else r["aruco"],
                size_mm=r["size"], location=r["location"], qty=r["qty"],
                code=str(r["id"])))
        self.refresh_list()
        invalid = sum(1 for s in self.markers
                      if s.marker_id is None or self.dict_obj.bits(s.marker_id) is None)
        msg = (f"成功导入 {len(records)} 条记录，"
               f"共 {total_labels(self.markers)} 个标签（按数量展开）。")
        if no_slot:
            msg += (f"\n\n⚠ {no_slot} 条记录因字典 {self.dict_obj.name} 码号已用尽被跳过，"
                    f"请换用更大的字典（如 DICT_7X7_1000）。")
        if errors:
            preview = "\n".join(f"  第{ln}行: {reason}" for ln, reason in errors[:10])
            more = f"\n  …（共 {len(errors)} 条问题行已跳过）" if len(errors) > 10 else ""
            msg += f"\n\n⚠ {len(errors)} 条问题行被跳过:\n{preview}{more}"
        if invalid:
            msg += f"\n\n⚠ {invalid} 条记录没有可用的 ArUco 码号，导出时会被跳过（可切换字典）。"
        QMessageBox.information(self, "导入完成", msg)
        self.statusBar().showMessage(
            f"已导入 {path}（{len(records)} 条记录 / "
            f"{total_labels(self.markers)} 个标签，{len(errors)} 条跳过）")

    def batch_numbers(self):
        dlg = BatchNumberDialog(self, self.size_spin.value())
        if dlg.exec_() != QDialog.Accepted:
            return
        numbers = dlg.numbers()
        if not numbers:
            QMessageBox.information(self, "提示", "编号区间为空，请检查起始/结束编号。")
            return

        size = dlg.size_spin.value()
        qty = dlg.qty_spin.value()
        tpl = dlg.loc_edit.text().strip()
        recs = []
        for i, num in enumerate(numbers, 1):
            loc = tpl.replace("{n}", str(i)).replace("{id}", str(num)).strip()
            recs.append({"id": num, "aruco": None, "location": loc,
                         "qty": qty, "size": size})

        reserved = {m.marker_id for m in self.markers if m.marker_id is not None}
        if not self._is_qr():
            assign_aruco_ids(recs, self.dict_obj.marker_count, reserved)

        added, no_slot = 0, 0
        for r in recs:
            if not self._is_qr() and r["aruco"] is None:
                no_slot += 1
                continue
            self.markers.append(MarkerSpec(
                dict_name="QR" if self._is_qr() else self.dict_obj.name,
                marker_id=None if self._is_qr() else r["aruco"],
                size_mm=r["size"], location=r["location"], qty=r["qty"],
                code=str(r["id"])))
            added += 1
        self.refresh_list()
        self.update_previews()

        msg = (f"已生成 {added} 条编号记录（编号 {numbers[0]}~{numbers[-1]}，"
               f"共 {len(numbers)} 个编号），标签共 {total_labels(self.markers)} 个。")
        if no_slot:
            msg += (f"\n\n⚠ {no_slot} 个编号因字典 {self.dict_obj.name} 码号不足被跳过，"
                    f"请换用更大的字典（如 DICT_7X7_1000），或改用 QR 二维码。")
        if not self._is_qr():
            msg += "\n\n记得「导出映射表」，扫码时用它把码号反查回编号。"
        QMessageBox.information(self, "生成完成", msg)
        self.statusBar().showMessage(
            f"批量编号完成：新增 {added} 条，标签共 {total_labels(self.markers)} 个")

    def add_marker(self):
        if self._is_qr():
            self.markers.append(MarkerSpec(
                dict_name="QR", marker_id=None,
                size_mm=self.size_spin.value(), location="",
                qty=self.qty_spin.value(), code=str(self._next_qr_code())))
            self.refresh_list()
            self.list_widget.setCurrentRow(len(self.markers) - 1)
            return
        used = {m.marker_id for m in self.markers if m.marker_id is not None}
        for i in range(self.dict_obj.marker_count):
            if i not in used:
                marker_id = i
                break
        else:
            QMessageBox.warning(self, "无法添加", f"字典 {self.dict_obj.name} 的码号已用完。")
            return
        self.markers.append(MarkerSpec(
            dict_name=self.dict_obj.name, marker_id=marker_id,
            size_mm=self.size_spin.value(), location="",
            qty=self.qty_spin.value(), code=""))
        self.refresh_list()
        self.list_widget.setCurrentRow(len(self.markers) - 1)

    def delete_selected(self):
        row = self.list_widget.currentRow()
        if 0 <= row < len(self.markers):
            del self.markers[row]
            self.refresh_list()
            self.update_previews()

    def clear_all(self):
        if self.markers:
            self.markers = []
            self.refresh_list()
            self.update_previews()

    def apply_size(self, to_all):
        size = self.size_spin.value()
        if to_all:
            for m in self.markers:
                m.size_mm = size
        else:
            spec = self._selected_spec()
            if spec is None:
                QMessageBox.information(self, "提示", "请先在左侧列表选中一个码。")
                return
            spec.size_mm = size
        self.refresh_list()
        self.update_previews()

    def apply_qty(self, to_all):
        qty = self.qty_spin.value()
        if to_all:
            for m in self.markers:
                m.qty = qty
        else:
            spec = self._selected_spec()
            if spec is None:
                QMessageBox.information(self, "提示", "请先在左侧列表选中一个码。")
                return
            spec.qty = qty
        self.refresh_list()
        self.update_previews()

    def apply_identity(self):
        spec = self._selected_spec()
        if spec is None:
            QMessageBox.information(self, "提示", "请先在左侧列表选中一个码。")
            return
        spec.code = self.code_edit.text().strip()
        spec.location = self.loc_edit.text().strip()
        self.refresh_list()
        self.update_previews()

    # ------------------------------------------------------------- 导出
    def export_mapping(self):
        """导出「编号 ↔ ArUco 码号」对照表，供扫码系统反查。"""
        if not self.markers:
            QMessageBox.information(self, "提示", "请先导入 CSV 或批量生成编号。")
            return
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        path, _ = QFileDialog.getSaveFileName(
            self, "保存映射表",
            os.path.join(os.path.expanduser("~"), "Desktop", f"编号映射表_v{stamp}.csv"),
            "CSV 文件 (*.csv)")
        if not path:
            return
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            if self._is_qr():
                w.writerow(["编号", "位置", "尺寸mm", "数量"])
                for s in self.markers:
                    w.writerow([csv_cell(s.display_code()), csv_cell(s.location),
                                f"{s.size_mm:g}", s.qty])
                tip = "QR 码直接编码编号本身，扫码结果即编号，无需反查。"
            else:
                w.writerow(["编号", "ArUco码号", "字典", "位置", "尺寸mm", "数量"])
                for s in self.markers:
                    w.writerow([csv_cell(s.display_code()), s.marker_id,
                                csv_cell(self.dict_obj.name), csv_cell(s.location),
                                f"{s.size_mm:g}", s.qty])
                tip = "扫码识别出 ArUco 码号后，用这张表反查对应编号。"
        QMessageBox.information(
            self, "导出完成",
            f"映射表已保存:\n{path}\n\n共 {len(self.markers)} 条记录。\n{tip}")
        self.statusBar().showMessage(f"映射表导出完成: {path}")

    def export_svg(self):
        if not self.markers:
            QMessageBox.information(self, "提示", "请先导入 CSV 或添加码。")
            return
        folder = QFileDialog.getExistingDirectory(self, "选择 SVG 导出目录")
        if not folder:
            return
        short = "qr" if self._is_qr() else self.dict_obj.name.replace("DICT_", "").lower()
        written, skipped, duplicated, unsafe = 0, [], 0, []
        used_names, seen_keys = set(), set()
        folder_real = os.path.realpath(folder)
        for spec in self.markers:
            bits = self._bits_for(spec)
            if bits is None:
                skipped.append(spec.display_code())
                continue
            # 同编号 + 同尺寸 + 同位置的重复标签内容完全一致，只落一份文件
            key = (spec.marker_id, round(spec.size_mm, 3), spec.location)
            if key in seen_keys:
                duplicated += spec.qty
                continue
            seen_keys.add(key)
            svg = marker_svg(spec, bits, self._quiet(), self.label_chk.isChecked(),
                             cut_line=self.cut_chk.isChecked(),
                             padding_mm=self.pad_spin.value(),
                             style=self._style())
            # 编号与位置都经白名单清洗：路径分隔符/.. /冒号等一律变成下划线，
            # 杜绝「编号 ../../../x」把文件写到所选目录之外（CWE-22）。
            base = f"{short}_{safe_filename(spec.display_code())}"
            if spec.location:
                safe_loc = safe_filename(spec.location, 20)
                if safe_loc != "label":       # 全是不安全字符时不留空段
                    base += f"_{safe_loc}"
            fname, k = base + ".svg", 2
            while fname in used_names:
                fname, k = f"{base}_{k}.svg", k + 1
            target = os.path.join(folder_real, fname)
            # 二次兜底：解析符号链接/目录联接后，父目录仍必须是所选目录
            if os.path.dirname(os.path.realpath(target)) != folder_real:
                unsafe.append(spec.display_code())
                continue
            used_names.add(fname)
            with open(target, "w", encoding="utf-8") as f:
                f.write(svg)
            written += 1
        msg = f"已导出 {written} 个 SVG 文件到:\n{folder}"
        if duplicated:
            msg += f"\n\nℹ 另有 {duplicated} 个重复标签（同 ID/尺寸/位置）未重复落盘——\nSVG 是单标签文件，数量请用「导出 PDF」体现。"
        if skipped:
            msg += f"\n\n⚠ {len(skipped)} 条记录无可用码号被跳过: {skipped[:20]}"
        if unsafe:
            msg += f"\n\n⚠ {len(unsafe)} 条记录的编号/位置无法生成安全文件名，已跳过: {unsafe[:20]}"
        QMessageBox.information(self, "导出完成", msg)
        self.statusBar().showMessage(f"SVG 导出完成: {written} 个 → {folder}")
