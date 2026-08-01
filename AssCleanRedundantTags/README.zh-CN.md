# AssCleanRedundantTags

[Repository](../README.md) · [English](README.md) · **简体中文**

`ass_clean_redundant_tags.py`

[查看脚本](ass_clean_redundant_tags.py)

清理 ASS/SSA 中不改变有效状态的 override 标签、显示前已被覆盖的写入和无意义数值格式。工具还可以执行兼容安全重排、合并连续相同的静态 Dialogue、清理 Aegisub 文件级元数据、生成审计报告，并分别使用 libass 与 xy‑VSFilter 验证清理前后的真实渲染结果。

脚本的图形界面、命令行帮助、进度信息、错误信息和生成报告使用英语；本文提供简体中文使用说明。

> **适用范围：** 支持单个文件、多个文件和目录批量处理。工具以保持 libass 与 xy‑VSFilter 各自的标签生效状态为安全目标，不要求两个渲染器彼此产生相同画面；无法证明安全的语法优先保留。

## 工作原理

工具从事件使用的 Style 初始状态开始，按照 override block 和显示文本的先后顺序维护字体、缩放、颜色、透明度、描边、阴影、定位、绘图、karaoke 与 transform 等有效状态。只有标签对后续状态没有影响，或在任何显示边界前已被同字段写入完全覆盖时，才会删除该标签。

可选的真实渲染差分始终在同一渲染器内部比较：libass 清理前后互相比较，xy‑VSFilter 清理前后互相比较。渲染器之间原本存在的视觉差异不会被当成清理失败。

## 依赖与安装

**依赖**

- Python 3.10 或更高版本
- libass 对比需要 FFmpeg
- 内置的 xy‑VSFilter 对比仅支持 Windows，需要 FFmpeg、AviSynth+ 和可加载的 xy‑VSFilter/VSFilter DLL

FFmpeg 和 xy‑VSFilter DLL 按以下顺序查找：

1. 命令行或图形界面中明确指定的位置
2. 主程序同目录及已知的相邻工具目录
3. 系统 `PATH`

AviSynth+ 直接使用系统已安装的运行时，不参与上述路径查找。

**安装**

将 `ass_clean_redundant_tags.py` 放在任意目录，通过 Python 直接运行即可：

```powershell
python ass_clean_redundant_tags.py
```

## 基本用法

1. 直接运行脚本打开图形界面；提供参数时默认执行清理，也可以显式写出 `clean`。
2. 添加一个或多个 ASS/SSA 文件、目录，并按需启用递归扫描。
3. 选择另存、输出目录或原位替换。
4. 按需启用安全重排、连续行合并、Comment 行删除、未知标签或 Aegisub 元数据清理。
5. 需要审计时选择 Markdown 或 HTML 报告。
6. 需要真实验证时启用 libass、xy‑VSFilter 或两者，并设置渲染并发数。

### 命令参数速查

清理命令的基本结构为：

```powershell
python ass_clean_redundant_tags.py [clean] <输入文件或目录> [其他设置]
```

`clean` 可以省略；未写 `clean` 或 `compare` 时按清理命令解析。独立比较必须显式使用 `compare`。

| 功能 | 命令写法 |
| :--- | :--- |
| 多个输入 | `file1.ass file2.ass` |
| 输入目录 | `"D:\Subtitles"`；递归扫描再加 `--recursive` |
| 指定单文件输出 | `-o output.ass` 或 `--output output.ass` |
| 指定批量输出目录 | `--output-dir "D:\Cleaned"` |
| 替换原文件 | `--in-place`；不创建备份再加 `--no-backup` |
| 兼容安全重排 | `--safe-reorder` |
| 合并连续相同行 | `--merge-lines` |
| 删除 Comment 事件行 | `--clean-comments`；默认关闭 |
| 删除两端未知标签 | `--clean-unknown-tags` |
| 删除 extradata 引用 | `--clean-extradata-references` |
| 删除 Project Garbage | `--clean-project-garbage` |
| 删除 Extradata 段 | `--clean-extradata` |
| 指定单文件报告 | `--report "Clean Report.html"` |
| 批量输出报告 | `--write-reports --report-dir "D:\Reports"` |
| 报告格式 | `--report-format md` 或 `--report-format html` |
| 清理后使用 libass 对比 | `--compare-libass` |
| 清理后使用 xy‑VSFilter 对比 | `--compare-vsfilter` |
| 并发渲染数 | `--render-workers 8` |
| 读取 JSON 配置 | `--settings settings.json` |
| 保存当前有效配置 | `--save-settings settings.json` |

