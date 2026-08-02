# AssCleanRedundantTags

[Repository](../README.md) · **English** · [简体中文](README.zh-CN.md)

`ass_clean_redundant_tags.py`

[View script](ass_clean_redundant_tags.py)

Cleans ASS/SSA override tags that do not change the effective state, writes that are completely superseded before any text is rendered, and insignificant numeric formatting. It can also apply compatibility-safe tag reordering, merge consecutive identical static Dialogue rows, clean Aegisub file-level metadata, generate audit reports, and validate the before/after output independently with libass and xy-VSFilter.

> **Scope:** Supports single files, multiple files, and directory batches. The safety target is to preserve the effective tag state of libass and xy-VSFilter independently. The two renderers are not expected to produce identical images, and syntax that cannot be proven safe is retained.

## How it works

Starting from the Style state used by each event, the cleaner follows override blocks and rendered text in order while tracking effective font, scale, color, alpha, border, shadow, position, drawing, karaoke, and transform state. A tag is removed only when it has no effect on subsequent state, or when another write to the same field completely supersedes it before any rendering boundary.

Optional rendering validation always compares output within the same renderer: libass before cleanup against libass after cleanup, and xy-VSFilter before cleanup against xy-VSFilter after cleanup. Existing visual differences between the renderers do not count as cleanup failures.

## Requirements and installation

**Requirements**

- Python 3.10 or later
- FFmpeg for libass comparison
- On Windows, FFmpeg, AviSynth+, and a loadable xy-VSFilter/VSFilter DLL for the built-in xy-VSFilter comparison

FFmpeg and the xy-VSFilter DLL are searched in this order:

1. A path explicitly supplied through the command line or GUI
2. The script directory and known adjacent tool directories
3. The system `PATH`

AviSynth+ uses the installed system runtime and is not included in the path search above.

**Installation**

Place `ass_clean_redundant_tags.py` in any directory and run it with Python:

```powershell
python ass_clean_redundant_tags.py
```

## Basic usage

1. Run the script without arguments to open the GUI. When arguments are supplied, cleanup is the default command; `clean` may also be written explicitly.
2. Add one or more ASS/SSA files or directories, and enable recursive scanning if needed.
3. Choose a separate output, an output directory, or in-place replacement.
4. Select tag reordering, line merging, Comment-row removal, unknown-tag cleanup, and Aegisub metadata cleanup as needed.
5. Select Markdown or HTML when an audit report is required.
6. Enable libass, xy-VSFilter, or both for actual rendering validation, and set the render concurrency limit.

### Command-line reference

The cleanup command has this form:

```powershell
python ass_clean_redundant_tags.py [clean] <input files or directories> [options]
```

`clean` is optional. If neither `clean` nor `compare` is present, arguments are parsed as a cleanup command. Standalone comparison requires the explicit `compare` command.

| Function | Syntax |
| :--- | :--- |
| Multiple inputs | `file1.ass file2.ass` |
| Input directory | `"D:\Subtitles"`; add `--recursive` to scan subdirectories |
| Single-file output | `-o output.ass` or `--output output.ass` |
| Batch output directory | `--output-dir "D:\Cleaned"` |
| Replace source files | `--in-place`; add `--no-backup` to disable backups |
| Compatibility-safe reorder | `--safe-reorder` |
| Merge consecutive identical rows | `--merge-lines` |
| Remove Comment event rows | `--clean-comments`; disabled by default |
| Remove tags unknown to both renderers | `--clean-unknown-tags` |
| Remove extradata references | `--clean-extradata-references` |
| Remove Project Garbage | `--clean-project-garbage` |
| Remove the Extradata section | `--clean-extradata` |
| Single-file report | `--report "Clean Report.html"` |
| Batch reports | `--write-reports --report-dir "D:\Reports"` |
| Report format | `--report-format md` or `--report-format html` |
| Compare with libass after cleanup | `--compare-libass` |
| Compare with xy-VSFilter after cleanup | `--compare-vsfilter` |
| Concurrent render limit | `--render-workers 8` |
| Load JSON settings | `--settings settings.json` |
| Save effective settings | `--save-settings settings.json` |

