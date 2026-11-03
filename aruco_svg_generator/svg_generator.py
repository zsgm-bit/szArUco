# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 zsgm-bit
# 本项目采用 MIT 许可证，详见 LICENSE。
"""ArUco 矢量 SVG 生成器。

坐标系：1 SVG 用户单位 = 1 mm。
三种显示样式（style 参数）：
    dark_panel  黑底标签（默认，通用可识别）：黑标签底 → 白色码区面板
                （内含静区）→ 标准黑码。普通扫描器 / OpenCV 直接识别。
    inverted    黑底反色：标签与静区为黑，标准标记整体反色 —— 白色边环 +
                白色底板、白色单元画成黑色。几何上是标准码的严格反相，
                识别需扫描端支持反色（OpenCV 检测前先反转图像，
                工业读码器开启 Inverse/反色 选项）；不支持反色的普通
                扫描器无法识别，请改用 dark_panel。
                注意：黑底上直接画白模块而省掉边环（让黑环融入黑底）的
                画法，任何标准检测器都无法识别 —— 已实测确认。
    light       白底黑码（经典）：白底上标准黑框黑码。
bits 约定：1 = ArUco 标准标记中的白色单元 / QR 的黑色模块。
ArUco 码方块含 1 格宽的黑色（反色时白色）外框环，size_mm 为含外框的总边长。
所有模块用 <rect> 绘制，纯矢量、无像素、可无限缩放，
适合交给广告厂商制作 OHT / AMR 地标耐磨地贴。
"""
from dataclasses import dataclass
from xml.sax.saxutils import escape

FONT_STACK = "Microsoft YaHei, SimHei, Arial, sans-serif"
LABEL_AREA_MM = 10.0   # 码下方文字标签区高度（mm）

# 配色
TAG_BG_DARK = "#000000"      # 黑底标签
TAG_BG_LIGHT = "#FFFFFF"     # 白底标签
PANEL_BG = "#FFFFFF"         # 码区面板（白底）
MODULE_COLOR = "#000000"     # 码模块（黑码）
TEXT_ON_DARK = "#FFFFFF"
TEXT_ON_DARK_SUB = "#D9D9D9"
TEXT_ON_LIGHT = "#000000"
TEXT_ON_LIGHT_SUB = "#444444"


@dataclass
class MarkerSpec:
    """一个码记录。

    marker_id : ArUco 字典内的码号（0 ~ 字典容量-1），决定黑白图案
    code      : 对外编号（如 100001 / 200001），印在标签上；空则显示 marker_id
    """
    dict_name: str
    marker_id: int
    size_mm: float = 60.0
    location: str = ""      # 安装位置（印在码下方）
    qty: int = 1            # 生成数量（拼版/打印时重复 N 次）
    code: str = ""          # 对外编号

    # 兼容旧字段名
    @property
    def remark(self):
        return self.location

    @remark.setter
    def remark(self, value):
        self.location = value

    def display_code(self) -> str:
        """标签上印的编号：优先 code，否则 ArUco 码号。"""
        if self.code:
            return self.code
        return str(self.marker_id) if self.marker_id is not None else ""


def qr_matrix(data: str, ecc: str = "M"):
    """生成 QR 二维码模块矩阵（numpy 0/1 二维数组，不含外部留白）。

    QR 直接编码内容本身（如编号 100001），无 ArUco 字典的码号容量限制。
    ecc: 纠错级别 L/M/Q/H，默认 M。内容无法编码（如超长）时返回 None。
    """
    if not data:
        return None
    try:
        import numpy as np
        import qrcode
        qr = qrcode.QRCode(
            error_correction=getattr(qrcode.constants, f"ERROR_CORRECT_{ecc}", None),
            border=0,   # 留白（静区）由外部统一绘制
        )
        qr.add_data(data)
        qr.make(fit=True)
        return np.array(qr.get_matrix(), dtype=np.uint8)
    except Exception:
        return None


def assign_aruco_ids(records, capacity: int, reserved=None):
    """为记录分配 ArUco 码号。

    records: [{"id": 原始编号, "aruco": 显式码号或 None, ...}]
    reserved: 已被占用的码号（追加导入时传已有记录的 marker_id）
    规则：
      1. 显式给了 aruco 列的，直接用；
      2. 编号本身落在字典范围内且未被占用，就用它当码号（兼容旧 CSV）；
      3. 其余（如 100001 这类资产编号）按顺序分配未被占用的最小码号。
    返回 records（原地写入 aruco 字段），超出容量的记录 aruco=None。
    """
    used = set(reserved or ())
    used.update(r["aruco"] for r in records if r.get("aruco") is not None)
    nxt = 0
    for r in records:
        if r.get("aruco") is not None:
            continue
        mid = r["id"]
        if 0 <= mid < capacity and mid not in used:
            r["aruco"] = mid
            used.add(mid)
            continue
        while nxt in used:
            nxt += 1
        r["aruco"] = nxt if nxt < capacity else None
        if nxt < capacity:
            used.add(nxt)
    return records