独立比较命令的基本结构为：

```powershell
python ass_clean_redundant_tags.py compare `
  --original-ass before.ass `
  --cleaned-ass after.ass `
  [其他设置]
```

| 功能 | 命令写法 |
| :--- | :--- |
| 比较现有文件 | `--original-ass before.ass --cleaned-ass after.ass` |
| 比较外部用例语料 | `--corpus cases.json` |
| 修改行时段逐帧比较 | `--full-frames` |
| 设置帧率 | `--fps 24000/1001` |
| 设置背景 | `--backgrounds black,white`；还可加入 `gray` |
| 指定 FFmpeg | `--ffmpeg "D:\Tools\ffmpeg.exe"` |
| 指定 xy‑VSFilter | `--xy-vsfilter-dll "D:\Filters\VSFilter.dll"` |
| 设置容差 | `--channel-tolerance N --pixel-tolerance N` |
| 保留差分产物 | `--artifacts "D:\Diff Artifacts"` |
| 指定报告 | `--report "Diff Report.html"` |
| 并发渲染数 | `--render-workers 8` |

完整清理示例：

```powershell
python ass_clean_redundant_tags.py input.ass `
  --safe-reorder `
  --merge-lines `
  --compare-libass `
  --compare-vsfilter `
  --render-workers 8 `
  --report "input.Clean Report.html"
```

## 图形界面

直接运行：

```powershell
python ass_clean_redundant_tags.py
```

图形界面提供以下独立开关：

- 添加多个字幕文件和字幕文件夹
- 是否扫描所选文件夹的子文件夹
- 输出文件夹，可填写绝对路径或相对路径
- 兼容安全重排标签
- 合并连续相同的静态 Dialogue
- 删除 `[Events]` 中的 Comment 事件行
- 删除 libass 与 xy‑VSFilter 均不识别的 override 标签
- 删除事件开头的 `{=数字}` extradata 引用
- 删除 `[Aegisub Project Garbage]`
- 删除 `[Aegisub Extradata]`
- libass 对比
- xy‑VSFilter 对比
- 并发渲染进程上限
- 报告及 Markdown/HTML 格式选择
- 保存为可移植的 JSON 配置
- 原位覆盖

输入列表可以同时包含文件和文件夹，并会按规范化绝对路径去重。文件夹默认只扫描顶层；启用 **Scan subdirectories** 后递归查找 `.ass` 和 `.ssa`。

输出文件夹留空时，程序在每个源字幕旁写入 `字幕名.Cleaned.ass`，不覆盖原字幕。填写绝对路径时，所有输入使用该目录作为共用输出根目录；填写相对路径时，单独添加的字幕以字幕所在目录为基准，所选文件夹中的字幕以该文件夹为基准。扫描文件夹得到的字幕会继续保留其相对子目录结构。选择原位覆盖时会为每个文件先创建 `字幕扩展名.bak` 备份。

图形界面设置保存在主程序同目录的 `ass_clean_redundant_tags.config.json`。

## 输入与输出

### 单文件

基本用法：

```powershell
python ass_clean_redundant_tags.py clean "input.ass"
```

### 多文件与目录

同时处理多个字幕：

```powershell
python ass_clean_redundant_tags.py clean "one.ass" "two.ssa"
```

处理文件夹顶层的字幕：

```powershell
python ass_clean_redundant_tags.py clean "Subtitle Folder"
```

递归扫描文件夹，并分别输出字幕和 HTML 报告：

```powershell
python ass_clean_redundant_tags.py clean "Subtitle Folder" `
  --recursive `
  --output-dir "Cleaned" `
  --write-reports `
  --report-dir "Reports" `
  --report-format html
```