The standalone comparison command has this form:

```powershell
python ass_clean_redundant_tags.py compare `
  --original-ass before.ass `
  --cleaned-ass after.ass `
  [options]
```

| Function | Syntax |
| :--- | :--- |
| Compare existing files | `--original-ass before.ass --cleaned-ass after.ass` |
| Compare an external case corpus | `--corpus cases.json` |
| Compare every frame in modified intervals | `--full-frames` |
| Set the frame rate | `--fps 24000/1001` |
| Set backgrounds | `--backgrounds black,white`; `gray` is also available |
| Select FFmpeg | `--ffmpeg "D:\Tools\ffmpeg.exe"` |
| Select xy-VSFilter | `--xy-vsfilter-dll "D:\Filters\VSFilter.dll"` |
| Set tolerances | `--channel-tolerance N --pixel-tolerance N` |
| Retain differential artifacts | `--artifacts "D:\Diff Artifacts"` |
| Set the report path | `--report "Diff Report.html"` |
| Concurrent render limit | `--render-workers 8` |

Complete cleanup example:

```powershell
python ass_clean_redundant_tags.py input.ass `
  --safe-reorder `
  --merge-lines `
  --compare-libass `
  --compare-vsfilter `
  --render-workers 8 `
  --report "input.Clean Report.html"
```

## Graphical interface

Run the script without arguments:

```powershell
python ass_clean_redundant_tags.py
```

The GUI provides independent controls for:

- Multiple subtitle files and directories
- Recursive directory scanning
- An absolute or relative output directory
- Compatibility-safe tag reordering
- Merging consecutive identical static Dialogue rows
- Removing Comment event rows from `[Events]`
- Removing override tags unknown to both libass and xy-VSFilter
- Removing leading `{=number}` extradata references
- Removing `[Aegisub Project Garbage]`
- Removing `[Aegisub Extradata]`
- libass comparison
- xy-VSFilter comparison
- Concurrent render-process limit
- Markdown or HTML reports
- Saving portable JSON settings
- In-place replacement

The input list may contain files and directories at the same time and is deduplicated by normalized absolute path. Directories are scanned at their top level by default; enable **Scan subdirectories** to recursively find `.ass` and `.ssa` files.

When the output directory is blank, the program writes `SubtitleName.Cleaned.ass` beside each source without overwriting it. An absolute path becomes the common output root. A relative path is resolved from the containing directory of an individually added file, or from the selected input directory. Relative subdirectories discovered during directory scanning are preserved. In-place replacement creates a backup for every file first.

GUI state is stored in `ass_clean_redundant_tags.config.json` beside the script.

## Input and output

### Single file

```powershell
python ass_clean_redundant_tags.py clean "input.ass"
```

### Multiple files and directories

Process multiple subtitles:

```powershell
python ass_clean_redundant_tags.py clean "one.ass" "two.ssa"
```

Process subtitles at the top level of a directory:

```powershell
python ass_clean_redundant_tags.py clean "Subtitle Folder"
```

Scan recursively and write subtitles and HTML reports to separate directories:

```powershell
python ass_clean_redundant_tags.py clean "Subtitle Folder" `
  --recursive `
  --output-dir "Cleaned" `
  --write-reports `
  --report-dir "Reports" `
  --report-format html
```

Directory scanning skips `.ass` and `.ssa` files whose stem ends in `.Cleaned`, preventing repeated names such as `name.Cleaned.Cleaned.ass`. Such a file is still processed when it is added explicitly as a command-line or GUI input.

If multiple inputs map to the same output or report path, the program stops before writing and reports the conflict instead of overwriting one of the files.

### Output locations

Specify a relative output directory:

```powershell
python ass_clean_redundant_tags.py clean "input.ass" --output-dir "Cleaned"
```

For an input at `D:\Subs\input.ass`, the output is:

```text
D:\Subs\Cleaned\input.Cleaned.ass
```

The same option accepts an absolute directory:

```powershell
python ass_clean_redundant_tags.py clean "input.ass" `
  --output-dir "E:\Cleaned Subtitles"
