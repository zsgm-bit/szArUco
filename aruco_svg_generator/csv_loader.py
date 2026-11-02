# -*- coding: utf-8 -*-
"""CSV 导入解析。

标准格式（推荐带表头）：
    id,位置,数量,尺寸
    100001,A区充电桩,1,80
    200001,预留特殊码,1,80

- id     ：对外编号（可超过 ArUco 字典容量，如 100001）；程序会自动分配码号
- 位置   ：任意文字，会印在码下方；留空则不印
- 数量   ：该码生成几份，留空按 1
- 尺寸   ：标记边长 mm，留空用界面默认尺寸；可写 80mm

可选列 aruco（码id/aruco码）：显式指定 ArUco 码号，不写则自动分配。
有表头时按表头名识别列（顺序任意）；无表头时按位置识别，并向后兼容
旧格式 id,尺寸mm / id,尺寸mm,备注。
编码自动尝试：UTF-8 (含 BOM) / UTF-16 / GBK / GB18030。
"""
import csv
import io

# 表头关键词 → 字段
_HEADER_ALIASES = {
    "id": ("id", "编号", "资产编号", "码号", "标识", "asset", "assetid", "资产码"),
    "aruco": ("aruco", "arucoid", "码id", "aruco码", "markerid", "marker", "字典码"),
    "location": ("位置", "安装位置", "location", "pos", "position", "点位", "地点"),
    "qty": ("数量", "个数", "份数", "qty", "quantity", "count", "num"),
    "size": ("尺寸", "边长", "规格", "size", "mm", "尺寸mm", "边长mm"),
}
_NUM_SUFFIX = ("mm", "毫米", "MM", "Mm")


class CsvLoadError(Exception):
    pass


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16", "gbk", "gb18030"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise CsvLoadError("无法识别 CSV 文件编码，请另存为 UTF-8 后重试")


def _to_float(text: str):
    """把 '80' / '80mm' / '80 mm' 转成 float，失败返回 None。"""
    s = text.strip()
    for suf in _NUM_SUFFIX:
        if s.endswith(suf):
            s = s[: -len(suf)]
            break
    s = s.strip()
    try:
        return float(s)
    except ValueError:
        return None


def _map_header(cells):
    """按表头文字建立字段 → 列号映射，识别不出返回 None。"""
    mapping = {}
    for idx, cell in enumerate(cells):
        key = cell.strip().lower().replace(" ", "").replace("（", "(").split("(")[0]
        for field, aliases in _HEADER_ALIASES.items():
            if field in mapping:
                continue
            if key in aliases:
                mapping[field] = idx
                break
    return mapping if "id" in mapping else None


def _guess_layout(cells):
    """无表头时按列数与内容推断各字段所在列。"""
    n = len(cells)
    if n >= 4:
        return {"id": 0, "location": 1, "qty": 2, "size": 3}
    if n == 3:
        # 旧格式 id,尺寸,备注 —— 第二列是数字；否则按 id,位置,数量
        if _to_float(cells[1]) is not None:
            return {"id": 0, "size": 1, "location": 2}
        return {"id": 0, "location": 1, "qty": 2}
    if n == 2:
        if _to_float(cells[1]) is not None:
            return {"id": 0, "size": 1}     # 旧格式 id,尺寸
        return {"id": 0, "location": 1}
    return {"id": 0}


def load_csv(path: str, default_size: float = 60.0):
    """解析 CSV，返回 (records, errors)。

    records: [{"id": int, "aruco": int|None, "location": str, "qty": int, "size": float}, ...]
             id 为对外编号；aruco 为显式指定的码号，None 表示由程序自动分配
    errors : [(行号, 原因), ...]
    """
    with open(path, "rb") as f:
        text = _decode(f.read())

    records, errors = [], []
    layout = None
    for lineno, row in enumerate(csv.reader(io.StringIO(text)), 1):
        cells = [c.strip() for c in row]
        if not any(cells):
            continue

        if layout is None:
            # 第一行：能解析成 ID 就是数据，否则按表头处理
            if _to_float(cells[0]) is None:
                layout = _map_header(cells)
                if layout is None:
                    errors.append((lineno, f"表头无法识别（需包含 id 列）：{','.join(cells)}"))
                continue
            layout = _guess_layout(cells)

        def cell(field):
            idx = layout.get(field)
            return cells[idx] if idx is not None and idx < len(cells) else ""

        raw_id = cell("id")
        mid = _to_float(raw_id)
        if mid is None or mid != int(mid):
            errors.append((lineno, f"ID “{raw_id}” 不是整数"))
            continue
        marker_id = int(mid)
        if marker_id < 0:
            errors.append((lineno, f"ID {marker_id} 不能为负数"))
            continue

        # 数量
        raw_qty = cell("qty")
        qty = 1
        if raw_qty:
            qf = _to_float(raw_qty)
            if qf is None or qf != int(qf):
                errors.append((lineno, f"数量 “{raw_qty}” 不是整数"))
                continue
            qty = int(qf)
            if not (1 <= qty <= 9999):
                errors.append((lineno, f"数量 {qty} 超出范围 (1–9999)"))
                continue

        # 尺寸
        size = default_size
        raw_size = cell("size")
        if raw_size:
            sf = _to_float(raw_size)
            if sf is None:
                errors.append((lineno, f"尺寸 “{raw_size}” 不是数字"))
                continue
            size = sf
            if not (2.0 <= size <= 1000.0):
                errors.append((lineno, f"尺寸 {size:g}mm 超出合理范围 (2–1000mm)"))
                continue

        # 显式指定的 ArUco 码号（可选）
        aruco_id = None
        raw_aruco = cell("aruco")
        if raw_aruco:
            af = _to_float(raw_aruco)
            if af is None or af != int(af) or af < 0:
                errors.append((lineno, f"ArUco 码号 “{raw_aruco}” 不是非负整数"))
                continue
            aruco_id = int(af)

        records.append({
            "id": marker_id,
            "aruco": aruco_id,
            "location": cell("location"),
            "qty": qty,
            "size": size,
        })

    if not records and not errors:
        raise CsvLoadError("CSV 中没有找到有效数据行")
    return records, errors
