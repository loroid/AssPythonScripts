# AssGlyphOpticalCenter

[Repository](../README.md) · **English** · [简体中文](README.zh-CN.md)

`AssGlyphOpticalCenter.py`

[View script](AssGlyphOpticalCenter.py)

Horizontally centers ASS subtitle text using its actual visible glyph bounds. The tool was created primarily to address cases in Chinese–Japanese bilingual subtitles where the Chinese and Japanese text appear not to share the same visual centerline. It also corrects italic text, punctuation, brackets, or unusual fonts that are mathematically centered but still look visually offset because of uneven side bearings. Optional Ruby following keeps matching annotation events aligned with the body text after correction.

> **Scope:** Multiple ASS files and multiple subtitle events can be processed in one operation, but each event should contain a single line of body text. The tool adjusts horizontal placement only.

## How it works

Standard subtitle alignment is based on the font's advance width. The visible glyph outline, however, is not always centered within that width.

The tool obtains the glyph outlines through the operating system's native text backend and measures their actual horizontal bounds:

```text
ink center = (leftmost bound + rightmost bound) / 2
```

It then calculates the required horizontal correction from the script resolution, ASS alignment, margins, or `\pos` coordinates.

## Requirements and installation

**Requirements**

- Python 3.10 or later
- Linux: system Pango and Cairo libraries; Fontconfig is also required with `--font-dir`. A desktop environment may already provide them; otherwise install them with the distribution's package manager
- macOS: install Pango, for example with `brew install pango`; Homebrew installs the required Cairo and Fontconfig dependencies with it

**Installation**

No Python packages need to be installed. Place `AssGlyphOpticalCenter.py` in any directory and run it directly with Python:

```powershell
python AssGlyphOpticalCenter.py --help
```

## Usage

1. Use `--list-styles` to inspect the Styles and event counts in an ASS file.
2. Supply one or more ASS files or directories, or configure JSON `inputs`.
3. Configure body Styles with `--match-style` or `--match-style-exact`; at least one include value is required.
4. Add `--exclude-style` or `--exclude-style-exact` as needed.
5. Choose `--mode margin` or `--mode pos`.
6. Configure the correction threshold, large-margin behavior, and optional Ruby Style rules.
7. Choose the default new-file output, a shared `--output-dir`, or `--in-place` replacement.
8. Run the tool. Add `--dry-run` when checking settings without writing files.

### Command quick reference

The basic command structure is:

```powershell
python AssGlyphOpticalCenter.py <input file or directory> <Style rules> [other options]
```

| Feature | Command syntax |
| :--- | :--- |
| List Styles | `--list-styles` |
| Style substring match | `--match-style TEXT`; repeat as needed |
| Style exact match | `--match-style-exact STYLE`; repeat as needed |
| Style substring exclude | `--exclude-style TEXT`; repeat as needed |
| Style exact exclude | `--exclude-style-exact STYLE`; repeat as needed |
| Case-insensitive Styles | `--ignore-style-case`; restore case sensitivity with `--no-ignore-style-case` |
| Margin mode | `--mode margin` |
| Coordinate mode | `--mode pos` |
| Correction threshold | `--threshold 0.2` |
| Large-margin threshold | `--large-margin-threshold 100` |
| Preserve large margins | `--preserve-large-margins`; disable with `--no-preserve-large-margins` |
| Ruby substring match | `--match-ruby-style TEXT`; repeat as needed |
| Ruby exact match | `--match-ruby-style-exact STYLE`; repeat as needed |
| Ruby maximum distance | `--ruby-distance-threshold-percent 3` |
| Process Comment events | `--process-comments`; disable with `--no-process-comments` |
| Write Debug clip | `--debug-clip`; disable with `--no-debug-clip` |
| Multiple inputs | `file1.ass file2.ass` |
| Input directory | `"D:\Subtitles"`; add `--recursive` to scan subdirectories |
| Custom single-file output | `-o output.ass` or `--output output.ass` |
| Shared batch output | `--output-dir "D:\Centered"` |
| Replace existing output | `--overwrite`; forbid replacement with `--no-overwrite` |
| Replace input files | `--in-place` or `--replace-original`; disable with `--no-in-place` |
| Analyze without writing | `--dry-run`; disable with `--no-dry-run` |
| Load JSON | `--settings settings.json` |
| Save effective settings | `--save-settings settings.json` |
| Add font directory | `--font-dir "D:\Fonts"`; repeat as needed |
| Show help or version | `--help`, `--version` |