```

A relative output directory does not depend on the process working directory. It is relative to a selected input directory for directory inputs, and to the file's containing directory for individually added files. `--output "output.ass"` remains available for a precise single-file destination; batch inputs use `--output-dir`. `--report` and `--report-dir` follow the same distinction.

### Cleanup-option examples

Compatibility-safe reordering, consecutive-line merging, removal of override tags unknown to both renderers, HTML reports, all three Aegisub metadata cleanup operations, and libass comparison are enabled by default. xy-VSFilter comparison is disabled by default:

```powershell
python ass_clean_redundant_tags.py clean "input.ass"
```

Every default-enabled option has a corresponding `--no-*` form. Add `--compare-vsfilter` when xy-VSFilter validation is required.

Remove tags unknown to both target renderers:

```powershell
python ass_clean_redundant_tags.py clean "input.ass" `
  --clean-unknown-tags
```

Clean all three categories of Aegisub metadata:

```powershell
python ass_clean_redundant_tags.py clean "input.ass" `
  --clean-extradata-references `
  --clean-project-garbage `
  --clean-extradata
```

### Replacing source files

```powershell
python ass_clean_redundant_tags.py clean "input.ass" --in-place
```

In-place replacement creates `input.ass.bak` by default. If that name exists, `input.ass.bak.1`, `input.ass.bak.2`, and so on are used without overwriting an earlier backup. Use `--no-backup` only when an unprotected overwrite is intentional.

## Tag cleanup rules

The program tracks effective state from the initial Style and the order in which text is rendered. It does not perform simple string deduplication.

If the Style has `ScaleX=100`:

```ass
{\fscx100}Example
```

can become:

```ass
Example
```

However:

```ass
Ex{\fscx60}am{\fscx100}ple
```

must keep the second `\fscx100`, because it restores the scale for later text.

A dead write before the same rendering boundary can be removed:

```ass
{\fscx60\fscx100}Example
```

becomes:

```ass
Example
```

### Complex state examples

The following examples assume that Style `Default` has `ScaleX=100`, a white primary color, primary alpha `&H80&`, `Outline=2`, and `Shadow=0`. Style `Alt` has `ScaleX=80`, `ScaleY=100`, bold enabled, primary color `&HFF0000&`, `Outline=3`, and `Shadow=1`.

Dead writes, Style-equivalent tags, first-wins geometry, and decimal formatting may occur before the same rendering boundary:

```ass
{\fscx60\fscx100\1c&HFFFFFF&\1a&H80&\bord2\shad0\pos(100.00,100.00)\move(0.00,1.0,10.00,20.00,0.00,500.00)}A
```

This becomes:

```ass
{\pos(100,100)}A
```

`\fscx60` is superseded by `\fscx100` before text is rendered. The other Style tags equal `Default`. `\pos` and `\move` are members of the same first-wins positioning family, so the earlier `\pos` is retained and its numbers are normalized.

When a Style reset and a transform occur together, the active Style is switched first and transform dependencies are then derived from the fields it actually reads and writes:

```ass
{\rAlt\fscx80\fscy100\b1\c&HFF0000&\t(0,500,\fscx160)\shad1}A
```

This becomes:

```ass
{\rAlt\t(0,500,\fscx160)}A
```

`\fscx80`, `\fscy100`, `\b1`, `\c&HFF0000&`, and `\shad1` are all supplied by Style `Alt`. After `\fscx80` is removed, the scale animation still starts from the active Style's `ScaleX=80`, so that tag is redundant as well.

With repeated writes across rendering boundaries and two Style changes:

```ass
{\blur200\blur200}A{\blur200\fscx60\fscx60}B{\rAlt\fscx80\fscy100\b1\bord3\shad1\c&HFF0000&}C{\rDefault\fscx100\1c&HFFFFFF&\1a&H80&}D
```

the result is:

```ass
{\blur200}A{\fscx60}B{\rAlt}C{\rDefault}D
```

The first `\blur200` and `\fscx60` affect already rendered text and cannot be removed because the same value appears later. Both resets are also required because they select the Style used by `C` and `D`. Only writes that truly have no remaining effect in the current state are removed.

Brace comments, malformed arguments, unparseable blocks, and renderer-specific HTML state form conservative boundaries.

### Tags unknown to both renderers

Removal of override tags unknown to both renderers is enabled by default. Use `--no-clean-unknown-tags` to retain them:

```ass
{\zunknown1\fs60}Text  →  {\fs60}Text
```

Unknown tags inside a simple `\t(...)` modifier are also removed. If that leaves no effective modifier, the empty transform is removed as well.

This option does not remove:

- A tag supported by either libass or xy-VSFilter
- A malformed argument following a known tag name
- Backslash-like text inside a block known to be a brace comment
- Content inside a complex transform that cannot be split safely

ASS tag names have no independent terminator. If a raw token begins with a known short tag, it is retained as that known tag with a malformed suffix. For example, `\unknown` begins with valid `\u`, so it is not deleted solely because more letters follow. This deliberately removes less in order to avoid deleting a token that either target renderer may partially parse.

“Unknown to both” refers only to the libass and xy-VSFilter compatibility sets maintained by this program. Other renderers, Aegisub automation scripts, or future versions may use extension tags. Disable this option when such tags must be preserved. Reports distinguish unknown tags actually removed from unknown tokens retained conservatively.

## Numeric and drawing normalization

Leading zeros, redundant decimal points, and trailing zeros that do not change a value are removed:

```ass
\pos(100.00,100.00)  →  \pos(100,100)
\pbo0.00             →  \pbo0
```

Normalization covers:

- Ordinary numeric tags
- `\pos`, `\move`, `\org`, `\fad`, and `\fade`
- Rectangular and vector `\clip` / `\iclip`
- Transform times, acceleration, and independently recognizable modifier values
- `\p` drawing coordinates
- Drawing paths inside vector clips

Adjacent drawing commands are converted to standard space-separated form:

```ass
m100.00 100.00l200.00 200.00
```

becomes:

```ass
m 100 100 l 200 200
```

Fragments containing unknown drawing tokens, indeterminate `\p` arguments, or complex nested expressions are retained unchanged.

## Transform safety boundaries

Simple `\t` expressions are analyzed field by field:

- Identity transforms can be removed
- A valid transform entirely after the event end can be removed
- Fields modified by a transform protect static state that actually changes the animation start; a preceding write equal to the current effective state can still be removed
- Static redundant tags unrelated to transform fields can still be removed independently
- Nested transforms, complex parenthesized content, and relative font-size animation are not semantically deleted field by field; independently recognizable numbers are still normalized, and unknown modifiers are removed only when `--clean-unknown-tags` is enabled and the content can be split safely

Assume the active Style has `ScaleX=100` and `ScaleY=100`. If a transform still targets the current value of 100, the effective value never changes before, during, or after interpolation, so the entire identity transform can be removed:

```ass
{\t(0,500,\fscx100)}Text
```

This becomes:

```ass
Text
```

`\fscy100` is unrelated to the following transform and can also be removed:

```ass
{\fscy100\t(\fscx200)}Text
```

Although `\fscx100` targets the same field as the transform, it equals the value already supplied by the Style. Removing it leaves the animation start at 100, so it can also be removed:

```ass
{\fscx100\t(0,500,\fscx200)}Text
```

The result is:

```ass
{\t(0,500,\fscx200)}Text
```

If the static value is `\fscx80`, it changes the animation start from the Style's 100 to 80 and must remain:

```ass
{\fscx80\t(0,500,\fscx200)}Text
```

### Complex transform examples

When multiple transforms modify different fields, their dependencies are combined instead of turning the entire event into an opaque block. Assume the active Style has `ScaleX=100`, `ScaleY=100`, `Outline=2`, `Shadow=0`, a white primary color, and primary alpha `&H80&`:

```ass
{\fscx100\fscy100\bord2\shad0\c&HFFFFFF&\t(0.00,500.00,\fscx200.00\bord4.00)\t(500.00,1000.00,\fscy150.00)\1a&H80&}A
```

This becomes:

```ass
{\t(0,500,\fscx200\bord4)\t(500,1000,\fscy150)}A
```

`\fscx100`, `\fscy100`, and `\bord2` correspond to animated fields, but each equals the starting value already supplied by the active Style. Removing them leaves transform interpolation unchanged. Shadow, color, and alpha also match the Style and are unrelated to the animations, so they are removed; numeric values inside both transforms are normalized independently.

A parseable transform may coexist with a complex one. Assume an event duration of `1000 ms` and Style `Alt` from the earlier example:

```ass
{\rAlt\fs40\fscx80\fscy100\bord3\t(1000.00,2000.00,\fscx160.00)\t(0.00,500.00,\fs+10\fscy150.00)\shad1}A
```

This becomes:

```ass
{\rAlt\t(0,500,\fs+10\fscy150)\shad1}A
```

The first transform begins at the event end and cannot affect an active frame, so it is removed. The second uses relative `\fs+10`, which depends on the current font size. The preceding `\fs40`, `\fscx80`, `\fscy100`, and `\bord3` equal Style `Alt` and can be removed without changing the animation start. The `\shad1` after the complex transform remains inside a conservative boundary and is retained.

## Compatibility-safe reordering

Reordering is enabled by default and uses this stable partial order:

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

This is not an unconditional sort. Two adjacent tags can exchange positions only when all of the following are true:

- Both tags are fully parseable by libass and xy-VSFilter
- Their complete read/write field sets do not intersect
- First-wins, reset, drawing, karaoke-timeline, and transform dependencies remain intact
- They do not cross a retained unknown tag, brace comment, malformed argument, or complex expression
- `\clip` / `\iclip` cross a simple transform only when its parsed modifier fields do not include clipping; duplicate clip-family tags are not reordered

Long vector clips therefore move toward the end of a locally commutative segment when safe, but never cross a boundary whose equivalence cannot be proven.

Within one override block, karaoke may cross static Style tags, `\r`, `\p`/`\pbo`, and a fully parsed simple `\t` to reach the order above. libass and xy-VSFilter preserve the same syllable timeline and use the same final static or animated state in these spellings. Karaoke tags still keep their order relative to other karaoke tags and do not cross an opaque or complex transform.

Overlapping deterministic Style writes are not rejected solely because their field names intersect. The reorderer simulates both orders and permits the exchange only when the complete resulting state is identical. Different reset values, global versus channel alpha, compound versus axis border/shadow, and `\fsc` versus explicit axis scaling therefore remain ordered whenever swapping would change effective state.

The undocumented `\fsc` compatibility reset never crosses `\r`: xy-VSFilter produces different pixels for the two orders even when their abstract active-Style end state appears identical.

For example:

```ass
{\fnExampleFont\an4\fs26\c&H000000&\blur1.5\1a&H78&\pos(119.66,201.29)}
```

with a black primary color in the active Style can become:

```ass
{\an4\pos(119.66,201.29)\fnExampleFont\fs26\1a&H78&\blur1.5}
```

## Consecutive identical-line merging

Merging is enabled by default. It merges adjacent Dialogue rows only when all of these conditions hold; use `--no-merge-lines` to disable it:

- The previous end time equals the next start time
- Every event field other than start and end time is identical
- Text is identical
- There is no `\t`, fade, karaoke, or other event-relative animation
- There is no unknown override content
- There is no overlap risk that could alter collision layout, unless the line has explicit static positioning

The first row is retained and its end time is extended to the end of the complete consecutive interval.

## Comment event cleanup

`--clean-comments` removes every event row beginning with `Comment:` in `[Events]`, including incomplete rows that cannot be parsed using the current `Format:`. It is disabled by default.

It does not remove:

- Ordinary `{comment}` text inside a `Dialogue:` row
- ASS file comments beginning with a semicolon
- Ordinary text containing `Comment:` in other sections

Comment events do not participate in libass or xy-VSFilter subtitle rendering, but may contain production notes, alternate versions, or automation data. Leave the option disabled when those contents are still needed for editing.

## Aegisub metadata cleanup

### `{=number}` references

Aegisub can place extradata references at the beginning of an event Text field:

```ass
{=267}{\pos(100,100)}Text
{=267=268}{\pos(100,100)}Text
```

One reference block can contain one or more IDs. Only leading event blocks matching `\{(=\d+)+\}` in full are treated as Aegisub extradata references, so ordinary brace comments are not removed as references.

`--clean-extradata-references` removes only the reference blocks and retains `[Aegisub Extradata]`.

### `[Aegisub Extradata]`

`--clean-extradata`:

1. Removes the complete `[Aegisub Extradata]` section
2. Removes all leading `{=number}` event references automatically, avoiding dangling IDs

Aegisub Extradata is general-purpose per-event data available to automation scripts; its content and meaning depend on the script that wrote it. For example, Aegisub-Motion stores original text and a UUID in `a-mo` extradata for its Revert feature. Removing Extradata does not change libass or xy-VSFilter rendering, but it may permanently break restore, tracking, or other editing features implemented by a script. Do not enable this option while those features are still needed.

### `[Aegisub Project Garbage]`

`--clean-project-garbage` removes the entire project-garbage section, including video, audio, keyframe paths, video position, and other Aegisub workspace state. It does not participate in subtitle rendering, but media may need to be relinked when the subtitle is opened again.

All three metadata options are enabled by default. Disable them individually with `--no-clean-extradata-references`, `--no-clean-project-garbage`, and `--no-clean-extradata`.

## Reports

Use `--report` to select a report path. The extension determines the format:

- `.html` / `.htm`: self-contained HTML
- Any other extension: Markdown

The GUI directly selects `html` or `md`. The default format and file name are:

```text
SubtitleName.Clean Report.html
```

Selecting Markdown changes the extension:

```text
SubtitleName.Clean Report.md
```

Reports include only events that were actually modified. Unchanged rows and tags that were not removed are not enumerated. A report contains:

- Summary statistics
- File-level metadata cleanup results
- A combined rendering differential section when comparison is enabled
- Tags unknown to both renderers that were actually removed
- Tokens unknown to both renderers that were retained conservatively
- Renderer-specific syntax
- Before/after text for every modified event
- Tags actually removed from each event
- Numeric-normalization and reorder counts

In a combined report, the actual rendering differential, unknown-tag sections, and renderer-specific syntax appear before change details. This puts the overall PASS/FAIL result and compatibility notes ahead of event-level inspection.

HTML reports have no external CSS, JavaScript, or network dependency. Every modified event is collapsed by default, and its before/after text stays in a lazy `template` until that event is expanded. This generally makes initial preview much faster than Markdown when many rows changed. **Expand all** and **Collapse all** controls are provided; expanding all still lays out every detail and can take time for a very large report.

For batch processing, `--write-reports` generates one report per subtitle. Reports are written beside each source if `--report-dir` is omitted. With a report directory, relative paths under selected input directories are preserved.

## Actual rendering comparison

Cleanup runs libass comparison by default and writes the result into the HTML report:

```powershell
python ass_clean_redundant_tags.py clean "input.ass" `
  --output-dir "Cleaned"
```

