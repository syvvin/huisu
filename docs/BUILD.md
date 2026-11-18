# 构建与数据

## 源码运行

需要 Python 3.10+。核心应用使用标准库；内置资源提取器需要：

```powershell
python -m pip install -r requirements.txt
```

另外需要 `ffmpeg` 和 `ffprobe` 在 `PATH` 中。源码启动：

```powershell
.\run.ps1
# 或
python packaging\launcher.py
```

默认打开原生桌面窗口，后台使用本机 8766 端口；资源提取器使用 8765。

## 离线词典

仓库不提交 `data/dict/ecdict.db`（约 97 MB）。生成方法：

1. 从 <https://github.com/skywind3000/ECDICT> 下载 `ecdict.csv`，放到 `tools/ecdict.csv`；
2. 运行：

```powershell
python tools\build_ecdict.py
```

生成 `data/dict/ecdict.db` 和 `meta.json`。没有该文件时应用仍可启动，但本地 ECDICT 查询不可用。

## CEFR 数据

`vocab/data/cefr.json.gz` 已内置，约 3.4 MB。如需从 Words CEFR Dataset 重建：

```powershell
python tools\build_cefr.py `
  --words words.csv --word-pos word_pos.csv --pos-tags pos_tags.csv `
  --out vocab/data/cefr.json.gz
```

CSV 来自 <https://github.com/Maximax67/Words-CEFR-Dataset>（MIT）。

## Windows 安装包

需要：

- Python 3.12
- PyInstaller、pywebview / pythonnet
- Inno Setup 6
- `ffmpeg` / `ffprobe` / `yt-dlp` 在 `PATH` 中
- `data/dict/ecdict.db`
- `vocab/data/cefr.json.gz`

`packaging/bin/` 已包含 `yt-dlp.exe` 和 WebView2 引导程序。构建：

```powershell
.\packaging\build.ps1
```

产物：

```text
release\回溯-0.0.1-win64-setup.exe
```

安装包会把 Python、WebView2、ffmpeg、ffprobe、yt-dlp、ECDICT、CEFR 和资源提取器一起打包；安装目录可选任意盘。

## 路径约定

- 源码数据：`data/state.json`、`data/vocab.db`、`data/dict/`、`data/thumbs/`
- 安装版数据：`%LOCALAPPDATA%\回溯\data\`
- 安装版片库默认：`%USERPROFILE%\Videos\回溯`
- 资源提取器缓存：`%LOCALAPPDATA%\回溯\data\extractor\cache\media`

`state.json` 是唯一需要备份的学习记录；`vocab.db` 是可重建索引。

## 测试

```powershell
python -m unittest discover -s tests -v
```

覆盖 SRT 解析、说话人拆分、分词偏移、不规则词形还原与 CEFR 数据，不联网。
