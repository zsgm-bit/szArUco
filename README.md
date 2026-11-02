# szArUco

ArUco / QR 矢量码生成器 —— 面向 OHT·AMR 地标地贴的 Windows 桌面工具。

生成**纯矢量** SVG（1 单位 = 1mm，无限缩放不失真）与矢量 PDF 拼版文件，
支持 CSV 批量导入、批量编号、编号↔码号映射、单码预览与整页拼版预览。

## 功能特性

- **码类型**：ArUco（内置 20 种 OpenCV 标准字典，如 4X4_50 ~ 6X6_250）+ QR 二维码
- **矢量输出**：SVG / PDF 全矢量绘制（reportlab），打印无锯齿
- **CSV 批量导入**：格式 `id,位置,数量,尺寸`，自动识别表头列序，兼容多编码（UTF-8 / GBK / UTF-16）
- **业务编号与码号分离**：编号支持 6–12 位（如 100001~100101 + 200001），超字典容量自动分配码号，可导出映射表反查
- **批量编号生成**：起始/结束/步长 + `{n}{id}` 模板 + 附加编号
- **三种显示样式**：
  - 黑底标签（白色码区）—— 默认，普通扫描器 / OpenCV 直测可识别
  - 白底黑码（经典）
  - 黑底反色 —— 需扫描端支持 Inverse 反色
- **PDF 双模式**：网格拼版 / 单码一页；纸张 A4/A3/A5/Letter + 自定义尺寸
- **可调参数**：静区宽度、裁切线、标签信息栏、每码数量
- **自检**：打包后的 exe 支持 `--selftest` 无界面自检（写日志到 `%TEMP%\aruco_selftest.log`）

## 快速开始

### 运行（Windows）

```bat
cd aruco_svg_generator
运行.bat        :: 自动创建 .venv 并安装依赖后启动 GUI
```

或手动：

```bat
python -m venv ..\.venv
..\.venv\Scripts\python.exe -m pip install -r requirements.txt
..\.venv\Scripts\python.exe main.py
```

### 打包 exe

```bat
打包exe.bat     :: PyInstaller 单文件，产出 dist\szArUco_v<时间戳>.exe
```

### 下载现成的 exe

在 GitHub **Actions** 页面选择最新的 **Build Windows EXE** 运行，
在 Artifacts 中下载 `szArUco-windows-exe`；
或到 **Releases** 页面下载打 `v*` 标签后自动附带的 exe。

## CSV 格式

```csv
id,位置,数量,尺寸
100001,A区-01充电桩,2,40
100002,A区-02充电桩,1,40
```

- 表头可省略；有表头时列序任意（自动识别别名）
- 数量留空按 1；尺寸单位 mm（含外框总边长）

## 开发

- Python 3.13 + PyQt5 / opencv-contrib-python / reportlab / qrcode / numpy
- 代码结构：`aruco_dict.py`（字典）、`svg_generator.py`（矢量绘制）、
  `pdf_exporter.py`（PDF 拼版）、`csv_loader.py`（导入）、`gui/`（界面）

## 自动构建（GitHub Actions）

`.github/workflows/build.yml`：

- push 到 `main` / PR → 自检（`--selftest`）+ 构建 exe + 上传 Artifact
- 推送 `v*` 标签 → 构建 exe 并自动发布到 Releases

```bash
git tag v1.0.0 && git push origin v1.0.0
```

## License

[MIT](LICENSE) © 2026 zsgm-bit —— 可自由使用、修改、商用与再分发，需保留版权声明。
