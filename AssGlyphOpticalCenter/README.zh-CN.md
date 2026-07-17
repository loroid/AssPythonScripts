# AssGlyphOpticalCenter

[Repository](../README.md) · [English](README.md) · **简体中文**

`AssGlyphOpticalCenter.py`

[查看脚本](AssGlyphOpticalCenter.py)

根据文字的实际字形墨迹边界，对 ASS 字幕进行水平视觉居中。本工具主要为解决中日双语字幕在部分情况下中文与日文看起来不在同一条视觉中心线上的问题而制作，同时也能修正斜体、标点、括号及特殊字体因左右留白不均造成的“数学上居中、视觉上偏移”。可选的 Ruby 跟随功能会在正文修正后同步移动对应的注音或注释事件。

> **适用范围：** 支持一次处理多个 ASS 文件和多条字幕，但每条事件内容应为单行正文。工具只调整水平方向，不改变垂直位置。

## 工作原理

普通字幕居中通常依据字体的排版推进宽度（advance width），但字形真正可见的轮廓不一定处于这个宽度的正中央。

工具使用操作系统的原生文字后端取得实际字形轮廓，并计算墨迹的左右边界：

```text
墨迹中心 = (最左边界 + 最右边界) / 2
```

随后根据脚本分辨率、ASS 对齐方式、Margin 或 `\pos` 坐标计算需要补偿的水平位移。

## 依赖与安装

**依赖**

- Python 3.10 或更高版本
- Linux：系统中需要存在 Pango 和 Cairo；使用 `--font-dir` 时还需要 Fontconfig。它们可能已由桌面环境安装，否则需使用发行版的软件包管理器安装
- macOS：需要安装 Pango，例如运行 `brew install pango`；Homebrew 会同时安装所需的 Cairo 和 Fontconfig

**安装**

无需安装 Python 软件包。将 `AssGlyphOpticalCenter.py` 放在任意目录，通过 Python 直接运行即可：

```powershell
python AssGlyphOpticalCenter.py --help
```

## 基本用法

1. 使用 `--list-styles` 查看 ASS 中的 Style 及事件数量。
2. 提供一个或多个 ASS 文件、目录，或通过 JSON 的 `inputs` 设置输入。
3. 使用 `--match-style` 或 `--match-style-exact` 配置正文 Style；至少需要一个匹配项。
4. 根据需要添加 `--exclude-style` 或 `--exclude-style-exact`。
5. 在 `--mode margin` 与 `--mode pos` 之间选择调整方式。
6. 设置判定阈值、大边距策略及可选的 Ruby Style 规则。
7. 选择默认另存、`--output-dir` 统一输出或 `--in-place` 替换原文件。
8. 运行处理；首次检查设置时可添加 `--dry-run`，只分析而不写入文件。

### 命令参数速查

命令的基本结构为：

```powershell
python AssGlyphOpticalCenter.py <输入文件或目录> <Style 规则> [其他设置]
```

| 功能 | 命令写法 |
| :--- | :--- |
| 查看 Style | `--list-styles` |
| Style 包含匹配 | `--match-style TEXT`，可重复使用 |
| Style 严格匹配 | `--match-style-exact STYLE`，可重复使用 |
| Style 包含排除 | `--exclude-style TEXT`，可重复使用 |
| Style 严格排除 | `--exclude-style-exact STYLE`，可重复使用 |
| Style 忽略大小写 | `--ignore-style-case`；恢复区分使用 `--no-ignore-style-case` |
| Margin 模式 | `--mode margin` |
| 坐标模式 | `--mode pos` |
| 判定阈值 | `--threshold 0.2` |
| 大边距阈值 | `--large-margin-threshold 100` |
| 保留大边距策略 | `--preserve-large-margins`；关闭使用 `--no-preserve-large-margins` |
| Ruby 包含匹配 | `--match-ruby-style TEXT`，可重复使用 |
| Ruby 严格匹配 | `--match-ruby-style-exact STYLE`，可重复使用 |
| Ruby 最大距离 | `--ruby-distance-threshold-percent 3` |
| 处理 Comment | `--process-comments`；关闭使用 `--no-process-comments` |
| 写入 Debug clip | `--debug-clip`；关闭使用 `--no-debug-clip` |
| 多个输入 | `file1.ass file2.ass` |
| 输入目录 | `"D:\Subtitles"`；递归扫描再加 `--recursive` |
| 指定单文件输出 | `-o output.ass` 或 `--output output.ass` |
| 指定批量输出目录 | `--output-dir "D:\Centered"` |
| 覆盖已有输出 | `--overwrite`；禁止覆盖使用 `--no-overwrite` |
| 替换原文件 | `--in-place` 或 `--replace-original`；关闭使用 `--no-in-place` |
| 只分析不写入 | `--dry-run`；关闭使用 `--no-dry-run` |
| 读取 JSON | `--settings settings.json` |
| 保存当前设置 | `--save-settings settings.json` |
| 添加字体目录 | `--font-dir "D:\Fonts"`，可重复使用 |
| 显示帮助或版本 | `--help`、`--version` |