文件夹扫描会跳过名称以 `.Cleaned` 结尾的 `.ass/.ssa`，避免重复产生 `name.Cleaned.Cleaned.ass`。直接把这类文件作为输入参数或在图形界面中明确添加时仍会处理。

多个输入文件映射到同一输出或报告路径时，程序会在写入前停止并报告冲突，不会覆盖其中一个文件。

### 输出位置

指定相对输出文件夹：

```powershell
python ass_clean_redundant_tags.py clean "input.ass" --output-dir "Cleaned"
```

若输入为 `D:\Subs\input.ass`，上述命令输出到：

```text
D:\Subs\Cleaned\input.Cleaned.ass
```

绝对输出文件夹也使用同一参数：

```powershell
python ass_clean_redundant_tags.py clean "input.ass" `
  --output-dir "E:\Cleaned Subtitles"
```

相对输出文件夹不会依赖启动 Python 时的工作目录。对于文件夹输入，它相对于所选文件夹；对于单独添加的文件，它相对于该文件所在目录。命令行仍保留 `--output "output.ass"`，供单文件任务精确指定输出文件名；多文件或文件夹输入统一使用 `--output-dir`。`--report` 和 `--report-dir` 的用途不变。

### 清理选项示例

安全重排、连续行合并、两端未知 override 标签清理、HTML 报告、三类 Aegisub 元数据清理和 libass 对比均默认启用；xy‑VSFilter 对比默认关闭：

```powershell
python ass_clean_redundant_tags.py clean "input.ass"
```

默认启用项均可用对应的 `--no-*` 参数关闭；需要 xy‑VSFilter 对比时添加 `--compare-vsfilter`。

删除两套目标渲染器均不识别的标签：

```powershell
python ass_clean_redundant_tags.py clean "input.ass" `
  --clean-unknown-tags
```

清理三类 Aegisub 元数据：

```powershell
python ass_clean_redundant_tags.py clean "input.ass" `
  --clean-extradata-references `
  --clean-project-garbage `
  --clean-extradata