Complete example:

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

List Styles:

```powershell
python AssGlyphOpticalCenter.py input.ass --list-styles
```

Process two exact Style names:

```powershell
python AssGlyphOpticalCenter.py input.ass `
  --match-style-exact Chinese `
  --match-style-exact Japanese
```

The default output is:

```text
input.centered.ass
```

> `--in-place` provides no automatic rollback or backup. Verify the settings or create your own backup before using it.

## Input and output

### Single file

Without an explicit output path, the result is written beside the input with `.centered` added to its name:

```text
D:\Subtitles\episode01.ass
D:\Subtitles\episode01.centered.ass
```

Use `-o` or `--output` to choose the complete output path for one input:

```powershell
python AssGlyphOpticalCenter.py input.ass -o output.ass `
  --match-style Body
```

Existing output files are rejected by default. Use `--overwrite` to replace them explicitly.

### Batch processing

Supply multiple ASS files in one command:

```powershell
python AssGlyphOpticalCenter.py episode01.ass episode02.ass `
  --match-style Body
```

A directory can also be supplied. Its first level is scanned for `.ass` files by default; `--recursive` includes subdirectories:

```powershell
python AssGlyphOpticalCenter.py "D:\Subtitles" `
  --recursive `
  --match-style Body `
  --output-dir "D:\Centered"
```

Batch behavior:

- Without `--output-dir`, each result is written beside its input
- `--output-dir` writes all results into one directory and does not preserve the source directory tree
- Inputs with the same filename in different directories cause an output collision; the tool stops before writing
- `-o` / `--output` is available only with a single input file
- Directory scans ignore generated `*.centered.ass` files; such a file can still be processed when supplied explicitly
- A per-file failure does not stop the remaining inputs; the final summary reports successes and failures and returns a nonzero exit status when any file fails

### Replacing input files

`--in-place` (alias: `--replace-original`) replaces each input ASS directly and does not create a `.centered.ass` file:

```powershell
python AssGlyphOpticalCenter.py input.ass `
  --match-style Body `
  --in-place
```

The tool first writes a temporary file beside the input and then asks the operating system to atomically replace the original ASS. A write failure leaves the original unchanged, but no backup is created.

`--in-place` supports single-file and batch input and cannot be combined with `-o` or `--output-dir`. `--dry-run --in-place` does not modify files.

## Style rules

At least one body `--match-style` or `--match-style-exact` value is required. All four groups are repeatable:

| Setting | Command-line option | Rule |
| :--- | :--- | :--- |
| `Style match` | `--match-style TEXT` | Style name contains any supplied value |
| `Style strict match` | `--match-style-exact STYLE` | Complete Style name equals any supplied value |
| `Style exclude` | `--exclude-style TEXT` | Exclude when the Style name contains any supplied value |
| `Style strict exclude` | `--exclude-style-exact STYLE` | Exclude when the complete Style name equals any supplied value |

Any include may select an event, while exclusions take precedence. Matching is case-sensitive by default; `--ignore-style-case` makes all body and Ruby Style rules case-insensitive.

```powershell
python AssGlyphOpticalCenter.py input.ass `
  --match-style CHS `
  --match-style-exact Japanese `
  --exclude-style Note `
  --exclude-style-exact "CHS Sign"
