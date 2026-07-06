#!/usr/bin/env python3
"""
Normalize ASS/SSA subtitle font names to font family names.

The tool edits only:
  - [V4+ Styles] / [V4 Styles] Style: Fontname fields
  - Dialogue/Comment override blocks containing \fn...

Before writing the changed subtitle, it verifies that each renamed font resolves
to the same installed font face. Optional pixel modes can additionally render
the original and candidate files with ffmpeg/libass.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import warnings
from collections import defaultdict
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Iterable

try:
    from fontTools.ttLib import TTCollection, TTFont
except ImportError:  # pragma: no cover - handled by main()
    TTCollection = None
    TTFont = None

logging.getLogger("fontTools").setLevel(logging.ERROR)
logging.getLogger("fontTools.ttLib").setLevel(logging.ERROR)
logging.getLogger("fontTools.ttLib.tables._n_a_m_e").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", message=r".*name.*table.*stringOffset.*incorrect.*")


STYLE_FIELDS = [
    "Name",
    "Fontname",
    "Fontsize",
    "PrimaryColour",
    "SecondaryColour",
    "OutlineColour",
    "BackColour",
    "Bold",
    "Italic",
    "Underline",
    "StrikeOut",
    "ScaleX",
    "ScaleY",
    "Spacing",
    "Angle",
    "BorderStyle",
    "Outline",
    "Shadow",
    "Alignment",
    "MarginL",
    "MarginR",
    "MarginV",
    "Encoding",
]

EVENT_FIELDS = [
    "Layer",
    "Start",
    "End",
    "Style",
    "Name",
    "MarginL",
    "MarginR",
    "MarginV",
    "Effect",
    "Text",
]

FONT_EXTENSIONS = {".ttf", ".otf", ".ttc", ".otc"}
NAME_IDS_FOR_ALIASES = {1, 4, 6, 16, 21}
NAME_IDS_FOR_SUBFAMILY = {2, 17, 22}
ALL_NAME_IDS = NAME_IDS_FOR_ALIASES | NAME_IDS_FOR_SUBFAMILY


@dataclass
class LookupResult:
    status: str
    new_font: str
    note: str = ""
    same_face: bool = False


@dataclass
class Replacement:
    line_no: int
    section: str
    entry_type: str
    occurrence: int
    rendered: str
    old_font: str
    new_font: str
    status: str
    same_face: bool = False
    style_name: str = ""
    note: str = ""


@dataclass
class FontFace:
    path: Path
    index: int
    face_id: str
    default_family: str
    aliases: list[tuple[str, str]]
    alias_keys: set[str] = field(default_factory=set)


@dataclass
class VerificationResult:
    passed: bool
    method: str = ""
    skipped: bool = False
    frame_count: int = 0
    fps: str = ""
    fps_source: str = ""
    backgrounds: dict[str, tuple[str, str]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class FontCatalog:
    def __init__(self) -> None:
        self._aliases: dict[str, set[str]] = defaultdict(set)
        self._alias_faces: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))

    def add_alias(self, alias: str, family: str, face_id: str = "") -> None:
        alias = clean_font_name(alias)
        family = clean_font_name(family)
        if not alias or not family:
            return
        alias_key = font_key(alias)
        self._aliases[alias_key].add(family)
        if face_id:
            self._alias_faces[alias_key][font_key(family)].add(face_id)

    def add_face(self, face: FontFace) -> None:
        for alias, family in face.aliases:
            self.add_alias(alias, family, face.face_id)

    def same_face_aliases(self, old_font: str, new_font: str) -> bool:
        old_key = font_key(old_font)
        new_key = font_key(new_font)
        old_faces = set()
        for faces in self._alias_faces.get(old_key, {}).values():
            old_faces.update(faces)
        new_faces = set()
        for faces in self._alias_faces.get(new_key, {}).values():
            new_faces.update(faces)
        return bool(old_faces and new_faces and old_faces.intersection(new_faces))

    def _result(self, status: str, old_font: str, new_font: str, note: str = "") -> LookupResult:
        same_face = status in {"changed", "unchanged"} and self.same_face_aliases(old_font, new_font)
        if status == "changed" and same_face:
            note = join_notes(note, "same font face verified")
        return LookupResult(status, new_font, note, same_face)

    def lookup(self, font_name: str) -> LookupResult:
        original = font_name
        cleaned = clean_font_name(font_name)
        if not cleaned:
            return LookupResult("empty", original, "empty font name", False)

        vertical = cleaned.startswith("@")
        key = font_key(cleaned)
        families = self._aliases.get(key)
        if not families:
            return LookupResult("unresolved", original, "font was not found in scanned fonts", False)

        normalized = {font_key(name): name for name in families}
        if len(normalized) > 1:
            preferred = choose_english_family(families)
            if preferred:
                preferred = preserve_vertical_prefix(preferred, vertical)
                if same_font_spelling(cleaned, preferred):
                    return self._result("unchanged", cleaned, cleaned)
                note = "resolved by preferring an English family name"
                if font_key(cleaned) == font_key(preferred):
                    note = join_notes(note, "case corrected to canonical family name")
                return self._result("changed", cleaned, preferred, note)

            old_key = font_key(cleaned)
            if old_key in normalized:
                family = normalized[old_key]
                family = preserve_vertical_prefix(family, vertical)
                if same_font_spelling(cleaned, family):
                    return self._result("unchanged", cleaned, cleaned)
                note = "case corrected to canonical family name" if font_key(cleaned) == font_key(family) else ""
                return self._result("changed", cleaned, family, note)
            choices = ", ".join(sorted(families))
            return LookupResult("ambiguous", original, f"alias maps to multiple families: {choices}", False)

        family = next(iter(families))
        family = preserve_vertical_prefix(family, vertical)
        if same_font_spelling(cleaned, family):
            return self._result("unchanged", cleaned, cleaned)
        note = "case corrected to canonical family name" if font_key(cleaned) == font_key(family) else ""
        return self._result("changed", cleaned, family, note)


def join_notes(*parts: str) -> str:
    return "; ".join(part for part in parts if part)


def same_font_spelling(left: str, right: str) -> bool:
    return clean_font_name(left) == clean_font_name(right)


def choose_english_family(families: set[str]) -> str:
    matches = [family for family in families if not has_cjk(family)]
    return matches[0] if len(matches) == 1 else ""


def has_cjk(value: str) -> bool:
    return any(
        "\u3400" <= char <= "\u4dbf"
        or "\u4e00" <= char <= "\u9fff"
        or "\uf900" <= char <= "\ufaff"
        for char in value
    )


def clean_font_name(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    value = value.replace("\x00", "")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def font_key(value: str) -> str:
    value = clean_font_name(value)
    if value.startswith("@"):
        value = value[1:].strip()
    return value.casefold()


def preserve_vertical_prefix(family: str, vertical: bool) -> str:
    family = clean_font_name(family)
    if vertical and family and not family.startswith("@"):
        return "@" + family
    return family


def default_font_dirs(extra_dirs: Iterable[Path]) -> list[Path]:
    dirs: list[Path] = []
    windir = os.environ.get("WINDIR")
    if windir:
        dirs.append(Path(windir) / "Fonts")
    localappdata = os.environ.get("LOCALAPPDATA")
    if localappdata:
        dirs.append(Path(localappdata) / "Microsoft" / "Windows" / "Fonts")

    home = Path.home()
    dirs.extend(
        [
            Path("/Library/Fonts"),
            Path("/System/Library/Fonts"),
            home / "Library" / "Fonts",
            Path("/usr/share/fonts"),
            Path("/usr/local/share/fonts"),
            home / ".fonts",
            home / ".local" / "share" / "fonts",
        ]
    )
    dirs.extend(extra_dirs)

    seen: set[Path] = set()
    unique: list[Path] = []
    for item in dirs:
        try:
            resolved = item.resolve()
        except OSError:
            resolved = item
        if resolved not in seen and item.is_dir():
            seen.add(resolved)
            unique.append(item)
    return unique


def iter_font_files(font_dirs: Iterable[Path]) -> dict[Path, set[str]]:
    files: dict[Path, set[str]] = defaultdict(set)
    for directory in font_dirs:
        try:
            iterator = directory.rglob("*")
            for path in iterator:
                if path.is_file() and path.suffix.lower() in FONT_EXTENSIONS:
                    files[path.resolve()].add("")
        except OSError:
            continue

    for alias, path in registry_font_entries():
        if path and path.is_file() and path.suffix.lower() in FONT_EXTENSIONS:
            files[path.resolve()].add(alias)
    return files


def registry_font_entries() -> list[tuple[str, Path]]:
    if os.name != "nt":
        return []
    try:
        import winreg
    except ImportError:
        return []

    windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    reg_paths = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
    ]
    entries: list[tuple[str, Path]] = []

    for hkey, reg_path in reg_paths:
        try:
            key = winreg.OpenKey(hkey, reg_path)
        except OSError:
            continue
        try:
            index = 0
            while True:
                try:
                    raw_name, raw_value, _ = winreg.EnumValue(key, index)
                except OSError:
                    break
                index += 1

                value = str(raw_value)
                path = Path(value)
                if not path.is_absolute():
                    path = windir / "Fonts" / value

                for alias in split_registry_aliases(raw_name):
                    entries.append((alias, path))
        finally:
            winreg.CloseKey(key)
    return entries


def split_registry_aliases(raw_name: str) -> list[str]:
    name = re.sub(r"\s*\([^)]*\)\s*$", "", raw_name).strip()
    if not name:
        return []
    parts = [part.strip() for part in name.split(" & ") if part.strip()]
    return parts or [name]


def build_font_catalog(extra_dirs: Iterable[Path], prefer_typographic: bool) -> FontCatalog:
    if TTFont is None:
        raise RuntimeError("fontTools is required. Install it with: python -m pip install fonttools")

    catalog = FontCatalog()
    font_files = iter_font_files(default_font_dirs(extra_dirs))
    for path, registry_aliases in font_files.items():
        with quiet_fonttools_output():
            faces = read_font_faces(path, prefer_typographic)
        for face in faces:
            catalog.add_face(face)

        for registry_alias in registry_aliases:
            if not registry_alias:
                continue
            registry_key = font_key(registry_alias)
            matched = [face for face in faces if registry_key in face.alias_keys]
            if len(matched) == 1:
                catalog.add_alias(registry_alias, matched[0].default_family, matched[0].face_id)
            elif len(faces) == 1:
                catalog.add_alias(registry_alias, faces[0].default_family, faces[0].face_id)

    return catalog


@contextlib.contextmanager
def quiet_fonttools_output():
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        yield


def read_font_faces(path: Path, prefer_typographic: bool) -> list[FontFace]:
    try:
        if path.suffix.lower() in {".ttc", ".otc"}:
            collection = TTCollection(str(path), lazy=True)
            try:
                return [
                    face
                    for index, font in enumerate(collection.fonts)
                    for face in [read_single_font_face(path, index, font, prefer_typographic)]
                    if face is not None
                ]
            finally:
                for font in collection.fonts:
                    close_font(font)
        font = TTFont(str(path), lazy=True, fontNumber=0)
        try:
            face = read_single_font_face(path, 0, font, prefer_typographic)
            return [face] if face else []
        finally:
            close_font(font)
    except Exception:
        return []


def close_font(font) -> None:
    close = getattr(font, "close", None)
    if close:
        close()


def read_single_font_face(path: Path, index: int, font, prefer_typographic: bool) -> FontFace | None:
    if "name" not in font:
        return None

    records: list[tuple[int, tuple[int, int, int], str]] = []
    for item in font["name"].names:
        if item.nameID not in ALL_NAME_IDS:
            continue
        try:
            text = clean_font_name(item.toUnicode())
        except Exception:
            continue
        if text:
            records.append((item.nameID, (item.platformID, item.platEncID, item.langID), text))

    if not records:
        return None

    by_locale: dict[tuple[int, int, int], dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
    for name_id, locale, text in records:
        values = by_locale[locale][name_id]
        if text not in values:
            values.append(text)

    default_family = pick_default_family(by_locale, prefer_typographic)
    if not default_family:
        return None
    preferred_family = default_family if not has_cjk(default_family) else ""

    aliases: list[tuple[str, str]] = []
    seen_pairs: set[tuple[str, str]] = set()

    for locale, names_by_id in by_locale.items():
        family = preferred_family or pick_family_for_locale(names_by_id, prefer_typographic) or default_family
        for name_id in NAME_IDS_FOR_ALIASES:
            for alias in names_by_id.get(name_id, []):
                pair = (alias, family)
                if pair not in seen_pairs:
                    seen_pairs.add(pair)
                    aliases.append(pair)

        for family_id in family_name_id_order(prefer_typographic):
            for family_name in names_by_id.get(family_id, []):
                for subfamily_id in subfamily_name_id_order(prefer_typographic):
                    for subfamily in names_by_id.get(subfamily_id, []):
                        if subfamily.casefold() in {"regular", "normal", "book", "roman"}:
                            continue
                        alias = f"{family_name} {subfamily}"
                        pair = (alias, preferred_family or family_name)
                        if pair not in seen_pairs:
                            seen_pairs.add(pair)
                            aliases.append(pair)

    alias_keys = {font_key(alias) for alias, _ in aliases}
    face_id = f"{path.resolve().as_posix().casefold()}#{index}"
    return FontFace(
        path=path,
        index=index,
        face_id=face_id,
        default_family=default_family,
        aliases=aliases,
        alias_keys=alias_keys,
    )


def family_name_id_order(prefer_typographic: bool) -> list[int]:
    return [16, 21, 1] if prefer_typographic else [1, 16, 21]


def subfamily_name_id_order(prefer_typographic: bool) -> list[int]:
    return [17, 22, 2] if prefer_typographic else [2, 17, 22]


def pick_family_for_locale(names_by_id: dict[int, list[str]], prefer_typographic: bool) -> str:
    for name_id in family_name_id_order(prefer_typographic):
        values = names_by_id.get(name_id)
        if values:
            return values[0]
    return ""


def pick_default_family(
    by_locale: dict[tuple[int, int, int], dict[int, list[str]]],
    prefer_typographic: bool,
) -> str:
    locales = list(by_locale)

    def locale_rank(locale: tuple[int, int, int]) -> tuple[int, int]:
        platform, _, lang = locale
        if platform == 3 and lang == 0x0409:
            return (0, 0)
        if platform == 1 and lang == 0:
            return (1, 0)
        if platform == 3 and (lang & 0x00FF) == 0x09:
            return (2, lang)
        return (3, lang)

    for locale in sorted(locales, key=locale_rank):
        family = pick_family_for_locale(by_locale[locale], prefer_typographic)
        if family:
            return family
    return ""


def split_line_ending(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    if line.endswith("\r"):
        return line[:-1], "\r"
    return line, ""


def starts_ass_keyword(line: str, keyword: str) -> bool:
    return line.lstrip().casefold().startswith((keyword + ":").casefold())


def parse_format_line(line: str, fallback: list[str]) -> list[str]:
    _, _, rest = line.partition(":")
    fields = [field.strip() for field in rest.split(",") if field.strip()]
    return fields or fallback[:]


def field_index(fields: list[str], name: str, default: int) -> int:
    target = name.casefold()
    for index, field in enumerate(fields):
        if field.casefold() == target:
            return index
    return default


def split_ass_fields(content: str, field_count: int) -> list[str]:
    if field_count <= 1:
        return [content]
    parts = content.split(",", field_count - 1)
    if len(parts) < field_count:
        parts.extend([""] * (field_count - len(parts)))
    return parts


def replace_field_preserve_ws(raw: str, new_value: str) -> str:
    match = re.match(r"^(\s*)(.*?)(\s*)$", raw, flags=re.DOTALL)
    if not match:
        return new_value
    return f"{match.group(1)}{new_value}{match.group(3)}"


def process_subtitle_text(
    text: str,
    catalog: FontCatalog,
    include_comments: bool,
) -> tuple[str, list[Replacement], bool]:
    lines = text.splitlines(keepends=True)
    out_lines: list[str] = []
    replacements: list[Replacement] = []

    section = ""
    style_fields = STYLE_FIELDS[:]
    event_fields = EVENT_FIELDS[:]
    occurrence_by_line: dict[int, int] = defaultdict(int)
    unresolved = False

    for line_no, raw_line in enumerate(lines, 1):
        body, newline = split_line_ending(raw_line)
        stripped = body.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1].strip()
            out_lines.append(raw_line)
            continue

        new_body = body
        if section in {"V4+ Styles", "V4 Styles"}:
            if starts_ass_keyword(body, "Format"):
                style_fields = parse_format_line(body, STYLE_FIELDS)
            elif starts_ass_keyword(body, "Style"):
                new_body, rows, had_unresolved = process_style_line(
                    body, line_no, style_fields, catalog, occurrence_by_line
                )
                replacements.extend(rows)
                unresolved = unresolved or had_unresolved
        elif section == "Events":
            if starts_ass_keyword(body, "Format"):
                event_fields = parse_format_line(body, EVENT_FIELDS)
            elif starts_ass_keyword(body, "Dialogue") or starts_ass_keyword(body, "Comment"):
                is_comment = starts_ass_keyword(body, "Comment")
                if include_comments or not is_comment:
                    new_body, rows, had_unresolved = process_event_line(
                        body, line_no, event_fields, catalog, occurrence_by_line, is_comment
                    )
                    replacements.extend(rows)
                    unresolved = unresolved or had_unresolved

        out_lines.append(new_body + newline)

    return "".join(out_lines), replacements, unresolved


def process_style_line(
    body: str,
    line_no: int,
    style_fields: list[str],
    catalog: FontCatalog,
    occurrence_by_line: dict[int, int],
) -> tuple[str, list[Replacement], bool]:
    font_index = field_index(style_fields, "Fontname", 1)
    _, colon, content = body.partition(":")
    if not colon:
        return body, [], False

    parts = split_ass_fields(content, max(len(style_fields), font_index + 1))
    if font_index >= len(parts):
        return body, [], False

    name_index = field_index(style_fields, "Name", 0)
    style_name = clean_font_name(parts[name_index]) if name_index < len(parts) else ""
    old_font = clean_font_name(parts[font_index])
    result = catalog.lookup(old_font)
    occurrence_by_line[line_no] += 1
    row = Replacement(
        line_no=line_no,
        section="V4+ Styles",
        entry_type="Style Fontname",
        occurrence=occurrence_by_line[line_no],
        rendered="maybe",
        old_font=old_font,
        new_font=result.new_font,
        status=result.status,
        same_face=result.same_face,
        style_name=style_name,
        note=result.note,
    )

    unresolved = result.status in {"unresolved", "ambiguous"}
    if result.status == "changed":
        parts[font_index] = replace_field_preserve_ws(parts[font_index], result.new_font)
    return body.partition(":")[0] + ":" + ",".join(parts), [row], unresolved


OVERRIDE_BLOCK_RE = re.compile(r"\{[^{}]*\}")
FN_TAG_RE = re.compile(r"(\\fn)([^\\}]*)")


def process_event_line(
    body: str,
    line_no: int,
    event_fields: list[str],
    catalog: FontCatalog,
    occurrence_by_line: dict[int, int],
    is_comment: bool,
) -> tuple[str, list[Replacement], bool]:
    text_index = field_index(event_fields, "Text", 9)
    prefix, colon, content = body.partition(":")
    if not colon:
        return body, [], False

    parts = split_ass_fields(content, max(len(event_fields), text_index + 1))
    if text_index >= len(parts):
        return body, [], False

    style_index = field_index(event_fields, "Style", 3)
    style_name = clean_font_name(parts[style_index]) if style_index < len(parts) else ""
    rows: list[Replacement] = []
    unresolved = False
    rendered = "no" if is_comment else "yes"

    def replace_block(match: re.Match[str]) -> str:
        block = match.group(0)

        def replace_fn(fn_match: re.Match[str]) -> str:
            nonlocal unresolved
            tag, raw_name = fn_match.groups()
            old_font = clean_font_name(raw_name)
            if not old_font:
                return fn_match.group(0)

            result = catalog.lookup(old_font)
            occurrence_by_line[line_no] += 1
            rows.append(
                Replacement(
                    line_no=line_no,
                    section="Events",
                    entry_type="Comment \\fn" if is_comment else "Dialogue \\fn",
                    occurrence=occurrence_by_line[line_no],
                    rendered=rendered,
                    old_font=old_font,
                    new_font=result.new_font,
                    status=result.status,
                    same_face=result.same_face,
                    style_name=style_name,
                    note=result.note,
                )
            )
            unresolved = unresolved or result.status in {"unresolved", "ambiguous"}
            if result.status == "changed":
                return tag + result.new_font
            return fn_match.group(0)

        return FN_TAG_RE.sub(replace_fn, block)

    parts[text_index] = OVERRIDE_BLOCK_RE.sub(replace_block, parts[text_index])
    return prefix + ":" + ",".join(parts), rows, unresolved


def parse_ass_time(value: str) -> Fraction | None:
    match = re.match(r"\s*(\d+):(\d{1,2}):(\d{1,2})(?:[.](\d{1,3}))?\s*$", value)
    if not match:
        return None
    hours = int(match.group(1))
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    frac = match.group(4) or "0"
    centiseconds = int((frac + "00")[:2])
    total = ((hours * 60 + minutes) * 60 + seconds) * 100 + centiseconds
    return Fraction(total, 100)


def collect_dialogue_intervals(
    text: str,
    fps: Fraction,
    target_line_numbers: set[int] | None = None,
    target_styles: set[str] | None = None,
) -> list[tuple[int, int]]:
    section = ""
    event_fields = EVENT_FIELDS[:]
    ranges: list[tuple[int, int]] = []

    normalized_target_styles = {style.casefold() for style in target_styles or set()}
    for line_no, raw_line in enumerate(text.splitlines(), 1):
        body = raw_line.strip()
        if body.startswith("[") and body.endswith("]"):
            section = body[1:-1].strip()
            continue
        if section != "Events":
            continue
        if starts_ass_keyword(raw_line, "Format"):
            event_fields = parse_format_line(raw_line, EVENT_FIELDS)
            continue
        if not starts_ass_keyword(raw_line, "Dialogue"):
            continue

        start_index = field_index(event_fields, "Start", 1)
        end_index = field_index(event_fields, "End", 2)
        style_index = field_index(event_fields, "Style", 3)
        parts = split_ass_fields(raw_line.partition(":")[2], max(len(event_fields), end_index + 1))
        if end_index >= len(parts) or start_index >= len(parts):
            continue
        style_name = clean_font_name(parts[style_index]) if style_index < len(parts) else ""
        if target_line_numbers is not None or normalized_target_styles:
            line_matches = target_line_numbers is not None and line_no in target_line_numbers
            style_matches = style_name.casefold() in normalized_target_styles
            if not line_matches and not style_matches:
                continue

        start = parse_ass_time(parts[start_index])
        end = parse_ass_time(parts[end_index])
        if start is None or end is None or end <= start:
            continue

        first_frame = ceil_fraction(start * fps)
        end_frame = ceil_fraction(end * fps)
        if end_frame > first_frame:
            ranges.append((first_frame, end_frame))

    return merge_ranges(ranges)


def merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not ranges:
        return []
    ranges.sort()
    merged: list[tuple[int, int]] = [ranges[0]]
    for start, end in ranges[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def collect_dialogue_interval_index(
    text: str,
    fps: Fraction,
) -> tuple[dict[int, tuple[int, int]], dict[str, list[tuple[int, int]]]]:
    section = ""
    event_fields = EVENT_FIELDS[:]
    by_line: dict[int, tuple[int, int]] = {}
    by_style: dict[str, list[tuple[int, int]]] = defaultdict(list)

    for line_no, raw_line in enumerate(text.splitlines(), 1):
        body = raw_line.strip()
        if body.startswith("[") and body.endswith("]"):
            section = body[1:-1].strip()
            continue
        if section != "Events":
            continue
        if starts_ass_keyword(raw_line, "Format"):
            event_fields = parse_format_line(raw_line, EVENT_FIELDS)
            continue
        if not starts_ass_keyword(raw_line, "Dialogue"):
            continue

        start_index = field_index(event_fields, "Start", 1)
        end_index = field_index(event_fields, "End", 2)
        style_index = field_index(event_fields, "Style", 3)
        parts = split_ass_fields(raw_line.partition(":")[2], max(len(event_fields), end_index + 1))
        if end_index >= len(parts) or start_index >= len(parts):
            continue

        start = parse_ass_time(parts[start_index])
        end = parse_ass_time(parts[end_index])
        if start is None or end is None or end <= start:
            continue

        first_frame = ceil_fraction(start * fps)
        end_frame = ceil_fraction(end * fps)
        if end_frame <= first_frame:
            continue

        interval = (first_frame, end_frame)
        style_name = clean_font_name(parts[style_index]) if style_index < len(parts) else ""
        by_line[line_no] = interval
        by_style[style_name.casefold()].append(interval)

    return by_line, {style: merge_ranges(ranges) for style, ranges in by_style.items()}


def changed_font_pair_label(item: Replacement) -> str:
    return f"{item.old_font} -> {item.new_font}"


def collect_sample_ranges_by_font(
    text: str,
    fps: Fraction,
    replacements: list[Replacement],
    max_frames_per_font: int,
) -> tuple[list[tuple[int, int]], int, int, list[str]]:
    by_line, by_style = collect_dialogue_interval_index(text, fps)
    intervals_by_pair: dict[str, list[tuple[int, int]]] = defaultdict(list)
    all_pairs: set[str] = set()

    for item in replacements:
        if item.status != "changed":
            continue
        label = changed_font_pair_label(item)
        all_pairs.add(label)
        if item.entry_type == "Dialogue \\fn" and item.rendered == "yes":
            interval = by_line.get(item.line_no)
            if interval:
                intervals_by_pair[label].append(interval)
        elif item.entry_type == "Style Fontname" and item.style_name:
            intervals_by_pair[label].extend(by_style.get(item.style_name.casefold(), []))

    sampled_ranges: list[tuple[int, int]] = []
    covered_pairs = 0
    inactive_pairs: list[str] = []
    for label in sorted(all_pairs):
        pair_ranges = merge_ranges(intervals_by_pair.get(label, []))
        if not pair_ranges:
            inactive_pairs.append(label)
            continue
        covered_pairs += 1
        sampled_ranges.extend(sample_frame_ranges(pair_ranges, max_frames_per_font))

    return merge_ranges(sampled_ranges), covered_pairs, len(all_pairs), inactive_pairs


def ceil_fraction(value: Fraction) -> int:
    return -(-value.numerator // value.denominator)


def parse_script_resolution(text: str) -> tuple[int, int]:
    width = 1920
    height = 1080
    section = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            continue
        if section != "Script Info":
            continue
        key, _, value = line.partition(":")
        if key.strip().casefold() == "playresx":
            width = parse_int(value, width)
        elif key.strip().casefold() == "playresy":
            height = parse_int(value, height)
    return width, height


def parse_int(value: str, default: int) -> int:
    try:
        parsed = int(float(value.strip()))
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def parse_project_fps(text: str) -> tuple[Fraction | None, str]:
    section = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            continue
        if section not in {"Aegisub Project Garbage", "Script Info"}:
            continue
        key, _, value = line.partition(":")
        normalized_key = key.strip().casefold()
        if normalized_key in {"video fps", "videofps", "fps"}:
            fps = parse_fps(value.strip())
            if fps:
                return fps, key.strip()
    return None, ""


def parse_fps(value: str) -> Fraction | None:
    value = value.strip()
    if not value:
        return None
    try:
        if "/" in value:
            numerator, denominator = value.split("/", 1)
            fps = Fraction(int(numerator.strip()), int(denominator.strip()))
        else:
            fps = Fraction(value)
    except (ValueError, ZeroDivisionError):
        return None
    return fps if fps > 0 else None


def fps_to_ffmpeg_arg(fps: Fraction) -> str:
    if fps.denominator == 1:
        return str(fps.numerator)
    return f"{fps.numerator}/{fps.denominator}"


def seconds_decimal(value: Fraction) -> str:
    return f"{float(value):.9f}".rstrip("0").rstrip(".") or "0"


def find_ffmpeg(explicit: str | None) -> Path | None:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    script_dir = Path(__file__).resolve().parent
    candidates.append(script_dir / "ffmpeg.exe")
    found = shutil.which("ffmpeg")
    if found:
        candidates.append(Path(found))

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def ffmpeg_filter_path(path: Path) -> str:
    value = path.resolve().as_posix()
    value = value.replace("\\", "/")
    value = value.replace(":", r"\:")
    value = value.replace("'", r"\'")
    return f"'{value}'"


def ass_filter(ass_path: Path, fonts_dir: Path | None) -> str:
    value = "ass=" + ffmpeg_filter_path(ass_path)
    if fonts_dir:
        value += ":fontsdir=" + ffmpeg_filter_path(fonts_dir)
    return value


def render_hash(
    ffmpeg: Path,
    ass_path: Path,
    fonts_dir: Path | None,
    width: int,
    height: int,
    fps: Fraction,
    frame_ranges: list[tuple[int, int]],
    background: str,
) -> tuple[str, int]:
    digest = hashlib.sha256()
    total_frames = 0
    fps_arg = fps_to_ffmpeg_arg(fps)

    for start_frame, end_frame in frame_ranges:
        frames = end_frame - start_frame
        if frames <= 0:
            continue
        total_frames += frames
        start_time = Fraction(start_frame, 1) / fps
        duration = Fraction(frames, 1) / fps
        vf = ",".join(
            [
                f"setpts=PTS+{seconds_decimal(start_time)}/TB",
                ass_filter(ass_path, fonts_dir),
                "setpts=PTS-STARTPTS",
                "format=rgba",
            ]
        )
        cmd = [
            str(ffmpeg),
            "-v",
            "error",
            "-nostdin",
            "-f",
            "lavfi",
            "-i",
            f"color=c={background}:s={width}x{height}:r={fps_arg}:d={seconds_decimal(duration)}",
            "-vf",
            vf,
            "-frames:v",
            str(frames),
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgba",
            "-",
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert proc.stdout is not None
        assert proc.stderr is not None
        while True:
            chunk = proc.stdout.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        stderr = proc.stderr.read().decode("utf-8", errors="replace")
        return_code = proc.wait()
        if return_code != 0:
            raise RuntimeError(stderr.strip() or f"ffmpeg exited with code {return_code}")

    return digest.hexdigest(), total_frames


def verify_font_faces(replacements: list[Replacement]) -> VerificationResult:
    changed = [item for item in replacements if item.status == "changed"]
    result = VerificationResult(passed=False, method="font-face", skipped=True)
    if not changed:
        result.passed = True
        result.notes.append("No changed font names required face verification.")
        return result

    failed = [item for item in changed if not item.same_face]
    if failed:
        result.errors.append(
            f"{len(failed)} changed font name(s) could not be proven to resolve to the same font face."
        )
        return result

    result.passed = True
    result.notes.append(
        "All changed font names were verified as aliases of the same installed font file and face index."
    )
    return result


def sample_frame_ranges(frame_ranges: list[tuple[int, int]], max_frames: int) -> list[tuple[int, int]]:
    frames: set[int] = set()
    if max_frames <= 0:
        return []

    for start, end in frame_ranges:
        if end <= start:
            continue
        frames.add(start)
        frames.add(end - 1)
        frames.add((start + end - 1) // 2)

    if len(frames) > max_frames:
        sorted_frames = sorted(frames)
        if max_frames == 1:
            frames = {sorted_frames[len(sorted_frames) // 2]}
        else:
            selected: set[int] = set()
            last_index = len(sorted_frames) - 1
            for i in range(max_frames):
                selected.add(sorted_frames[round(i * last_index / (max_frames - 1))])
            frames = selected

    if not frames:
        return []
    sorted_frames = sorted(frames)
    ranges: list[tuple[int, int]] = []
    current_start = sorted_frames[0]
    current_end = current_start + 1
    for frame in sorted_frames[1:]:
        if frame == current_end:
            current_end += 1
        else:
            ranges.append((current_start, current_end))
            current_start = frame
            current_end = frame + 1
    ranges.append((current_start, current_end))
    return ranges


def prepare_temp_fonts(font_dirs: list[Path], tmp_dir: Path) -> Path | None:
    sources: list[Path] = []
    for font_dir in font_dirs:
        if not font_dir.is_dir():
            continue
        try:
            for path in font_dir.rglob("*"):
                if path.is_file() and path.suffix.lower() in FONT_EXTENSIONS:
                    sources.append(path)
        except OSError:
            continue

    if not sources:
        return None

    dest_dir = tmp_dir / "fonts"
    dest_dir.mkdir(exist_ok=True)
    used_names: set[str] = set()
    for source in sources:
        target_name = source.name
        if target_name.casefold() in used_names:
            stem = source.stem
            suffix = source.suffix
            counter = 2
            while f"{stem}_{counter}{suffix}".casefold() in used_names:
                counter += 1
            target_name = f"{stem}_{counter}{suffix}"
        used_names.add(target_name.casefold())
        shutil.copy2(source, dest_dir / target_name)
    return dest_dir


def verify_pixels(
    original_text: str,
    candidate_text: str,
    input_path: Path,
    ffmpeg_path: Path,
    font_dirs: list[Path],
    fps: Fraction,
    fps_source: str,
    target_line_numbers: set[int] | None = None,
    target_styles: set[str] | None = None,
    sample: bool = False,
    sample_frames: int = 24,
    replacements: list[Replacement] | None = None,
) -> VerificationResult:
    width, height = parse_script_resolution(original_text)
    covered_pairs = 0
    total_pairs = 0
    inactive_pairs: list[str] = []
    if sample and replacements is not None:
        frame_ranges, covered_pairs, total_pairs, inactive_pairs = collect_sample_ranges_by_font(
            original_text,
            fps,
            replacements,
            sample_frames,
        )
    else:
        frame_ranges = collect_dialogue_intervals(original_text, fps, target_line_numbers, target_styles)
        if sample:
            frame_ranges = sample_frame_ranges(frame_ranges, sample_frames)
    frame_count = sum(end - start for start, end in frame_ranges)
    result = VerificationResult(
        passed=False,
        method="sample pixel" if sample else "exhaustive pixel",
        frame_count=frame_count,
        fps=fps_to_ffmpeg_arg(fps),
        fps_source=fps_source,
    )

    if target_line_numbers is not None or target_styles:
        result.notes.append("Pixel verification was limited to rendered intervals affected by changed font names.")
    if sample:
        result.notes.append(
            f"Sample mode checks up to {sample_frames} affected frame(s) per changed font pair."
        )
        if total_pairs:
            result.notes.append(
                f"Pixel samples cover {covered_pairs}/{total_pairs} changed font pair(s) with active Dialogue frames."
            )
        if inactive_pairs:
            shown = ", ".join(inactive_pairs[:8])
            suffix = "" if len(inactive_pairs) <= 8 else f", ... (+{len(inactive_pairs) - 8} more)"
            result.notes.append(
                "No active Dialogue frame was available for these changed font pair(s); "
                f"font-face verification still passed: {shown}{suffix}"
            )

    if frame_count == 0:
        result.passed = True
        result.skipped = True
        result.notes.append("No active affected Dialogue frames were found at the selected FPS.")
        return result

    with tempfile.TemporaryDirectory(prefix="ass_font_family_") as tmp:
        tmp_dir = Path(tmp)
        original_ass = tmp_dir / "original.ass"
        candidate_ass = tmp_dir / "candidate.ass"
        fonts_dir = prepare_temp_fonts(font_dirs, tmp_dir)
        original_ass.write_text(original_text, encoding="utf-8-sig", newline="")
        candidate_ass.write_text(candidate_text, encoding="utf-8-sig", newline="")

        for background in ("black", "white"):
            try:
                before_hash, before_frames = render_hash(
                    ffmpeg_path, original_ass, fonts_dir, width, height, fps, frame_ranges, background
                )
                after_hash, after_frames = render_hash(
                    ffmpeg_path, candidate_ass, fonts_dir, width, height, fps, frame_ranges, background
                )
            except Exception as exc:
                result.errors.append(f"{background}: {exc}")
                continue

            result.backgrounds[background] = (before_hash, after_hash)
            if before_frames != after_frames:
                result.errors.append(
                    f"{background}: frame count changed ({before_frames} -> {after_frames})"
                )
            elif before_hash != after_hash:
                result.errors.append(f"{background}: pixel hash mismatch")

    result.passed = not result.errors
    return result


def escape_md_cell(value: object) -> str:
    text = str(value if value is not None else "")
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def write_report(
    report_path: Path,
    input_path: Path,
    output_path: Path,
    replacements: list[Replacement],
    verification: VerificationResult | None,
    wrote_output: bool,
    unresolved: bool,
) -> None:
    changed = sum(1 for item in replacements if item.status == "changed")
    unchanged = sum(1 for item in replacements if item.status == "unchanged")
    failed = sum(1 for item in replacements if item.status in {"unresolved", "ambiguous"})

    lines = [
        "# ASS Font Family Replacement Report",
        "",
        f"- Input: `{input_path}`",
        f"- Output: `{output_path}`" if wrote_output else f"- Output: not written (`{output_path}`)",
        f"- Changed entries: {changed}",
        f"- Already family names: {unchanged}",
        f"- Unresolved or ambiguous entries: {failed}",
    ]

    if verification:
        status = "PASS" if verification.passed else "FAIL"
        if verification.skipped and verification.method != "font-face":
            status += " (no active rendered dialogue frames)"
        elif verification.skipped:
            status += " (pixel render skipped)"
        lines.extend(
            [
                f"- Verification: {status}",
                f"- Verification method: {verification.method or 'unknown'}",
            ]
        )
        if verification.fps:
            lines.append(f"- Verification FPS: {verification.fps} ({verification.fps_source})")
        if verification.method != "font-face":
            lines.append(f"- Checked frames per background: {verification.frame_count}")
        for background, hashes in verification.backgrounds.items():
            lines.append(f"- {background} SHA256 before/after: `{hashes[0]}` / `{hashes[1]}`")
        for note in verification.notes:
            lines.append(f"- Note: {note}")
        for error in verification.errors:
            lines.append(f"- Error: {error}")
    else:
        lines.append("- Verification: not run")

    if unresolved:
        lines.append("- Safety: output was blocked because not every font name could be normalized.")

    lines.extend(
        [
            "",
            "| Line | Section | Type | Occurrence | Rendered | Old Font | New Font | Status | Note |",
            "| ---: | --- | --- | ---: | --- | --- | --- | --- | --- |",
        ]
    )
    for item in replacements:
        lines.append(
            "| "
            + " | ".join(
                [
                    escape_md_cell(item.line_no),
                    escape_md_cell(item.section),
                    escape_md_cell(item.entry_type),
                    escape_md_cell(item.occurrence),
                    escape_md_cell(item.rendered),
                    escape_md_cell(item.old_font),
                    escape_md_cell(item.new_font),
                    escape_md_cell(item.status),
                    escape_md_cell(item.note),
                ]
            )
            + " |"
        )

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_subtitle(path: Path, encoding: str) -> str:
    return path.read_text(encoding=encoding)


def choose_output_path(input_path: Path, output: str | None, in_place: bool) -> Path:
    if output:
        return Path(output)
    if in_place:
        return input_path
    suffix = input_path.suffix or ".ass"
    return input_path.parent / "family output" / f"{input_path.stem}.family{suffix}"


def choose_report_path(input_path: Path, report: str | None) -> Path:
    if report:
        return Path(report)
    return input_path.parent / "family output" / f"{input_path.stem}.font_family_report.md"


def collect_input_files(inputs: list[str]) -> tuple[list[Path], list[Path]]:
    files: list[Path] = []
    missing: list[Path] = []
    seen: set[Path] = set()

    for raw in inputs:
        path = Path(raw)
        if path.is_dir():
            candidates = list(path.rglob("*.ass")) + list(path.rglob("*.ssa"))
        elif path.is_file():
            candidates = [path]
        else:
            missing.append(path)
            continue

        for candidate in sorted(candidates, key=lambda item: str(item).casefold()):
            if candidate.suffix.casefold() not in {".ass", ".ssa"}:
                continue
            if any(part.casefold() == "family output" for part in candidate.parts):
                continue
            if candidate.name.casefold().endswith(".family.ass"):
                continue
            resolved = candidate.resolve()
            if resolved not in seen:
                seen.add(resolved)
                files.append(candidate)

    return files, missing


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Replace ASS subtitle font names with font family names and write "
            "the result only after verification passes."
        )
    )
    parser.add_argument("inputs", nargs="+", help="Input .ass/.ssa subtitle file(s) or folder(s)")
    parser.add_argument(
        "-o",
        "--output",
        help="Output subtitle path. Defaults to '<subtitle folder>/family output/*.family.ass'.",
    )
    parser.add_argument("--in-place", action="store_true", help="Overwrite the input file after verification passes")
    parser.add_argument(
        "--report",
        help="Markdown report path. Defaults to '<subtitle folder>/family output/*.font_family_report.md'.",
    )
    parser.add_argument(
        "--font-dir",
        action="append",
        default=[],
        help="Extra directory to scan for .ttf/.otf/.ttc/.otc fonts. Can be passed more than once.",
    )
    parser.add_argument("--ffmpeg", help="Path to ffmpeg. Defaults to ./ffmpeg.exe or PATH ffmpeg")
    parser.add_argument(
        "--verify-mode",
        choices=("font-face", "sample", "exhaustive"),
        default="sample",
        help=(
            "Verification mode. sample first proves old/new names are aliases of the same installed font face, "
            "then renders a capped set of affected frames. font-face skips rendering. "
            "exhaustive renders every affected video frame."
        ),
    )
    parser.add_argument(
        "--sample-frames",
        type=int,
        default=24,
        help="Maximum affected frames to render per changed font pair in sample mode. Default: 24.",
    )
    parser.add_argument(
        "--fps",
        help=(
            "Pixel verification FPS, e.g. 24000/1001 or 25. "
            "Defaults to Video FPS in the ASS file, otherwise 24000/1001."
        ),
    )
    parser.add_argument(
        "--encoding",
        default="utf-8-sig",
        help="Subtitle text encoding for reading input. Default: utf-8-sig",
    )
    parser.add_argument(
        "--skip-comments",
        action="store_true",
        help="Do not replace \\fn tags in Comment lines.",
    )
    parser.add_argument("--include-comments", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--typographic-family",
        action="store_true",
        help=(
            "Prefer typographic family name ID 16, e.g. Source Han Sans SC. "
            "By default the script uses legacy family name ID 1, e.g. Source Han Sans SC Heavy."
        ),
    )
    parser.add_argument("--legacy-family", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--allow-mismatch-output",
        action="store_true",
        help="Write candidate output even if pixel verification fails. Not recommended.",
    )
    return parser.parse_args(argv)


def process_one_file(
    input_path: Path,
    args: argparse.Namespace,
    catalog: FontCatalog,
    extra_font_dirs: list[Path],
) -> int:
    output_path = choose_output_path(input_path, args.output, args.in_place)
    report_path = choose_report_path(input_path, args.report)

    try:
        original_text = read_subtitle(input_path, args.encoding)
    except UnicodeError as exc:
        print(f"Failed to read subtitle with encoding {args.encoding}: {exc}", file=sys.stderr)
        return 2

    candidate_text, replacements, unresolved = process_subtitle_text(
        original_text,
        catalog,
        include_comments=not args.skip_comments,
    )

    verification: VerificationResult | None = None
    changed = any(item.status == "changed" for item in replacements)
    changed_dialogue_lines = {
        item.line_no
        for item in replacements
        if item.status == "changed" and item.entry_type == "Dialogue \\fn"
    }
    changed_style_names = {
        item.style_name
        for item in replacements
        if item.status == "changed" and item.entry_type == "Style Fontname" and item.style_name
    }
    wrote_output = False

    if unresolved:
        print("Blocked: at least one font name could not be resolved or was ambiguous.", file=sys.stderr)
    elif changed:
        face_verification = verify_font_faces(replacements)
        verification = face_verification
        if not face_verification.passed:
            print("Blocked: font-face verification failed.", file=sys.stderr)
        elif args.verify_mode != "font-face":
            ffmpeg = find_ffmpeg(args.ffmpeg)
            if not ffmpeg:
                verification = VerificationResult(
                    passed=False,
                    method=args.verify_mode,
                    errors=["ffmpeg was not found. Pass --ffmpeg or place ffmpeg.exe next to this script."],
                )
                print("Blocked: ffmpeg was not found for pixel verification.", file=sys.stderr)
            else:
                fps_source = "argument"
                fps = parse_fps(args.fps) if args.fps else None
                if fps is None and args.fps:
                    print(f"Invalid --fps value: {args.fps}", file=sys.stderr)
                    return 2
                if fps is None:
                    fps, source = parse_project_fps(original_text)
                    if fps is not None:
                        fps_source = f"subtitle {source}"
                if fps is None:
                    fps = Fraction(24000, 1001)
                    fps_source = "default 24000/1001"

                verification = verify_pixels(
                    original_text,
                    candidate_text,
                    input_path,
                    ffmpeg,
                    extra_font_dirs,
                    fps,
                    fps_source,
                    changed_dialogue_lines,
                    changed_style_names,
                    sample=args.verify_mode == "sample",
                    sample_frames=args.sample_frames,
                    replacements=replacements,
                )
                verification.notes.insert(0, "Font-face verification passed before pixel rendering.")
                if not verification.passed:
                    print("Blocked: pixel verification failed.", file=sys.stderr)
    elif args.verify_mode != "font-face":
        verification = VerificationResult(passed=True, method=args.verify_mode, skipped=True)
        verification.notes.append("No changed font names required pixel verification.")

    if not replacements:
        print("No ASS style Fontname fields or \\fn tags were found.")
    elif unresolved:
        wrote_output = False
    elif not changed:
        wrote_output = True
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(candidate_text, encoding="utf-8-sig", newline="")
        print("No font names needed changes; wrote a normalized copy.")
    elif verification and verification.passed:
        wrote_output = True
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(candidate_text, encoding="utf-8-sig", newline="")
        print("Verification passed; wrote output subtitle.")
    elif args.allow_mismatch_output:
        wrote_output = True
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(candidate_text, encoding="utf-8-sig", newline="")
        print("Pixel verification failed, but --allow-mismatch-output was set; wrote candidate output.")

    report_path.parent.mkdir(parents=True, exist_ok=True)
    write_report(
        report_path=report_path,
        input_path=input_path,
        output_path=output_path,
        replacements=replacements,
        verification=verification,
        wrote_output=wrote_output,
        unresolved=unresolved,
    )
    print(f"Report: {report_path}")

    if unresolved:
        return 1
    if changed and verification and not verification.passed and not args.allow_mismatch_output:
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])

    input_files, missing = collect_input_files(args.inputs)
    for path in missing:
        print(f"Input path not found: {path}", file=sys.stderr)
    if not input_files:
        print("No .ass/.ssa input files found.", file=sys.stderr)
        return 2

    if len(input_files) > 1 and args.output:
        print("--output can only be used with a single input file.", file=sys.stderr)
        return 2
    if len(input_files) > 1 and args.report:
        print("--report can only be used with a single input file.", file=sys.stderr)
        return 2
    if args.in_place and args.output:
        print("--in-place and --output cannot be used together.", file=sys.stderr)
        return 2
    if args.verify_mode == "sample" and args.sample_frames < 1:
        print("--sample-frames must be at least 1 in sample mode.", file=sys.stderr)
        return 2

    extra_font_dirs = [Path(item) for item in args.font_dir]
    try:
        prefer_typographic = args.typographic_family and not args.legacy_family
        catalog = build_font_catalog(extra_font_dirs, prefer_typographic)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    failures = 0
    for index, input_path in enumerate(input_files, 1):
        if len(input_files) > 1:
            print(f"[{index}/{len(input_files)}] {input_path}")
        result = process_one_file(input_path, args, catalog, extra_font_dirs)
        if result != 0:
            failures += 1

    if len(input_files) > 1:
        succeeded = len(input_files) - failures
        print(f"Batch complete: {succeeded} succeeded, {failures} failed.")
    if missing:
        return 2
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
