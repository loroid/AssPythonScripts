# ASS 字体 Family Name 规范化工具

`ass_font_family_normalize.py` 用于把 ASS/SSA 字幕里的字体名替换为字体的 family name，并在写出结果前做安全验证。

它主要处理两类位置：

- `[V4+ Styles]` / `[V4 Styles]` 中 `Style:` 行的 `Fontname` 字段
- `Dialogue:` / `Comment:` 行覆盖标签里的 `\fn...`

默认会优先使用英文 legacy family name，也就是字体 `name` table 里的 name ID 1。这个字段适用于常见的 TrueType/OpenType 字体。比如：

```text
思源黑体 Heavy -> Source Han Sans SC Heavy
```

这比 typographic family name `Source Han Sans SC` 更适合 ASS/Aegisub/Windows 字体匹配场景。

字体查找本身会忽略大小写，避免因为用户输入大小写不同而找不到字体；但判断是否需要替换时是大小写敏感的。如果字幕里写的是 `source han sans sc heavy`，而字体表中的 canonical family name 是 `Source Han Sans SC Heavy`，脚本会把它作为一次替换写回，不会跳过。

## 依赖

- Python 3
- `fontTools`
- `ffmpeg`（`sample` / `exhaustive` 模式需要）

脚本会优先使用同目录下的 `ffmpeg.exe`，找不到时再使用 PATH 中的 `ffmpeg`。

## 基本用法

处理单个字幕：

```powershell
python ass_font_family_normalize.py input.ass
```

一次处理多个字幕：

```powershell
python ass_font_family_normalize.py a.ass b.ass c.ass
```

处理整个文件夹：

```powershell
python ass_font_family_normalize.py subtitles
```

拖拽多个 `.ass` / `.ssa` 文件到脚本上也可以，Windows 会把多个路径作为多个输入传入。

## 输出位置

默认不会覆盖原字幕，而是写到字幕所在目录下的 `family output` 文件夹：

```text
input.ass
family output/input.family.ass
family output/input.font_family_report.md
```

处理文件夹时，脚本会自动跳过：

- `family output` 文件夹
- 已生成的 `*.family.ass`

避免重复处理自己的输出。

## 字体目录

脚本会扫描系统字体目录，也可以额外指定字体目录：

```powershell
python ass_font_family_normalize.py input.ass --font-dir path\to\fonts
```

可以多次传入：

```powershell
python ass_font_family_normalize.py input.ass --font-dir fonts-a --font-dir fonts-b
```

如果字幕使用了未安装字体，建议用 `--font-dir` 指向包含字体文件的目录。

## 验证模式

默认验证模式是 `sample`：

```powershell
python ass_font_family_normalize.py input.ass --verify-mode sample
```

它会先确认替换前后字体名来自同一个字体文件和同一个 face index，然后进行少量像素渲染抽检。抽检按每个修改过的字体对分组，确保每个实际出现在 Dialogue 画面中的 changed font pair 都有渲染校验。

### sample

默认模式。每个修改过的字体对最多抽样 24 个受影响帧，并在黑底、白底上分别渲染比较像素哈希。

```powershell
python ass_font_family_normalize.py input.ass --sample-frames 8
```

`--sample-frames` 是“每个 changed font pair”的上限，不是全文件总上限。

### font-face

最快，只验证替换前后字体名是否为同一个已安装字体文件、同一个 face index，不做像素渲染：

```powershell
python ass_font_family_normalize.py input.ass --verify-mode font-face
```

适合大量批处理或已经信任当前字体环境时使用。

### exhaustive

最严格，也最慢。会渲染所有受影响的实际视频帧：

```powershell
python ass_font_family_normalize.py input.ass --verify-mode exhaustive --fps 24000/1001
```

完整剧集字幕可能会非常慢，尤其是逐帧特效字幕。

## FPS

只有 `sample` 和 `exhaustive` 像素渲染校验需要 FPS。

如果不指定，脚本会尝试读取字幕里的 FPS；读不到时默认使用：

```text
24000/1001
```

也可以手动指定：

```powershell
python ass_font_family_normalize.py input.ass --fps 25
python ass_font_family_normalize.py input.ass --fps 24000/1001
```

## 报告内容

每个字幕都会生成一个 Markdown 报告，包含：

- 输入和输出路径
- 替换数量
- 未解析或歧义字体数量
- 验证方式和结果
- 像素校验帧数
- 每一处替换的行号、位置、替换前字体名、替换后字体名

报告表格示例：

```text
Line | Section | Type | Old Font | New Font | Status | Note
```

其中 `same font face verified` 表示替换前后已确认是同一个字体文件和 face index。

## 常用命令

默认安全处理：

```powershell
python ass_font_family_normalize.py input.ass
```

跳过 `Comment:` 行：

```powershell
python ass_font_family_normalize.py input.ass --skip-comments
```

处理多个字幕：

```powershell
python ass_font_family_normalize.py *.ass
```

处理文件夹并指定字体目录：

```powershell
python ass_font_family_normalize.py subtitles --font-dir path\to\fonts
```

快速模式，不渲染：

```powershell
python ass_font_family_normalize.py subtitles --verify-mode font-face
```

更少抽样帧：

```powershell
python ass_font_family_normalize.py subtitles --sample-frames 4
```

严格逐帧校验：

```powershell
python ass_font_family_normalize.py input.ass --verify-mode exhaustive --fps 24000/1001
```

覆盖原文件：

```powershell
python ass_font_family_normalize.py input.ass --in-place
```

显式指定输出路径，仅适用于单文件：

```powershell
python ass_font_family_normalize.py input.ass -o output.ass --report report.md
```

## 注意事项

- 多文件或文件夹输入时不能使用 `--output` / `--report`，因为每个输入都需要独立输出路径。
- 默认会处理 `Comment:` 行里的 `\fn`，但注释行不会参与像素渲染校验。若要跳过注释行，添加 `--skip-comments`。
- 如果某个 changed font pair 只出现在注释或没有活跃 Dialogue 帧的 Style 中，报告会说明没有可渲染帧；此时仍会保留 font-face 验证结果。
- `sample` 模式能大幅降低渲染数量，但不是逐帧完全证明；需要最终逐像素保证时使用 `--verify-mode exhaustive`。