```

## `Adjustment mode`

### `margin` (single-margin mode)

**Command syntax:** `--mode margin`

This is the default mode. The tool writes an event-level margin according to horizontal alignment:

| Alignment tags | Horizontal alignment | Adjusted field |
| :--- | :---: | :--- |
| `\an1`, `\an4`, `\an7` | Left | MarginL |
| `\an2`, `\an5`, `\an8` | Center | MarginL or MarginR |
| `\an3`, `\an6`, `\an9` | Right | MarginR |

For center-aligned text, a single margin changes by twice the desired visual shift because the center of the available area moves by only half of that margin change.

> Body events containing `\pos` or `\move` are skipped to avoid conflicts between margins and explicit positioning.

### `pos` (coordinate mode)

**Command syntax:** `--mode pos`

The tool calculates the `\pos(x,y)` value that places the ink center at the horizontal center of the frame:

- Existing `\pos`: replaces X and preserves Y
- No `\pos`: inserts a position tag
- Existing `\move`: skips the event

## `Correction threshold (%)`

**Command syntax:** `--threshold 0.2`

The default is **`0.20%`**.

```text
shift percentage = abs(pending visual shift) / video width × 100
```

A correction is applied only when the percentage is strictly greater than the threshold.

> **Example:** At a width of 1920 pixels, `0.20%` is approximately `3.84` pixels. Visual offsets of about 3.84 pixels or less are left unchanged. Set the threshold to `0` to process every nonzero offset.

## `Large-margin threshold`

**Command syntax:** `--large-margin-threshold 100`

The default is **`100`**.

## `Large-margin behavior`

**Command syntax:** `--preserve-large-margins`; disable with `--no-preserve-large-margins`

`--preserve-large-margins` is enabled by default and applies only in `margin` mode.

When an event-level MarginL or MarginR is greater than `--large-margin-threshold`, the tool treats the placement as intentional. Instead of moving the subtitle to the center of the frame, it only compensates for the difference between the glyph ink center and the font's advance center, changing only the side with the large margin.

This is useful for:

- Body text already positioned with margins
- Name cards or on-screen text
- Left/right split layouts that should remain intact

> Large-margin detection uses event-level margins, not Style margins.

## `Ruby following`

Ruby following moves an associated Ruby event by the actual horizontal distance applied to its body event. It is disabled when both Ruby Style groups are empty. Events identified as Ruby are not also processed as body events.

### `Ruby Style match`

**Command syntax:** `--match-ruby-style TEXT`

Repeat `--match-ruby-style TEXT` to match Ruby events whose Style name contains a supplied value.

### `Ruby Style strict match`

**Command syntax:** `--match-ruby-style-exact STYLE`

Repeat `--match-ruby-style-exact STYLE` to match Ruby events whose complete Style name equals a supplied value.

### `Ruby maximum distance (% of video height)`

**Command syntax:** `--ruby-distance-threshold-percent 3`

`--ruby-distance-threshold-percent` sets the maximum spatial distance between a Ruby event and its body event as a percentage of `PlayResY`. The default is **`3%`**, or `32.4` script pixels at 1920×1080.

For each Ruby event, the tool considers only body events in the current operation that have exactly the same start and end times and are within this maximum distance. It measures the shortest distance from the Ruby visible-area center to each candidate body's rendered rectangle and chooses the nearest candidate. An exact unresolved tie is skipped.

After the body is corrected, Ruby follows its actual resulting horizontal displacement:

- Ruby with `\pos(x,y)`: changes only X to `x + displacement` and preserves Y
- Ruby without `\pos`: changes MarginL or MarginR according to horizontal alignment; center alignment changes one margin by twice the displacement
- Ruby with `\move`: is skipped

## `Process comment events`

**Command syntax:** `--process-comments`; disable with `--no-process-comments`

Comment events are skipped by default. When `--process-comments` is enabled, Comment events that pass the body or Ruby Style rules are processed in the same way as normal Dialogue events.

Leave it disabled when Comment events contain templates, notes, or other non-rendered working data.

## `Advanced`

**Command syntax:** `--debug-clip`; disable with `--no-debug-clip`

When `--debug-clip` is enabled, the tool writes a rectangle `\clip(x1,0,x2,PlayResY)` whose left and right edges match the calculated glyph bounds.

> **Warning:** `\clip` performs real clipping; it does not draw a visible outline. It may cut off borders, shadows, or blur. If an event already contains `\clip`, Debug mode replaces the first matching tag. Use this option only for temporary inspection.

Debug mode modifies an event by inserting `\clip` even when its positional correction is below the threshold.

## JSON settings

Processing options, Style rules, and input, output, and batch execution settings can all be stored in JSON. The included [`settings.example.json`](settings.example.json) provides a ready-to-edit template.

Execution settings example:

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

| JSON field | Purpose |
| :--- | :--- |
| `inputs` | Optional input files or directories; omit it or use `[]` to read inputs from the CLI |
| `output` | Custom output path for one input, or `null` |
| `output_dir` | Shared batch output directory, or `null` |
| `recursive` | Recursively scan input directories |
| `overwrite` | Replace existing output files |
| `dry_run` | Analyze without writing files |
| `list_styles` | List Styles and exit |
| `in_place` | Atomically replace each input ASS |

`output` is valid only for one input and cannot be combined with `output_dir`. An effective `in_place: true` setting is incompatible with either output path. Relative JSON paths are resolved from the current working directory used to run the command.

When JSON supplies `inputs`, the settings file is sufficient:

```powershell
python AssGlyphOpticalCenter.py --settings settings.json
```

Explicit command-line inputs replace the complete JSON `inputs` array; other command-line values override their corresponding fields. `--no-recursive`, `--no-overwrite`, `--no-dry-run`, `--no-list-styles`, and `--no-in-place` temporarily disable enabled JSON booleans.

Save the effective processing and execution settings to a new file:

```powershell
python AssGlyphOpticalCenter.py input.ass `
  --match-style Body `
  --save-settings my-settings.json `
  --dry-run
```

