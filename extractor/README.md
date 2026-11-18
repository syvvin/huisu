# UTTU 故事资源提取器

针对 [YouYouTu / UTTU](https://uttu.merui.net/) 故事资料库的专用提取工具。它只做一件事：

> 把站点的故事目录显示成可勾选的树，按 Part 导出**切分好的视频**和**英中双语字幕**。

不做通用下载器，不抓取无关资源，不扩展站点镜像功能。

---

## 快速开始

### 1. 环境

- Python 3.10+
- [ffmpeg / ffprobe](https://ffmpeg.org/) 在 `PATH` 中
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) 在 `PATH` 中，或 `pip install yt-dlp`
- 可访问 `uttu.merui.net`、`voice.merui.net` 和 YouTube

```powershell
cd uttu-extractor
python -m pip install -r requirements.txt
# 可选：无自动字幕时的本地 ASR 兜底
# python -m pip install -r requirements-optional.txt
```

### 2. 启动网页界面

```powershell
python -m uttu serve --open
```

默认地址：<http://127.0.0.1:8765/>

- 展开分类 / 章节，勾选任意层级的复选框。
- 分类复选框是“全选 / 取消全选”切换；即使部分章节读取失败，再点一次也会清空该分类的选择。
- “当前选择”里选择分辨率（默认 1080p，可选 720p / 1440p 2K / 2160p 4K）。
- 树节点会显示本地状态：分类显示 `本地 N`，章节显示 `已提取 6/6` 或 `本地 N`，Part 显示绿色 `已提取`（点击可直接打开视频）。
- 勾选章节或分类会自动读取该组下所有 Part，然后整组选中。
- 点“开始提取”，右侧显示进度、完整阶段日志、编码进度、错误汇总和下载链接；日志面板可展开/收起。
- 输出位于 `output/<分类>/<章节>/part-XX_<标题>/`。

### 3. 命令行

```powershell
# 看目录
python -m uttu scan
python -m uttu tree

# 看一个章节的 Part
python -m uttu chapter --path /story/main/chapter-0/

# 提取指定 Part
python -m uttu extract --chapter /story/main/chapter-0/ --parts 1,2

# 提取整章
python -m uttu extract --chapter /story/main/chapter-0/ --all

# 指定 4K 上限（默认 1080）
python -m uttu extract --chapter /story/main/chapter-0/ --parts 1 --height 2160

# 独立复检所有输出
python -m uttu validate --root output
python -m uttu validate --root output --decode   # 完整解码每个视频

# 生成 SHA256 清单
python -m uttu manifest --root output

# 对齐算法更新后，只刷新已有字幕（不重新编码视频）
python -m uttu refresh-subtitles --root output

# 离线单元测试
python -m unittest discover -s tests -v
```

---

## 每个 Part 的输出

```
output/main/chapter-0_This-Is-Tomorrow/part-01_Rock-n-Roll-Radio/
├─ video.mp4          # 精确按 Part 边界切分、重新编码
├─ en.srt             # 英文（含说话人）
├─ zh.srt             # 中文（含说话人）
├─ bilingual.srt      # 每条字幕两行：英文 / 中文
└─ part.json          # 来源、边界、对齐指标、校验结果
```

此外还有 `output/validation.json`（结构复检）、`output/validation_decode.json`（完整解码复检）、`output/MANIFEST.json`（SHA256 清单）和 `output/reports/`（每次任务报告）。

视频为 H.264 + AAC；默认 **1080p**，可在网页里选择 `720p / 1080p / 1440p（2K）/ 2160p（4K）`，或 CLI 使用 `--height 2160`。下载器会取所选高度以内的最高可用源；实际成片分辨率以源视频为准。切分为重新编码，不是关键帧粗切，保证首尾帧与源视频对应。已实测 1080p 与 2160p（3840×2160 VP9 → H.264）的下载、切分流程，1440p 走同一路径。

---

## 实现方式

### 扫描

站点的每个章节在 `/en/` 与 `/chs/` 页面内嵌 `chaptersData` JSON：

- `frontmatter`：章节标题、标签、`recording`（整章 YouTube 录像）。
- `chapters`：Part 列表，含 `textPartNumber`、标题、正文 HTML。

扫描器优先解析该 JSON；若结构变化，会回退到任意包含 `chapters` / `frontmatter` 的 JSON script，失败时给出明确错误而不是猜。

### 英中配对

正文里每个有声句子都带 `data-voice`。英文页与中文页的 Voice ID **顺序完全一致**。工具按 Voice ID 逐句配对，而不是按行号或长度猜：

- 说话人标签、强调、嵌套 `<b>` 都能正确归入同一句。
- 某一侧缺行 / ID 顺序变化时，会尝试按 ID 重排；仍不匹配则拒绝该 Part，不输出错位字幕。

### Part 边界

优先读取 YouTube 录像的 chapter 标记：

- 用 `Part N` 或 Part 标题相似度定位每个 Part 的起点。
- 以**下一个 Part 的起点**作为外层上界，不因为中间的 `Summon Tutorial` / `Story Trails` / `Daily Moon` 等标题提前截断——这些段落可能仍包含当前 Part 的后续剧情（第二章 Part 1 就是这种情况）。
- 如果有无关段，按“最后一句字幕 / 无关段起点”裁尾；如果中间没有无关段，保留到下一个 Part 起点，避免把无对白的 gameplay 尾段裁掉。
- 没有 chapter、无法可靠定位、或边界时长为负时，**直接报错并跳过**，不做按字数均分的假切分。

### 字幕时间

站点文稿本身不带时间戳。工具使用录像的英文自动字幕（`json3`，词级时间）作为时间锚点：

1. 对 Part 窗口内的英文文稿与 ASR 词流做模糊 Needleman–Wunsch 对齐。
2. 每个句子取匹配词簇的起点，并用 `voice.merui.net` 上该句语音的实际时长校正终点。
3. ASR 漏掉的句子按上下文插值，并在 `part.json` 中标记低置信度。
4. 时间轴做单调性、重叠、越界、最短/最长时长检查。
5. 没有自动字幕时，可选本地 `faster-whisper` 兜底（需自行安装）。

### 短语气词 / 省略号

ASR 常常识别不到 `Whoa ...`、`...` 这类短句。如果直接把这种句子按“自己的语音时长”插到上一句后面，会把上一句长台词截断（例如上一句 3.8 秒，却只分到 1.7 秒）。现在插值使用的是**上一句的语音时长**，长句先完整结束，短语气词再开始；回归测试覆盖了这个场景。已有输出可用 `refresh-subtitles` 只刷新字幕，不必重编码视频。

### 发布

每个 Part 先在 `output/.staging/<job>/` 中生成全部文件，随后：

- 重新解析三份 SRT，核对条数、双语行、时间单调性、视频时长；
- 校验视频含视频流 + 音频流；
- 全部通过后才把整个目录原子移动到最终位置；
- 任一检查失败，临时目录删除，原输出保持不动，错误写入任务报告。

### 本地状态与运行日志

- `/api/outputs` 扫描 `output/**/part.json`，给树节点返回 `已提取` / `本地 N` 状态；Part 的 `已提取` 徽标可直接打开视频。
- 任务日志带单调递增的 `seq`，前端按 `seq` 更新，不会因为日志条数达到上限而停止刷新；服务端返回最近 800 条，面板支持展开/收起。
- 日志覆盖章节/边界、语音时长、ASR 对齐覆盖率、编码百分比、成片大小/分辨率、SRT 校验和最终发布路径。

---

## 本次真实提取结果

已用该工具完整提取 **Main Story / Prologue「This Is Tomorrow / 此即明日」** 全 6 个 Part：

| Part | 标题 | 时长 | 字幕条数 | 对齐覆盖率 |
| ---: | --- | ---: | ---: | ---: |
| 1 | Rock 'n' Roll Radio / 摇滚电台 | 233.0s | 46 | 97.8% |
| 2 | King's Trail / 国王小径 | 520.0s | 73 | 98.2% |
| 3 | Teleport / 传送 | 382.0s | 53 | 99.1% |
| 4 | Invisible Pirate / 隐形海盗 | 336.0s | 44 | 98.2% |
| 5 | The Storm Overture / 暴雨序幕 | 405.0s | 50 | 99.4% |
| 6 | The Living Past / 活着的过去 | 456.0s | 74 | 97.9% |

合计 6 个视频、18 份字幕、340 条双语字幕。独立复检结果：

> 注：已有输出分辨率并不统一（Prologue / 第三章为 1080p，第二章主线部分历史输出为 720p）；当前新任务默认 **1080p**。对已存在但分辨率不一致的 Part，提取时会自动重新处理。

- 6/6 Part 通过结构校验；
- 340 条字幕全部可重新解析，英/中/双语三份时间一致；
- 每段视频首尾帧与源录像对应位置的平均绝对误差均 < 5.3/255；
- 完整解码 6 个视频无错误帧。

复检报告：`output/validation.json`、`output/validation_decode.json`、`output/reports/*.md`。

真实帧对照图：`output/preview/contact-sheet.png`（每个 Part 取一条字幕对应时刻的实际帧，叠加该条英中文字）。

完整验收记录：`output/VERIFICATION.md`。

第二章 Part 1 / Part 6 的边界与重叠回归修复记录：`output/VERIFICATION_CH1.md`。

字幕长句被短语气词截断的修复与全量刷新记录：`output/VERIFICATION_SUBTITLES.md`。

取消任务不生效的修复与验证：`output/VERIFICATION_CANCEL.md`。

---

## 稳健性设计

- **HTTP**：超时、指数退避重试、429 `Retry-After`、404 明确区分。
- **缓存**：章节 JSON、YouTube 元数据、ASR、语音时长、视频均缓存；视频缓存按分辨率分开（如 `<id>_1080p.mkv`），重跑不重复下载。
- **下载**：yt-dlp 断点续传、失败重试、下载后用 ffprobe 验证时长和流；坏文件删除重下。
- **切分**：NVENC 失败自动回退 libx264；输出时长与目标不符则判失败。
- **重试**：单个 Part 失败自动重试一次；仍失败则跳过，不影响其他 Part。
- **取消**：立即终止当前 yt-dlp / ffmpeg 子进程并停止后续 Part；未完成的编码只存在于 staging，已发布输出不变。下载中的 `.part` 文件保留以便下次续传。
- **错误汇总**：任务状态、每个失败阶段/Part、日志、报告 JSON + Markdown 全部保留。
- **无半成品**：失败只影响 staging 目录；成功 Part 与失败 Part 不会混在一起。
- **站点变化**：优先 JSON，多个回退路径；解析结果做 Part 数、Voice ID、正文长度、双语对应校验。

---

## 已知限制

- **必须能定位 Part 边界**：依赖 YouTube chapter 或简介时间戳。没有边界信息时工具会拒绝输出，而不是生成错误切分。
- **站点本地化并非逐词对应**：少数 PV / 独白的英中文本会跨时间窗重排（官方本地化如此）。工具保证每个时间窗的英中行来自同一 Voice ID / 同一时间窗口，但逐句语义在极少数段落可能存在官方译文的语序差异。
- **自动字幕质量**：时间锚点来自 YouTube 英文自动字幕。工具用语音时长和结构校验约束，但仍可能有少量非语音句被标为低置信度。
- **部分录像没有英文自动字幕**：工具会尝试本地 `faster-whisper` 兜底（需 `requirements-optional.txt`），CPU 上会明显变慢；未安装时该 Part 会带明确错误跳过，不会生成错误时间轴。
- 仅用于个人学习 / 资料整理；请遵守站点与 YouTube 的使用条款，不要重新分发抓取的视频。

---

## 目录

```
uttu-extractor/
├─ uttu/
│  ├─ site.py          # 目录扫描、章节 JSON 解析、双语合并
│  ├─ transcript.py    # data-voice 行提取、英中配对
│  ├─ boundaries.py    # YouTube chapter → Part 边界
│  ├─ asr.py           # 自动字幕 json3 → 词级时间
│  ├─ timing.py        # 文稿/ASR 对齐、SRT 生成、校验
│  ├─ voices.py        # 语音时长
│  ├─ media.py         # 下载、探测、精确切分
│  ├─ subtitles.py     # SRT 写入与独立解析
│  ├─ pipeline.py      # 任务编排、原子发布、报告
│  ├─ validate.py      # 输出复检
│  ├─ outputs.py       # 本地已提取 Part 索引（树节点状态）
│  ├─ refresh.py       # 只重新对齐/改写字幕，不重编码视频
│  ├─ manifest.py      # SHA256 清单
│  ├─ whisper_fallback.py  # 无自动字幕时的本地 ASR 兜底
│  ├─ server.py        # 本地 Web UI / API
│  └─ cli.py           # 命令行
├─ web/                # 树状勾选界面
├─ data/cache/         # 站点 / ASR / 视频 / 语音缓存
└─ output/             # 提取结果
```