完整示例：

```powershell
python AssGlyphOpticalCenter.py input.ass `
  --match-style-exact Chinese `
  --match-style-exact Japanese `
  --exclude-style Sign `
  --mode margin `
  --threshold 0.2 `
  --large-margin-threshold 100 `
  --preserve-large-margins `
  --match-ruby-style-exact Ruby `
  --ruby-distance-threshold-percent 3 `
  --no-process-comments
```

查看 Style：

```powershell
python AssGlyphOpticalCenter.py input.ass --list-styles
```

处理两个严格匹配的 Style：

```powershell
python AssGlyphOpticalCenter.py input.ass `
  --match-style-exact Chinese `
  --match-style-exact Japanese
```

默认输出为：

```text
input.centered.ass
```

> `--in-place` 不提供自动回退或备份。使用前请确认设置或先自行备份。

## 输入与输出

### 单文件

未指定输出位置时，结果保存在输入文件旁，并在原文件名后增加 `.centered`：

```text
D:\Subtitles\episode01.ass
D:\Subtitles\episode01.centered.ass
```

使用 `-o` 或 `--output` 可以完全指定单文件输出路径：

```powershell
python AssGlyphOpticalCenter.py input.ass -o output.ass `
  --match-style Body
```

输出已存在时默认拒绝覆盖；明确需要覆盖时使用 `--overwrite`。

### 批量处理

一次传入多个 ASS 文件：

```powershell
python AssGlyphOpticalCenter.py episode01.ass episode02.ass `
  --match-style Body
```

也可以传入目录。默认只扫描目录第一层中的 `.ass` 文件；`--recursive` 会递归扫描子目录：

```powershell
python AssGlyphOpticalCenter.py "D:\Subtitles" `
  --recursive `
  --match-style Body `
  --output-dir "D:\Centered"
```

批量处理规则：

- 未设置 `--output-dir` 时，每个结果保存在对应输入文件旁
- `--output-dir` 将全部结果写入同一目录，不保留原来的子目录结构
- 不同目录中具有相同文件名的输入会造成输出冲突；程序会在写入前停止
- `-o` / `--output` 只适用于单个输入文件
- 目录扫描会忽略已生成的 `*.centered.ass`，但仍可将这类文件作为明确的文件参数传入
- 单个文件失败时，批量任务继续处理其余文件，最后显示成功和失败数量，并返回非零退出状态

### 替换原文件

`--in-place`（别名：`--replace-original`）直接替换每个输入 ASS，不生成 `.centered.ass`：

```powershell
python AssGlyphOpticalCenter.py input.ass `
  --match-style Body `
  --in-place
```

工具会先在输入文件所在目录写入临时文件，成功后再由操作系统原子替换原 ASS。写入失败时原文件保持不变，但该模式不会自动创建备份。

`--in-place` 支持单文件和批量输入，不能与 `-o` 或 `--output-dir` 同时使用。`--dry-run --in-place` 不会修改文件。

## Style 筛选规则

正文必须至少提供一个 `--match-style` 或 `--match-style-exact`。四类规则均可重复使用：

| 设置项 | 命令行参数 | 判断方式 |
| :--- | :--- | :--- |
| `Style match` | `--match-style TEXT` | Style 名称包含任意一个匹配项 |
| `Style strict match` | `--match-style-exact STYLE` | Style 名称与任意一个严格匹配项完全相同 |
| `Style exclude` | `--exclude-style TEXT` | Style 名称包含任意一个排除项时排除 |
| `Style strict exclude` | `--exclude-style-exact STYLE` | Style 名称与任意一个严格排除项完全相同时排除 |

多个匹配项之间为“任意一个命中即可”，排除规则优先。默认区分大小写；`--ignore-style-case` 会让正文和 Ruby 的 Style 规则均忽略大小写。

```powershell
python AssGlyphOpticalCenter.py input.ass `
  --match-style CHS `
  --match-style-exact Japanese `
  --exclude-style Note `
  --exclude-style-exact "CHS Sign"