```

### 替换原文件

原位覆盖：

```powershell
python ass_clean_redundant_tags.py clean "input.ass" --in-place
```

原位覆盖默认创建 `input.ass.bak`；已有同名备份时依次使用 `input.ass.bak.1`、`input.ass.bak.2`，不会覆盖旧备份。只有明确接受无备份覆盖时才使用 `--no-backup`。

## 标签清理规则

程序按 Style 初始状态和文本推进顺序维护有效状态，不做单纯的字符串去重。

例如，Style 的 `ScaleX` 为 `100` 时：

```ass
{\fscx100}例句
```

可简化为：

```ass
例句
```

但：

```ass
例{\fscx60}句{\fscx100}例句
```

第二个 `\fscx100` 恢复了后续文字的缩放，不能删除。

同一显示边界前的死写入会被删除：

```ass
{\fscx60\fscx100}例句
```

可简化为：

```ass
例句
```

### 复杂状态案例

以下案例假定 `Default` Style 的 `ScaleX=100`、主色为白色且主透明度为 `&H80&`、`Outline=2`、`Shadow=0`；`Alt` Style 的 `ScaleX=80`、`ScaleY=100`、粗体开启、主色为 `&HFF0000&`、`Outline=3`、`Shadow=1`。

同一显示边界前同时存在死写入、Style 等值标签、first-wins 几何标签和小数格式时：

```ass
{\fscx60\fscx100\1c&HFFFFFF&\1a&H80&\bord2\shad0\pos(100.00,100.00)\move(0.00,1.0,10.00,20.00,0.00,500.00)}A
```

清理为：

```ass
{\pos(100,100)}A
```

这里 `\fscx60` 在显示文字前被 `\fscx100` 覆盖，其余样式标签等于 `Default` Style；`\pos` 与 `\move` 同属 first-wins 定位族，因此保留先出现的 `\pos`，再规范化其数值。

Style reset 与 transform 同时出现时，程序先切换活动 Style，再按 transform 实际读写的字段建立依赖：

```ass
{\rAlt\fscx80\fscy100\b1\c&HFF0000&\t(0,500,\fscx160)\shad1}A
```

清理为：

```ass
{\rAlt\t(0,500,\fscx160)}A
```

`\fscx80`、`\fscy100`、`\b1`、`\c&HFF0000&` 和 `\shad1` 均已由 `Alt` Style 提供。删除 `\fscx80` 后，缩放动画仍从活动 Style 的 `ScaleX=80` 开始，因此它也是冗余的。

跨越多个显示边界、重复写入并两次切换 Style 时：

```ass
{\blur200\blur200}A{\blur200\fscx60\fscx60}B{\rAlt\fscx80\fscy100\b1\bord3\shad1\c&HFF0000&}C{\rDefault\fscx100\1c&HFFFFFF&\1a&H80&}D
```

清理为：

```ass
{\blur200}A{\fscx60}B{\rAlt}C{\rDefault}D
```

首个 `\blur200` 和 `\fscx60` 分别影响已经显示的 `A`、`B`，不能因后续出现相同值而删除；两个 reset 也必须保留，因为它们分别决定 `C`、`D` 的 Style。只有当前状态下确实不再产生作用的重复写入和 Style 等值标签会被移除。

普通 brace comment、畸形参数、无法解析的块和渲染器特有 HTML 状态会形成保守边界。

### 两端未知标签

两端未知 override 标签清理默认启用。程序会删除能够确认位于 override block 中、且 libass 与 xy‑VSFilter 均不识别的标签；需要保守保留时使用 `--no-clean-unknown-tags`：

```ass
{\zunknown1\fs60}Text  →  {\fs60}Text
```

简单 `\t(...)` modifier 中的未知标签也会删除；如果 transform 删除后不再包含任何有效 modifier，空 transform 会一并删除。

以下内容不会因此删除：

- libass 或 xy‑VSFilter 至少一端支持的标签
- 已知标签名称后带有畸形参数的写法
- 能够确认为普通 brace comment 的块中、看起来像反斜杠命令的文本
- 无法安全拆分的复杂 transform 内容

ASS 标签名没有独立终止符，因此只要原始 token 以某个已知短标签开头，就按“已知标签加畸形参数”保留。例如 `\unknown` 以有效的 `\u` 开头，不会仅凭后续字母被当成可安全删除的未知标签。这一规则会少删，但能避免把两套渲染器可能部分解析的 token 误删。

“两端未知”只针对本程序维护的 libass 与 xy‑VSFilter 兼容性集合。其他渲染器、Aegisub 自动化脚本或未来版本仍可能使用这些标签；需要保留此类扩展标签时应关闭该选项。报告会分别列出实际删除的未知标签和仍被保守保留的两端未知标签。

## 数值与绘图规范化

程序删除不影响数值的前导零、小数点和尾随零：

```ass
\pos(100.00,100.00)  →  \pos(100,100)
\pbo0.00             →  \pbo0
```

规范化范围包括：

- 普通数值标签
- `\pos`、`\move`、`\org`、`\fad`、`\fade`
- 矩形与 vector `\clip` / `\iclip`
- `\t` 的时间、加速度和简单 modifier
- `\p` 绘图路径坐标
- vector clip 中的绘图路径

粘连绘图命令会转换为标准空格分隔形式：

```ass
m100.00 100.00l200.00 200.00
```

变为：

```ass
m 100 100 l 200 200
```

遇到未知 drawing token、无法确定的 `\p` 参数或复杂嵌套表达式时，相关片段保持原样。

## transform 安全边界

程序按字段分析简单 `\t`：

- identity transform 可以删除
- 完全落在事件结束时间之后的有效 transform 可以删除
- transform 修改的字段会保护真正改变动画起点的静态状态；transform 之前与当前有效状态相同的写入仍可删除
- 与 transform 字段无关的静态冗余标签仍可独立删除
- 嵌套 transform、复杂括号内容和相对字号动画不做字段级语义删除；其中能够独立识别的数值仍会规范化，未知 modifier 仅在启用 `--clean-unknown-tags` 且能够安全拆分时删除

假设活动 Style 的 `ScaleX=100`、`ScaleY=100`。如果 transform 的目标值仍为当前的 100，动画前后及插值期间的有效值都不发生变化，整个 identity transform 可以删除：

```ass
{\t(0,500,\fscx100)}Text
```

清理为：

```ass
Text
```

下面的 `\fscy100` 与 transform 无关，也可以删除：

```ass
{\fscy100\t(\fscx200)}Text
```

下面的 `\fscx100` 虽然与 transform 修改同一字段，但它等于 Style 提供的当前值，删除后动画起点仍为 100，因此也可以删除：

```ass
{\fscx100\t(0,500,\fscx200)}Text
```

清理为：

```ass
{\t(0,500,\fscx200)}Text
```

如果静态值改为 `\fscx80`，它会把动画起点从 Style 的 100 改为 80，此时必须保留：

```ass
{\fscx80\t(0,500,\fscx200)}Text
```

### 复杂 transform 案例

多个 transform 分别修改不同字段时，程序会合并它们的字段依赖，而不是把整行一律视为不可清理。假设当前 Style 的 `ScaleX=100`、`ScaleY=100`、`Outline=2`、`Shadow=0`、主色为白色且主透明度为 `&H80&`：

```ass
{\fscx100\fscy100\bord2\shad0\c&HFFFFFF&\t(0.00,500.00,\fscx200.00\bord4.00)\t(500.00,1000.00,\fscy150.00)\1a&H80&}A
```

清理为：

```ass
{\t(0,500,\fscx200\bord4)\t(500,1000,\fscy150)}A
```

`\fscx100`、`\fscy100` 和 `\bord2` 虽对应动画字段，但都等于活动 Style 已提供的起点，删除后 transform 的插值起点不变。阴影、颜色和透明度同样等于 Style 且与动画无关，因此也会删除；transform 内的数值则独立规范化。

可解析 transform 与复杂 transform 并存时，程序只处理能够独立证明安全的部分。假设事件时长为 `1000 ms`，并沿用上文的 `Alt` Style：

```ass
{\rAlt\fs40\fscx80\fscy100\bord3\t(1000.00,2000.00,\fscx160.00)\t(0.00,500.00,\fs+10\fscy150.00)\shad1}A
```

清理为：

```ass
{\rAlt\t(0,500,\fs+10\fscy150)\shad1}A
```

第一个 transform 从事件结束时才开始，不会作用于任何生效帧，因此可以删除。第二个 transform 使用依赖当前字号的相对字号动画 `\fs+10`；它之前的 `\fs40`、`\fscx80`、`\fscy100` 和 `\bord3` 都等于 `Alt` Style，可在不改变动画起点的前提下删除。复杂 transform 之后的 `\shad1` 仍位于保守边界内，因此保留。

## 兼容安全重排

重排默认启用。程序使用稳定的部分顺序：

```text
\an/\a
\r
\q
\p/\pbo
\fn, \fs, \fsp, \b, \i, \u, \s
\c/\1c, \2c, \3c, \4c
\alpha, \1a, \2a, \3a, \4a, \blur, \be, \fe
\k/\K/\kf/\ko/\kt
\fax, \fay, \fsc, \fscx, \fscy, \frz/\fr, \frx, \fry
\bord, \xbord, \ybord, \shad, \xshad, \yshad
\org, \pos/\move, \fad/\fade
\t
\clip/\iclip
```

这不是无条件排序。只有满足以下条件的相邻标签才能交换：

- 两个标签在 libass 与 xy‑VSFilter 下都能被完整解析
- 完整读写字段集合不相交
- first-wins、reset、drawing、karaoke 和 transform 依赖不被破坏
- 不跨越仍保留的未知标签、普通 comment、畸形参数或复杂表达式
- `\clip` / `\iclip` 不跨越修改 clip 的 transform，也不重排重复 clip 族

因此较长的 vector clip 会尽量移到局部可交换片段末尾，但不会穿过无法证明安全的边界。

例如：

```ass
{\fnExampleFont\an4\fs26\c&H000000&\blur1.5\1a&H78&\pos(119.66,201.29)}
```

在相应 Style 的主色为黑色时可得到：

```ass
{\an4\pos(119.66,201.29)\fnExampleFont\fs26\1a&H78&\blur1.5}
```

## 连续相同行合并

连续行合并默认启用，只合并满足全部条件的首尾相接 Dialogue；可使用 `--no-merge-lines` 关闭：

- 前一行结束时间等于后一行开始时间
- 除开始、结束时间外的全部事件字段相同
- 文本相同
- 不含 `\t`、fade、karaoke 或其他事件相对动画
- 不含未知 override 内容
- 没有会改变碰撞布局的重叠风险，或该行有明确静态定位

合并结果保留第一行并把结束时间延长到整个连续区间末尾。

## Comment 事件清理

`--clean-comments` 会删除 `[Events]` 中所有以 `Comment:` 开头的事件行，包括字段不完整、无法按当前 `Format:` 解析的 Comment 行。该选项默认关闭。

它不会删除：

- `Dialogue:` 文本中的普通 `{comment}`
- 分号开头的 ASS 文件注释
- 其他段落中恰好包含 `Comment:` 的普通文本

Comment 事件不参与 libass 或 xy‑VSFilter 的字幕呈现，但可能保存制作记录、备用版本或自动化脚本数据。仍需继续编辑这些内容时不要启用该选项。

## Aegisub 元数据清理

### `{=数字}` 引用

Aegisub 可以在事件 Text 字段开头写入 extradata 引用：

```ass
{=267}{\pos(100,100)}Text
{=267=268}{\pos(100,100)}Text
```

一个引用块可以包含一个或多个 ID。程序只把事件开头、完全符合 `\{(=\d+)+\}` 的块视为 Aegisub extradata 引用，不会把普通 brace comment 当成引用删除。

使用 `--clean-extradata-references` 只删除引用块，保留 `[Aegisub Extradata]` 段。

### `[Aegisub Extradata]`

使用 `--clean-extradata` 会：

1. 删除整个 `[Aegisub Extradata]` 段
2. 自动删除所有事件开头的 `{=数字}` 引用，防止留下悬空 ID。

Aegisub Extradata 是可供自动化脚本使用的通用事件附加数据，记录内容和用途取决于写入它的脚本。例如，Aegisub‑Motion 会把原始文本和 UUID 写入名为 `a-mo` 的 extradata，并用它实现 Revert。删除 Extradata 不会改变 libass 或 xy‑VSFilter 的字幕呈现，但可能永久破坏相关脚本的还原、跟踪或其他后续编辑功能；仍需使用这些功能时不要启用此项。

### `[Aegisub Project Garbage]`

使用 `--clean-project-garbage` 会删除整个项目垃圾段，包括视频、音频、关键帧路径、视频位置和界面状态等 Aegisub 工作区信息。它不参与字幕渲染，但删除后重新打开字幕时需要重新关联相关媒体。

三个元数据选项默认均启用；可分别使用 `--no-clean-extradata-references`、`--no-clean-project-garbage` 和 `--no-clean-extradata` 关闭。

## 报告

命令行通过 `--report` 指定报告路径，格式由扩展名决定：

- `.html` / `.htm`：自包含 HTML
- 其他扩展名：Markdown

图形界面可以直接选择 `html` 或 `md`。默认格式与默认文件名为：

```text
字幕名.Clean Report.html
```

选择 Markdown 后对应为：

```text
字幕名.Clean Report.md
```

报告只列出实际修改的事件，不列出未修改行，也不罗列没有被删除的标签。内容包括：

- 汇总统计
- 文件级元数据删除结果
- 启用渲染对比时的组合差分章节
- 实际删除的两端未知标签
- 两端均不识别的标签
- 渲染器特有语法
- 每个修改事件的清理前后文本
- 该事件实际删除的标签
- 数值规范化与重排数量

组合报告会依次把真实渲染差分、实际删除及仍保留的两端未知标签、渲染器特有语法放在修改明细之前，便于先确认整体 PASS/FAIL 与兼容性提示，再按需查看具体事件。

HTML 不依赖外部 CSS、JavaScript 或网络资源。每个修改事件默认折叠，其清理前后文本保存在惰性 `template` 中，只有展开该事件时才进入页面布局，因此修改行很多时通常比 Markdown 预览更快。页面顶部提供 **Expand all** 和 **Collapse all**；展开全部会一次载入所有明细，大报告此时仍可能需要较长时间。

批量处理通过 `--write-reports` 为每个字幕生成独立报告。未指定 `--report-dir` 时报告写在各自源字幕旁；指定报告目录后会保留输入文件夹内的相对目录结构。

## 真实渲染对比

清理时默认执行 libass 对比，并把结果写入 HTML 报告：

```powershell
python ass_clean_redundant_tags.py clean "input.ass" `
  --output-dir "Cleaned"
```

