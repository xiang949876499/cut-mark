# Kdenlive 自动剪辑后端设计

## 目标

将项目的默认剪辑后端从剪映草稿和剪映 UI 自动化迁移到 Kdenlive/MLT。工具继续接收网页文本、HTML、视频文案或剪辑教程文本，生成可继续编辑的 `.kdenlive` 项目，并自动渲染 H.264/AAC MP4。

保留现有剪映路线作为显式备用选项，但默认命令不再依赖剪映、`pyJianYingDraft` 或桌面 UI 点击。

## 已确认决策

- 默认输出 Kdenlive 项目和 MP4。
- Kdenlive 未安装时，自动下载官方 Windows Standalone 版本。
- 固定使用 Kdenlive 26.04.2，不自动升级。
- Kdenlive 成为默认后端，剪映相关代码暂时保留。
- 自动下载内容放在项目缓存目录，不修改系统级安装。
- 渲染失败时保留项目、素材和诊断报告，不删除可用产物。

## 总体架构

现有输入和解析层保持不变：

1. 从 `.txt`、`.html`、视频链接或本地视频取得文本。
2. 普通文本继续拆分为卡片。
3. 教程文本继续解析为 `operation_plan.json`。
4. `OperationExecutor` 继续负责音频解析、标记点、占位素材、贴纸和确定性随机位置。
5. 新的 Kdenlive 后端把卡片、素材和操作计划转换为 MLT/Kdenlive 项目。
6. 便携版中的 `melt.exe` 渲染项目并输出 MP4。

新增模块：

### `KdenliveRuntime`

职责：

- 优先查找配置指定的 Kdenlive Runtime。
- 其次查找项目缓存中的固定便携版。
- 缺失时从 KDE 官方地址下载 Kdenlive 26.04.2 Windows Standalone。
- 校验下载文件 SHA-256。
- 解压到 `generated/runtime/kdenlive-26.04.2/`。
- 定位 `kdenlive.exe`、`melt.exe` 和运行所需资源目录。
- 检查 MLT 服务和 H.264/AAC 编码能力。

下载失败、校验失败或运行时不完整时立即停止，并生成明确诊断。不得静默使用其他版本。

### `KdenliveProjectBuilder`

职责：

- 使用 `xml.etree.ElementTree` 等结构化 XML API 创建项目。
- 生成与固定 Kdenlive 版本兼容的 MLT XML。
- 写入项目规格、素材引用、轨道、播放列表、片段、转场、效果、关键帧、标记点和序列信息。
- 使用绝对素材路径，并在报告中记录所有引用。
- 在保存后重新解析 XML，检查引用、ID、时间范围和必要节点。

项目文件名使用输入内容哈希：

```text
output/<project_name>_<content_hash>.kdenlive
```

不同 `content.txt` 会产生不同项目名；相同内容可按配置覆盖或复用。

### `KdenliveRenderer`

职责：

- 调用固定 Runtime 中的 `melt.exe`。
- 使用 `avformat` consumer 输出 H.264/AAC MP4。
- 将命令、环境、标准输出、标准错误、退出码和耗时写入报告。
- 渲染完成后使用 `ffprobe` 验证文件。

输出文件：

```text
output/<project_name>_<content_hash>.mp4
```

## CLI 和配置

保留入口：

```powershell
uv run python generate_draft.py --config config.json --input input/content.txt --assets assets --mode operations
```

`--route` 调整为：

- `kdenlive`：默认。生成项目并渲染 MP4。
- `kdenlive-project`：只生成项目，不渲染。
- `direct`：保留现有 FFmpeg 近似渲染。
- `jianying`：保留剪映草稿备用路线。
- `ui-only`：保留实验性的剪映 UI 路线。

配置新增：

```json
{
  "backend": "kdenlive",
  "kdenlive_version": "26.04.2",
  "kdenlive_runtime_dir": "generated/runtime",
  "auto_download_kdenlive": true,
  "render_video_codec": "libx264",
  "render_audio_codec": "aac",
  "render_crf": 20,
  "render_preset": "medium",
  "render_fps": 30
}
```

