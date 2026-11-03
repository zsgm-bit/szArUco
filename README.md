# szArUco

![Build Windows EXE](https://github.com/zsgm-bit/szArUco/actions/workflows/build.yml/badge.svg)

![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)

ArUco / QR 矢量码生成器 —— 面向 OHT·AMR 地标地贴的 Windows 桌面工具。

生成**纯矢量** SVG（1 单位 = 1mm，无限缩放不失真）与矢量 PDF 拼版文件，  
支持 CSV 批量导*入、批量编号、编号↔码号映射、单码预览与整页拼版预览。*

## *功能特性*

- ***码类型****：ArUco（内置 20 种 OpenCV 标准字典，如 4X4_50 ~ 6X6_250）+ QR 二维码*
- ***矢量输出****：SVG / PDF 全矢量绘制（reportlab），打印无锯齿*
- ***CSV 批量导入****：格式 `id,位置,数量,尺寸`，自动识别表头列序，兼容多编码（UTF-8 / GBK / UTF-16）*
- ***业务编号与码号分离****：编号支持 6–12 位（如 100001~100101 + 200001），超字典容量自动分配码号，可导出映射表反查*
- ***批量编号生成****：起始/结束/步长 + `{n}{id}` 模板 + 附加编号*
- ***三种显示样式****：*
  - *黑底标签（白色码区）—— 默认，普通扫描器 / OpenCV 直测可识别*
  - *白底黑码（经典）*
  - *黑底反色 —— 需扫描端支持 Inverse 反色*
- ***PDF 双模式****：网格拼版 / 单码一页；纸张 A4/A3/A5/Letter + 自定义尺寸*
- ***可调参数****：静区宽度、裁切线、*&#x6807;签信息栏、每码数量
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
- 推送 `v*` 标签 → 构建 exe 并自动发布到 Releases（附带 `SHA256SUMS.txt`）

```bash
git tag v1.0.0 && git push origin v1.0.0
```

CI 权限按最小化拆分：`build` 任务只有 `contents: read`，只有发布 Release 的
`release` 任务才持有 `contents: write`。

## 安全说明

这是一个**纯本地单机工具**：不联网、无账号体系、无数据库、无服务。唯一的不可信输入
是导入的 CSV 与手工填写的编号/位置，安全加固集中在"文本 → 文件名/表格"的落盘路径。

| 加固项 | 说明 |
| --- | --- |
| 文件名净化 | 编号与位置经白名单过滤（只留中英文、数字、`._-`），`../`、`/`、`\`、`:` 等一律转下划线；落盘前再用 `realpath` 校验父目录，杜绝越出所选导出目录（CWE-22/CWE-73） |
| CSV 公式注入 | 导出映射表时，以 `=` `+` `-` `@` 开头的单元格前加 `'`，避免在 Excel/WPS 打开时被当作公式执行（CWE-1236） |
| 资源上限 | 单个 CSV ≤ 20 MB、单次 ≤ 20000 条记录、数量 ≤ 9999、位置 ≤ 200 字符，超限给出明确提示（CWE-400） |
| 临时文件 | 自检日志写入 `mkdtemp` 生成的独占目录并以 `x` 模式创建，不落到可预测的公共路径（CWE-377）；CI 用 `SZARUCO_SELFTEST_LOG` 指定位置 |
| 异常兜底 | 导入被占用/无权限的文件时给出友好提示，不把 traceback 和绝对路径抛给用户（CWE-209/CWE-755） |
| 依赖锁定 | `requirements.txt` 全部使用 `==` 精确版本，避免装到被投毒或不兼容的上游版本（CWE-1357） |
| 构建日志脱敏 | 打包日志中的绝对路径替换为 `<proj>` / `~`，不泄露本机目录结构（CWE-532） |
| 完整性校验 | Release 附带 `SHA256SUMS.txt`，下载后可校验 exe 是否被替换 |

校验下载的 exe（PowerShell）：

```powershell
Get-FileHash .\szArUco_vXXXXXXXXXXXXXX.exe -Algorithm SHA256
```

结果应与 `SHA256SUMS.txt` 中对应行的哈希一致。

SVG/XML 输出已使用 `xml.sax.saxutils.escape` 转义，字典名走固定白名单，
代码中无 `eval`/`exec`/`pickle`/`shell=True`，不含硬编码凭证。

> 以上为代码层面的已知加固，不代表不存在 0day，也不覆盖业务逻辑层面的问题
> （如编号分配策略、映射表与现场安装位置的一致性），这些需结合现场流程自行评审。

## 授权许可（License）

[MIT](LICENSE) © 2026 zsgm-bit

任何人可自由使用、复制、修改、合并、发布、分发、再许可和/或销售本软件的副本，
包括商业使用与闭源集成，**无需支付费用、无需公开修改后的源代码**。

唯一要求：在所有副本或软件的实质性部分中，保留上述版权声明与本许可声明。

本软件按「原样」提供，不附带任何明示或暗示的担保，包括但不限于适销性、
特定用途适用性和非侵权的担保。作者或版权持有人不对任何索赔、损害或其他
责任承担责任。
