# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 zsgm-bit
# 本项目采用 AGPL-3.0 + 商业许可 双轨制：开源许可见 LICENSE，商业许可见 LICENSE-COMMERCIAL.md。
"""szArUco — ArUco/QR 矢量码生成器，程序入口。

Windows 11 直接运行: python main.py
打包后的 exe 支持 `--selftest`：无界面跑一遍字典/QR/SVG/PDF，结果写入
唯一临时目录下的 selftest.log，退出码 0 表示全部通过（用于验证打包完整性）。
可用环境变量 SZARUCO_SELFTEST_LOG 指定日志路径（CI 用）。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _selftest_log_path() -> str:
    """自检日志路径：默认落在 mkdtemp 创建的独占临时目录（CWE-377），
    CI 可用 SZARUCO_SELFTEST_LOG 指定固定位置以便读取。"""
    override = os.environ.get("SZARUCO_SELFTEST_LOG", "").strip()
    if override:
        return override
    return os.path.join(tempfile.mkdtemp(prefix="szaruco_selftest_"), "selftest.log")


def _stdout_utf8():
    """把标准输出切到 UTF-8：CI（cp1252 控制台）打印中文时不会抛 UnicodeEncodeError。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:      # noqa: BLE001 —— 旧环境无 reconfigure 时忽略
            pass


def _selftest():
    """无界面自检：验证字典、QR 矩阵、SVG 生成、PDF 导出。"""
    _stdout_utf8()
    lines, ok = [], True
    tmpdir = tempfile.mkdtemp(prefix="aruco_selftest_")
    try:
        from aruco_dict import ArucoDictionary, list_dictionaries
        from pdf_exporter import export_pdf
        from svg_generator import MarkerSpec, marker_svg, qr_matrix

        names = list_dictionaries()
        lines.append(f"字典数量: {len(names)}")
        d = ArucoDictionary("DICT_6X6_250")
        bits = d.bits(12)
        assert bits is not None and bits.shape == (6, 6)
        lines.append(f"字典 {d.name}: {d.marker_count} 个码, {d.marker_bits}×{d.marker_bits} 模块")

        svg = marker_svg(MarkerSpec(d.name, 12, 60.0, "自检", 1, "100001"), bits, 5.0, True)
        assert "100001" in svg and "<rect" in svg
        lines.append(f"ArUco 单码 SVG: {len(svg)} 字节")

        qm = qr_matrix("100001")
        assert qm is not None and qm.ndim == 2
        lines.append(f"QR 矩阵: {qm.shape[0]}×{qm.shape[1]}")

        specs = [MarkerSpec(d.name, i, 60.0, f"工位{i}", 1, f"10{i:04d}") for i in range(6)]
        out = os.path.join(tmpdir, "selftest.pdf")
        info = export_pdf(out, specs, lambda s: d.bits(s.marker_id), paper="A4", mode="grid")
        assert info["written"] == 6 and os.path.getsize(out) > 1000
        lines.append(f"PDF: {info['written']} 个标签, {info['pages']} 页, "
                     f"{os.path.getsize(out)} 字节")

        qspecs = [MarkerSpec("QR", None, 60.0, "", 1, str(100001 + i)) for i in range(6)]
        out2 = os.path.join(tmpdir, "selftest_qr.pdf")
        info2 = export_pdf(out2, qspecs, lambda s: qr_matrix(s.display_code()),
                           paper="A4", mode="grid")
        assert info2["written"] == 6
        lines.append(f"QR PDF: {info2['written']} 个标签, {info2['pages']} 页")
        lines.append("结果: 全部通过")
    except Exception as exc:      # noqa: BLE001 —— 自检需要把任何异常都记录下来
        import traceback
        ok = False
        lines.append("结果: 失败 —— " + repr(exc))
        lines.append(traceback.format_exc())
    finally:
        log = _selftest_log_path()
        lines.append("日志: " + log)
        log_dir = os.path.dirname(log)
        if log_dir:
            try:
                os.makedirs(log_dir, exist_ok=True)
            except OSError:
                pass
        for attempt in (0, 1):
            if attempt:
                # 已存在（如 CI 固定路径的旧日志）：先删链接/文件本身再独占创建，
                # 绝不顺着已存在的符号链接/硬链接写入（CWE-377）。
                try:
                    os.remove(log)
                except OSError:
                    break
            try:
                with open(log, "x", encoding="utf-8") as f:
                    f.write("\n".join(lines))
                break
            except FileExistsError:
                continue
            except OSError:
                break         # 日志写不了不影响自检结论（以退出码为准）
        try:
            print("\n".join(lines))
        except Exception:      # noqa: BLE001 —— 控制台编码不可控，绝不因此让自检失败
            print("\n".join(lines).encode("ascii", "replace").decode("ascii"))
    return 0 if ok else 1


def main():
    if "--selftest" in sys.argv:
        return _selftest()

    from PyQt5.QtWidgets import QApplication

    from gui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    f = app.font()
    f.setFamily("微软雅黑")
    f.setPointSize(10)
    app.setFont(f)
    win = MainWindow()
    win.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