下载 URL 和 SHA-256 由程序内置为受支持版本清单，不要求用户填写。配置可以覆盖 Runtime 目录，但不能绕过哈希校验。

## 操作映射

### 音频与卡点

- `add_audio`：添加音频生产者和音频播放列表。
- `ai_beat`：使用本地节拍检测结果生成时间线标记。
- 如果缺少音频，继续使用自动生成的节拍 WAV。
- 标记时间统一转换为项目帧号，所有剪切使用同一时间基准。

第一版的“AI 卡点”是本地节拍分析的等价实现，不调用 Kdenlive UI。

### 分割和时间线

- `split_at_marker`：按指定标记帧拆分源素材，生成多个 playlist entry。
- 普通文章卡片按标记间隔或 `segment_duration_sec` 排列。
- 素材不足时循环使用；没有素材时使用黑色默认素材。
- 图片片段持续时间由项目时间线决定，视频片段需要限制源入点和出点。

### 贴纸与变换

- `add_sticker`：PNG/SVG 作为独立透明视频轨道。
- 本地贴纸优先；缺失时继续使用生成的占位贴纸。
- `set_scale`：映射到 MLT Transform/affine 参数。
- `random_place`：使用当前 seed 生成 X/Y/缩放参数，结果可复现。
- 位置和缩放需要兼容竖屏 `1080x1920`。

### 选择器

`selected`、`all_video_segments`、`all_stickers`、`named:<name>` 不生成鼠标选择动作，而是在构建项目时解析为目标片段集合。后续效果、复制和变换直接应用到匹配对象。

### 复制

- `copy` 表示复制片段时，克隆 playlist entry、源范围和效果栈，并偏移到目标时间。
- “复制属性”只复制效果、参数和关键帧，不复制媒体引用和时间位置。
- 无法从文本判断复制类型时，根据原句中的“属性”关键词选择复制属性，否则复制片段。

### 复合片段

- `compound_clip` 使用 Kdenlive Sequence/Nested Timeline。
- 将当前 selector 选中的片段移入子序列。
- 主序列中插入对子序列的引用。
- 子序列持续时间、轨道层级和透明背景必须保持原视觉结果。

### 转场

转场名称映射到 MLT 服务：

- 叠化：`luma`/dissolve。
- 淡入淡出：透明度或 mix。
- 推拉、滑动：带关键帧的 composite/transform。
- 模糊转场：模糊效果和透明度关键帧组合。

无法精确匹配的转场使用最接近的视觉实现，并在报告中标记 `approximated`。

### 蒙版与动画

- 圆形蒙版优先映射到 Alpha Shape、Shape Alpha 或可用的 MLT mask 服务。
- 蒙版放大、缩小和移动使用关键帧。
- 入场、出场和循环动画转换为 Transform、Opacity、Rotation 等效果的关键帧。
- 固定版本 Runtime 缺少对应服务时，使用预渲染透明素材作为回退。

### 特效和滤镜

建立独立的 Kdenlive/MLT 效果别名表，不再使用剪映资源 ID：

- `运动模糊`：映射到可用的 motion blur 或 FFmpeg/MLT 近似组合。
- `故障2`：映射到 glitch、RGB shift、noise 等组合。
- `流动烟雾`：使用本地烟雾素材叠加，或使用噪声/位移近似。

每个效果结果标记为：

- `exact`：使用明确对应的 MLT 服务。
- `approximated`：使用视觉近似组合。
- `unsupported`：固定 Runtime 中不存在且无法安全近似。

`unsupported` 不得静默忽略，必须写入报告。

### 文字与字幕

- `cards` 模式生成标题或字幕轨。
- `operations` 模式默认不把教程说明文字放入视频。
- 视频转写结果可生成字幕轨，字幕时间优先使用转写时间戳；没有时间戳时使用卡片时间范围。

## Runtime 下载与安全