## Supported inline tags

Glyph measurement tracks:

`\fn` · `\b` · `\i` · `\u` · `\s` · `\fs` · `\fscx` · `\fscy` · `\fsp` · `\fax` · `\fay` · `\p` · `\r`

`\fax` and `\fay` are applied to the glyph outline before its bounds are measured, with effective shear accounting for `\fscx` and `\fscy`. Drawing sections such as `\p1` are excluded from text-bound measurement.

## Font measurement backends

- Windows always uses GDI with 64× precision for font-outline measurement and font-height conversion; it never switches to Pango
- Linux and macOS use Pango + Cairo, including 64× precision, the libass font hack, letter spacing, and Cairo path-control-point bounds
- Repeat `--font-dir DIR` to add font directories; fonts are registered privately with GDI on Windows and through Fontconfig for Pango on Linux/macOS
- A missing native backend is an error; an event whose native measurement fails is skipped and counted under `Measurement failed`, with no alternate measurement backend

Results are stable when the operating system, native-library versions, and font environment remain the same. GDI and Pango can still differ across operating systems.

## Limitations

- Events are selected only through Style rules; interactive manual row selection is not available
- Multiple files and events can be processed at once, but each event should contain one line of body text; automatic wrapping is not measured per visual line
- Only horizontal visual centering is corrected; Y coordinates are not adjusted
- Events containing `\move` are skipped
- Ruby following requires exactly matching start and end times; unmatched, over-threshold, and ambiguous Ruby events are left unchanged
- Font fallback, complex shaping, and mixed fonts depend on the local GDI/Pango version and font environment
- Rotation, borders, shadows, blur, and animated transforms are not included in the measured ink bounds
- Debug `\clip` affects actual rendering and should not remain in final subtitles
- Input ASS files must be UTF-8

## Tests

```powershell
python -m unittest discover -s tests -v
```
