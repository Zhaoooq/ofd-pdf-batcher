# OFD、JPG转PDF

这是一个本地运行的批量转换工具，支持上传 `.ofd`、`.jpg`、`.jpeg`、`.png`、`.bmp`、`.tif`、`.tiff`、`.webp`，每个源文件生成一个 PDF。转换完成后可以单独下载、下载总 ZIP，也可以选择转换后合并为一个 PDF。

## 功能

- 批量拖拽或点击上传 OFD 和图片文件
- 每个源文件独立转换为 PDF
- 可选转换纸张大小：保持原尺寸、A4、A3、Letter，支持横向和竖向
- 可选转换后合并：按上传顺序合并所有成功转换的 PDF
- 下载单个 PDF、合并 PDF 或包含全部结果的 ZIP

## 启动

在 PowerShell 里进入项目目录后运行：

```powershell
.\start.ps1
```

首次启动会创建 `.venv` 并安装依赖。服务启动后打开：

```text
http://127.0.0.1:8765
```

也可以双击 `start.bat`。

## 配置

可选环境变量：

- `OFD_PORT`: 服务端口，默认 `8765`
- `OFD_HOST`: 监听地址，默认 `127.0.0.1`
- `OFD_MAX_REQUEST_MB`: 单次批量上传大小限制，默认 `500`

## 文件位置

转换任务保存在：

```text
data/jobs/<任务ID>/
```

每个任务包含上传源文件、独立 PDF、任务状态、可选的 `merged.pdf` 和 `converted-pdfs.zip`。

## 验证

安装依赖后运行：

```powershell
.\.venv\Scripts\python.exe tests\test_smoke.py
```
