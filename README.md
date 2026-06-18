# 网页/教程文本自动剪辑工具

这个项目现在默认使用 Kdenlive 26.04.2 生成可编辑工程，并自动渲染 MP4。剪映相关能力仍保留为显式 fallback 路线，但默认流程不会启动剪映。

## 安装

```powershell
python -m pip install -r requirements.txt
```

## 准备

1. 复制 `config.example.json` 为 `config.json`。
2. 把网页正文、教程步骤或提取出的视频文案保存到 `input/content.txt`。
3. 可选：把素材放到 `assets/`，按 `01.png`、`02.mp4`、`03.jpg` 这类自然顺序命名。

如果 `assets/` 为空，脚本会生成 `generated/default_black.png` 作为黑色默认素材。不同的 `content.txt` 会产生不同的 8 位内容哈希输出名。

## 主流程：Kdenlive 工程 + MP4

```powershell
uv run python generate_draft.py --config config.json --input input/content.txt --assets assets --mode operations
```

典型输出：

```text
output/web_transition_video_7382d461.kdenlive
output/web_transition_video_7382d461.mp4
generated/kdenlive_report.json
generated/kdenlive_render.log
generated/final_decision.txt
```

`7382d461` 只是示例内容哈希，输入内容变化时会变化。第一次运行会下载约 127.3 MB 的 Kdenlive Windows standalone runtime，并缓存到 `generated/runtime/`。下载包会校验固定大小和 SHA-256 后才解压使用。

只生成可编辑工程、不渲染 MP4：

```powershell
uv run python generate_draft.py --config config.json --input input/content.txt --assets assets --mode operations --route kdenlive-project
```

## 操作模式

`--mode operations` 会把教程文案解析为操作计划，再映射到 Kdenlive/MLT 时间线。支持本地近似或直接表达的能力包括：

- 素材排布、黑底兜底、贴纸 overlay
- 默认节拍音频、音频脉冲标记点、按标记点切分
- 基础缩放、随机摆放、遮罩/动画/转场近似
- 复制片段、复制属性、复合片段的 nested sequence 等价表达
- 常见特效/滤镜的 MLT 候选映射

报告里的状态含义：

- `exact`：可以用 Kdenlive/MLT 直接表达。
- `approximated`：用本地视觉近似表达。
- `unsupported`：当前 Kdenlive runtime 缺少对应服务，未静默伪装成功。

## 视频文案提取

本地视频先提取文案：

```powershell
uv run python local_video_text.py "D:/videos/demo.mp4" --output input/content.txt
```

网络视频也可以走现有 `video_subtitle.py`：

```powershell
uv run python generate_draft.py --config config.json --video-url "https://example.com/video" --assets assets --mode operations
```

如果没有平台字幕，可在 `config.json` 中启用本地 Whisper：

```json
"whisper_mode": "local",
"whisper_model": "base",
"language": "zh"
```

## 剪映 fallback

剪映路线仍可显式使用：

```powershell
uv run python generate_draft.py --config config.json --input input/content.txt --assets assets --mode operations --route jianying
```

纯剪映 UI 任务计划：

```powershell
uv run python generate_draft.py --config config.json --input input/content.txt --assets assets --mode operations --route ui-only
```

真正尝试操作剪映 UI：

```powershell
uv run python generate_draft.py --config config.json --input input/content.txt --assets assets --mode operations --route ui-only --ui-mode experimental
```

UI 模式不会自动导出，不删除素材，不修改系统设置；如果找不到 `JianyingPro.exe / 剪映专业版` 或校准失败，会停止并写入 `generated/ui_report.json`。
## ComfyUI 场景换人/换背景

这一条路线不依赖剪映或 Kdenlive。脚本会先检测视频场景切换，把原视频切成多段；每段按照场景编号匹配人物图和背景图，调用 ComfyUI 生成处理后片段，最后再按顺序合并为一个新 MP4。

参考图放在一个文件夹里，推荐命名如下：

```text
refs/
  default_person.png
  default_background.png
  scene_001_person.png
  scene_001_background.png
  scene_002_person.jpg
  scene_002_background.jpg
```

匹配规则：

- `scene_001_person/background.*` 只用于第 1 个场景。
- 没有场景专用图时使用 `default_person/background.*`。
- 一个场景必须同时能找到人物参考图和背景参考图，否则会停止并提示缺哪个文件。

先把 ComfyUI 里真实可运行的 API workflow 导出，替换 `comfy/workflows/realistic_replace.json`。如果你的节点 ID 或字段名不同，在 `config.json` 里改 `comfy_workflow_bindings`：

```json
"comfy_workflow_bindings": {
  "video_path": {"node": "10", "field": "video"},
  "person_image": {"node": "11", "field": "image"},
  "background_image": {"node": "12", "field": "image"},
  "output_prefix": {"node": "13", "field": "filename_prefix"}
}
```

运行：

```powershell
uv run python video_replace.py --video "D:/videos/input.mp4" --refs refs --workflow comfy/workflows/realistic_replace.json --output-dir output
```

输出：

```text
output/input_replaced_<job_id>.mp4
generated/video_replace/<video>_<job_id>/manifest.json
generated/video_replace/<video>_<job_id>/report.json
```

### 只按换衣服切成独立视频

`scene_split_preview.py` 支持衣服区域检测模式。它会用 FFmpeg 抽帧，裁剪画面中部偏上的衣服区域，计算 HSV 颜色直方图差异；`outfit-change` 预设默认输出 `outfit_001.mp4`、`outfit_002.mp4` 这类独立视频。

```powershell
uv run python scene_split_preview.py "D:/videos/input.mp4" --output-dir generated/clothing_split/input --preset outfit-change --clip-prefix outfit
```

如果漏切，降低阈值：

```powershell
uv run python scene_split_preview.py "D:/videos/input.mp4" --output-dir generated/clothing_split/input --preset outfit-change --clip-prefix outfit --clothing-threshold 0.35
```

默认 `outfit-change` 会尝试使用 YOLO 人体 bbox 检测；如果本机没有安装 `ultralytics`，会自动退回到固定上身区域裁剪。安装 YOLO 依赖后可以强制启用：

```powershell
uv pip install ultralytics
uv run python scene_split_preview.py "D:/videos/input.mp4" --output-dir generated/clothing_split/input_yolo --preset outfit-change --clip-prefix outfit --person-detector yolo --yolo-model yolo11n.pt
```

如果人物检测不稳定，可以先用自动模式：

```powershell
uv run python scene_split_preview.py "D:/videos/input.mp4" --output-dir generated/clothing_split/input_auto --preset outfit-change --clip-prefix outfit --person-detector auto
```