- 只允许从 KDE 官方域名下载。
- 固定版本清单包含版本、URL、文件名、SHA-256 和解压后的可执行文件相对路径。
- 下载写入临时文件，校验成功后再原子重命名。
- 解压前检查归档路径，拒绝绝对路径和目录穿越。
- 不请求管理员权限，不写入 `Program Files`，不修改系统 PATH。
- Runtime 缓存可复用，不在每次运行时检查最新版本。

## 报告

新增：

```text
generated/kdenlive_report.json
generated/kdenlive_render.log
generated/final_decision.txt
```

报告包含：

- 输入内容哈希。
- Kdenlive/MLT Runtime 版本和路径。
- 项目与 MP4 输出路径。
- 素材、音频、贴纸和标记点。
- 每条操作的映射结果和状态。
- 使用的 MLT 服务、参数与关键帧。
- 下载、项目验证、渲染和 `ffprobe` 验证结果。
- 所有近似项和不支持项。

当存在 `unsupported` 操作时仍生成项目，但默认不声称全部完成。是否继续渲染由配置 `render_with_unsupported_operations` 控制，默认允许渲染并明确报告缺失。

## 错误处理

- 输入为空：终止，不创建空项目。
- 素材损坏：跳过损坏素材，若无可用素材则使用黑底。
- Runtime 下载失败：停止，保留下载日志。
- Runtime 校验失败：删除临时归档，不执行其中任何文件。
- 项目 XML 验证失败：不调用 `melt.exe`。
- 渲染失败：保留 `.kdenlive` 项目、日志和所有生成素材。
- `ffprobe` 验证失败：MP4 标记为无效，不覆盖已有有效输出。

## 测试策略

### 单元测试

- 固定 Runtime 清单和路径解析。
- 下载哈希校验及目录穿越防护。
- 内容哈希产生唯一项目名。
- 秒数到帧号的稳定转换。
- 标记点分割生成正确 entry 数量和范围。
- 图片、视频、音频和贴纸轨道生成。
- selector 匹配和批量应用。
- Transform、关键帧、复制属性和复制片段。
- 嵌套 Sequence 结构。
- 效果别名映射及 exact/approximated/unsupported 状态。
- XML 转义和重新解析。

### 集成测试

- 使用小型测试素材生成 `.kdenlive`。
- 使用真实固定 Runtime 的 `melt.exe` 渲染短 MP4。
- 使用 `ffprobe` 验证：
  - 分辨率为配置值。
  - 帧率符合配置。
  - 时长与时间线一致。
  - 存在视频流。
  - 有背景音频时存在音频流。
- 用 Kdenlive 26.04.2 打开项目，确认没有损坏提示。

网络下载测试默认使用模拟响应；真实 Runtime 下载和渲染测试作为显式集成测试，避免普通单元测试重复下载大型文件。

## 迁移顺序

1. 增加 Runtime 管理和安全下载。
2. 增加最小 MLT 项目构建器，支持黑底、图片/视频和音频。
3. 支持标记点分割、贴纸与 Transform。
4. 支持复制、效果栈和常见转场。
5. 支持嵌套 Sequence。
6. 接入 `melt.exe` 渲染和 `ffprobe` 验证。
7. 将 `kdenlive` 设为默认路线。
8. 更新配置、README 和测试。
9. 保留并隔离剪映备用路线。

## 非目标

第一版不实现：

- 自动操作 Kdenlive UI。
- 自动升级 Kdenlive。
- 安装系统级 Kdenlive。
- 与剪映内置资源逐像素一致的专有特效。
- 删除现有剪映兼容代码。

## 验收标准

以下命令在没有系统安装 Kdenlive 的 Windows 环境中可以：

```powershell
uv run python generate_draft.py --config config.json --input input/content.txt --assets assets --mode operations
```

并完成：

1. 下载或复用固定 Kdenlive 26.04.2 Standalone Runtime。
2. 生成唯一命名且可由 Kdenlive 打开的 `.kdenlive` 项目。
3. 将教程中可映射的操作真实写入时间线和效果结构。
4. 自动输出 H.264/AAC MP4。
5. 生成包含精确、近似和不支持操作的完整报告。
6. 不调用剪映、不激活剪映窗口、不依赖剪映草稿 API。