需要同时验证 xy‑VSFilter 时添加 `--compare-vsfilter`；libass 对比可用 `--no-compare-libass` 关闭。

也可以单独比较两个现有文件：

```powershell
python ass_clean_redundant_tags.py compare `
  --original-ass "before.ass" `
  --cleaned-ass "after.ass" `
  --full-frames `
  --render-workers 12 `
  --report "Diff Report.html"
```

完整帧模式并不渲染整部视频的所有帧。它只计算文本发生变化的 Dialogue 的有效时间并集，再比较这些区间中的每一帧。

完整帧任务按“渲染器 × 清理前后 × 背景”拆分并放入同一个并发池。启用 libass 与 xy‑VSFilter、使用默认黑白背景时共有 `2 × 2 × 2 = 8` 个任务。`--render-workers` 决定同时运行的进程上限：默认值为 `4`，设为 `8` 可让全部任务同时开始，设为 `1` 可退回串行。图形界面中可直接设置同一参数。较高并发会明显增加 CPU 和内存占用，但不改变帧选择、哈希比较或最终判定。

每个渲染器都只比较自己的清理前后结果：

```text
libass(before)       ↔ libass(after)
xy-VSFilter(before)  ↔ xy-VSFilter(after)
```

不会比较：

```text
libass ↔ xy-VSFilter
```

因此两个渲染器本来就不同的视觉结果不会被误判为清理失败。

默认在黑、白两种背景上验证，以覆盖透明度、边框、阴影和颜色通道差异。需要额外验证灰色背景时，可显式使用 `--backgrounds black,white,gray`。出现差异时会保留 before、after 和红色差异图。

差分状态：

- `PASS`：所有已执行比较均通过
- `FAIL`：至少一个渲染器发现清理前后差异
- `INCOMPLETE`：请求的真实渲染器不可用
- 配置错误：输入、工具路径、帧率或参数无效

## JSON 设置

清理选项、输入输出、报告和真实渲染设置都可以保存到 JSON。项目包含可直接修改使用的 [`settings.example.json`](settings.example.json) 模板。

示范配置中的输入和输出保持为空：

```json
{
  "inputs": [],
  "output": null,
  "output_dir": null,
  "in_place": false,
  "safe_reorder": true,
  "merge_lines": true,
  "clean_comments": false,
  "clean_unknown_tags": true,
  "clean_extradata_references": true,
  "clean_project_garbage": true,
  "clean_extradata": true,
  "write_reports": true,
  "report_format": "html",
  "compare_libass": true,
  "compare_vsfilter": false,
  "backgrounds": ["black", "white"],
  "render_workers": 4
}
```

此时在命令行中指定输入和输出：

```powershell
python ass_clean_redundant_tags.py clean input.ass `
  --settings settings.example.json `
  --output output.ass