def expand_specs(specs):
    """按数量把记录展开成逐个标签的列表（预览/拼版/打印用）。"""
    out = []
    for s in specs:
        try:
            q = int(s.qty)
        except (TypeError, ValueError):
            q = 1
        out.extend([s] * max(1, q))
    return out


def total_labels(specs) -> int:
    """展开后的标签总数。"""
    return sum(max(1, int(getattr(s, "qty", 1) or 1)) for s in specs)


def _fmt(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return s if s else "0"


def footprint(size_mm: float, quiet_mm: float, show_label: bool, padding_mm: float = 0.0):
    """单个标签的总占位（黑边框 + 码区面板 + 标签区），返回 (宽, 高) mm。"""
    w = size_mm + 2 * quiet_mm + 2 * padding_mm
    h = w + (LABEL_AREA_MM if show_label else 0.0)
    return w, h


def compute_grid(count: int, cell_w: float, cell_h: float,
                 page_w: float, page_h: float, margin_mm: float, gap_mm: float):
    """按纸张计算网格行列数。返回 (cols, rows, per_page, pages)。"""
    usable_w = max(page_w - 2 * margin_mm, 1.0)
    usable_h = max(page_h - 2 * margin_mm, 1.0)
    cols = max(1, int((usable_w + gap_mm) // (cell_w + gap_mm)))
    rows = max(1, int((usable_h + gap_mm) // (cell_h + gap_mm)))
    per_page = cols * rows
    pages = max(1, -(-count // per_page)) if count else 1
    return cols, rows, per_page, pages


def _rect(x, y, w, h, fill):
    return (f'<rect x="{_fmt(x)}" y="{_fmt(y)}" width="{_fmt(w)}" height="{_fmt(h)}" '
            f'fill="{fill}"/>')


def _marker_body(spec: MarkerSpec, bits, ox: float, oy: float,
                 quiet_mm: float, show_label: bool,
                 padding_mm: float = 0.0, style: str = "inverted"):
    """生成单个标签的矢量元素。

    ox/oy 为标签左上角；码区内缩 padding_mm，再留 quiet_mm 静区，
    码方块（ArUco 含 1 格宽外框环）从静区内侧开始，总边长 = size_mm。
    """
    size = spec.size_mm
    n = bits.shape[0]
    is_qr = spec.dict_name == "QR"
    ring = 0 if is_qr else 1          # ArUco 外框环宽度（格数）
    cells = n + 2 * ring
    cell = size / cells
    tag_w, tag_h = footprint(size, quiet_mm, show_label, padding_mm)
    panel = size + 2 * quiet_mm
    px, py = ox + padding_mm, oy + padding_mm        # 码区外沿左上角
    mx, my = px + quiet_mm, py + quiet_mm            # 码方块左上角

    parts = []
    # 1) 标签底色
    parts.append(_rect(ox, oy, tag_w, tag_h,
                       TAG_BG_LIGHT if style == "light" else TAG_BG_DARK))
    # 2) 码区白色面板（仅 dark_panel 需要，内含静区）
    if style == "dark_panel":
        parts.append(_rect(px, py, panel, panel, PANEL_BG))
    # 3) 码方块底色 + 模块
    if is_qr:      # QR：1 = 黑模块
        base = TAG_BG_DARK if style == "inverted" else PANEL_BG
        mark = PANEL_BG if style == "inverted" else MODULE_COLOR
    else:          # ArUco：1 = 标准标记中的白色单元
        base = PANEL_BG if style == "inverted" else MODULE_COLOR
        mark = MODULE_COLOR if style == "inverted" else PANEL_BG
    parts.append(_rect(mx, my, size, size, base))
    off = cell * ring
    for r in range(n):
        for c in range(n):
            if bits[r][c]:
                parts.append(_rect(mx + off + c * cell, my + off + r * cell,
                                   cell, cell, mark))
    # 4) 标签文字（位于码区下方）
    if show_label:
        cx = ox + tag_w / 2.0
        base_y = py + panel
        fill1 = TEXT_ON_LIGHT if style == "light" else TEXT_ON_DARK
        fill2 = TEXT_ON_LIGHT_SUB if style == "light" else TEXT_ON_DARK_SUB
        code = spec.display_code()
        fs = 5.0 if len(code) <= 6 else max(3.0, 5.0 * 6.0 / len(code))
        parts.append(
            f'<text x="{_fmt(cx)}" y="{_fmt(base_y + 4.6)}" font-family="{FONT_STACK}" '
            f'font-size="{_fmt(fs)}" font-weight="bold" fill="{fill1}" text-anchor="middle">'
            f'{escape(code)}</text>'
        )
        if spec.location:
            parts.append(
                f'<text x="{_fmt(cx)}" y="{_fmt(base_y + 9.2)}" font-family="{FONT_STACK}" '
                f'font-size="3.8" fill="{fill2}" text-anchor="middle">'
                f'{escape(spec.location)}</text>'
            )
    return parts


def _resolve_style(style, dark_bg):
    """兼容旧的 dark_bg 布尔参数：未显式给 style 时按 dark_bg 推导。"""
    if style is not None:
        return style
    return "dark_panel" if dark_bg else "light"


def marker_svg(spec: MarkerSpec, bits, quiet_mm: float = 5.0,
               show_label: bool = True, with_bg: bool = True,
               cut_line: bool = False, padding_mm: float = 3.0,
               style: str = "dark_panel", dark_bg=None) -> str:
    """生成单个标签的完整 SVG 字符串。

    style: "dark_panel"（黑底标签+白色码区，默认，通用可识别）/
           "inverted"（黑底反色，需扫描端支持反色）/ "light"（白底黑码）。
    传旧参数 dark_bg 时按其值推导。
    """
    style = _resolve_style(style, dark_bg)
    size = spec.size_mm
    w, h = footprint(size, quiet_mm, show_label, padding_mm)
    p = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_fmt(w)}mm" height="{_fmt(h)}mm" '
        f'viewBox="0 0 {_fmt(w)} {_fmt(h)}">'
    ]
    if with_bg and style == "light":
        p.append(_rect(0, 0, w, h, "#FFFFFF"))
    if cut_line:
        stroke = "#888888" if style != "light" else "#BBBBBB"
        p.append(
            f'<rect x="0.15" y="0.15" width="{_fmt(w - 0.3)}" height="{_fmt(h - 0.3)}" '
            f'fill="none" stroke="{stroke}" stroke-width="0.2"/>'
        )
    p.extend(_marker_body(spec, bits, 0.0, 0.0, quiet_mm, show_label,
                          padding_mm, style))
    p.append('</svg>')
    return "\n".join(p)


def placeholder_svg(text: str, w: float = 120.0, h: float = 90.0) -> str:
    """预览占位图。"""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}">'
        f'<rect width="{w}" height="{h}" fill="#FFFFFF"/>'
        f'<text x="{w / 2}" y="{h / 2}" font-family="{FONT_STACK}" font-size="9" '
        f'fill="#999999" text-anchor="middle">{escape(text)}</text></svg>'
    )


def sheet_svg(specs, bits_fn, page_w: float, page_h: float,
              margin_mm: float = 10.0, gap_mm: float = 5.0, quiet_mm: float = 5.0,
              show_label: bool = True, cut_line: bool = False,
              padding_mm: float = 3.0, style: str = "dark_panel", dark_bg=None,
              page: int = 0):
    """生成整页拼版预览 SVG（page=0 为第 1 页，可翻页预览）。

    返回 (svg_text, cols, rows, per_page)。数量 qty > 1 会展开为多个标签。
    """
    style = _resolve_style(style, dark_bg)
    head = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{_fmt(page_w)}mm" '
            f'height="{_fmt(page_h)}mm" viewBox="0 0 {_fmt(page_w)} {_fmt(page_h)}">')
    flat = expand_specs(specs)
    if not flat:
        return (head + f'<rect width="{_fmt(page_w)}" height="{_fmt(page_h)}" '
                f'fill="#FFFFFF"/></svg>', 0, 0, 0)

    max_size = max(s.size_mm for s in flat)
    cell_w, cell_h = footprint(max_size, quiet_mm, show_label, padding_mm)
    cols, rows, per_page, _pages = compute_grid(
        len(flat), cell_w, cell_h, page_w, page_h, margin_mm, gap_mm)

    p = [head,
         f'<rect width="{_fmt(page_w)}" height="{_fmt(page_h)}" fill="#FFFFFF"/>']
    if cut_line:
        p.append(f'<rect x="0.5" y="0.5" width="{_fmt(page_w - 1)}" '
                 f'height="{_fmt(page_h - 1)}" fill="none" stroke="#CCCCCC" stroke-width="0.3"/>')

    start = max(0, page) * per_page
    for idx, spec in enumerate(flat[start:start + per_page]):
        bits = bits_fn(spec)
        if bits is None:
            continue
        col, row = idx % cols, idx // cols
        x0 = margin_mm + col * (cell_w + gap_mm)
        y0 = margin_mm + row * (cell_h + gap_mm)
        tag_w, _tag_h = footprint(spec.size_mm, quiet_mm, show_label, padding_mm)
        ox = x0 + (cell_w - tag_w) / 2.0
        if cut_line:
            p.append(f'<rect x="{_fmt(ox)}" y="{_fmt(y0)}" width="{_fmt(tag_w)}" '
                     f'height="{_fmt(cell_h)}" fill="none" stroke="#888888" stroke-width="0.2"/>')
        p.extend(_marker_body(spec, bits, ox, y0, quiet_mm, show_label,
                              padding_mm, style))
    p.append('</svg>')
    return "\n".join(p), cols, rows, per_page
