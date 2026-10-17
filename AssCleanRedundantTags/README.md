# AssCleanRedundantTags

[Repository](../README.md) · **English** · [简体中文](README.zh-CN.md)

`ass_clean_redundant_tags.py`

[View script](ass_clean_redundant_tags.py)

Cleans ASS/SSA override tags that do not change the effective state, writes that are completely superseded before any text is rendered, insignificant numeric formatting, and event margins proven inert under `\pos`. It can optionally remove collision-safe Dialogue rows that remain fully transparent, apply compatibility-safe tag reordering, merge consecutive identical static Dialogue rows, clean Aegisub file-level metadata, generate audit reports, and validate the before/after output independently with libass and xy-VSFilter.

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
4. Select tag reordering, positioned-margin cleanup, line merging, Comment-row removal, always-transparent Dialogue-row removal, unknown-tag cleanup, and Aegisub metadata cleanup as needed.
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
| Clear ineffective margins on positioned rows | `--clean-pos-margins`; enabled by default |
| Remove Comment event rows | `--clean-comments`; disabled by default |
| Remove always-transparent Dialogue rows | `--remove-transparent-dialogues`; disabled by default |
| Remove proven-ineffective unknown or malformed tags | `--clean-unknown-tags` |
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
- Clearing ineffective event margins on safely analyzable `\pos` Dialogue and Comment rows
- Removing Comment event rows from `[Events]`
- Removing collision-safe Dialogue rows that remain fully transparent
- Removing proven-ineffective unknown or malformed override tags
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

Compatibility-safe reordering, consecutive-line merging, removal of proven-ineffective unknown or malformed override tags, HTML reports, all three Aegisub metadata cleanup operations, and libass comparison are enabled by default. Comment-row removal, always-transparent Dialogue-row removal, and xy-VSFilter comparison are disabled by default:

```powershell
python ass_clean_redundant_tags.py clean "input.ass"
```

Every default-enabled option has a corresponding `--no-*` form. Add `--compare-vsfilter` when xy-VSFilter validation is required.

Remove proven-ineffective unknown or malformed override tags:

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

### Cleanup quick reference