```

## `Adjustment mode`（调整模式）

### `margin`（单侧 Margin 模式）

**命令写法：** `--mode margin`

默认模式。工具根据水平对齐方式写入事件级 Margin：

| 对齐标签 | 水平对齐 | 调整项 |
| :--- | :---: | :--- |
| `\an1`、`\an4`、`\an7` | 左对齐 | MarginL |
| `\an2`、`\an5`、`\an8` | 居中 | MarginL 或 MarginR |
| `\an3`、`\an6`、`\an9` | 右对齐 | MarginR |

居中对齐时，单侧 Margin 的改变量是目标视觉位移的两倍，因为可用区域的中心只移动 Margin 改变量的一半。

> 含有 `\pos` 或 `\move` 的正文事件会被跳过，避免 Margin 与显式坐标发生冲突。

### `pos`（坐标模式）

**命令写法：** `--mode pos`

工具直接计算使墨迹中心落在画面水平中心的 `\pos(x,y)`：

- 已有 `\pos`：替换 X，保留原来的 Y
- 没有 `\pos`：自动添加坐标标签
- 含有 `\move`：跳过该事件

## `Correction threshold (%)`（判定阈值）

**命令写法：** `--threshold 0.2`

默认值为 **`0.20%`**。

```text
偏移百分比 = abs(即将发生的视觉位移) / 视频宽度 × 100
```

只有偏移百分比严格大于阈值时，工具才会应用位置修正。

> **示例：** 1920 像素宽的视频中，`0.20%` 约等于 `3.84` 像素。不超过约 3.84 像素的视觉偏移不会被修正；将阈值设为 `0` 可处理所有非零偏移。

## `Large-margin threshold`（大边距阈值）

**命令写法：** `--large-margin-threshold 100`

默认值为 **`100`**。

## `Large-margin behavior`（大边距策略）

**命令写法：** `--preserve-large-margins`；关闭使用 `--no-preserve-large-margins`

`--preserve-large-margins` 默认开启，仅在 `margin` 模式下生效。

当事件自身的 MarginL 或 MarginR 大于 `--large-margin-threshold` 时，工具将其视为有意设计的构图，不再强制把字幕移动到屏幕正中央。此时只补偿字形墨迹中心与字体排版中心之间的差值，并仅修改大边距所在的一侧。

适用于：

- 已通过 Margin 定位的正文
- 人名牌或屏幕文字
- 需要保留左右分区布局的字幕

> 大边距判断以事件级 Margin 为准，不读取 Style 中的 Margin。

## `Ruby following`（Ruby 跟随）

Ruby 跟随会让对应的 Ruby 事件按照正文实际发生的水平位移同步移动。两个 Ruby Style 规则均为空时禁用；识别为 Ruby 的事件不会同时作为正文处理。

### `Ruby Style match`

**命令写法：** `--match-ruby-style TEXT`

`--match-ruby-style TEXT` 匹配 Style 名称包含指定文字的 Ruby 事件，可重复设置。

### `Ruby Style strict match`

**命令写法：** `--match-ruby-style-exact STYLE`

`--match-ruby-style-exact STYLE` 匹配 Style 名称完全相同的 Ruby 事件，可重复设置。

### `Ruby maximum distance (% of video height)`

**命令写法：** `--ruby-distance-threshold-percent 3`

`--ruby-distance-threshold-percent` 设置 Ruby 与正文之间允许的最大空间距离，以脚本纵向分辨率 `PlayResY` 的百分比表示。默认值为 **`3%`**；在 1920×1080 的脚本分辨率下，相当于 `32.4` 个脚本像素。

工具只会在本次实际处理、开始时间和结束时间与 Ruby 完全一致、且未超过最大距离的正文事件中查找对应行。它使用 Ruby 可见区域的中心点到各正文渲染矩形的最短距离选择最近正文；无法消除的完全相同距离会被跳过。

正文修正完成后，Ruby 按照正文最终实际发生的水平位移跟随：

- Ruby 含有 `\pos(x,y)`：只把 X 改为 `x + 位移`，保留 Y
- Ruby 不含 `\pos`：根据水平对齐方式修改 MarginL 或 MarginR；居中对齐时单侧 Margin 改变量为位移的两倍
- Ruby 含有 `\move`：跳过

## `Process comment events`（处理 Comment 行）

**命令写法：** `--process-comments`；关闭使用 `--no-process-comments`

默认不处理 Comment 事件。启用 `--process-comments` 后，符合正文或 Ruby Style 规则的 Comment 事件会按照与普通 Dialogue 相同的规则参与处理。

如果 Comment 用于保存模板、备注或其他不参与渲染的工作数据，建议保持关闭。

## `Advanced`（高级选项）

**命令写法：** `--debug-clip`；关闭使用 `--no-debug-clip`

启用 `--debug-clip` 后，工具会写入矩形 `\clip(x1,0,x2,PlayResY)`，其左右边界对应计算出的字形墨迹范围。

> **注意：** `\clip` 是实际裁剪标签，不会绘制可见线框。它可能裁掉描边、阴影或模糊区域；如果事件已有 `\clip`，Debug 模式会替换第一个匹配标签。该选项只适合临时检查。

即使视觉偏移没有超过阈值，启用 Debug 后仍会因为写入 `\clip` 而修改事件。

## JSON 设置

处理设置、Style 规则以及输入、输出和批量运行设置都可以保存到 JSON。项目包含可直接修改使用的 [`settings.example.json`](settings.example.json) 模板。

运行设置示例：

```json
{
  "inputs": ["D:\\Subtitles"],
  "output": null,
  "output_dir": "D:\\Centered",
  "recursive": true,
  "overwrite": false,
  "dry_run": false,
  "list_styles": false,
  "in_place": false
}
```

| JSON 字段 | 用途 |
| :--- | :--- |
| `inputs` | 可选的输入文件或目录数组；省略或设为 `[]` 时从命令行读取 |
| `output` | 单文件自定义输出路径；不使用时为 `null` |
| `output_dir` | 批量统一输出目录；不使用时为 `null` |
| `recursive` | 是否递归扫描输入目录 |
| `overwrite` | 是否覆盖已经存在的输出文件 |
| `dry_run` | 是否只分析而不写入文件 |
| `list_styles` | 是否只列出 Style 后退出 |
| `in_place` | 是否直接原子替换输入 ASS |

`output` 只适用于单个输入，不能和 `output_dir` 同时设置；`in_place: true` 不能和两种输出路径同时使用。JSON 中的相对路径以运行命令时的当前目录为基准。

当 JSON 已填写 `inputs` 时，可以只提供设置文件：

```powershell
python AssGlyphOpticalCenter.py --settings settings.json
```

命令行中明确提供的输入会整体替换 JSON 的 `inputs`，其他命令行参数分别覆盖对应字段。`--no-recursive`、`--no-overwrite`、`--no-dry-run`、`--no-list-styles` 和 `--no-in-place` 可临时关闭 JSON 中的布尔设置。

将当前生效的处理和运行设置保存为新文件：

```powershell
python AssGlyphOpticalCenter.py input.ass `
  --match-style Body `
  --save-settings my-settings.json `
  --dry-run
```