Add `--compare-vsfilter` to validate xy-VSFilter as well. Disable libass comparison with `--no-compare-libass`.

Two existing files can also be compared directly:

```powershell
python ass_clean_redundant_tags.py compare `
  --original-ass "before.ass" `
  --cleaned-ass "after.ass" `
  --full-frames `
  --render-workers 12 `
  --report "Diff Report.html"
```

Full-frame mode does not render every frame of an entire video. It computes the union of active times for Dialogue rows whose text changed and compares every frame in those intervals.

Full-frame tasks are split by renderer, before/after side, and background, then submitted to one shared pool. With libass and xy-VSFilter enabled on the default black and white backgrounds, there are `2 × 2 × 2 = 8` tasks. `--render-workers` limits simultaneously running processes: the default is `4`; `8` allows all tasks to start together, while `1` is serial. The same setting is available in the GUI. Higher concurrency uses more CPU and memory but does not change frame selection, hash comparison, or the verdict.

Each renderer is compared only with itself:

```text
libass(before)       ↔ libass(after)
xy-VSFilter(before)  ↔ xy-VSFilter(after)
```

The following comparison is not performed:

```text
libass ↔ xy-VSFilter
```

Existing visual differences between the two renderers therefore do not cause a false cleanup failure.

Black and white backgrounds are used by default to expose alpha, border, shadow, and color-channel differences. Add gray explicitly with `--backgrounds black,white,gray` when required. On a mismatch, before, after, and red difference images are retained.

Differential statuses are:

- `PASS`: every executed comparison passed
- `FAIL`: at least one renderer found a before/after difference
- `INCOMPLETE`: a requested actual renderer was unavailable
- Configuration error: an input, tool path, frame rate, or argument was invalid

## JSON settings

Cleanup options, input and output paths, reports, and actual-rendering settings can all be stored in JSON. [`settings.example.json`](settings.example.json) is a ready-to-edit template.

Inputs and outputs are intentionally blank in the example:

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

Supply input and output paths on the command line:

```powershell
python ass_clean_redundant_tags.py clean input.ass `
  --settings settings.example.json `
  --output output.ass