| Situation | Tags or syntax | Removed when | Retained when |
| --- | --- | --- | --- |
| Equal to the current effective state | `\fn`, `\fs`, `\fsp`, `\b`, `\i`, `\u`, `\s`, scale/rotation/shear, border/shadow/blur, `\c`/`\1c`–`\4c`, `\alpha`/`\1a`–`\4a`, `\q`, `\p`, `\pbo`, `\fe` | The parsed value already equals the active Style or preceding effective state, with no relevant transform dependency | The tag restores a value after visible text, changes the active state, or supplies a transform start field |
| Superseded before the same rendering boundary | Static Style-state tags such as `\fs`, `\fscx`, `\c`, `\alpha`, `\bord`, `\shad` | A later deterministic write completely replaces every field written by the earlier tag before any text uses it | Text occurs between the writes, only part of a compound field is replaced, or a transform may read the earlier value |
| Color channel fully transparent | `\c`/`\1c`, `\2c`, `\3c`, `\4c` | The corresponding `\alpha` or `\1a`–`\4a` value is provably `&HFF&` for the color write's complete lifetime | Opacity returns before replacement/reset, or a transform may modify that channel's color or alpha |
| Outline or shadow geometry absent | `\3c`, `\3a`; `\4c`, `\4a` | Both outline axes or both shadow axes stay zero for the write's complete lifetime, including BorderStyle 1 and 3 | Either axis becomes nonzero while the write is active, or a relevant transform or uncertain syntax can make the effect observable |
| Entire Dialogue row always transparent | The complete Dialogue event | `remove_transparent_dialogues` is enabled, all four channels are provably `&HFF&` at every rendered text/drawing span, no alpha transform can restore visibility, and deleting the event cannot alter collision placement | The option is disabled, any span can become visible, syntax is opaque, or an unpositioned same-layer collision group also contains a visible event |
| First-wins line property | `\pos`/`\move`, `\org`, `\fad`/`\fade`, `\an`/`\a` | A later valid tag belongs to a family whose first valid occurrence already determines the line; a first alignment equal to Style can also disappear | The first effective geometry/fade tag remains; malformed forms create a conservative boundary |
| Repeated Style reset | `\r`, `\rStyleName` | The same reset target repeats before text and the later reset changes no effective field | The reset selects a different Style, clears intervening changes, or occurs after rendered text |
| Repeated-target or temporally inactive transform | `\t(...)` | A supported instantaneous write repeats a settled target; a constant-result or future-time proof makes a modifier redundant; event-wide layout flags must remain | Nonzero-duration same-value interpolation without a native constant-result proof, dynamic/unknown starting state, different targets, unresolved overlap/backward times, or removal of the last unpositioned transform |
| Unknown to both target renderers | Unknown override tags, including safely separable modifiers inside `\t` | `clean_unknown_tags` is enabled and the token is confirmed outside both compatibility sets | Either renderer supports it, or its syntax/location cannot be isolated safely |
| Karaoke timing | `\k`, `\K`, `\kf`, `\ko`, `\kt` | Not removed merely because the numeric value looks ineffective | Preserved as timing/interpretation state; values may still be normalized and tags may be compatibility-safely reordered |
| Clip and drawing data | `\clip`, `\iclip`, `\p` drawing paths | Redundant numeric zeroes and drawing token spelling are normalized; an unused clip is not deleted solely from geometry guesses | Clip semantics or drawing visibility cannot be proved from static field equality |
| Event margins with `\pos` | `MarginL`, `MarginR`, `MarginV` on Dialogue and Comment rows | `MarginV` is inert with an effective `\pos`; left and right margins are also inert with final `WrapStyle=2` or provably pure drawing content | Ordinary wrapped text under `WrapStyle=0/1/3`, `\move`, non-empty Effect, malformed or ambiguous syntax |
| Override blocks emptied by cleanup or adjacent override blocks | `{\redundant}`, `{\tag1}{\tag2}` | Empty blocks without a required rendering boundary are removed; adjacent parseable blocks with no intervening content can merge | Between two nonempty spans, an emptied block remains as `{}` to preserve drawing objects and text shaping; comments and uncertain syntax also retain boundaries |

“Removed” in this table always means that libass and xy-VSFilter keep their respective effective tag state. It does not mean that every textual duplicate is deleted.

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

### Deletion scope, bare tags, and relative font sizes

Cleanup combines forward state propagation with backward read/write analysis. A later complete assignment can remove an earlier group, including a relative operation or a malformed setter, before any text reads it. Analysis restarts after a change and stops at a fixed point; it does not use a fixed number of cleanup rounds.

`clean_unknown_tags` also controls proven-ineffective malformed known tags. It does **not** mean “delete anything that fails parsing.” When disabled, malformed tokens and connected writes to the same fields are protected; unrelated fields can still be cleaned. Legal bare tags and relative `\fs` are independent of this option. With Style font size `24`, outline `2`, and spacing `0`:

| Input | Option enabled | Option disabled |
| --- | --- | --- |
| `{\bord4\bordgarbage\bord4}X` | `{\bord4}X` | unchanged |
| `{\fs40\fsbad\fs40}X` | `{\fs40}X` | unchanged |
| `{\fsp5}{\fsp}{\fsp5}X` | `{\fsp5}X` | `{\fsp5}X` |
| `{\fs40\fs+10\fs40}X` | `{\fs40}X` | `{\fs40}X` |

Bare forms are categorized individually, including empty parentheses, use inside transforms, and named resets:

