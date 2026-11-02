# -*- coding: utf-8 -*-
"""PDF 导出：单码一页 / 网格拼版 两种模式。

用 reportlab 直接按比特矩阵绘制矢量矩形（非嵌入位图），
打印为纯矢量，任意放大不失真。中文备注使用内置 CID 字体 STSong-Light。

显示样式（style 参数，与 svg_generator 一致）：
    dark_panel  黑底标签（默认，通用可识别）：黑标签底 → 白色码区面板
                （含静区）→ 标准黑码。
    inverted    黑底反色：码整幅反色（白边环+白底板、模块反转），静区为黑，
                识别需扫描端支持反色（OpenCV 先取反图像 / 读码器开 Inverse）。
    light       白底黑码（经典）。
"""
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

from svg_generator import LABEL_AREA_MM, compute_grid, expand_specs, footprint

pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
LABEL_FONT = "STSong-Light"

PAPERS = {
    "A3": (297.0, 420.0),
    "A4": (210.0, 297.0),
    "A5": (148.0, 210.0),
    "Letter": (215.9, 279.4),
}


def page_size(paper: str, custom=None):
    return PAPERS.get(paper) or (custom or (210.0, 297.0))


def draw_marker_pdf(c, spec, bits, quiet_mm, ox, oy_top, show_label, cut_line,
                    padding_mm: float = 0.0, style: str = "dark_panel"):
    """在 PDF 上绘制单个标签（reportlab 原点在左下角）。

    ox     : 标签左边缘 x (mm)
    oy_top : 标签顶边 y (mm，自下而上坐标)
    """
    size = spec.size_mm
    n = bits.shape[0]
    is_qr = spec.dict_name == "QR"
    ring = 0 if is_qr else 1          # ArUco 外框环宽度（格数）
    cells = n + 2 * ring
    cell = size / cells
    tag_w, tag_h = footprint(size, quiet_mm, show_label, padding_mm)
    panel = size + 2 * quiet_mm
    tag_bottom = oy_top - tag_h
    px = ox + padding_mm                       # 码区外沿左边缘
    panel_top = oy_top - padding_mm             # 码区外沿顶边
    m_left = px + quiet_mm                      # 码方块左边缘
    m_top = panel_top - quiet_mm                # 码方块顶边

    # 1) 标签底色
    if style != "light":
        c.setFillColorRGB(0, 0, 0)
        c.rect(ox * mm, tag_bottom * mm, tag_w * mm, tag_h * mm, stroke=0, fill=1)
        # 2) 码区白色面板（仅 dark_panel）
        if style == "dark_panel":
            c.setFillColorRGB(1, 1, 1)
            c.rect(px * mm, (panel_top - panel) * mm, panel * mm, panel * mm,
                   stroke=0, fill=1)

    # 3) 码方块底色 + 模块
    if is_qr:      # QR：1 = 黑模块
        base_black = style == "inverted"
        mark_black = style != "inverted"
    else:          # ArUco：1 = 标准标记中的白色单元
        base_black = style != "inverted"
        mark_black = style == "inverted"
    c.setFillColorRGB(0, 0, 0) if base_black else c.setFillColorRGB(1, 1, 1)
    c.rect(m_left * mm, (m_top - size) * mm, size * mm, size * mm, stroke=0, fill=1)
    if mark_black:
        c.setFillColorRGB(0, 0, 0)
    else:
        c.setFillColorRGB(1, 1, 1)
    off = cell * ring
    for r in range(n):
        for col in range(n):
            if bits[r][col]:
                x = m_left + off + col * cell
                y_top_bit = m_top - off - r * cell
                c.rect(x * mm, (y_top_bit - cell) * mm, cell * mm, cell * mm,
                       stroke=0, fill=1)

    # 4) 标签文字
    if show_label:
        cx = ox + tag_w / 2.0
        base = panel_top - panel            # 码区底边
        if style == "light":
            c.setFillColorRGB(0, 0, 0)
        else:
            c.setFillColorRGB(1, 1, 1)
        c.setFont(LABEL_FONT, 5 * mm)
        c.drawCentredString(cx * mm, (base - 4.6) * mm, spec.display_code())
        if spec.location:
            c.setFont(LABEL_FONT, 3.8 * mm)
            if style == "light":
                c.setFillColorRGB(0.27, 0.27, 0.27)
            else:
                c.setFillColorRGB(0.85, 0.85, 0.85)
            c.drawCentredString(cx * mm, (base - 9.2) * mm, spec.location)

    # 裁切参考线（最后画，避免被黑底覆盖）
    if cut_line:
        c.setStrokeColorRGB(0.53, 0.53, 0.53)
        c.setLineWidth(0.3)
        c.rect(ox * mm, tag_bottom * mm, tag_w * mm, tag_h * mm, stroke=1, fill=0)