```

JSON 已填写 `inputs` 和输出路径时，也可以只提供配置文件：

```powershell
python ass_clean_redundant_tags.py clean --settings settings.json
```

| JSON 字段 | 用途 |
| :--- | :--- |
| `inputs` | 输入文件或目录数组；省略、设为 `null` 或 `[]` 时从命令行读取 |
| `output` | 单文件输出路径；不使用时为 `null` |
| `output_dir` | 批量输出目录；不使用时为 `null` |
| `in_place` | 是否替换原字幕 |
| `backup` | 原位替换时是否创建 `.bak` |
| `recursive` | 是否递归扫描输入目录 |
| `safe_reorder` | 是否执行兼容安全重排 |
| `merge_lines` | 是否合并连续相同的静态 Dialogue |
| `clean_comments` | 是否删除 `[Events]` 中的 Comment 事件行；默认 `false` |
| `clean_unknown_tags` | 是否删除 libass 与 xy‑VSFilter 均不识别的标签；默认 `true` |
| `clean_extradata_references` | 是否删除事件开头的 extradata 引用 |
| `clean_project_garbage` | 是否删除 `[Aegisub Project Garbage]` |
| `clean_extradata` | 是否删除 `[Aegisub Extradata]` 及关联引用 |
| `report` | 单文件报告路径；不使用时为 `null` |
| `report_dir` | 批量报告目录；不使用时为 `null` |
| `write_reports` | 是否为每个输入生成报告 |
| `report_format` | 批量报告格式：`md` 或 `html` |
| `compare_libass` | 是否在清理后使用 libass 对比 |
| `compare_vsfilter` | 是否在清理后使用 xy‑VSFilter 对比；默认 `false` |
| `ffmpeg` | FFmpeg 命令或路径 |
| `xy_vsfilter_dll` | xy‑VSFilter/VSFilter DLL 路径；自动查找时为 `null` |
| `xy_adapter` | 外部 xy‑VSFilter 适配器配置；不使用时为 `null` |
| `fps` | 完整帧比较使用的帧率 |
| `backgrounds` | 比较背景数组，可包含 `black`、`white`、`gray` |
| `allow_partial` | 缺少一个目标渲染器时是否允许以成功退出码结束 |
| `channel_tolerance` | 单通道像素容差 |
| `pixel_tolerance` | 允许不同的像素数量 |
| `timeout` | 单次外部命令超时秒数 |
| `render_workers` | 并发渲染进程上限 |

命令行中明确提供的输入会整体替换 JSON 的 `inputs`。`--output`、`--output-dir`、`--report` 和 `--report-dir` 会覆盖 JSON 中对应的路径选择；其他命令行参数分别覆盖同名 JSON 字段。布尔选项可以使用对应的 `--no-*` 写法临时关闭，例如 `--no-safe-reorder`、`--no-merge-lines` 和 `--no-compare-libass`。

JSON 中的相对路径以运行命令时的当前目录为基准。`output`、`output_dir` 与 `in_place: true` 不能同时生效；`report` 与 `report_dir` 也不能同时设置。

把命令行和 JSON 合并后的有效设置保存为新文件：

```powershell
python ass_clean_redundant_tags.py clean input.ass `
  --settings settings.json `
  --safe-reorder `
  --save-settings effective-settings.json
```

图形界面中的 **Save as JSON settings…** 会保存当前选项。保存时可以选择是否写入当前输入、字幕输出和报告路径；选择不写入时，生成的 `inputs` 为空，`output`、`output_dir`、`report` 和 `report_dir` 为 `null`，之后可由命令行指定。

主程序同目录自动生成的 `ass_clean_redundant_tags.config.json` 只用于记住本机图形界面状态，不等同于通过 `--settings` 使用的可移植任务配置。