| Family | Empty-argument behavior / dependency |
| --- | --- |
| Font, scale, spacing, Z rotation, border/shadow, color/alpha, font flags/encoding | Native Style fallback; libass uses the active reset Style, while xy-VSFilter uses the event's original Style |
| `\frx`, `\fry`, `\fax`, `\fay`, `\blur`, `\be`, `\p`, `\pbo` | Fixed zero fallback |
| `\q` | Script Info `WrapStyle` fallback |
| `\an`, `\a` | Alignment fallback plus first-wins state |
| `\r` | Original event Style reset, including cancellation of preceding animated field state |
| `\k`, `\K`, `\kf`, `\ko`; `\kt` | Karaoke timeline effects; bare duration is 100 cs, or zero for `\kt` |
| Position/origin, fade, clip, and `\t` | Dedicated argument parsing and structural/layout effects; not treated as empty no-ops |

For example, after `\rAlt`, a bare `\fs` need not produce the same size in both renderers. The cleaner tracks the two fallback states separately.

An immediate sign in `\fs+n` or `\fs-n` applies a factor `1+n/10` or `1-n/10` to the current size. Other numeric tags do not use this formula. `\fs +1` is not the relative spelling `\fs+1`; the separating whitespace is preserved.

By default optimization deletes existing writes rather than inventing replacement tags. One exception allows a static chain of at least two font-size writes to become one existing absolute `\fs`, when both native operation orders produce the same positive integer. Intermediate values are never rounded:

| Chain | Result |
| --- | --- |
| `\fs40\fs+1` | `\fs44` |
| `\fs40\fs-1` | `\fs36` |
| `\fs40\fs+10` | `\fs80` |
| `\fs33\fs+1` | Retain the fractional-result chain |
| `\fs40\fs+1\fs-1` | `\fs44\fs-1`; only the integer prefix folds, and the final result remains 39.6 |
| `\fs33\fs+1\fs40` | `\fs40` by complete overwrite |

These examples assume the initial size differs from the required final size. If the entire chain restores the already effective state, deleting it is preferred. Chains do not fold across rendered content, resets, relevant animation, or unknown writes. Reports count integer-size folding separately from the actual number of tags deleted.

Rendering equivalence, a modeled cleanup fixed point, and minimality are separate results. Native frame PASS and idempotence alone do not prove minimality. Development audits enumerate permitted tag/modifier deletion subsets and integer-font candidates; unsupported syntax without a proof remains **minimality unproven**, not “fully minimized.” Necessary `{}` boundaries and animation/collision effects are never counted as redundant tags.

### Complex state examples

The following examples assume that Style `Default` has `ScaleX=100`, a white primary color, primary alpha `&H80&`, `Outline=2`, and `Shadow=0`. Style `Alt` has `Fontsize=40`, `ScaleX=80`, `ScaleY=100`, bold enabled, primary color `&HFF0000&`, `Outline=3`, and `Shadow=1`.

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

### Fully transparent color channels

Secondary color/alpha writes (`\2c`, `\2a`) can also be removed when no karaoke timeline can read them. A global `\alpha` is removed only when all of its channel writes are either unobservable or overwritten before use; the program does not replace it with newly synthesized channel tags. Entirely hidden content may shed paint fields, while font/geometry fields also require independent placement before their collision effects can be disregarded.

A `\c` / `\1c`–`\4c` write is removed when its corresponding `\alpha` or `\1a`–`\4a` channel is provably `&HFF&` for the write's complete lifetime. The analysis follows later text, channel-alpha overrides, replacement colors, and `\r` instead of looking only inside one override block. For example:

```ass
{\1a&HFF&\1c&H112233&}A{\1c&H445566&\1a&H00&}B
```

becomes:

```ass
{\1a&HFF&}A{\1c&H445566&\1a&H00&}B
```

The first color is invisible while active and is replaced before primary opacity returns. Without that replacement, the color must remain because it becomes visible on `B`:

```ass
{\1a&HFF&\1c&H112233&}A{\1a&H00&}B
```