def export_pdf(path, specs, bits_fn, paper="A4", custom_size=None, mode="grid",
               quiet_mm=5.0, margin_mm=10.0, gap_mm=5.0,
               show_label=True, cut_line=False,
               padding_mm: float = 0.0, style: str = "dark_panel", dark_bg=None):
    """导出 PDF，返回统计信息 dict。

    mode: "grid" 网格拼版 | "single" 单码一页
    每条记录的 qty 会展开为 qty 个标签分别排布。
    style: "dark_panel"（黑底标签+白色码区，默认，通用可识别）/
           "inverted"（黑底反色，需扫描端支持反色）/ "light"（白底黑码）；
    传旧参数 dark_bg 时按其值推导。
    """
    if style is None:
        style = "dark_panel" if dark_bg else "light"
    pw, ph = page_size(paper, custom_size)
    flat = expand_specs(specs)
    valid, skipped = [], []
    for s in flat:
        (valid if bits_fn(s) is not None else skipped).append(s)

    info = {"records": len(specs), "total": len(flat), "written": 0,
            "skipped": skipped, "pages": 0, "per_page": 0, "cols": 0, "rows": 0,
            "overflow": False, "overflow_ids": []}
    if not valid:
        return info

    c = canvas.Canvas(path, pagesize=(pw * mm, ph * mm))
    c.setTitle("ArUco Markers")
    max_size = max(s.size_mm for s in valid)
    cell_w, cell_h = footprint(max_size, quiet_mm, show_label, padding_mm)

    if mode == "single":
        info["per_page"] = 1
        for i, spec in enumerate(valid):
            if i:
                c.showPage()
            tag_w, tag_h = footprint(spec.size_mm, quiet_mm, show_label, padding_mm)
            if tag_w > pw + 1e-6 or tag_h > ph + 1e-6:
                info["overflow"] = True
                if spec.marker_id not in info["overflow_ids"]:
                    info["overflow_ids"].append(spec.marker_id)
            ox = (pw - tag_w) / 2.0
            oy_top = (ph + tag_h) / 2.0   # 标签顶部（文字标签在其下方）
            draw_marker_pdf(c, spec, bits_fn(spec), quiet_mm,
                            ox, oy_top, show_label, cut_line, padding_mm, style)
        info["pages"] = len(valid)
        info["written"] = len(valid)
    else:
        cols, rows, per_page, pages = compute_grid(
            len(valid), cell_w, cell_h, pw, ph, margin_mm, gap_mm)
        info.update(pages=pages, per_page=per_page, cols=cols, rows=rows,
                    written=len(valid))
        if cell_w > pw - 2 * margin_mm + 1e-6 or cell_h > ph - 2 * margin_mm + 1e-6:
            info["overflow"] = True
        for i, spec in enumerate(valid):
            if i and i % per_page == 0:
                c.showPage()
            k = i % per_page
            col, row = k % cols, k // cols
            x0 = margin_mm + col * (cell_w + gap_mm)
            y_top = ph - margin_mm - row * (cell_h + gap_mm)   # 单元格顶部
            tag_w, _tag_h = footprint(spec.size_mm, quiet_mm, show_label, padding_mm)
            ox = x0 + (cell_w - tag_w) / 2.0
            draw_marker_pdf(c, spec, bits_fn(spec), quiet_mm,
                            ox, y_top, show_label, cut_line, padding_mm, style)
    c.save()
    return info