## 支持的行内标签

测量字形时会追踪：

`\fn` · `\b` · `\i` · `\u` · `\s` · `\fs` · `\fscx` · `\fscy` · `\fsp` · `\fax` · `\fay` · `\p` · `\r`

`\fax` 和 `\fay` 会在计算边界前应用到字形轮廓，并结合 `\fscx`、`\fscy` 换算实际剪切比例。`\p1` 等绘图内容不参与文字边界计算。

## 字体测量后端

- Windows 固定使用 GDI，以 64 倍精度测量字体轮廓并换算字号，不会切换到 Pango
- Linux 和 macOS 使用 Pango + Cairo，包括 64 倍精度、libass font hack、字符间距和 Cairo 路径控制点边界
- `--font-dir DIR` 可重复设置附加字体目录；Windows 会向 GDI 私有注册字体，Linux/macOS 会通过 Fontconfig 注册给 Pango
- 原生后端缺失时直接报错；单个事件测量失败时跳过并计入 `Measurement failed`，不提供其他测量后备

相同操作系统、原生库版本和字体环境下可获得稳定结果；Windows GDI 与非 Windows Pango 本身仍可能产生跨系统差异。

## 使用限制

- 事件仅通过 Style 规则筛选，不提供交互式手动选行
- 支持一次处理多个文件和事件，但每条事件应为单行正文；不负责自动换行后的逐视觉行测量
- 只修正水平视觉中心，不调整 Y 坐标
- 含有 `\move` 的事件不会处理
- Ruby 跟随要求开始时间和结束时间完全一致；没有对应正文、超过距离阈值或无法判定的 Ruby 保持不变
- 字体回退、复杂文字塑形和混合字体取决于本机 GDI/Pango 版本与字体环境
- 旋转、描边、阴影、模糊和动画变换不计入墨迹边界
- Debug `\clip` 会影响实际渲染，不应直接保留在成品字幕中
- 输入 ASS 必须为 UTF-8

## 测试

```powershell
python -m unittest discover -s tests -v
```