The four channels are analyzed independently. A transform that may modify the channel's color or alpha protects it; a fully parsed transform affecting only unrelated fields does not. Line fades cannot make a statically `&HFF&` channel visible. Malformed syntax, an opaque transform, or an unknown transform field disables this optimization conservatively.

### Color and alpha writes for absent outline or shadow

libass and xy-VSFilter both leave outline color/alpha unobservable while `\xbord` and `\ybord` are zero, and leave back color/alpha unobservable while `\xshad` and `\yshad` are zero. This has been verified with both BorderStyle 1 and BorderStyle 3. The program therefore removes explicit `\3c`/`\3a` or `\4c`/`\4a` writes only when the corresponding geometry remains absent for that write's complete lifetime:

```ass
{\bord0\3c&H112233&\3a&H80&}A
```

becomes:

```ass
{\bord0}A
```

If either outline axis becomes nonzero before the color or alpha is overwritten or reset, the writes are observable and remain unchanged:

```ass
{\bord0\3c&H112233&\3a&H80&}A{\xbord2}B
```

Replacement is tracked independently for color and alpha. A global `\alpha` write replaces the prior `\3a`/`\4a` state, but is never removed merely because one effect has zero geometry because it also controls the other channels:

```ass
{\shad0\4a&H80&}A{\alpha&H20&\shad2}B
```

becomes:

```ass
{\shad0}A{\alpha&H20&\shad2}B
```

Style resets, compound and axis-specific geometry, later text spans, and relevant transforms all participate in the lifetime proof. A transform limited to channel color/alpha cannot create missing geometry and does not block removal; a transform that may make an outline or shadow axis nonzero does. Unknown geometry or an opaque dependency retains the candidate tag.

### Always-transparent Dialogue rows

This feature is disabled by default. Enable **Remove always-transparent Dialogue rows when collision-safe** in the GUI, set `"remove_transparent_dialogues": true` in JSON, or use:

```powershell
python ass_clean_redundant_tags.py clean "input.ass" --remove-transparent-dialogues
```

When enabled, a complete Dialogue row is removed only if every rendered text or drawing span is provably fully transparent in all four channels. `\alpha&HFF&` and four equivalent `\1a`–`\4a` writes are both recognized; later overrides, `\r`, and alpha transforms are followed before making the decision.

Invisible events can still occupy a collision box. In both libass and xy-VSFilter, an unpositioned transparent event placed before an overlapping visible event on the same layer can move the visible subtitle. The cleaner therefore removes an always-transparent row only when one of these layout conditions is also proven:

- The event has an effective `\pos` or `\move`, so it does not participate in normal collision placement.
- Its complete same-layer, unpositioned, time-overlapping collision component contains only always-transparent events.
- No same-layer unpositioned event overlaps it, which is the single-event form of the previous rule.

An overlapping visible event on another layer does not block removal. A same-layer visible event in the collision component does block removal even though the candidate row itself produces no pixels. Unknown or malformed syntax, renderer-specific HTML state, and any transform that may modify `\alpha` or `\1a`–`\4a` also keep the row.

Brace comments, malformed arguments, unparseable blocks, and renderer-specific HTML state form conservative boundaries.

### Tags unknown to both renderers

Removal of proven-ineffective unknown or malformed override tokens is enabled by default. Use `--no-clean-unknown-tags` to retain those tokens and their related field writes:

```ass
{\zunknown1\fs60}Text  →  {\fs60}Text
```

Unknown tags inside a simple `\t(...)` modifier can also be removed. If that leaves an empty transform, its event-wide animation and collision effects are still checked before deleting it.

This option does not remove:

- An effective tag supported by either libass or xy-VSFilter
- A malformed known setter whose effect is not proven dead or redundant
- Backslash-like text inside a block known to be a brace comment
- Content inside a complex transform that cannot be split safely

