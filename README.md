# 网页文本转剪映草稿生成器

把粘贴保存的网页正文或 HTML 转成剪映草稿。第一版只生成草稿，不控制剪映自动导出。

## 安装

```powershell
python -m pip install -r requirements.txt
```

## 准备

1. 复制 `config.example.json` 为 `config.json`。
2. 把 `draft_folder` 改成剪映的草稿目录，通常类似 `.../JianyingPro Drafts`。
3. 把网页内容保存到 `input/content.txt` 或 `input/content.html`。
4. 可选：把素材放到 `assets/`，按 `01.png`、`02.mp4`、`03.jpg` 这类顺序命名。

如果 `assets/` 为空或不存在，脚本会生成 `generated/default_black.png` 作为黑色默认素材。

## 运行

```powershell
python generate_draft.py --config config.json --input input/content.txt --assets assets
```

生成后打开剪映，在草稿列表中找到 `draft_name` 对应的草稿，检查、替换素材或导出。

## 文本与转场

- 普通文本按空行分段，长段落会按句子拆分。
- HTML 会去掉 `script`、`style`、导航、页脚等噪音，再提取标题和段落。
- 如果片段文本里出现剪映转场名，例如 `叠化`、`右移`、`信号故障`，脚本会优先使用它。
- 没识别到转场名时，按 `fallback_transitions` 轮换。