```

If `inputs` and an output destination are present in JSON, the settings file can be used alone:

```powershell
python ass_clean_redundant_tags.py clean --settings settings.json
```

| JSON field | Purpose |
| :--- | :--- |
| `inputs` | Array of input files or directories; read from the command line when omitted, `null`, or `[]` |
| `output` | Single-file output path; `null` when unused |
| `output_dir` | Batch output directory; `null` when unused |
| `in_place` | Replace source subtitles |
| `backup` | Create `.bak` files during in-place replacement |
| `recursive` | Recursively scan input directories |
| `safe_reorder` | Apply compatibility-safe reordering |
| `merge_lines` | Merge consecutive identical static Dialogue rows |
| `clean_comments` | Remove Comment event rows from `[Events]`; default: `false` |
| `clean_unknown_tags` | Remove tags unknown to both libass and xy-VSFilter; default: `true` |
| `clean_extradata_references` | Remove leading event extradata references |
| `clean_project_garbage` | Remove `[Aegisub Project Garbage]` |
| `clean_extradata` | Remove `[Aegisub Extradata]` and associated references |
| `report` | Single-file report path; `null` when unused |
| `report_dir` | Batch report directory; `null` when unused |
| `write_reports` | Generate one report for every input |
| `report_format` | Batch report format: `md` or `html` |
| `compare_libass` | Compare with libass after cleanup |
| `compare_vsfilter` | Compare with xy-VSFilter after cleanup; default: `false` |
| `ffmpeg` | FFmpeg command or path |
| `xy_vsfilter_dll` | xy-VSFilter/VSFilter DLL path; `null` for automatic discovery |
| `xy_adapter` | External xy-VSFilter adapter settings; `null` when unused |
| `fps` | Frame rate for full-frame comparison |
| `backgrounds` | Comparison background array containing `black`, `white`, and optionally `gray` |
| `allow_partial` | Allow a successful exit code when one requested target renderer is unavailable |
| `channel_tolerance` | Per-channel pixel tolerance |
| `pixel_tolerance` | Allowed changed-pixel count |
| `timeout` | Timeout in seconds for each external command |
| `render_workers` | Concurrent render-process limit |

Explicit command-line inputs replace JSON `inputs` as a group. `--output`, `--output-dir`, `--report`, and `--report-dir` override the corresponding path choices in JSON. Other command-line arguments override matching JSON fields individually. Boolean options have `--no-*` forms for temporary overrides, such as `--no-safe-reorder`, `--no-merge-lines`, and `--no-compare-libass`.

Relative paths in JSON are resolved from the current directory in which the command runs. `output`, `output_dir`, and `in_place: true` are mutually exclusive; `report` and `report_dir` are also mutually exclusive.

Save the effective settings produced by merging command-line arguments and JSON:

```powershell
python ass_clean_redundant_tags.py clean input.ass `
  --settings settings.json `
  --safe-reorder `
  --save-settings effective-settings.json
```

**Save as JSON settings…** in the GUI saves the current options. The dialog lets you include or omit current input, subtitle-output, and report paths. When omitted, `inputs` is empty and `output`, `output_dir`, `report`, and `report_dir` are `null`, allowing those paths to be supplied later on the command line.

The automatically generated `ass_clean_redundant_tags.config.json` beside the script stores local GUI state only. It is separate from portable task settings loaded with `--settings`.