ASS tag names have no independent terminator. A token beginning with a known short tag is analyzed as that setter, not as an inert unknown name. For example, `\unknown` begins with `\u` and is not removed merely because more letters follow. A later complete overwrite may still prove it unnecessary.

An unknown outer tag containing backslashes, such as `\z(abc(\b1)\i1)`, is normally retained because a renderer may execute an inner tag. A narrowly recognized inner flag group can be removed only when execution and native Style fallback are both proven no-ops. Unbalanced or otherwise unmodeled syntax remains protected. Verified simple scalar prefixes can establish native values without rewriting their malformed spelling; other uncertain setters invalidate affected values. Color/alpha parameters with leading whitespace, such as `\c &HFF&`, are not treated as their whitespace-free equivalents. Numeric normalization never rounds a long decimal coefficient or silently strips unverified Unicode whitespace.

“Unknown to both” refers only to the libass and xy-VSFilter compatibility sets maintained by this program. Other renderers, Aegisub automation scripts, or future versions may use extension tags. Disable this option when such tags must be preserved. Reports distinguish unknown tags actually removed from unknown tokens retained conservatively.

## Numeric and drawing normalization

Numeric syntax uses ASCII digits. Fullwidth/Arabic digits and unverified Unicode whitespace are not converted into valid parameters. Unicode separators inside event text remain text, not ASS line endings; ambiguous Style numbers or Script Info `WrapStyle` do not establish known cleanup state.

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

`\be` retains its fractional value: `\be1.600` becomes `\be1.6`, never `\be1`. This also applies inside transforms, where changing the target before interpolation can change rendering. Nonstandard bold/italic/underline/strikeout values are analyzed using their Style fallback rather than treating every nonzero value as enabled.

Adjacent drawing commands are converted to standard space-separated form:

```ass
m100.00 100.00l200.00 200.00
```

becomes:

```ass
m 100 100 l 200 200
```

Fragments containing unknown drawing tokens, uppercase drawing commands, comma-separated coordinates, indeterminate `\p` arguments, or complex nested expressions are retained unchanged rather than converted into potentially different geometry.

## Transform safety boundaries

Simple `\t` expressions are analyzed field by field:

- A same-value target alone is insufficient: interpolation, integer quantization, animation flags, and collision layout can still change output
- A supported later instantaneous transform at a nonzero time can be removed when its target is already settled and no intervening write changes it
- Empty transforms and supported interpolation entirely after the event end can be removed only when their event-wide effects are also preserved
- Fields modified by a transform protect static state that actually changes the animation start; a preceding write equal to the current effective state can still be removed
- Static redundant tags unrelated to transform fields can still be removed independently
- Nested transforms and complex parenthesized content are not semantically decomposed. Relative font-size animation is retained unless a dedicated no-op or complete-overwrite proof applies; for example, a proven `\fs+0` modifier can be removed. Independently recognizable numbers are still normalized, and unknown modifiers are removed only when `--clean-unknown-tags` is enabled and the content can be split safely

The last original transform is retained on an unpositioned event to preserve animation/collision behavior. Even `\t()` can change the placement of overlapping events. An empty transform on an unambiguously positioned event can be removed:

```ass
{\pos(100,100)\t()}Text
```

This becomes:

```ass
{\pos(100,100)}Text
```

Assume the active Style has `ScaleX=100` and `ScaleY=100`. `\fscy100` is unrelated to the following transform and can be removed:

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

Frame-sampled exports may keep writing the same settled target. Here the first `\1a&H00&` at 250 ms is effective, while the later identical instantaneous writes are redundant:

```ass
{\1a&HFF&\t(42,42,\1a&HCC&)\t(83,83,\1a&H9A&)\t(250,250,\1a&H00&)\t(292,292,\1a&H00&)\t(334,334,\1a&H00&)}Text
```

This becomes:

```ass
{\1a&HFF&\t(42,42,\1a&HCC&)\t(83,83,\1a&H9A&)\t(250,250,\1a&H00&)}Text
```

This timeline proof accepts fully parsed absolute numeric/color/alpha modifiers with explicit start/end times. Only a later instantaneous interval with equal, nonzero start/end times is deleted as a repeated target. Nonzero-duration repetitions require a separate interpolation/quantization proof; mathematical equality alone is insufficient. End time `0` means the event duration, so `\t(0,0,...)` is not instantaneous. Font names, bold, italic, underline, strikeout, encoding, `\be`, and the immediate compatibility reset `\fsc` do not participate in this timeline deletion proof. Returning to a Style value after an earlier animation is a real change; a reset after a transform also remains when it cancels animated state.

Separate constant-result proofs cover exact binary interpolation (using libass's actual scale units) and stable native `\be` quantization. For example, `{\fs32\t(0,333,\fs32)}Text` becomes `{\fs32\t(0,333,)}Text`; the last transform still carries layout flags. An ineffective future modifier can likewise be removed while retaining its parent: with duration `1000 ms`, `{\t(1000,2000,\fscx200)}Text` becomes `{\t(1000,2000,)}Text`. Unknown or non-finite starting values do not qualify for that future-time proof.

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
{\rAlt\t(0,500,\fs+10\fscy150)}A
```

The first transform begins at the event end and cannot affect an active frame, so it is removed. The second uses relative `\fs+10`, which depends on the current font size. The preceding `\fs40`, `\fscx80`, `\fscy100`, and `\bord3` equal Style `Alt` and can be removed without changing the animation start. The independent `\shad1` also equals Style `Alt` and is removed.

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
- Their complete read/write field sets are independent, or simulating both orders proves an overlapping deterministic write equivalent
- First-wins, reset, drawing, karaoke-timeline, and transform dependencies remain intact
- They do not cross a retained unknown tag, brace comment, malformed argument, or complex expression
- `\clip` / `\iclip` cross a simple transform only when its parsed modifier fields do not include clipping; duplicate clip-family tags are not reordered

Long vector clips therefore move toward the end of a locally commutative segment when safe, but never cross a boundary whose equivalence cannot be proven.

Within one override block, karaoke may cross static Style tags, `\r`, `\p`/`\pbo`, and a fully parsed simple `\t` to reach the order above. libass and xy-VSFilter preserve the same syllable timeline and use the same final static or animated state in these spellings. Karaoke tags still keep their order relative to other karaoke tags and do not cross an opaque or complex transform.

Overlapping deterministic Style writes are not rejected solely because their field names intersect. The reorderer simulates both orders and permits the exchange only when the complete resulting state is identical. Different reset values, global versus channel alpha, compound versus axis border/shadow, and `\fsc` versus explicit axis scaling therefore remain ordered whenever swapping would change effective state.

The undocumented `\fsc` compatibility reset never crosses `\r`: xy-VSFilter produces different pixels for the two orders even when their abstract active-Style end state appears identical.

For example:

```ass
{\fnExampleFont\an4\fs26\c&H000000&\blur1.5\1a&H78&\pos(119.66,201.29)}Text
```

with a black primary color in the active Style can become:

```ass
{\an4\fnExampleFont\fs26\1a&H78&\blur1.5\pos(119.66,201.29)}Text
```

## Positioned event-margin cleanup

`clean_pos_margins` is enabled by default and can be disabled with `--no-clean-pos-margins`. It processes both Dialogue and Comment events. Comment rows use the same conservative rules so their layout is preserved if they are later converted back to Dialogue.

The program first resolves the first effective position-family tag. Cleanup proceeds only when that tag is a valid `\pos`; `\move`, malformed position syntax, a non-empty Effect field, and ambiguous override structure leave every Margin unchanged.

For a safely positioned event:

- `MarginV` is set to `0`.
- `MarginL` and `MarginR` are set to `0` only when the final effective wrap mode is `2`, or every renderable span is provably in `\p>0` drawing mode.
- The wrap mode starts at `[Script Info]` `WrapStyle` and follows the last strict `\q0`–`\q3`. Ambiguous or malformed `\q` syntax keeps the horizontal margins.
- Ordinary text under wrap modes `0`, `1`, and `3` keeps its horizontal margins because they can still change automatic line wrapping.

Nested or uncertain writes to drawing/wrap state, including `\t(\p0)` and `\t(\q0)`, prevent a pure-drawing/no-wrap proof. Horizontal margins remain in that situation; uncertain positioning preserves every margin.

Only the event-field values are rewritten; ASS/SSA CSV fields are not removed. Margin cleanup runs after text/tag cleanup and before consecutive-line merging, so rows that become fully identical can then be merged. Audit reports list each changed field, for example `MarginV: 0160 → 0`. Comment-only changes are recorded in the report but do not create actual-rendering comparison frame ranges.

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
- Before/after text when an event's Text field changed
- Event-field changes such as cleared Margin values
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

Inputs remain blank in the example; relative subtitle-output and report folders are preconfigured:

```json
{
  "inputs": [],
  "output": null,
  "output_dir": "cleanoutput",
  "in_place": false,
  "safe_reorder": true,
  "merge_lines": true,
  "clean_pos_margins": true,
  "clean_comments": false,
  "clean_unknown_tags": true,
  "clean_extradata_references": true,
  "clean_project_garbage": true,
  "clean_extradata": true,
  "report": null,
  "report_dir": "cleanoutput",
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
| `output_dir` | Batch output directory; the example uses the relative `cleanoutput` folder |
| `in_place` | Replace source subtitles |
| `backup` | Create `.bak` files during in-place replacement |
| `recursive` | Recursively scan input directories |
| `safe_reorder` | Apply compatibility-safe reordering |
| `merge_lines` | Merge consecutive identical static Dialogue rows |
| `clean_pos_margins` | Clear event margins proven ineffective on valid `\pos` Dialogue and Comment rows; default: `true` |
| `clean_comments` | Remove Comment event rows from `[Events]`; default: `false` |
| `remove_transparent_dialogues` | Remove always-transparent Dialogue rows when collision-safe; default: `false` |
| `clean_unknown_tags` | Remove proven-ineffective unknown or malformed override tags; default: `true` |
| `clean_extradata_references` | Remove leading event extradata references |
| `clean_project_garbage` | Remove `[Aegisub Project Garbage]` |
| `clean_extradata` | Remove `[Aegisub Extradata]` and associated references |
| `report` | Single-file report path; `null` when unused |
| `report_dir` | Batch report directory; relative paths follow the same input-relative rule as `output_dir` |
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

Relative `output_dir` and `report_dir` use the same input-relative rules described above. With both set to `cleanoutput`, each cleaned subtitle and its report are written together under a `cleanoutput` folder beside an individually selected file or under the selected input directory, while preserving scanned subdirectories. Relative single-file `output`/`report` paths and external-tool paths are resolved from the current working directory. `output`, `output_dir`, and `in_place: true` are mutually exclusive; `report` and `report_dir` are also mutually exclusive.

Save the effective settings produced by merging command-line arguments and JSON:

```powershell
python ass_clean_redundant_tags.py clean input.ass `
  --settings settings.json `
  --safe-reorder `
  --save-settings effective-settings.json
```

**Save as JSON settings…** in the GUI saves the current options. The dialog lets you include or omit current input, subtitle-output, and report paths. When omitted, `inputs` is empty and `output`, `output_dir`, `report`, and `report_dir` are `null`, allowing those paths to be supplied later on the command line.

The automatically generated `ass_clean_redundant_tags.config.json` beside the script stores local GUI state only. It is separate from portable task settings loaded with `--settings`.
