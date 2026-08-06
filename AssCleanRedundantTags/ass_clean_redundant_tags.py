#!/usr/bin/env python3
"""Clean ASS files and verify the result with libass and xy-VSFilter.

This is the single Python implementation of CleanRedundantTags.  It owns ASS
parsing, tag cleanup, safe ordering, metadata cleanup, consecutive-line
merging, Markdown reporting, and independent before/after rendering checks.
libass and xy-VSFilter output is never compared against each other because
cross-renderer visual differences are expected.
"""

from __future__ import annotations

import argparse
import collections
import concurrent.futures
import copy
import dataclasses
import datetime as _datetime
from decimal import Decimal, InvalidOperation
import difflib
import html as _html
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import traceback
from typing import Any, Callable, Iterable, Iterator, Sequence


VERSION = "1.1"
EXIT_PASS = 0
EXIT_DIFFERENCE = 1
EXIT_INCOMPLETE = 2
EXIT_CONFIGURATION = 3


@dataclasses.dataclass
class CleanOptions:
    safe_reorder: bool = False
    merge_lines: bool = False
    clean_comments: bool = False
    remove_transparent_dialogues: bool = False
    clean_unknown_tags: bool = False
    clean_extradata_refs: bool = False
    clean_project_garbage: bool = False
    clean_extradata_section: bool = False


@dataclasses.dataclass
class TextCleanStats:
    removed_tags: int = 0
    removed_tag_labels: list[str] = dataclasses.field(default_factory=list)
    removed_unknown_tags: int = 0
    removed_unknown_tag_labels: list[str] = dataclasses.field(default_factory=list)
    reordered_tags: int = 0
    normalized_numbers: int = 0
    removed_blocks: int = 0
    unsupported_tags: list[str] = dataclasses.field(default_factory=list)
    renderer_specific_tags: list[str] = dataclasses.field(default_factory=list)

    def add(self, other: "TextCleanStats") -> None:
        self.removed_tags += other.removed_tags
        self.removed_tag_labels.extend(other.removed_tag_labels)
        self.removed_unknown_tags += other.removed_unknown_tags
        self.removed_unknown_tag_labels.extend(other.removed_unknown_tag_labels)
        self.reordered_tags += other.reordered_tags
        self.normalized_numbers += other.normalized_numbers
        self.removed_blocks += other.removed_blocks
        self.unsupported_tags.extend(other.unsupported_tags)
        self.renderer_specific_tags.extend(other.renderer_specific_tags)


@dataclasses.dataclass
class LineChange:
    event_number: int
    kind: str
    start: str
    end: str
    style: str
    before: str
    after: str
    stats: TextCleanStats
    removed_event: bool = False


@dataclasses.dataclass
class CleanResult:
    input_path: Path
    output_path: Path
    changed_dialogues: int = 0
    processed_dialogues: int = 0
    transparent_dialogues_removed: int = 0
    comment_lines_removed: int = 0
    marker_references_removed: int = 0
    project_garbage_removed: bool = False
    extradata_section_removed: bool = False
    extradata_records_removed: int = 0
    merged_groups: int = 0
    merged_lines: int = 0
    changes: list[LineChange] = dataclasses.field(default_factory=list)
    stats: TextCleanStats = dataclasses.field(default_factory=TextCleanStats)


@dataclasses.dataclass(frozen=True)
class SubtitleInput:
    path: Path
    source_root: Path | None = None


@dataclasses.dataclass
class BatchCleanItem:
    input_path: Path
    output_path: Path | None
    report_path: Path | None
    code: int
    result: CleanResult | None = None
    error: str = ""


@dataclasses.dataclass
class BatchCleanResult:
    items: list[BatchCleanItem] = dataclasses.field(default_factory=list)

    @property
    def succeeded(self) -> int:
        return sum(item.result is not None for item in self.items)

    @property
    def failed(self) -> int:
        return sum(item.result is None for item in self.items)

    @property
    def changed_dialogues(self) -> int:
        return sum(
            item.result.changed_dialogues for item in self.items if item.result is not None
        )

    @property
    def removed_tags(self) -> int:
        return sum(
            item.result.stats.removed_tags for item in self.items if item.result is not None
        )

    @property
    def removed_extradata_references(self) -> int:
        return sum(
            item.result.marker_references_removed
            for item in self.items
            if item.result is not None
        )

    @property
    def removed_comment_lines(self) -> int:
        return sum(
            item.result.comment_lines_removed
            for item in self.items
            if item.result is not None
        )

    @property
    def removed_transparent_dialogues(self) -> int:
        return sum(
            item.result.transparent_dialogues_removed
            for item in self.items
            if item.result is not None
        )


@dataclasses.dataclass
class AssStyle:
    name: str
    fontname: str = ""
    fontsize: str = "0"
    primary: str = "&H00FFFFFF"
    secondary: str = "&H000000FF"
    outline_color: str = "&H00000000"
    back_color: str = "&H00000000"
    bold: str = "0"
    italic: str = "0"
    underline: str = "0"
    strikeout: str = "0"
    scale_x: str = "100"
    scale_y: str = "100"
    spacing: str = "0"
    angle: str = "0"
    outline: str = "0"
    shadow: str = "0"
    alignment: str = "2"
    encoding: str = "1"


@dataclasses.dataclass
class AssEvent:
    line_index: int
    event_number: int
    kind: str
    prefix: str
    values: list[str]
    fields: tuple[str, ...]

    def field_index(self, name: str) -> int | None:
        wanted = name.casefold()
        for index, field in enumerate(self.fields):
            if field.casefold() == wanted:
                return index
        return None

    def get(self, name: str, default: str = "") -> str:
        index = self.field_index(name)
        if index is None or index >= len(self.values):
            return default
        return self.values[index]

    def set(self, name: str, value: str) -> None:
        index = self.field_index(name)
        if index is not None and index < len(self.values):
            self.values[index] = value

    @property
    def text(self) -> str:
        return self.get("Text")

    @text.setter
    def text(self, value: str) -> None:
        self.set("Text", value)

    @property
    def start_ms(self) -> int | None:
        return parse_ass_time_ms(self.get("Start"))

    @property
    def end_ms(self) -> int | None:
        return parse_ass_time_ms(self.get("End"))

    def render(self) -> str:
        return self.prefix + ",".join(self.values)


@dataclasses.dataclass
class AssDocument:
    lines: list[str]
    newline: str
    final_newline: bool
    encoding: str
    styles: dict[str, AssStyle]
    events: list[AssEvent]
    wrap_style: int = 0

    def rebuild_events(self) -> None:
        parsed = parse_ass_document_text(
            self.newline.join(self.lines)
            + (self.newline if self.final_newline else ""),
            self.encoding,
        )
        self.styles = parsed.styles
        self.events = parsed.events
        self.wrap_style = parsed.wrap_style

    def render_text(self) -> str:
        return self.newline.join(self.lines) + (self.newline if self.final_newline else "")


def detect_text_encoding(data: bytes) -> tuple[str, str]:
    if data.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig", data.decode("utf-8-sig")
    if data.startswith(b"\xff\xfe"):
        return "utf-16", data.decode("utf-16")
    if data.startswith(b"\xfe\xff"):
        return "utf-16", data.decode("utf-16")
    for encoding in ("utf-8", "gb18030", "cp1252"):
        try:
            return encoding, data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return "utf-8", data.decode("utf-8", errors="replace")


def read_ass_document(path: Path) -> AssDocument:
    data = path.read_bytes()
    encoding, text = detect_text_encoding(data)
    return parse_ass_document_text(text, encoding)


def parse_ass_document_text(text: str, encoding: str = "utf-8") -> AssDocument:
    newline_match = re.search(r"\r\n|\n|\r", text)
    newline = newline_match.group(0) if newline_match else os.linesep
    final_newline = text.endswith(("\r", "\n"))
    lines = text.splitlines()
    styles: dict[str, AssStyle] = {}
    events: list[AssEvent] = []
    section = ""
    style_fields: tuple[str, ...] = ()
    event_fields: tuple[str, ...] = ()
    event_number = 0
    wrap_style = 0

    for line_index, line in enumerate(lines):
        section_match = re.match(r"^\s*\[([^\]]+)\]\s*$", line)
        if section_match:
            section = section_match.group(1).strip().casefold()
            continue
        if section == "script info":
            info = re.match(r"^\s*([^:;]+?)\s*:\s*(.*?)\s*$", line)
            if info and info.group(1).strip().casefold() == "wrapstyle":
                try:
                    candidate = int(info.group(2))
                    if 0 <= candidate <= 3:
                        wrap_style = candidate
                except ValueError:
                    pass
        elif section in ("v4+ styles", "v4 styles"):
            if line.casefold().startswith("format:"):
                style_fields = tuple(item.strip() for item in line.split(":", 1)[1].split(","))
            elif line.casefold().startswith("style:") and style_fields:
                payload = line.split(":", 1)[1].lstrip()
                values = payload.split(",", len(style_fields) - 1)
                if len(values) == len(style_fields):
                    mapping = {
                        key.casefold(): value.strip()
                        for key, value in zip(style_fields, values)
                    }
                    style = AssStyle(
                        name=mapping.get("name", ""),
                        fontname=mapping.get("fontname", ""),
                        fontsize=mapping.get("fontsize", "0"),
                        primary=mapping.get("primarycolour", "&H00FFFFFF"),
                        secondary=mapping.get("secondarycolour", "&H000000FF"),
                        outline_color=mapping.get("outlinecolour", "&H00000000"),
                        back_color=mapping.get("backcolour", "&H00000000"),
                        bold=mapping.get("bold", "0"),
                        italic=mapping.get("italic", "0"),
                        underline=mapping.get("underline", "0"),
                        strikeout=mapping.get("strikeout", "0"),
                        scale_x=mapping.get("scalex", "100"),
                        scale_y=mapping.get("scaley", "100"),
                        spacing=mapping.get("spacing", "0"),
                        angle=mapping.get("angle", "0"),
                        outline=mapping.get("outline", "0"),
                        shadow=mapping.get("shadow", "0"),
                        alignment=mapping.get("alignment", "2"),
                        encoding=mapping.get("encoding", "1"),
                    )
                    styles[style.name] = style
        elif section == "events":
            if line.casefold().startswith("format:"):
                event_fields = tuple(item.strip() for item in line.split(":", 1)[1].split(","))
                continue
            event_match = re.match(r"^(\s*(Dialogue|Comment)\s*:\s*)(.*)$", line, re.I)
            if not event_match or not event_fields:
                continue
            values = event_match.group(3).split(",", len(event_fields) - 1)
            if len(values) != len(event_fields):
                continue
            event_number += 1
            events.append(
                AssEvent(
                    line_index=line_index,
                    event_number=event_number,
                    kind=event_match.group(2).capitalize(),
                    prefix=event_match.group(1),
                    values=values,
                    fields=event_fields,
                )
            )
    return AssDocument(lines, newline, final_newline, encoding, styles, events, wrap_style)


def write_ass_document(document: AssDocument, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = document.render_text()
    target_encoding = document.encoding
    if target_encoding == "utf-16":
        data = text.encode("utf-16")
    elif target_encoding == "utf-8-sig":
        data = text.encode("utf-8-sig")
    else:
        try:
            data = text.encode(target_encoding)
        except UnicodeEncodeError:
            target_encoding = "utf-8-sig"
            data = text.encode(target_encoding)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def parse_ass_time_ms(value: str) -> int | None:
    match = re.fullmatch(r"\s*(\d+):(\d\d):(\d\d)[.](\d\d)\s*", value)
    if not match:
        return None
    hours, minutes, seconds, centiseconds = (int(item) for item in match.groups())
    if minutes >= 60 or seconds >= 60:
        return None
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + centiseconds * 10


def section_span(lines: Sequence[str], section_name: str) -> tuple[int, int] | None:
    wanted = section_name.casefold()
    start: int | None = None
    for index, line in enumerate(lines):
        match = re.match(r"^\s*\[([^\]]+)\]\s*$", line)
        if not match:
            continue
        if start is None:
            if match.group(1).strip().casefold() == wanted:
                start = index
        else:
            return start, index
    if start is not None:
        return start, len(lines)
    return None


def remove_ass_section(document: AssDocument, section_name: str) -> tuple[bool, int]:
    span = section_span(document.lines, section_name)
    if not span:
        return False, 0
    start, end = span
    removed_records = sum(
        1 for line in document.lines[start + 1 : end] if line.lstrip().casefold().startswith("data:")
    )
    delete_start = start
    delete_end = end
    while delete_start > 0 and document.lines[delete_start - 1].strip() == "":
        delete_start -= 1
        break
    while delete_end < len(document.lines) and document.lines[delete_end].strip() == "":
        delete_end += 1
    replacement = [""] if delete_start > 0 and delete_end < len(document.lines) else []
    document.lines[delete_start:delete_end] = replacement
    document.rebuild_events()
    return True, removed_records


EXTRADATA_REFERENCE_RE = re.compile(r"^(?:\{(?:=\d+)+\})+")


def remove_extradata_references(document: AssDocument) -> int:
    removed = 0
    for event in document.events:
        match = EXTRADATA_REFERENCE_RE.match(event.text)
        if not match:
            continue
        removed += len(re.findall(r"=\d+", match.group(0)))
        event.text = event.text[match.end() :]
        document.lines[event.line_index] = event.render()
    return removed


def remove_comment_events(document: AssDocument) -> int:
    """Remove Comment event rows, including malformed rows, only inside Events."""
    section = ""
    retained: list[str] = []
    removed = 0
    for line in document.lines:
        section_match = re.match(r"^\s*\[([^\]]+)\]\s*$", line)
        if section_match:
            section = section_match.group(1).strip().casefold()
            retained.append(line)
            continue
        if section == "events" and re.match(r"^\s*Comment\s*:", line, re.I):
            removed += 1
            continue
        retained.append(line)
    if removed:
        document.lines = retained
        document.rebuild_events()
    return removed


NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")
NUMBER_PREFIX_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)")


def canonical_decimal(
    value: object,
    *,
    preserve_plus: bool = False,
    preserve_negative_zero: bool = False,
) -> str | None:
    text = str(value).strip()
    if not NUMBER_RE.fullmatch(text):
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    if not number.is_finite():
        return None
    was_plus = text.startswith("+")
    was_negative = text.startswith("-")
    if number == 0:
        if preserve_negative_zero and was_negative:
            result = "-0"
        else:
            result = "0"
    else:
        result = format(number.normalize(), "f")
        if "." in result:
            result = result.rstrip("0").rstrip(".")
        if result == "-0":
            result = "0"
    if preserve_plus and was_plus and not result.startswith(("+", "-")):
        result = "+" + result
    return result


def split_top_level(value: str) -> list[str]:
    output: list[str] = []
    start = 0
    depth = 0
    for index, character in enumerate(value):
        if character == "(":
            depth += 1
        elif character == ")":
            depth = max(0, depth - 1)
        elif character == "," and depth == 0:
            output.append(value[start:index])
            start = index + 1
    output.append(value[start:])
    return output


def matching_close(value: str, open_index: int) -> int | None:
    depth = 0
    for index in range(open_index, len(value)):
        if value[index] == "(":
            depth += 1
        elif value[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


TAG_NAME_ORDER = (
    "iclip", "alpha", "fscx", "fscy", "xbord", "ybord", "xshad", "yshad",
    "frx", "fry", "frz", "fsp", "blur", "bord", "shad", "pbo",
    "move", "fade", "clip", "pos", "org", "fad", "fsc", "fontname",
    "fn", "fs", "fr", "fe", "fax", "fay", "be", "an", "kt", "kf",
    "ko", "1c", "2c", "3c", "4c", "1a", "2a", "3a", "4a",
    "r", "b", "i", "u", "s", "p", "q", "c", "a", "t", "k", "K",
)

RENDERER_TAG_PREFIXES = (
    "alpha", "iclip", "xbord", "ybord", "xshad", "yshad",
    "blur", "bord", "shad", "move", "fade", "clip",
    "fscx", "fscy", "pbo", "pos", "org", "fad",
    "fax", "fay", "fsp", "frx", "fry", "frz",
    "1c", "2c", "3c", "4c", "1a", "2a", "3a", "4a",
    "fsc", "fn", "fs", "fr", "fe", "be", "an", "kt", "kf", "ko",
    "r", "b", "i", "u", "s", "p", "q", "c", "a", "t", "k", "K",
)

NUMERIC_TAGS = {
    "fs", "fscx", "fscy", "fsp", "fr", "frx", "fry", "frz",
    "fax", "fay", "bord", "xbord", "ybord", "shad", "xshad", "yshad",
    "blur", "be", "fe", "b", "i", "u", "s", "p", "pbo", "q", "an",
    "a", "k", "K", "kf", "ko", "kt",
}

STATIC_TAG_FIELDS: dict[str, tuple[str, ...]] = {
    "fn": ("fontname",),
    "fontname": ("fontname",),
    "fs": ("font_size",),
    "fsc": ("scale_x", "scale_y"),
    "fscx": ("scale_x",),
    "fscy": ("scale_y",),
    "fsp": ("spacing",),
    "fr": ("frz",),
    "frz": ("frz",),
    "frx": ("frx",),
    "fry": ("fry",),
    "fax": ("fax",),
    "fay": ("fay",),
    "b": ("bold",),
    "i": ("italic",),
    "u": ("underline",),
    "s": ("strikeout",),
    "bord": ("xbord", "ybord"),
    "xbord": ("xbord",),
    "ybord": ("ybord",),
    "shad": ("xshad", "yshad"),
    "xshad": ("xshad",),
    "yshad": ("yshad",),
    "blur": ("blur",),
    "be": ("be",),
    "fe": ("encoding",),
    "c": ("color1",),
    "1c": ("color1",),
    "2c": ("color2",),
    "3c": ("color3",),
    "4c": ("color4",),
    "alpha": ("alpha1", "alpha2", "alpha3", "alpha4"),
    "1a": ("alpha1",),
    "2a": ("alpha2",),
    "3a": ("alpha3",),
    "4a": ("alpha4",),
}

LINE_TAG_FIELDS: dict[str, tuple[str, ...]] = {
    "an": ("alignment",),
    "a": ("alignment",),
    "pos": ("line_position",),
    "move": ("line_position",),
    "org": ("line_origin",),
    "fad": ("line_fade",),
    "fade": ("line_fade",),
    "q": ("wrap_style",),
    "p": ("p", "drawing_control", "text_interpretation"),
    "pbo": ("pbo", "drawing_control"),
    "clip": ("line_clip",),
    "iclip": ("line_clip",),
}

KARAOKE_FIELDS: dict[str, tuple[str, ...]] = {
    "k": (
        "karaoke_timeline", "text_interpretation",
        "color1", "color2", "alpha1", "alpha2",
    ),
    "K": (
        "karaoke_timeline", "text_interpretation",
        "color1", "color2", "alpha1", "alpha2",
    ),
    "kf": (
        "karaoke_timeline", "text_interpretation",
        "color1", "color2", "alpha1", "alpha2",
    ),
    "ko": (
        "karaoke_timeline", "text_interpretation",
        "color1", "color2", "alpha1", "alpha2",
        "xbord", "ybord", "color3", "alpha3",
    ),
    "kt": ("karaoke_timeline", "text_interpretation"),
}

SAFE_REORDER_NAMES = (
    "an", "a",
    "r",
    "q",
    "p", "pbo",
    "fn", "fontname", "fs", "fsp", "b", "i", "u", "s",
    "c", "1c", "2c", "3c", "4c",
    "alpha", "1a", "2a", "3a", "4a", "blur", "be", "fe",
    "k", "K", "kf", "ko", "kt",
    "fax", "fay", "fsc", "fscx", "fscy", "frz", "fr", "frx", "fry",
    "bord", "xbord", "ybord", "shad", "xshad", "yshad",
    "org", "pos", "move", "fad", "fade",
    "t",
    "clip", "iclip",
)

SAFE_REORDER_RANK = {name: index for index, name in enumerate(SAFE_REORDER_NAMES)}
for alias, master in {
    "a": "an",
    "move": "pos",
    "fade": "fad",
    "fr": "frz",
    "fontname": "fn",
    "1c": "c",
    "pbo": "p",
    "K": "k",
    "kf": "k",
    "ko": "k",
    "kt": "k",
    "iclip": "clip",
}.items():
    SAFE_REORDER_RANK[alias] = SAFE_REORDER_RANK[master]


@dataclasses.dataclass(eq=False)
class TagPiece:
    kind: str
    raw: str
    name: str | None = None
    argument: str = ""
    removed: bool = False
    original_ordinal: int = -1


@dataclasses.dataclass
class TextPart:
    kind: str
    raw: str
    pieces: list[TagPiece] = dataclasses.field(default_factory=list)
    normalized_numbers: int = 0

    @property
    def is_override(self) -> bool:
        return any(piece.kind == "tag" for piece in self.pieces) and all(
            piece.kind == "tag" or not piece.raw.strip() for piece in self.pieces
        )

    def content(self) -> str:
        return "".join(piece.raw for piece in self.pieces if not piece.removed)


@dataclasses.dataclass
class TagAction:
    valid: bool
    fields: tuple[str, ...] = ()
    values: dict[str, str] = dataclasses.field(default_factory=dict)
    barrier: bool = False
    line_wide: bool = False


def parse_tag_name(raw: str) -> str | None:
    if not raw.startswith("\\"):
        return None
    for name in TAG_NAME_ORDER:
        if raw.startswith("\\" + name):
            return name
    match = re.match(r"^\\(\d?[A-Za-z]+)", raw)
    return match.group(1) if match else None


def split_override_content(content: str) -> list[TagPiece]:
    pieces: list[TagPiece] = []
    cursor = 0
    ordinal = 0
    while cursor < len(content):
        slash = content.find("\\", cursor)
        if slash < 0:
            pieces.append(TagPiece("text", content[cursor:]))
            break
        if slash > cursor:
            pieces.append(TagPiece("text", content[cursor:slash]))
        index = slash + 1
        depth = 0
        while index < len(content):
            character = content[index]
            if character == "(":
                depth += 1
            elif character == ")" and depth > 0:
                depth -= 1
            elif character == "\\" and depth == 0:
                break
            index += 1
        raw = content[slash:index]
        name = parse_tag_name(raw)
        argument = raw[len(name) + 1 :] if name else ""
        pieces.append(TagPiece("tag", raw, name, argument, False, ordinal))
        ordinal += 1
        cursor = index
    if not pieces:
        pieces.append(TagPiece("text", content))
    return pieces


def find_unescaped_open_brace(text: str, start: int) -> int:
    index = start
    while True:
        index = text.find("{", index)
        if index < 0:
            return -1
        if index == 0 or text[index - 1] != "\\":
            return index
        index += 1


def parse_text_parts(text: str) -> list[TextPart]:
    parts: list[TextPart] = []
    cursor = 0
    while cursor < len(text):
        open_at = find_unescaped_open_brace(text, cursor)
        if open_at < 0:
            parts.append(TextPart("text", text[cursor:]))
            break
        if open_at > cursor:
            parts.append(TextPart("text", text[cursor:open_at]))
        close_at = text.find("}", open_at + 1)
        if close_at < 0:
            parts.append(TextPart("text", text[open_at:]))
            break
        content = text[open_at + 1 : close_at]
        parts.append(TextPart("block", content, split_override_content(content)))
        cursor = close_at + 1
    if not parts:
        parts.append(TextPart("text", text))
    return parts


def renderer_tag_prefix(raw: str) -> str | None:
    for prefix in RENDERER_TAG_PREFIXES:
        if raw.startswith("\\" + prefix):
            return prefix
    return None


def unsupported_tag_label(raw: str) -> str | None:
    match = re.match(r"^\\(\d?[A-Za-z]+)", raw)
    return "\\" + match.group(1) if match else None


def collect_unsupported_tags(parts: Iterable[TextPart]) -> list[str]:
    found: list[str] = []

    def visit(pieces: Iterable[TagPiece], depth: int = 0) -> None:
        if depth > 16:
            return
        for piece in pieces:
            if piece.kind != "tag" or piece.removed:
                continue
            prefix = renderer_tag_prefix(piece.raw)
            if not prefix:
                label = unsupported_tag_label(piece.raw)
                if label and label not in found:
                    found.append(label)
            elif prefix == "t":
                open_at = piece.raw.find("(")
                close_at = matching_close(piece.raw, open_at) if open_at >= 0 else None
                if close_at is not None:
                    arguments = split_top_level(piece.raw[open_at + 1 : close_at])
                    if arguments and "\\" in arguments[-1]:
                        visit(split_override_content(arguments[-1]), depth + 1)

    for part in parts:
        if part.kind == "block":
            visit(part.pieces)
    return found


VSFILTER_ONLY_HTML_RE = re.compile(
    r"</?(?:text|b|strong|i|em|u|s|strike|del|font|k)(?:\s[^<>]*)?>",
    re.I,
)


def collect_renderer_specific_tags(text: str) -> list[str]:
    found: list[str] = []
    for match in VSFILTER_ONLY_HTML_RE.finditer(text):
        name_match = re.match(r"</?\s*([A-Za-z]+)", match.group(0))
        if name_match:
            label = "<" + name_match.group(1).casefold() + ">"
            if label not in found:
                found.append(label)
    return found


DRAWING_COMMANDS = frozenset("mnlbspc")


def normalize_drawing_text(value: str) -> tuple[str, int] | None:
    tokens: list[str] = []
    changed = 0
    index = 0
    while index < len(value):
        character = value[index]
        if character.isspace() or character == ",":
            index += 1
            continue
        lower = character.casefold()
        if lower in DRAWING_COMMANDS:
            tokens.append(lower)
            if character != lower:
                changed += 1
            index += 1
            continue
        match = re.match(
            r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?",
            value[index:],
        )
        if not match:
            return None
        raw = match.group(0)
        if "e" in raw.casefold():
            normalized = raw
        else:
            normalized = canonical_decimal(raw)
            if normalized is None:
                return None
        tokens.append(normalized)
        if normalized != raw:
            changed += 1
        index += len(raw)
    if not tokens or tokens[0] not in ("m", "n"):
        return None
    if any(token.isalpha() and token not in DRAWING_COMMANDS for token in tokens):
        return None
    normalized_text = " ".join(tokens)
    if normalized_text != value:
        changed = max(1, changed)
    return normalized_text, changed


def normalize_parenthesized_tag(name: str, argument: str) -> tuple[str, int] | None:
    if not argument.startswith("("):
        return None
    close_at = matching_close(argument, 0)
    if close_at != len(argument) - 1:
        return None
    inner = argument[1:-1]
    arguments = split_top_level(inner)
    if name in ("pos", "org", "fad"):
        expected = 2
    elif name == "move":
        expected = 4 if len(arguments) == 4 else 6
    elif name == "fade":
        expected = 7
    else:
        expected = 0
    if expected:
        if len(arguments) != expected:
            return None
        normalized: list[str] = []
        changes = 0
        for value in arguments:
            number = canonical_decimal(value)
            if number is None:
                return None
            normalized.append(number)
            changes += number != value
        rebuilt = "\\" + name + "(" + ",".join(normalized) + ")"
        return rebuilt, changes
    if name in ("clip", "iclip"):
        if len(arguments) == 4:
            normalized = []
            changes = 0
            for value in arguments:
                number = canonical_decimal(value)
                if number is None:
                    return None
                normalized.append(number)
                changes += number != value
            return "\\" + name + "(" + ",".join(normalized) + ")", changes
        scale: str | None = None
        drawing = inner
        if len(arguments) == 2:
            scale = canonical_decimal(arguments[0])
            if scale is None:
                return None
            drawing = arguments[1]
        elif len(arguments) != 1:
            return None
        drawing_result = normalize_drawing_text(drawing)
        if not drawing_result:
            return None
        normalized_drawing, drawing_changes = drawing_result
        body = normalized_drawing if scale is None else scale + "," + normalized_drawing
        rebuilt = "\\" + name + "(" + body + ")"
        return rebuilt, drawing_changes + (1 if rebuilt != "\\" + name + argument else 0)
    return None


def transform_arguments(argument: str) -> tuple[list[str], str] | None:
    if not argument.startswith("("):
        return None
    close_at = matching_close(argument, 0)
    if close_at != len(argument) - 1:
        return None
    values = split_top_level(argument[1:-1])
    if not values:
        return [], ""
    return values[:-1], values[-1]


def normalize_tag_piece(piece: TagPiece, depth: int = 0) -> int:
    if piece.kind != "tag" or not piece.name:
        return 0
    name = piece.name
    argument = piece.argument
    normalized: str | None = None
    count = 0
    if name in NUMERIC_TAGS:
        if argument != argument.strip():
            return 0
        preserve_sign = name == "fs" and argument.startswith(("+", "-"))
        if name in ("be", "fe"):
            number = integer_truncated(argument)
        else:
            number = canonical_decimal(
                argument,
                preserve_plus=preserve_sign,
                preserve_negative_zero=preserve_sign,
            )
        if number is not None:
            normalized = "\\" + name + number
            count = int(normalized != piece.raw)
    elif name in ("pos", "move", "org", "fad", "fade", "clip", "iclip"):
        result = normalize_parenthesized_tag(name, argument)
        if result:
            normalized, count = result
    elif name == "t":
        parsed = transform_arguments(argument)
        if parsed:
            prefix, modifiers = parsed
            normalized_prefix: list[str] = []
            valid = True
            for item in prefix:
                number = canonical_decimal(item)
                if number is None:
                    valid = False
                    break
                normalized_prefix.append(number)
                count += number != item
            if valid:
                nested = split_override_content(modifiers)
                if all(item.kind == "tag" for item in nested):
                    if depth < 16:
                        for item in nested:
                            count += normalize_tag_piece(item, depth + 1)
                    normalized_modifiers = "".join(item.raw for item in nested)
                    body = normalized_prefix + [normalized_modifiers]
                    normalized = "\\t(" + ",".join(body) + ")"
    if normalized is not None and normalized != piece.raw:
        piece.raw = normalized
        piece.argument = normalized[len(name) + 1 :]
        return max(1, int(count))
    return 0


def normalize_parts(parts: list[TextPart]) -> int:
    changed = 0
    for part in parts:
        if part.kind != "block":
            continue
        for piece in part.pieces:
            changed += normalize_tag_piece(piece)
    return changed


def ass_color_components(value: str) -> tuple[str, str] | None:
    text = value.strip()
    match = re.fullmatch(r"&H([0-9A-Fa-f]{1,8})&?", text)
    if not match:
        return None
    hex_value = match.group(1).upper().rjust(8, "0")
    return hex_value[-6:], hex_value[:2]


def tag_color(value: str, digits: int) -> str | None:
    match = re.fullmatch(r"&H([0-9A-Fa-f]{1,%d})&?" % digits, value.strip())
    return match.group(1).upper().rjust(digits, "0") if match else None


def integer_rounded(value: str) -> str | None:
    number = canonical_decimal(value)
    if number is None:
        return None
    decimal_value = Decimal(number)
    if decimal_value >= 0:
        rounded = int(decimal_value + Decimal("0.5"))
    else:
        rounded = int(decimal_value - Decimal("0.5"))
    return str(rounded)


def integer_truncated(value: str) -> str | None:
    number = canonical_decimal(value)
    if number is None:
        return None
    return str(int(Decimal(number)))


def style_flag(value: str) -> str:
    number = canonical_decimal(value)
    if number is None:
        return "?"
    return "0" if Decimal(number) == 0 else "1"


STYLE_STATE_FIELDS = (
    "fontname", "font_size", "scale_x", "scale_y", "spacing",
    "bold", "italic", "underline", "strikeout",
    "frx", "fry", "frz", "fax", "fay",
    "xbord", "ybord", "xshad", "yshad", "blur", "be", "encoding",
    "color1", "color2", "color3", "color4",
    "alpha1", "alpha2", "alpha3", "alpha4",
)


def style_state(style: AssStyle, wrap_style: int) -> dict[str, str]:
    colors = [
        ass_color_components(style.primary),
        ass_color_components(style.secondary),
        ass_color_components(style.outline_color),
        ass_color_components(style.back_color),
    ]
    state: dict[str, str] = {
        "fontname": style.fontname,
        "font_size": canonical_decimal(style.fontsize) or "?",
        "scale_x": canonical_decimal(style.scale_x) or "?",
        "scale_y": canonical_decimal(style.scale_y) or "?",
        "spacing": canonical_decimal(style.spacing) or "?",
        "bold": "700" if style_flag(style.bold) == "1" else "0",
        "italic": style_flag(style.italic),
        "underline": style_flag(style.underline),
        "strikeout": style_flag(style.strikeout),
        "frx": "0",
        "fry": "0",
        "frz": canonical_decimal(style.angle) or "?",
        "fax": "0",
        "fay": "0",
        "xbord": canonical_decimal(style.outline) or "?",
        "ybord": canonical_decimal(style.outline) or "?",
        "xshad": canonical_decimal(style.shadow) or "?",
        "yshad": canonical_decimal(style.shadow) or "?",
        "blur": "0",
        "be": "0",
        "encoding": integer_rounded(style.encoding) or "?",
        "alignment": integer_rounded(style.alignment) or "?",
        "wrap_style": str(wrap_style),
        "p": "0",
        "pbo": "0",
        "active_style": style.name,
    }
    for index, components in enumerate(colors, 1):
        state["color%d" % index] = components[0] if components else "?"
        state["alpha%d" % index] = components[1] if components else "?"
    return state


def legacy_alignment(value: int) -> int | None:
    mapping = {1: 1, 2: 2, 3: 3, 5: 7, 6: 8, 7: 9, 9: 4, 10: 5, 11: 6}
    return mapping.get(value)


def action_for_piece(
    piece: TagPiece,
    state: dict[str, str],
    styles: dict[str, AssStyle],
    original_style: str,
    wrap_style: int,
) -> TagAction:
    name = piece.name
    argument = piece.argument
    if not name:
        return TagAction(False, barrier=True)
    if name in ("pos", "move", "org", "fad", "fade", "clip", "iclip"):
        result = normalize_parenthesized_tag(name, argument)
        if not result:
            return TagAction(False, barrier=True)
        return TagAction(True, LINE_TAG_FIELDS[name], barrier=True, line_wide=True)
    if name in ("an", "a"):
        number = integer_truncated(argument)
        if number is None:
            return TagAction(False, barrier=True)
        value = int(number)
        if name == "a":
            mapped = legacy_alignment(value)
            if mapped is None:
                return TagAction(False, barrier=True)
            value = mapped
        if not 1 <= value <= 9:
            return TagAction(False, barrier=True)
        return TagAction(
            True,
            ("alignment",),
            {"alignment": str(value)},
            barrier=True,
            line_wide=True,
        )
    if name == "q":
        number = integer_truncated(argument)
        if number is None:
            return TagAction(False, barrier=True)
        return TagAction(
            True,
            ("wrap_style",),
            {"wrap_style": number},
            barrier=True,
            line_wide=True,
        )
    if name == "p":
        number = integer_truncated(argument)
        if number is None:
            return TagAction(False, barrier=True)
        return TagAction(
            True,
            LINE_TAG_FIELDS[name],
            {"p": str(max(0, int(number)))},
            barrier=True,
        )
    if name == "pbo":
        number = canonical_decimal(argument)
        if number is None:
            return TagAction(False, barrier=True)
        return TagAction(
            True,
            LINE_TAG_FIELDS[name],
            {"pbo": number},
            barrier=True,
        )
    if name in KARAOKE_FIELDS:
        number = canonical_decimal(argument)
        if number is None or Decimal(number) < 0:
            return TagAction(False, barrier=True)
        return TagAction(True, KARAOKE_FIELDS[name], barrier=True)
    if name == "t":
        return TagAction(False, barrier=True)
    if name == "r":
        if argument != argument.strip():
            return TagAction(False, barrier=True)
        target_name = argument or original_style
        target = styles.get(target_name)
        if not target:
            return TagAction(False, barrier=True)
        target_state = style_state(target, wrap_style)
        fields = ("active_style", "animation_epoch", "karaoke_timeline") + STYLE_STATE_FIELDS
        values = {field: target_state[field] for field in STYLE_STATE_FIELDS}
        values["active_style"] = target_name
        return TagAction(True, fields, values, barrier=True)
    fields = STATIC_TAG_FIELDS.get(name)
    if not fields:
        return TagAction(False, barrier=True)
    values: dict[str, str] = {}
    active_style = styles.get(state.get("active_style", original_style), styles.get(original_style))
    if name in ("fn", "fontname"):
        if argument[:1].isspace() or argument.startswith("("):
            return TagAction(False, barrier=True)
        value = argument
        if value in ("", "0") and active_style:
            value = active_style.fontname
        values["fontname"] = value
    elif name == "fs":
        if argument.startswith(("+", "-")):
            return TagAction(False, fields, barrier=True)
        number = canonical_decimal(argument)
        if number is None:
            return TagAction(False, barrier=True)
        values["font_size"] = number
    elif name == "fsc":
        if not active_style:
            return TagAction(False, barrier=True)
        # Undocumented compatibility reset supported by libass and xy-VSFilter.
        # Any suffix is ignored: \fsc50 and bare \fsc both restore the active
        # Style's ScaleX and ScaleY values.
        values["scale_x"] = canonical_decimal(active_style.scale_x) or "?"
        values["scale_y"] = canonical_decimal(active_style.scale_y) or "?"
    elif name in ("fscx", "fscy", "fsp", "fr", "frx", "fry", "frz", "fax", "fay",
                  "bord", "xbord", "ybord", "shad", "xshad", "yshad", "blur"):
        number = canonical_decimal(argument)
        if number is None:
            return TagAction(False, barrier=True)
        for field in fields:
            values[field] = number
    elif name in ("be", "fe"):
        number = integer_truncated(argument)
        if number is None:
            return TagAction(False, barrier=True)
        values[fields[0]] = number
    elif name == "b":
        number = integer_truncated(argument)
        if number is None:
            return TagAction(False, barrier=True)
        numeric = int(number)
        values["bold"] = "0" if numeric == 0 else ("700" if numeric in (-1, 1) else str(numeric))
    elif name in ("i", "u", "s"):
        number = integer_truncated(argument)
        if number is None:
            return TagAction(False, barrier=True)
        values[fields[0]] = "0" if int(number) == 0 else "1"
    elif name in ("c", "1c", "2c", "3c", "4c"):
        channel = 1 if name == "c" else int(name[0])
        if argument == "" and active_style:
            style_color = (
                active_style.primary,
                active_style.secondary,
                active_style.outline_color,
                active_style.back_color,
            )[channel - 1]
            components = ass_color_components(style_color)
            if not components:
                return TagAction(False, barrier=True)
            value = components[0]
        else:
            value = tag_color(argument, 6)
            if value is None:
                return TagAction(False, barrier=True)
        values["color%d" % channel] = value
    elif name == "alpha":
        value = tag_color(argument, 2)
        if value is None:
            return TagAction(False, barrier=True)
        for field in fields:
            values[field] = value
    elif name in ("1a", "2a", "3a", "4a"):
        value = tag_color(argument, 2)
        if value is None:
            return TagAction(False, barrier=True)
        values[fields[0]] = value
    return TagAction(True, fields, values)


def apply_action(state: dict[str, str], action: TagAction) -> None:
    for field, value in action.values.items():
        state[field] = value


def action_is_noop(state: dict[str, str], action: TagAction) -> bool:
    return bool(action.values) and all(state.get(field) == value for field, value in action.values.items())


def nested_transform_info(
    piece: TagPiece,
    state: dict[str, str],
    styles: dict[str, AssStyle],
    original_style: str,
    wrap_style: int,
    event_duration_ms: int | None,
) -> tuple[set[str] | None, bool]:
    parsed = transform_arguments(piece.argument)
    if parsed is None:
        return None, False
    prefix, modifiers = parsed
    if not modifiers.strip():
        return set(), True
    numeric_prefix: list[Decimal] = []
    for value in prefix:
        number = canonical_decimal(value)
        if number is None:
            return None, False
        numeric_prefix.append(Decimal(number))
    if len(numeric_prefix) not in (0, 1, 2, 3):
        return None, False
    if len(numeric_prefix) in (2, 3) and event_duration_ms is not None:
        start = numeric_prefix[0]
        if start >= event_duration_ms:
            return set(), True
    if "(" in modifiers or ")" in modifiers:
        return None, False
    fields: set[str] = set()
    trial = dict(state)
    seen_fields: set[str] = set()
    identity = True
    saw = False
    for nested in split_override_content(modifiers):
        if nested.kind != "tag" or not nested.name or nested.name in LINE_TAG_FIELDS or nested.name in KARAOKE_FIELDS:
            return None, False
        action = action_for_piece(nested, trial, styles, original_style, wrap_style)
        if not action.valid or action.barrier or not action.values:
            return None, False
        if any(field in seen_fields for field in action.fields):
            identity = False
        seen_fields.update(action.fields)
        fields.update(action.fields)
        if not action_is_noop(trial, action):
            identity = False
        apply_action(trial, action)
        saw = True
    return fields if saw else set(), identity


def iter_tag_pieces(parts: Iterable[TextPart]) -> Iterator[TagPiece]:
    for part in parts:
        if part.kind == "block":
            for piece in part.pieces:
                if piece.kind == "tag":
                    yield piece


def remove_piece(piece: TagPiece, stats: TextCleanStats) -> None:
    if piece.removed:
        return
    piece.removed = True
    stats.removed_tags += 1
    stats.removed_tag_labels.append("\\" + (piece.name or "?"))


def remove_unknown_piece(piece: TagPiece, stats: TextCleanStats) -> None:
    if piece.removed:
        return
    label = unsupported_tag_label(piece.raw)
    remove_piece(piece, stats)
    stats.removed_unknown_tags += 1
    stats.removed_unknown_tag_labels.append(label or ("\\" + (piece.name or "?")))


def clean_unknown_transform_modifiers(
    piece: TagPiece,
    stats: TextCleanStats,
    depth: int = 0,
) -> bool:
    if depth > 16:
        return False
    parsed = transform_arguments(piece.argument)
    if parsed is None:
        return False
    prefix, modifiers = parsed
    nested = split_override_content(modifiers)
    if not all(item.kind == "tag" or not item.raw.strip() for item in nested):
        return False
    changed = False
    for item in nested:
        if item.kind != "tag":
            continue
        renderer_prefix = renderer_tag_prefix(item.raw)
        if renderer_prefix is None and unsupported_tag_label(item.raw):
            remove_unknown_piece(item, stats)
            changed = True
        elif renderer_prefix == "t":
            changed = clean_unknown_transform_modifiers(item, stats, depth + 1) or changed
    if not changed:
        return False
    modifiers_after = "".join(item.raw for item in nested if not item.removed)
    if not any(item.kind == "tag" and not item.removed for item in nested):
        remove_piece(piece, stats)
        return True
    body = prefix + [modifiers_after]
    piece.raw = "\\t(" + ",".join(body) + ")"
    piece.argument = piece.raw[2:]
    return True


def remove_unknown_tags(parts: Iterable[TextPart], stats: TextCleanStats) -> None:
    for part in parts:
        if part.kind != "block" or not part.is_override:
            continue
        for piece in part.pieces:
            if piece.kind != "tag" or piece.removed:
                continue
            renderer_prefix = renderer_tag_prefix(piece.raw)
            if renderer_prefix is None and unsupported_tag_label(piece.raw):
                remove_unknown_piece(piece, stats)
            elif renderer_prefix == "t":
                clean_unknown_transform_modifiers(piece, stats)


def mark_first_wins(
    parts: list[TextPart],
    initial: dict[str, str],
    styles: dict[str, AssStyle],
    original_style: str,
    wrap_style: int,
    stats: TextCleanStats,
) -> None:
    first_seen: dict[str, bool] = {}
    for piece in iter_tag_pieces(parts):
        if piece.removed or not piece.name:
            continue
        family = {
            "pos": "position",
            "move": "position",
            "org": "origin",
            "fad": "fade",
            "fade": "fade",
            "an": "alignment",
            "a": "alignment",
        }.get(piece.name)
        if not family:
            continue
        action = action_for_piece(piece, initial, styles, original_style, wrap_style)
        if not action.valid:
            continue
        if first_seen.get(family):
            remove_piece(piece, stats)
            continue
        first_seen[family] = True
        if family == "alignment" and action_is_noop(initial, action):
            remove_piece(piece, stats)


def remove_identity_transforms(
    parts: list[TextPart],
    initial: dict[str, str],
    styles: dict[str, AssStyle],
    original_style: str,
    wrap_style: int,
    event_duration_ms: int | None,
    stats: TextCleanStats,
) -> None:
    state = dict(initial)
    for part in parts:
        if part.kind != "block" or not part.is_override:
            continue
        for piece in part.pieces:
            if piece.kind != "tag" or piece.removed:
                continue
            if piece.name == "t":
                _, identity = nested_transform_info(
                    piece,
                    state,
                    styles,
                    original_style,
                    wrap_style,
                    event_duration_ms,
                )
                if identity:
                    remove_piece(piece, stats)
                continue
            action = action_for_piece(piece, state, styles, original_style, wrap_style)
            if action.valid:
                apply_action(state, action)


def collect_transform_analysis(
    parts: list[TextPart],
    initial: dict[str, str],
    styles: dict[str, AssStyle],
    original_style: str,
    wrap_style: int,
    event_duration_ms: int | None,
) -> tuple[set[str] | None, dict[TagPiece, set[str] | None]]:
    fields: set[str] = set()
    all_fields_known = True
    preceding_fields: set[str] = set()
    preceding_fields_known = True
    preceding_by_piece: dict[TagPiece, set[str] | None] = {}
    state = dict(initial)
    for part in parts:
        if part.kind != "block" or not part.is_override:
            continue
        for piece in part.pieces:
            if piece.kind != "tag" or piece.removed:
                continue
            if piece.name == "t":
                nested, _ = nested_transform_info(
                    piece,
                    state,
                    styles,
                    original_style,
                    wrap_style,
                    event_duration_ms,
                )
                if nested is None:
                    all_fields_known = False
                    preceding_fields_known = False
                else:
                    fields.update(nested)
                    if preceding_fields_known:
                        preceding_fields.update(nested)
                continue
            preceding_by_piece[piece] = (
                set(preceding_fields) if preceding_fields_known else None
            )
            action = action_for_piece(piece, state, styles, original_style, wrap_style)
            if action.valid:
                apply_action(state, action)
    return (fields if all_fields_known else None), preceding_by_piece


def remove_fully_transparent_color_writes(
    parts: list[TextPart],
    initial: dict[str, str],
    styles: dict[str, AssStyle],
    original_style: str,
    wrap_style: int,
    protected_fields: set[str] | None,
    stats: TextCleanStats,
) -> None:
    """Remove color writes whose complete lifetime is statically transparent."""
    if protected_fields is None:
        return

    eligible_channels = {
        channel
        for channel in range(1, 5)
        if "color%d" % channel not in protected_fields
        and "alpha%d" % channel not in protected_fields
    }
    if not eligible_channels:
        return

    state = dict(initial)
    current_writer: dict[int, TagPiece | None] = {
        channel: None for channel in eligible_channels
    }
    candidates: set[TagPiece] = set()
    live: set[TagPiece] = set()

    for part in parts:
        if part.kind == "text":
            if part.raw:
                for channel, writer in current_writer.items():
                    if writer is not None and state.get("alpha%d" % channel) != "FF":
                        live.add(writer)
            continue
        if not part.is_override:
            # Verified extradata-reference comments are renderer-inert. Other
            # comments have already stopped semantic cleanup for the event.
            continue
        for piece in part.pieces:
            if piece.kind != "tag" or piece.removed:
                continue
            if piece.name == "t":
                # Candidate channels touched by any transform were excluded
                # above; disjoint transforms do not affect their visibility.
                continue
            action = action_for_piece(
                piece, state, styles, original_style, wrap_style
            )
            if not action.valid:
                # Do not make a visibility claim across malformed or retained
                # extension syntax whose renderer interpretation is uncertain.
                return

            written_colors = {
                channel
                for channel in eligible_channels
                if "color%d" % channel in action.values
            }
            explicit_channel: int | None = None
            if piece.name in ("c", "1c", "2c", "3c", "4c"):
                explicit_channel = 1 if piece.name == "c" else int(piece.name[0])
            for channel in written_colors:
                if channel == explicit_channel:
                    current_writer[channel] = piece
                    candidates.add(piece)
                else:
                    # A Style reset overwrites the prior explicit color write.
                    current_writer[channel] = None
            apply_action(state, action)

    for piece in candidates - live:
        remove_piece(piece, stats)


def raw_empty_reset(piece: TagPiece) -> bool:
    return piece.name in ("c", "1c", "2c", "3c", "4c") and piece.argument == ""


def process_override_group(
    blocks: list[TextPart],
    start_state: dict[str, str],
    styles: dict[str, AssStyle],
    original_style: str,
    wrap_style: int,
    protected_fields: set[str] | None,
    preceding_transform_fields: dict[TagPiece, set[str] | None],
    has_following_text: bool,
    stats: TextCleanStats,
) -> dict[str, str]:
    pieces = [piece for block in blocks for piece in block.pieces]
    for _ in range(5):
        changed = False
        state = dict(start_state)
        seen_empty_resets: set[str] = set()
        seen_style_resets: set[str] = set()
        for piece in pieces:
            if piece.kind != "tag" or piece.removed:
                continue
            if piece.name == "t":
                continue
            action = action_for_piece(piece, state, styles, original_style, wrap_style)
            if not action.valid:
                continue
            protected = protected_fields is None or bool(set(action.fields) & protected_fields)
            preceding = preceding_transform_fields.get(piece)
            blocked_by_preceding_transform = preceding is None or bool(
                set(action.fields) & preceding
            )
            may_remove_noop = not blocked_by_preceding_transform and (
                piece.name in STATIC_TAG_FIELDS
                or piece.name in ("q", "p", "pbo")
            )
            if piece.name == "r":
                if piece.argument in seen_style_resets and action_is_noop(state, action):
                    remove_piece(piece, stats)
                    changed = True
                    continue
                seen_style_resets.add(piece.argument)
            elif raw_empty_reset(piece):
                if piece.name in seen_empty_resets and action_is_noop(state, action):
                    remove_piece(piece, stats)
                    changed = True
                    continue
                seen_empty_resets.add(piece.name)
            elif may_remove_noop and action_is_noop(state, action):
                remove_piece(piece, stats)
                changed = True
                continue
            apply_action(state, action)

        overwritten: set[str] = set()
        explicitly_overwritten: set[str] = set()
        for piece in reversed(pieces):
            if piece.kind != "tag" or piece.removed:
                continue
            if piece.name == "t":
                nested = protected_fields
                if nested is None:
                    overwritten.clear()
                    explicitly_overwritten.clear()
                else:
                    overwritten.difference_update(nested)
                    explicitly_overwritten.difference_update(nested)
                continue
            action = action_for_piece(piece, start_state, styles, original_style, wrap_style)
            if not action.valid:
                if piece.name is None:
                    overwritten.clear()
                    explicitly_overwritten.clear()
                continue
            pure = piece.name in STATIC_TAG_FIELDS and bool(action.values)
            protected = protected_fields is None or bool(set(action.fields) & protected_fields)
            if (
                pure
                and not protected
                and set(action.fields).issubset(overwritten)
                and (
                    not raw_empty_reset(piece)
                    or set(action.fields).issubset(explicitly_overwritten)
                )
            ):
                remove_piece(piece, stats)
                changed = True
                continue
            if action.values:
                overwritten.update(action.fields)
                if not raw_empty_reset(piece):
                    explicitly_overwritten.update(action.fields)

        if not has_following_text:
            for piece in pieces:
                if piece.kind != "tag" or piece.removed or piece.name not in STATIC_TAG_FIELDS:
                    continue
                action = action_for_piece(piece, start_state, styles, original_style, wrap_style)
                protected = protected_fields is None or bool(set(action.fields) & protected_fields)
                if action.valid and not protected and not raw_empty_reset(piece):
                    remove_piece(piece, stats)
                    changed = True
        if not changed:
            break

    state = dict(start_state)
    for piece in pieces:
        if piece.kind != "tag" or piece.removed or piece.name == "t":
            continue
        action = action_for_piece(piece, state, styles, original_style, wrap_style)
        if action.valid:
            apply_action(state, action)
    return state


def block_can_merge(
    block: TextPart,
    state: dict[str, str],
    styles: dict[str, AssStyle],
    original_style: str,
    wrap_style: int,
) -> bool:
    if not block.is_override:
        return False
    trial = dict(state)
    for piece in block.pieces:
        if piece.kind != "tag" or piece.removed:
            continue
        if piece.name == "t":
            if transform_arguments(piece.argument) is None:
                return False
            continue
        action = action_for_piece(piece, trial, styles, original_style, wrap_style)
        if not action.valid:
            return False
        apply_action(trial, action)
    return True


def transform_descriptor_fields(
    piece: TagPiece,
    state: dict[str, str],
    styles: dict[str, AssStyle],
    original_style: str,
    wrap_style: int,
    event_duration_ms: int | None,
) -> tuple[str, ...] | None:
    fields, _ = nested_transform_info(
        piece,
        state,
        styles,
        original_style,
        wrap_style,
        event_duration_ms,
    )
    if fields is None:
        return None
    return tuple(sorted(fields | {"animation_epoch"}))


def reorder_descriptor(
    piece: TagPiece,
    state: dict[str, str],
    styles: dict[str, AssStyle],
    original_style: str,
    wrap_style: int,
    event_duration_ms: int | None,
    clip_count: int,
) -> tuple[int, tuple[str, ...]] | None:
    if piece.removed or not piece.name or piece.name not in SAFE_REORDER_RANK:
        return None
    name = piece.name
    if name == "t":
        fields = transform_descriptor_fields(
            piece, state, styles, original_style, wrap_style, event_duration_ms
        )
        return (SAFE_REORDER_RANK[name], fields) if fields else None
    if name in ("clip", "iclip") and clip_count != 1:
        return None
    action = action_for_piece(piece, state, styles, original_style, wrap_style)
    if not action.valid:
        return None
    if name == "r":
        # Both target renderers keep the already parsed karaoke timeline when
        # a reset and a karaoke tag exchange positions inside one override
        # block. The reset still conflicts with transforms and Style fields.
        fields = ("animation_epoch",) + STYLE_STATE_FIELDS
    elif name in KARAOKE_FIELDS:
        # Within one override block, both target renderers apply the final
        # static color/alpha/outline state to the syllable regardless of
        # whether that static tag appears immediately before or after the
        # karaoke tag. Keep only the true sequencing dependencies here.
        fields = ("karaoke_timeline",)
    else:
        fields = action.fields
    if not fields:
        return None
    return SAFE_REORDER_RANK[name], tuple(fields)


def deterministic_reorder_actions_commute(
    left: TagPiece,
    right: TagPiece,
    state: dict[str, str],
    styles: dict[str, AssStyle],
    original_style: str,
    wrap_style: int,
) -> bool:
    """Prove equality for overlapping deterministic Style-state writes."""
    allowed = set(STATIC_TAG_FIELDS) | {"r"}
    if left.name not in allowed or right.name not in allowed:
        return False
    # xy-VSFilter does not model the undocumented \fsc compatibility reset
    # across \r like the abstract active-Style state below. Real rendering
    # differs even when both simulated end states are identical.
    if {left.name, right.name} == {"fsc", "r"}:
        return False

    def result(order: tuple[TagPiece, TagPiece]) -> dict[str, str] | None:
        trial = dict(state)
        for piece in order:
            action = action_for_piece(
                piece, trial, styles, original_style, wrap_style
            )
            if not action.valid or not action.values:
                return None
            apply_action(trial, action)
        return trial

    original = result((left, right))
    swapped = result((right, left))
    return original is not None and original == swapped


def safely_reorder_block(
    block: TextPart,
    start_state: dict[str, str],
    styles: dict[str, AssStyle],
    original_style: str,
    wrap_style: int,
    event_duration_ms: int | None,
    clip_count: int,
) -> int:
    active = [piece for piece in block.pieces if not piece.removed]
    tag_pieces = [piece for piece in active if piece.kind == "tag"]
    if len(active) != len(tag_pieces):
        if any(piece.raw.strip() for piece in active if piece.kind != "tag"):
            return 0
    before = {piece: index for index, piece in enumerate(tag_pieces)}
    pieces = list(tag_pieces)
    changed = True
    while changed:
        changed = False
        prefix_state = dict(start_state)
        index = 0
        while index + 1 < len(pieces):
            left = pieces[index]
            right = pieces[index + 1]
            left_descriptor = reorder_descriptor(
                left,
                prefix_state,
                styles,
                original_style,
                wrap_style,
                event_duration_ms,
                clip_count,
            )
            right_descriptor = reorder_descriptor(
                right,
                prefix_state,
                styles,
                original_style,
                wrap_style,
                event_duration_ms,
                clip_count,
            )
            may_swap = bool(
                left_descriptor
                and right_descriptor
                and right_descriptor[0] < left_descriptor[0]
                and (
                    not (set(left_descriptor[1]) & set(right_descriptor[1]))
                    or deterministic_reorder_actions_commute(
                        left,
                        right,
                        prefix_state,
                        styles,
                        original_style,
                        wrap_style,
                    )
                )
            )
            if may_swap:
                pieces[index], pieces[index + 1] = right, left
                changed = True
                if index:
                    index -= 1
                continue
            action = action_for_piece(left, prefix_state, styles, original_style, wrap_style)
            if action.valid:
                apply_action(prefix_state, action)
            index += 1
    moved = sum(before[piece] != index for index, piece in enumerate(pieces))
    if moved:
        block.pieces = pieces
    return moved


def normalize_drawing_parts(
    parts: list[TextPart],
    initial: dict[str, str],
    styles: dict[str, AssStyle],
    original_style: str,
    wrap_style: int,
) -> int:
    state = dict(initial)
    changed = 0
    for part in parts:
        if part.kind == "block":
            if not part.is_override:
                continue
            for piece in part.pieces:
                if piece.kind != "tag" or piece.removed or piece.name == "t":
                    continue
                action = action_for_piece(piece, state, styles, original_style, wrap_style)
                if action.valid:
                    apply_action(state, action)
        elif part.raw and int(Decimal(state.get("p", "0"))) > 0:
            result = normalize_drawing_text(part.raw)
            if result:
                normalized, count = result
                if normalized != part.raw:
                    part.raw = normalized
                    changed += max(1, count)
    return changed


def clean_ass_text(
    text: str,
    style_name: str,
    styles: dict[str, AssStyle],
    *,
    wrap_style: int = 0,
    safe_reorder: bool = False,
    clean_unknown_tags: bool = False,
    event_duration_ms: int | None = None,
) -> tuple[str, TextCleanStats]:
    stats = TextCleanStats()
    parts = parse_text_parts(text)
    if clean_unknown_tags:
        remove_unknown_tags(parts, stats)
    stats.unsupported_tags = collect_unsupported_tags(parts)
    stats.renderer_specific_tags = collect_renderer_specific_tags(text)
    stats.normalized_numbers += normalize_parts(parts)
    opaque_comment = any(
        part.kind == "block"
        and not part.is_override
        and re.fullmatch(r"(?:=\d+)+", part.raw) is None
        for part in parts
    )
    if opaque_comment or stats.renderer_specific_tags:
        # Arbitrary brace comments can conceal renderer-extension syntax, and
        # xy-VSFilter's HTML compatibility tags alter the effective style
        # state outside ASS override blocks.  Keep semantic cleanup/reordering
        # disabled for the whole event unless the block is a verified Aegisub
        # extradata reference, which both target renderers ignore as a comment.
        return render_text_parts(parts, stats), stats
    style = styles.get(style_name)
    if not style:
        return render_text_parts(parts, stats), stats
    initial = style_state(style, wrap_style)

    mark_first_wins(parts, initial, styles, style_name, wrap_style, stats)
    remove_identity_transforms(
        parts,
        initial,
        styles,
        style_name,
        wrap_style,
        event_duration_ms,
        stats,
    )
    protected_fields, preceding_transform_fields = collect_transform_analysis(
        parts,
        initial,
        styles,
        style_name,
        wrap_style,
        event_duration_ms,
    )

    state = dict(initial)
    index = 0
    while index < len(parts):
        part = parts[index]
        if part.kind != "block" or not part.is_override:
            index += 1
            continue
        end = index + 1
        blocks = [part]
        while end < len(parts) and parts[end].kind == "block" and parts[end].is_override:
            blocks.append(parts[end])
            end += 1
        has_following_text = any(
            candidate.kind == "text" and candidate.raw != "" for candidate in parts[end:]
        )
        state = process_override_group(
            blocks,
            state,
            styles,
            style_name,
            wrap_style,
            protected_fields,
            preceding_transform_fields,
            has_following_text,
            stats,
        )
        index = end

    remove_fully_transparent_color_writes(
        parts,
        initial,
        styles,
        style_name,
        wrap_style,
        protected_fields,
        stats,
    )

    stats.normalized_numbers += normalize_drawing_parts(
        parts, initial, styles, style_name, wrap_style
    )

    if safe_reorder:
        clip_count = sum(
            1
            for piece in iter_tag_pieces(parts)
            if not piece.removed and piece.name in ("clip", "iclip")
        )
        reorder_state = dict(initial)
        for part in parts:
            if part.kind != "block" or not part.is_override:
                continue
            stats.reordered_tags += safely_reorder_block(
                part,
                reorder_state,
                styles,
                style_name,
                wrap_style,
                event_duration_ms,
                clip_count,
            )
            for piece in part.pieces:
                if piece.kind != "tag" or piece.removed or piece.name == "t":
                    continue
                action = action_for_piece(
                    piece, reorder_state, styles, style_name, wrap_style
                )
                if action.valid:
                    apply_action(reorder_state, action)

    result = render_text_parts(parts, stats, styles, style_name, wrap_style)
    if result != text:
        second_parts = parse_text_parts(result)
        second_normalized = normalize_parts(second_parts)
        if second_normalized:
            stats.normalized_numbers += second_normalized
            result = render_text_parts(second_parts, TextCleanStats())
    return result, stats


def render_text_parts(
    parts: list[TextPart],
    stats: TextCleanStats,
    styles: dict[str, AssStyle] | None = None,
    style_name: str = "",
    wrap_style: int = 0,
) -> str:
    output: list[str] = []
    state: dict[str, str] = {}
    if styles and style_name in styles:
        state = style_state(styles[style_name], wrap_style)
    index = 0
    while index < len(parts):
        part = parts[index]
        if part.kind != "block":
            output.append(part.raw)
            index += 1
            continue
        if not part.is_override:
            output.append("{" + part.raw + "}")
            index += 1
            continue
        end = index + 1
        blocks = [part]
        while end < len(parts) and parts[end].kind == "block" and parts[end].is_override:
            blocks.append(parts[end])
            end += 1
        contents = [block.content() for block in blocks if block.content()]
        removed_blocks = sum(1 for block in blocks if not block.content())
        stats.removed_blocks += removed_blocks
        can_merge = bool(styles and state) and all(
            block_can_merge(block, state, styles, style_name, wrap_style) for block in blocks
        )
        if can_merge and contents:
            output.append("{" + "".join(contents) + "}")
        else:
            output.extend("{" + content + "}" for content in contents)
        if styles and state:
            for block in blocks:
                for piece in block.pieces:
                    if piece.kind != "tag" or piece.removed or piece.name == "t":
                        continue
                    action = action_for_piece(piece, state, styles, style_name, wrap_style)
                    if action.valid:
                        apply_action(state, action)
        index = end
    return "".join(output)


TIME_RELATIVE_TAGS = {"t", "fad", "fade", "k", "K", "kf", "ko", "kt"}


def event_text_is_always_fully_transparent(
    event: AssEvent,
    styles: dict[str, AssStyle],
    wrap_style: int,
    clean_unknown_tags: bool,
) -> bool:
    """Prove that every renderable span has all four static alpha channels at FF."""
    style_name = event.get("Style", "Default")
    style = styles.get(style_name)
    start_ms, end_ms = event.start_ms, event.end_ms
    if not style or start_ms is None or end_ms is None or end_ms <= start_ms:
        return False
    text = event.text
    if collect_renderer_specific_tags(text):
        return False
    parts = parse_text_parts(text)
    stats = TextCleanStats()
    if clean_unknown_tags:
        remove_unknown_tags(parts, stats)
    if collect_unsupported_tags(parts):
        return False
    opaque_comment = any(
        part.kind == "block"
        and not part.is_override
        and re.fullmatch(r"(?:=\d+)+", part.raw) is None
        for part in parts
    )
    if opaque_comment:
        return False

    initial = style_state(style, wrap_style)
    duration = end_ms - start_ms
    remove_identity_transforms(
        parts,
        initial,
        styles,
        style_name,
        wrap_style,
        duration,
        stats,
    )
    protected_fields, _ = collect_transform_analysis(
        parts,
        initial,
        styles,
        style_name,
        wrap_style,
        duration,
    )
    alpha_fields = {"alpha1", "alpha2", "alpha3", "alpha4"}
    if protected_fields is None or protected_fields & alpha_fields:
        return False

    state = dict(initial)
    saw_renderable_text = False
    for part in parts:
        if part.kind == "text":
            if part.raw:
                saw_renderable_text = True
                if any(state.get(field) != "FF" for field in alpha_fields):
                    return False
            continue
        if not part.is_override:
            continue
        for piece in part.pieces:
            if piece.kind != "tag" or piece.removed:
                continue
            if piece.name == "t":
                continue
            action = action_for_piece(
                piece, state, styles, style_name, wrap_style
            )
            if not action.valid:
                return False
            apply_action(state, action)
    return saw_renderable_text


def event_collision_layer(event: AssEvent) -> str:
    if event.field_index("Layer") is None:
        return "0"
    value = integer_truncated(event.get("Layer"))
    return value if value is not None else "0"


def fully_transparent_events_safe_to_remove(
    document: AssDocument,
    clean_unknown_tags: bool,
) -> list[AssEvent]:
    """Find invisible events whose deletion cannot alter collision placement."""
    dialogues = [event for event in document.events if event.kind == "Dialogue"]
    transparent = {
        id(event)
        for event in dialogues
        if event_text_is_always_fully_transparent(
            event,
            document.styles,
            document.wrap_style,
            clean_unknown_tags,
        )
    }
    if not transparent:
        return []

    safe: set[int] = {
        id(event)
        for event in dialogues
        if id(event) in transparent and event_has_explicit_position(event)
    }
    collision_events = [
        event
        for event in dialogues
        if not event_has_explicit_position(event)
        and event.start_ms is not None
        and event.end_ms is not None
        and event.end_ms > event.start_ms
    ]
    remaining = {id(event): event for event in collision_events}
    while remaining:
        _, seed = remaining.popitem()
        component = [seed]
        frontier = [seed]
        while frontier:
            current = frontier.pop()
            current_start, current_end = current.start_ms, current.end_ms
            if current_start is None or current_end is None:
                continue
            for other_id, other in list(remaining.items()):
                if event_collision_layer(other) != event_collision_layer(current):
                    continue
                other_start, other_end = other.start_ms, other.end_ms
                if (
                    other_start is not None
                    and other_end is not None
                    and overlaps(current_start, current_end, other_start, other_end)
                ):
                    component.append(other)
                    frontier.append(other)
                    del remaining[other_id]
        if all(id(event) in transparent for event in component):
            safe.update(id(event) for event in component)

    return [event for event in dialogues if id(event) in safe]


def event_has_time_relative_content(event: AssEvent) -> bool:
    if event.get("Effect").strip():
        return True
    for piece in iter_tag_pieces(parse_text_parts(event.text)):
        if piece.name in TIME_RELATIVE_TAGS:
            return True
    return False


def event_has_explicit_position(event: AssEvent) -> bool:
    for piece in iter_tag_pieces(parse_text_parts(event.text)):
        if piece.name in ("pos", "move") and normalize_parenthesized_tag(
            piece.name, piece.argument
        ):
            return True
    return False


def event_has_unsupported_override(event: AssEvent) -> bool:
    parts = parse_text_parts(event.text)
    if collect_unsupported_tags(parts):
        return True
    return any(part.kind == "block" and not part.is_override for part in parts)


def merge_key(event: AssEvent) -> tuple[str, ...]:
    excluded = {
        index
        for name in ("Start", "End")
        if (index := event.field_index(name)) is not None
    }
    return tuple(
        value for index, value in enumerate(event.values) if index not in excluded
    )


def overlaps(start_a: int, end_a: int, start_b: int, end_b: int) -> bool:
    return start_a < end_b and start_b < end_a


def merge_consecutive_identical_events(document: AssDocument) -> tuple[int, int]:
    dialogues = [event for event in document.events if event.kind == "Dialogue"]
    groups: list[list[AssEvent]] = []
    index = 0
    while index < len(dialogues):
        group = [dialogues[index]]
        cursor = index + 1
        while cursor < len(dialogues):
            previous = group[-1]
            candidate = dialogues[cursor]
            if candidate.line_index != previous.line_index + 1:
                break
            if previous.end_ms is None or candidate.start_ms != previous.end_ms:
                break
            if merge_key(previous) != merge_key(candidate):
                break
            group.append(candidate)
            cursor += 1
        if len(group) > 1:
            groups.append(group)
        index = cursor if cursor > index + 1 else index + 1

    accepted: list[list[AssEvent]] = []
    for group in groups:
        if any(event_has_time_relative_content(event) for event in group):
            continue
        if any(event_has_unsupported_override(event) for event in group):
            continue
        group_start = group[0].start_ms
        group_end = group[-1].end_ms
        if group_start is None or group_end is None:
            continue
        if not event_has_explicit_position(group[0]):
            group_ids = {id(event) for event in group}
            collision = False
            for other in dialogues:
                if id(other) in group_ids:
                    continue
                other_start, other_end = other.start_ms, other.end_ms
                if (
                    other_start is not None
                    and other_end is not None
                    and overlaps(group_start, group_end, other_start, other_end)
                ):
                    collision = True
                    break
            if collision:
                continue
        accepted.append(group)

    delete_indices: list[int] = []
    for group in accepted:
        first = group[0]
        first.set("End", group[-1].get("End"))
        document.lines[first.line_index] = first.render()
        delete_indices.extend(event.line_index for event in group[1:])
    for line_index in sorted(delete_indices, reverse=True):
        del document.lines[line_index]
    if delete_indices:
        document.rebuild_events()
    return len(accepted), len(delete_indices)


def default_clean_output_path(input_path: Path) -> Path:
    return input_path.with_name(input_path.stem + ".Cleaned" + input_path.suffix)


def default_clean_report_path(input_path: Path, report_format: str = "html") -> Path:
    suffix = ".html" if str(report_format).casefold() in ("html", "htm") else ".md"
    return input_path.with_name(input_path.stem + ".Clean Report" + suffix)


SUBTITLE_SUFFIXES = (".ass", ".ssa")


def is_generated_cleaned_subtitle(path: Path) -> bool:
    return (
        path.suffix.casefold() in SUBTITLE_SUFFIXES
        and path.stem.casefold().endswith(".cleaned")
    )


def discover_subtitle_inputs(
    values: Sequence[str | os.PathLike[str]],
    recursive: bool = False,
) -> list[SubtitleInput]:
    """Expand explicit subtitle files and folders into a stable de-duplicated list."""
    discovered: dict[str, SubtitleInput] = {}
    for raw_value in values:
        candidate = Path(raw_value).expanduser().resolve()
        if candidate.is_file():
            if candidate.suffix.casefold() not in SUBTITLE_SUFFIXES:
                raise ValueError("Only .ass or .ssa files are supported: %s" % candidate)
            item = SubtitleInput(candidate, None)
            key = os.path.normcase(str(candidate))
            discovered.setdefault(key, item)
            continue
        if not candidate.is_dir():
            raise FileNotFoundError("Input file or directory does not exist: %s" % candidate)

        iterator = candidate.rglob("*") if recursive else candidate.iterdir()
        folder_items: list[Path] = []
        try:
            for path in iterator:
                if (
                    path.is_file()
                    and path.suffix.casefold() in SUBTITLE_SUFFIXES
                    and not is_generated_cleaned_subtitle(path)
                ):
                    folder_items.append(path.resolve())
        except OSError as exc:
            raise OSError("Could not scan subtitle directory %s: %s" % (candidate, exc)) from exc
        for path in sorted(folder_items, key=lambda value: str(value).casefold()):
            key = os.path.normcase(str(path))
            item = SubtitleInput(path, candidate)
            previous = discovered.get(key)
            if previous is None or previous.source_root is None:
                discovered[key] = item

    if not discovered:
        depth = "including subdirectories" if recursive else "top level only"
        raise ValueError("No ASS/SSA subtitles were found in the selected inputs (%s)" % depth)
    return list(discovered.values())


def subtitle_relative_path(item: SubtitleInput) -> Path:
    if item.source_root is None:
        return Path(item.path.name)
    try:
        return item.path.relative_to(item.source_root)
    except ValueError:
        return Path(item.path.name)


def output_root_for_item(
    item: SubtitleInput,
    output_dir: str | os.PathLike[str],
) -> Path:
    requested = Path(output_dir).expanduser()
    if requested.drive and not requested.is_absolute():
        raise ValueError(
            "Output directory %r is drive-relative; use an absolute path or a relative path without a drive letter"
            % str(output_dir)
        )
    if requested.is_absolute():
        return requested.resolve()
    source_base = item.source_root if item.source_root is not None else item.path.parent
    return (source_base / requested).resolve()


def batch_clean_output_path(
    item: SubtitleInput,
    output_dir: str | os.PathLike[str] | None,
) -> Path:
    if output_dir is None:
        return default_clean_output_path(item.path)
    output_root = output_root_for_item(item, output_dir)
    relative = subtitle_relative_path(item)
    return (
        output_root
        / relative.parent
        / (relative.stem + ".Cleaned" + relative.suffix)
    ).resolve()


def batch_clean_report_path(
    item: SubtitleInput,
    report_dir: Path | None,
    report_format: str,
) -> Path:
    if report_dir is None:
        return default_clean_report_path(item.path, report_format)
    relative = subtitle_relative_path(item)
    suffix = ".html" if report_format.casefold() == "html" else ".md"
    return (
        report_dir
        / relative.parent
        / (relative.stem + ".Clean Report" + suffix)
    ).resolve()


def strip_extradata_prefix(text: str) -> tuple[str, int]:
    match = EXTRADATA_REFERENCE_RE.match(text)
    if not match:
        return text, 0
    return text[match.end() :], len(re.findall(r"=\d+", match.group(0)))


def clean_ass_file(
    input_path: Path,
    output_path: Path,
    options: CleanOptions,
) -> CleanResult:
    input_path = input_path.resolve()
    output_path = output_path.resolve()
    document = read_ass_document(input_path)
    result = CleanResult(input_path, output_path)
    remove_refs = options.clean_extradata_refs or options.clean_extradata_section

    if options.clean_comments:
        result.comment_lines_removed = remove_comment_events(document)

    transparent_events = (
        fully_transparent_events_safe_to_remove(
            document, options.clean_unknown_tags
        )
        if options.remove_transparent_dialogues
        else []
    )
    transparent_ids = {id(event) for event in transparent_events}
    result.transparent_dialogues_removed = len(transparent_events)

    for event in list(document.events):
        before = event.text
        if id(event) in transparent_ids:
            result.processed_dialogues += 1
            result.changed_dialogues += 1
            _, marker_count = strip_extradata_prefix(before)
            result.marker_references_removed += marker_count
            result.changes.append(
                LineChange(
                    event_number=event.event_number,
                    kind=event.kind,
                    start=event.get("Start"),
                    end=event.get("End"),
                    style=event.get("Style"),
                    before=before,
                    after="",
                    stats=TextCleanStats(),
                    removed_event=True,
                )
            )
            continue
        working = before
        marker_count = 0
        if remove_refs:
            working, marker_count = strip_extradata_prefix(working)
            result.marker_references_removed += marker_count
        line_stats = TextCleanStats()
        if event.kind == "Dialogue":
            result.processed_dialogues += 1
            duration = None
            if event.start_ms is not None and event.end_ms is not None:
                duration = max(0, event.end_ms - event.start_ms)
            working, line_stats = clean_ass_text(
                working,
                event.get("Style", "Default"),
                document.styles,
                wrap_style=document.wrap_style,
                safe_reorder=options.safe_reorder,
                clean_unknown_tags=options.clean_unknown_tags,
                event_duration_ms=duration,
            )
            result.stats.add(line_stats)
        if working != before:
            event.text = working
            document.lines[event.line_index] = event.render()
            if event.kind == "Dialogue":
                result.changed_dialogues += 1
            result.changes.append(
                LineChange(
                    event_number=event.event_number,
                    kind=event.kind,
                    start=event.get("Start"),
                    end=event.get("End"),
                    style=event.get("Style"),
                    before=before,
                    after=working,
                    stats=line_stats,
                )
            )

    for line_index in sorted(
        (event.line_index for event in transparent_events), reverse=True
    ):
        del document.lines[line_index]
    if transparent_events:
        document.rebuild_events()

    if options.merge_lines:
        result.merged_groups, result.merged_lines = merge_consecutive_identical_events(document)

    if options.clean_project_garbage:
        result.project_garbage_removed, _ = remove_ass_section(
            document, "Aegisub Project Garbage"
        )
    if options.clean_extradata_section:
        (
            result.extradata_section_removed,
            result.extradata_records_removed,
        ) = remove_ass_section(document, "Aegisub Extradata")

    write_ass_document(document, output_path)
    return result


def markdown_code(value: str) -> str:
    fence = "```"
    while fence in value:
        fence += "`"
    return fence + "ass\n" + value + "\n" + fence


def format_removed_labels(labels: Sequence[str]) -> str:
    counts = collections.Counter(labels)
    return ", ".join(
        label + ((" × %d" % count) if count > 1 else "")
        for label, count in sorted(counts.items())
    )


def build_clean_report(result: CleanResult) -> str:
    output = [
        "# ASS Cleanup Report",
        "",
        "## Files",
        "",
        "- Input: `%s`" % result.input_path,
        "- Output: `%s`" % result.output_path,
        "",
        "## Summary",
        "",
        "| Item | Count |",
        "| --- | ---: |",
        "| Processed Dialogue rows | %d |" % result.processed_dialogues,
        "| Modified Dialogue rows | %d |" % result.changed_dialogues,
        "| Removed always-transparent Dialogue rows | %d |"
        % result.transparent_dialogues_removed,
        "| Removed Comment rows | %d |" % result.comment_lines_removed,
        "| Removed tags | %d |" % result.stats.removed_tags,
        "| Removed tags unknown to both renderers | %d |" % result.stats.removed_unknown_tags,
        "| Compatibility-safe reordered tags | %d |" % result.stats.reordered_tags,
        "| Normalized numeric/drawing items | %d |" % result.stats.normalized_numbers,
        "| Removed empty override blocks | %d |" % result.stats.removed_blocks,
        "| Removed extradata reference IDs | %d |" % result.marker_references_removed,
        "| Merged consecutive identical groups | %d |" % result.merged_groups,
        "| Dialogue rows removed by merging | %d |" % result.merged_lines,
        "",
        "## File-level metadata",
        "",
        "- `[Aegisub Project Garbage]`: %s."
        % ("removed" if result.project_garbage_removed else "not removed or not present"),
        "- `[Aegisub Extradata]`: %s."
        % ("removed" if result.extradata_section_removed else "not removed or not present"),
    ]
    if result.extradata_section_removed:
        output.append("- Removed extradata records: %d." % result.extradata_records_removed)
    output.append("")
    unsupported = sorted(set(result.stats.unsupported_tags))
    removed_unknown = result.stats.removed_unknown_tag_labels
    renderer_specific = sorted(set(result.stats.renderer_specific_tags))
    if removed_unknown:
        output.extend(
            [
                "## Removed tags unknown to both renderers",
                "",
                format_removed_labels(removed_unknown),
                "",
            ]
        )
    if unsupported:
        output.extend(
            [
                "## Tags unknown to both renderers",
                "",
                ", ".join("`%s`" % item for item in unsupported),
                "",
            ]
        )
    if renderer_specific:
        output.extend(
            [
                "## Renderer-specific syntax",
                "",
                ", ".join("`%s`" % item for item in renderer_specific),
                "",
            ]
        )
    output.extend(["", "## Change details", ""])
    if not result.changes:
        output.append("No subtitle event text was modified.")
    for change in result.changes:
        output.extend(
            [
                "### Event %d · %s · %s–%s · `%s`"
                % (
                    change.event_number,
                    change.kind,
                    change.start,
                    change.end,
                    change.style.replace("`", "\\`"),
                ),
                "",
            ]
        )
        if change.stats.removed_tag_labels:
            output.extend(
                [
                    "- Removed tags: %s."
                    % format_removed_labels(change.stats.removed_tag_labels),
                    "",
                ]
            )
        if change.stats.reordered_tags:
            output.extend(
                ["- Compatibility-safe reorder: %d tags moved." % change.stats.reordered_tags, ""]
            )
        if change.stats.normalized_numbers:
            output.extend(
                ["- Normalized items: %d." % change.stats.normalized_numbers, ""]
            )
        if change.removed_event:
            output.extend(
                [
                    "- Dialogue row removed: fully transparent for its complete rendered lifetime and collision-safe.",
                    "",
                ]
            )
        output.extend(
            [
                "Before:",
                "",
                markdown_code(change.before),
                "",
                "After:",
                "",
                (
                    "**Dialogue row removed.**"
                    if change.removed_event
                    else markdown_code(change.after)
                ),
                "",
            ]
        )
    return "\n".join(output).rstrip() + "\n"


def combine_markdown_reports(clean_report: str, differential_report: str) -> str:
    differential = differential_report.lstrip("\ufeff")
    differential = re.sub(r"^# ", "## ", differential, count=1)
    differential = re.sub(r"(?m)^## ", "### ", differential)
    differential_section = (
        "## Real rendering differential\n\n" + differential.rstrip()
    )
    insertion_markers = (
        "\n## Removed tags unknown to both renderers\n",
        "\n## Tags unknown to both renderers\n",
        "\n## Renderer-specific syntax\n",
        "\n## Change details\n",
    )
    marker_indices = [
        index
        for marker in insertion_markers
        if (index := clean_report.find(marker)) >= 0
    ]
    marker_index = min(marker_indices) if marker_indices else -1
    if marker_index < 0:
        return clean_report.rstrip() + "\n\n" + differential_section + "\n"
    prefix = clean_report[:marker_index].rstrip()
    details = clean_report[marker_index:].lstrip()
    return prefix + "\n\n" + differential_section + "\n\n" + details.rstrip() + "\n"


def _markdown_table_cells(line: str) -> list[str]:
    value = line.strip()
    if value.startswith("|"):
        value = value[1:]
    if value.endswith("|"):
        value = value[:-1]
    cells: list[str] = []
    current: list[str] = []
    index = 0
    while index < len(value):
        character = value[index]
        if character == "\\" and index + 1 < len(value) and value[index + 1] == "|":
            current.append("|")
            index += 2
            continue
        if character == "|":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(character)
        index += 1
    cells.append("".join(current).strip())
    return cells


def _markdown_inline(value: str) -> str:
    output: list[str] = []
    position = 0
    token_re = re.compile(r"`([^`]*)`|\*\*([^*]+)\*\*")
    for match in token_re.finditer(value):
        output.append(_html.escape(value[position : match.start()]))
        if match.group(1) is not None:
            output.append("<code>%s</code>" % _html.escape(match.group(1)))
        else:
            output.append("<strong>%s</strong>" % _html.escape(match.group(2)))
        position = match.end()
    output.append(_html.escape(value[position:]))
    return "".join(output)


def _is_markdown_table_separator(cells: Sequence[str]) -> bool:
    return bool(cells) and all(
        re.fullmatch(r":?-{3,}:?", cell.strip()) is not None for cell in cells
    )


def markdown_report_to_html(markdown: str, title: str | None = None) -> str:
    """Convert the report subset of Markdown to a fast, self-contained HTML file.

    Event bodies are kept in ``template`` elements until opened.  Large reports
    therefore avoid laying out thousands of before/after ASS blocks at startup.
    """
    lines = markdown.lstrip("\ufeff").splitlines()
    body: list[str] = []
    index = 0
    list_open = False
    event_open = False
    event_count = 0

    def close_list() -> None:
        nonlocal list_open
        if list_open:
            body.append("</ul>")
            list_open = False

    def close_event() -> None:
        nonlocal event_open
        if event_open:
            close_list()
            body.append("</div></template></details>")
            event_open = False

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        fence_match = re.fullmatch(r"(`{3,})([A-Za-z0-9_-]*)", stripped)
        if fence_match:
            close_list()
            fence = fence_match.group(1)
            language = fence_match.group(2)
            index += 1
            code_lines: list[str] = []
            while index < len(lines) and lines[index].strip() != fence:
                code_lines.append(lines[index])
                index += 1
            body.append(
                '<pre><code%s>%s</code></pre>'
                % (
                    (' class="language-%s"' % _html.escape(language))
                    if language
                    else "",
                    _html.escape("\n".join(code_lines)),
                )
            )
            index += 1
            continue

        heading_match = re.fullmatch(r"(#{1,6})\s+(.+)", stripped)
        if heading_match:
            close_list()
            close_event()
            level = len(heading_match.group(1))
            heading = heading_match.group(2)
            if level == 3 and heading.startswith("Event "):
                event_count += 1
                body.append(
                    '<details class="event"><summary>%s</summary>'
                    '<template class="event-template"><div class="event-body">'
                    % _markdown_inline(heading)
                )
                # Close the wrapper inside the template when the event closes.
                event_open = True
            else:
                body.append(
                    "<h%d>%s</h%d>" % (level, _markdown_inline(heading), level)
                )
            index += 1
            continue

        if (
            stripped.startswith("|")
            and index + 1 < len(lines)
            and lines[index + 1].strip().startswith("|")
        ):
            headers = _markdown_table_cells(stripped)
            separators = _markdown_table_cells(lines[index + 1])
            if _is_markdown_table_separator(separators):
                close_list()
                index += 2
                rows: list[list[str]] = []
                while index < len(lines) and lines[index].strip().startswith("|"):
                    row = _markdown_table_cells(lines[index])
                    if len(row) > len(headers) and headers:
                        row = row[: len(headers) - 1] + [
                            " | ".join(row[len(headers) - 1 :])
                        ]
                    rows.append(row)
                    index += 1
                table_class = ""
                if [item.strip() for item in headers] == ["Item", "Count"]:
                    table_class = " table-summary"
                elif "Runtime" in headers:
                    table_class = " table-runtime"
                elif "Mismatched frames" in headers or "Compared frames" in headers:
                    table_class = " table-differential"
                body.append(
                    '<div class="table-wrap%s"><table><thead><tr>' % table_class
                )
                body.extend("<th>%s</th>" % _markdown_inline(cell) for cell in headers)
                body.append("</tr></thead><tbody>")
                for row in rows:
                    body.append("<tr>")
                    body.extend("<td>%s</td>" % _markdown_inline(cell) for cell in row)
                    if len(row) < len(headers):
                        body.extend("<td></td>" for _ in range(len(headers) - len(row)))
                    body.append("</tr>")
                body.append("</tbody></table></div>")
                continue

        if stripped.startswith("- "):
            if not list_open:
                body.append("<ul>")
                list_open = True
            body.append("<li>%s</li>" % _markdown_inline(stripped[2:]))
            index += 1
            continue

        close_list()
        if not stripped:
            index += 1
            continue
        if stripped.startswith(">"):
            body.append(
                '<p class="notice">%s</p>'
                % _markdown_inline(stripped[1:].lstrip())
            )
        elif stripped == "---":
            body.append("<hr>")
        else:
            body.append("<p>%s</p>" % _markdown_inline(stripped))
        index += 1

    close_list()
    close_event()
    body_html = "\n".join(body)
    if title is None:
        first_heading = next(
            (
                match.group(1)
                for line in lines
                if (match := re.fullmatch(r"#\s+(.+)", line.strip()))
            ),
            "ASS Cleanup Report",
        )
        title = re.sub(r"[`*]", "", first_heading)
    escaped_title = _html.escape(title)
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'">
<title>%s</title>
<style>
:root { color-scheme: light dark; --bg:#f7f8fa; --panel:#fff; --text:#202124;
  --muted:#62666d; --line:#dfe1e5; --accent:#1769aa; --code:#f1f3f4; }
@media (prefers-color-scheme:dark) { :root { --bg:#111315; --panel:#1b1e21;
  --text:#e8eaed; --muted:#aeb4bb; --line:#3a3f45; --accent:#7fc1ff; --code:#25292d; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text);
  font:14px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif; }
.toolbar { position:sticky; top:0; z-index:10; display:flex; align-items:center;
  gap:8px; padding:10px max(16px,calc((100%% - 1500px)/2)); background:color-mix(in srgb,var(--panel) 94%%,transparent);
  border-bottom:1px solid var(--line); backdrop-filter:blur(8px); }
.toolbar span { color:var(--muted); margin-right:auto; }
button { border:1px solid var(--line); border-radius:6px; padding:5px 10px;
  background:var(--panel); color:var(--text); cursor:pointer; }
main { max-width:1500px; margin:0 auto; padding:20px 24px 60px; }
h1,h2,h3,h4 { line-height:1.25; margin:1.3em 0 .55em; }
h1 { margin-top:0; } h2 { border-bottom:1px solid var(--line); padding-bottom:.3em; }
p,ul { margin:.55em 0; }
code { background:var(--code); border-radius:4px; padding:.08em .32em;
  font-family:Consolas,"Cascadia Mono",monospace; }
pre { margin:.65em 0; padding:12px; overflow:auto; white-space:pre-wrap;
  overflow-wrap:anywhere; background:var(--code); border:1px solid var(--line); border-radius:7px; }
pre code { padding:0; background:none; }
.table-wrap { overflow-x:auto; overflow-y:hidden; margin:.7em 0 1.2em; }
table { width:100%%; border-collapse:collapse; table-layout:auto; background:var(--panel); }
th,td { border:1px solid var(--line); padding:6px 9px; text-align:left;
  vertical-align:top; overflow-wrap:anywhere; word-break:break-word; }
th { background:var(--code); }
.table-summary { max-width:720px; }
.table-summary table { table-layout:fixed; }
.table-summary th:last-child,.table-summary td:last-child {
  width:14ch; text-align:right; white-space:nowrap; }
.table-runtime table { min-width:760px; }
.table-differential table { min-width:980px; }
.notice { padding:9px 12px; border-left:4px solid var(--accent); background:var(--panel); }
details.event { margin:7px 0; border:1px solid var(--line); border-radius:7px;
  background:var(--panel); content-visibility:auto; contain-intrinsic-size:44px; }
details.event > summary { padding:9px 12px; cursor:pointer; color:var(--accent);
  font-weight:600; overflow-wrap:anywhere; }
.event-body { padding:0 12px 12px; border-top:1px solid var(--line); }
.event-placeholder { padding:8px 12px; color:var(--muted); }
@media print { .toolbar { display:none; } main { max-width:none; padding:0; } }
</style>
</head>
<body>
<div class="toolbar">
  <span>Modified events: %d. Details are loaded on demand to keep the initial preview responsive.</span>
  <button type="button" id="expand">Expand all</button>
  <button type="button" id="collapse">Collapse all</button>
</div>
<main id="report">
%s
</main>
<script>
function materialize(item) {
  const template = item.querySelector(':scope > template.event-template');
  if (template) { item.appendChild(template.content); template.remove(); }
}
document.addEventListener('toggle', function (event) {
  const item = event.target;
  if (item instanceof HTMLDetailsElement && item.classList.contains('event') && item.open) {
    materialize(item);
  }
}, true);
document.getElementById('expand').addEventListener('click', function () {
  document.querySelectorAll('details.event').forEach(function (item) {
    materialize(item); item.open = true;
  });
});
document.getElementById('collapse').addEventListener('click', function () {
  document.querySelectorAll('details.event').forEach(function (item) { item.open = false; });
});
</script>
</body>
</html>
""" % (escaped_title, event_count, body_html)


def report_is_html(path: Path) -> bool:
    return path.suffix.casefold() in (".html", ".htm")


def write_report(path: Path, markdown: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if report_is_html(path):
        path.write_text(
            markdown_report_to_html(markdown),
            encoding="utf-8",
        )
    else:
        path.write_text(markdown, encoding="utf-8-sig")


@dataclasses.dataclass(frozen=True)
class Case:
    case_id: str
    description: str
    original: str
    cleaned: str
    times: tuple[float, ...]
    original_dialogues: tuple[str, ...] = ()
    cleaned_dialogues: tuple[str, ...] = ()


@dataclasses.dataclass
class Result:
    renderer: str
    case_id: str
    time: float
    status: str
    changed_pixels: int = 0
    total_pixels: int = 0
    max_channel_delta: int = 0
    detail: str = ""
    background: str = "black"
    artifacts: tuple[str, ...] = ()


@dataclasses.dataclass
class SequenceResult:
    renderer: str
    status: str
    total_frames: int = 0
    changed_frames: int = 0
    first_mismatch: int | None = None
    detail: str = ""
    backgrounds: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class AssFileInfo:
    width: int
    height: int
    duration: float
    event_times: tuple[float, ...]


@dataclasses.dataclass(frozen=True)
class AssDialogue:
    raw: str
    start: float | None
    end: float | None


@dataclasses.dataclass(frozen=True)
class FrameSelection:
    modified_dialogues: int
    ranges: tuple[tuple[int, int], ...]
    source_frame_count: int
    selected_frame_count: int


class Renderer:
    name = "renderer"

    def probe(self) -> tuple[bool, str]:
        raise NotImplementedError

    def render(
        self,
        ass_path: Path,
        time_seconds: float,
        width: int,
        height: int,
        output_path: Path,
        background: str = "black",
    ) -> None:
        raise NotImplementedError

    def render_sequence(
        self,
        ass_path: Path,
        fps: str,
        selection: FrameSelection,
        width: int,
        height: int,
        background: str = "black",
    ) -> list[str]:
        raise NotImplementedError("This renderer has no full-frame sequence backend")


def hidden_startupinfo() -> subprocess.STARTUPINFO | None:  # type: ignore[name-defined]
    if os.name != "nt":
        return None
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return startupinfo


def run_command(command: Sequence[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        startupinfo=hidden_startupinfo(),
        check=False,
    )


def ffmpeg_option_is_unsupported(
    completed: subprocess.CompletedProcess[str], option: str
) -> bool:
    if completed.returncode == 0:
        return False
    output = "%s\n%s" % (completed.stdout or "", completed.stderr or "")
    output = output.lower()
    option_name = option.lstrip("-").lower()
    option_names = {option_name, option_name.lstrip("/")}
    markers = (
        "unrecognized option",
        "unknown option",
        "option not found",
        "option does not exist",
    )
    return any(marker in output for marker in markers) and any(
        name and name in output for name in option_names
    )


def resolve_executable(value: str) -> str | None:
    candidate = Path(value)
    if candidate.is_file():
        return str(candidate.resolve())
    return shutil.which(value)


def ffmpeg_filter_path(path: Path) -> str:
    value = str(path.resolve()).replace("\\", "/")
    value = value.replace(":", "\\:").replace("'", "\\'")
    return "'%s'" % value


BACKGROUND_COLORS = {
    "black": (0, 0, 0),
    "white": (255, 255, 255),
    "gray": (128, 128, 128),
}
DEFAULT_BACKGROUNDS = ("black", "white")


def background_rgb(name: str) -> tuple[int, int, int]:
    try:
        return BACKGROUND_COLORS[str(name).lower()]
    except KeyError as exc:
        raise ValueError("Unsupported comparison background: %s" % name) from exc


def ffmpeg_background(name: str) -> str:
    red, green, blue = background_rgb(name)
    return "0x%02x%02x%02x" % (red, green, blue)


def avisynth_background(name: str) -> str:
    red, green, blue = background_rgb(name)
    return "$%02X%02X%02X" % (blue, green, red)


class LibassFfmpegRenderer(Renderer):
    name = "libass (FFmpeg ass filter)"

    def __init__(self, ffmpeg: str, timeout: float) -> None:
        self.ffmpeg = ffmpeg
        self.timeout = timeout
        self._resolved: str | None = None
        self._filter_file_option: str | None = None

    def probe(self) -> tuple[bool, str]:
        self._resolved = resolve_executable(self.ffmpeg)
        if not self._resolved:
            return False, "FFmpeg executable not found"
        version = run_command([self._resolved, "-hide_banner", "-version"], self.timeout)
        if version.returncode != 0:
            return False, compact_error(version)
        filters = run_command([self._resolved, "-hide_banner", "-filters"], self.timeout)
        if filters.returncode != 0 or not any(
            line.split()[1:2] == ["ass"] for line in filters.stdout.splitlines()
            if len(line.split()) >= 2
        ):
            return False, "This FFmpeg build has no ass/libass video filter"
        first_line = (version.stdout or version.stderr).splitlines()[0]
        detail = first_line.strip()
        descriptor, filename = tempfile.mkstemp(prefix="clean_tags_probe_", suffix=".ass")
        os.close(descriptor)
        probe_ass = Path(filename)
        try:
            probe_ass.write_text(
                "[Script Info]\n"
                "ScriptType: v4.00+\n"
                "PlayResX: 16\n"
                "PlayResY: 16\n\n"
                "[V4+ Styles]\n"
                "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
                "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
                "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
                "Alignment, MarginL, MarginR, MarginV, Encoding\n"
                "Style: Default,Arial,8,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
                "0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n\n"
                "[Events]\n"
                "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
                "Dialogue: 0,0:00:00.00,0:00:00.04,Default,,0,0,0,,probe\n",
                encoding="utf-8",
            )
            probe = run_command(
                [
                    self._resolved,
                    "-hide_banner",
                    "-loglevel",
                    "verbose",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=black:s=16x16:d=0.04",
                    "-vf",
                    "ass=%s" % ffmpeg_filter_path(probe_ass),
                    "-frames:v",
                    "1",
                    "-f",
                    "null",
                    "-",
                ],
                self.timeout,
            )
            output = (probe.stdout or "") + "\n" + (probe.stderr or "")
            api_match = re.search(r"libass API version:\s*(0x[0-9A-Fa-f]+)", output)
            source_match = re.search(r"libass source:\s*([^\r\n]+)", output)
            if api_match:
                detail += "; libass API " + api_match.group(1)
            if source_match:
                detail += "; " + source_match.group(1).strip()
            if probe.returncode != 0 or not api_match:
                detail += "; libass API version could not be read"
        except OSError as exc:
            detail += "; libass version probe failed: %s" % exc
        finally:
            try:
                probe_ass.unlink()
            except OSError:
                pass
        return True, detail

    def render(
        self,
        ass_path: Path,
        time_seconds: float,
        width: int,
        height: int,
        output_path: Path,
        background: str = "black",
    ) -> None:
        assert self._resolved
        source = "color=c=%s:s=%dx%d:d=0.04:r=25" % (
            ffmpeg_background(background), width, height
        )
        video_filter = "setpts=PTS+%.6f/TB,ass=%s" % (
            time_seconds,
            ffmpeg_filter_path(ass_path),
        )
        command = [
            self._resolved,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            source,
            "-vf",
            video_filter,
            "-frames:v",
            "1",
            "-pix_fmt",
            "rgb24",
            "-c:v",
            "ppm",
            str(output_path),
        ]
        completed = run_command(command, self.timeout)
        if completed.returncode != 0 or not output_path.is_file():
            raise RuntimeError(compact_error(completed) or "FFmpeg did not produce a PPM image")

    def render_sequence(
        self,
        ass_path: Path,
        fps: str,
        selection: FrameSelection,
        width: int,
        height: int,
        background: str = "black",
    ) -> list[str]:
        assert self._resolved
        if not selection.ranges:
            return []
        source = "color=c=%s:s=%dx%d:r=%s:d=%.9f" % (
            ffmpeg_background(background),
            width,
            height,
            fps,
            selection.source_frame_count / parse_fps(fps),
        )
        selector = "+".join(
            "between(n\\,%d\\,%d)" % (start, end)
            for start, end in selection.ranges
        )
        descriptor, filename = tempfile.mkstemp(
            prefix="clean_tags_sequence_", suffix=".filter"
        )
        os.close(descriptor)
        filter_path = Path(filename)
        try:
            filter_path.write_text(
                "select=%s,ass=%s\n" % (selector, ffmpeg_filter_path(ass_path)),
                encoding="utf-8",
            )
            filter_file_option = self._filter_file_option or "-/filter:v"
            command = [
                self._resolved,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                source,
                filter_file_option,
                str(filter_path),
                "-map",
                "0:v:0",
                "-frames:v",
                str(selection.selected_frame_count),
                "-pix_fmt",
                "rgb24",
                "-fps_mode",
                "passthrough",
                "-f",
                "framemd5",
                "-",
            ]
            completed = run_command(
                command,
                self.timeout * max(1.0, selection.selected_frame_count / 120.0),
            )
            if (
                self._filter_file_option is None
                and filter_file_option == "-/filter:v"
                and ffmpeg_option_is_unsupported(completed, filter_file_option)
            ):
                filter_file_option = "-filter_script:v"
                command[command.index("-/filter:v")] = filter_file_option
                completed = run_command(
                    command,
                    self.timeout * max(1.0, selection.selected_frame_count / 120.0),
                )
            if completed.returncode == 0:
                self._filter_file_option = filter_file_option
            if completed.returncode != 0:
                raise RuntimeError(
                    compact_error(completed) or "FFmpeg did not produce a libass frame sequence for the modified intervals"
                )
            return parse_framemd5(completed.stdout)
        finally:
            try:
                filter_path.unlink()
            except OSError:
                pass


def avisynth_string(value: Path) -> str:
    return str(value.resolve()).replace("\\", "\\\\").replace('"', '\\"')


class AviSynthVsFilterRenderer(Renderer):
    name = "xy-VSFilter (AviSynth TextSub)"

    def __init__(self, ffmpeg: str, plugin: Path, timeout: float) -> None:
        self.ffmpeg = ffmpeg
        self.plugin = plugin
        self.timeout = timeout
        self._resolved: str | None = None

    def probe(self) -> tuple[bool, str]:
        if not self.plugin.is_file():
            return False, "xy-VSFilter/VSFilter DLL not found: %s" % self.plugin
        self._resolved = resolve_executable(self.ffmpeg)
        if not self._resolved:
            return False, "FFmpeg capable of reading AviSynth input was not found"
        demuxers = run_command([self._resolved, "-hide_banner", "-demuxers"], self.timeout)
        if demuxers.returncode != 0 or "avisynth" not in demuxers.stdout.lower():
            return False, "This FFmpeg build has no AviSynth demuxer"
        return True, "TextSub plugin: %s" % self.plugin.resolve()

    def render(
        self,
        ass_path: Path,
        time_seconds: float,
        width: int,
        height: int,
        output_path: Path,
        background: str = "black",
    ) -> None:
        assert self._resolved
        fps = 1000
        frame = max(0, int(round(time_seconds * fps)))
        avs_path = output_path.with_suffix(".avs")
        script = (
            'LoadPlugin("%s")\n'
            "clip = BlankClip(width=%d, height=%d, length=%d, fps=%d, "
            'color=%s, pixel_type="RGB32")\n'
            'clip = TextSub(clip, "%s")\n'
            "return Trim(clip, %d, %d)\n"
        ) % (
            avisynth_string(self.plugin),
            width,
            height,
            frame + 2,
            fps,
            avisynth_background(background),
            avisynth_string(ass_path),
            frame,
            frame,
        )
        # AviSynth+ rejects UTF-8 source files carrying a BOM.  All generated
        # paths are represented directly as UTF-8, so write the script without
        # a signature and let the AviSynth demuxer detect it.
        avs_path.write_text(script, encoding="utf-8")
        command = [
            self._resolved,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(avs_path),
            "-frames:v",
            "1",
            "-pix_fmt",
            "rgb24",
            "-c:v",
            "ppm",
            str(output_path),
        ]
        completed = run_command(command, self.timeout)
        if completed.returncode != 0 or not output_path.is_file():
            raise RuntimeError(compact_error(completed) or "TextSub did not produce a PPM image")

    def render_sequence(
        self,
        ass_path: Path,
        fps: str,
        selection: FrameSelection,
        width: int,
        height: int,
        background: str = "black",
    ) -> list[str]:
        assert self._resolved
        if not selection.ranges:
            return []
        descriptor, filename = tempfile.mkstemp(prefix="clean_tags_sequence_", suffix=".avs")
        os.close(descriptor)
        avs_path = Path(filename)
        try:
            script_lines = [
                'LoadPlugin("%s")\n'
                "clip = BlankClip(width=%d, height=%d, length=%d, fps=%s, "
                'color=%s, pixel_type="RGB32")\n'
                'clip = TextSub(clip, "%s")\n'
                % (
                    avisynth_string(self.plugin),
                    width,
                    height,
                    selection.source_frame_count,
                    fps,
                    avisynth_background(background),
                    avisynth_string(ass_path),
                )
            ]
            first_start, first_end = selection.ranges[0]
            script_lines.append("selected = Trim(clip, %d, %d)\n" % (first_start, first_end))
            for start, end in selection.ranges[1:]:
                script_lines.append(
                    "selected = selected ++ Trim(clip, %d, %d)\n" % (start, end)
                )
            script_lines.append("return selected\n")
            script = "".join(script_lines)
            avs_path.write_text(script, encoding="utf-8")
            command = [
                self._resolved,
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(avs_path),
                "-map",
                "0:v:0",
                "-frames:v",
                str(selection.selected_frame_count),
                "-pix_fmt",
                "rgb24",
                "-f",
                "framemd5",
                "-",
            ]
            completed = run_command(
                command,
                self.timeout * max(1.0, selection.selected_frame_count / 120.0),
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    compact_error(completed) or "TextSub did not produce an xy-VSFilter frame sequence for the modified intervals"
                )
            return parse_framemd5(completed.stdout)
        finally:
            try:
                avs_path.unlink()
            except OSError:
                pass


class ExternalRenderer(Renderer):
    def __init__(self, name: str, command: list[str], timeout: float) -> None:
        self.name = name
        self.command = command
        self.timeout = timeout
        self._resolved: str | None = None

    def probe(self) -> tuple[bool, str]:
        if not self.command:
            return False, "External adapter command is empty"
        self._resolved = resolve_executable(self.command[0])
        if not self._resolved:
            return False, "External adapter not found: %s" % self.command[0]
        return True, "External P6 PPM adapter: %s" % self._resolved

    def render(
        self,
        ass_path: Path,
        time_seconds: float,
        width: int,
        height: int,
        output_path: Path,
        background: str = "black",
    ) -> None:
        assert self._resolved
        values = {
            "ass": str(ass_path.resolve()),
            "output": str(output_path.resolve()),
            "time": "%.6f" % time_seconds,
            "width": str(width),
            "height": str(height),
            "background": str(background),
        }
        command = [self._resolved]
        command.extend(token.format(**values) for token in self.command[1:])
        completed = run_command(command, self.timeout)
        if completed.returncode != 0 or not output_path.is_file():
            raise RuntimeError(compact_error(completed) or "External adapter did not produce a PPM image")


def compact_error(completed: subprocess.CompletedProcess[str]) -> str:
    text = (completed.stderr or completed.stdout or "").strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return " | ".join(lines[-4:])


def load_corpus(path: Path) -> tuple[dict[str, Any], list[Case]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Could not read corpus: %s" % exc) from exc
    if data.get("schema") != 1:
        raise ValueError("Only differential corpus schema=1 is supported")
    cases: list[Case] = []
    identifiers: set[str] = set()
    for raw in data.get("cases", []):
        case_id = str(raw.get("id", "")).strip()
        if not case_id or case_id in identifiers:
            raise ValueError("Case ID is empty or duplicated: %r" % case_id)
        identifiers.add(case_id)
        original = str(raw.get("original", ""))
        cleaned = str(raw.get("cleaned", ""))
        if "\n" in original or "\r" in original or "\n" in cleaned or "\r" in cleaned:
            raise ValueError("Case %s Text must not contain physical line breaks" % case_id)
        original_dialogues = tuple(str(value) for value in raw.get("original_dialogues", []))
        cleaned_dialogues = tuple(str(value) for value in raw.get("cleaned_dialogues", []))
        if bool(original_dialogues) != bool(cleaned_dialogues):
            raise ValueError("Case %s must provide both original_dialogues and cleaned_dialogues" % case_id)
        for dialogue in original_dialogues + cleaned_dialogues:
            if "\n" in dialogue or "\r" in dialogue:
                raise ValueError("Case %s Dialogue rows must not contain physical line breaks" % case_id)
            if not dialogue.lstrip().lower().startswith("dialogue:"):
                raise ValueError("Case %s contains an invalid Dialogue row" % case_id)
        times = tuple(float(value) for value in raw.get("times", [0.5]))
        if not times or any(value < 0 for value in times):
            raise ValueError("Case %s has an invalid times value" % case_id)
        cases.append(
            Case(
                case_id=case_id,
                description=str(raw.get("description", "")),
                original=original,
                cleaned=cleaned,
                times=times,
                original_dialogues=original_dialogues,
                cleaned_dialogues=cleaned_dialogues,
            )
        )
    if not cases:
        raise ValueError("The corpus contains no cases")
    return data, cases


def ass_time(seconds: float) -> str:
    centiseconds = max(0, int(round(seconds * 100)))
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    whole_seconds, fraction = divmod(remainder, 100)
    return "%d:%02d:%02d.%02d" % (hours, minutes, whole_seconds, fraction)


def build_ass(
    corpus: dict[str, Any],
    text: str,
    end_seconds: float,
    dialogues: Sequence[str] = (),
) -> str:
    canvas = corpus.get("canvas", {})
    width = int(canvas.get("width", 640))
    height = int(canvas.get("height", 360))
    style = str(
        corpus.get(
            "style",
            "Style: Default,Arial,50,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
            "0,0,0,0,100,100,0,0,1,0,0,7,10,10,10,1",
        )
    )
    event_lines = list(dialogues) or [
        "Dialogue: 0,0:00:00.00,%s,Default,,0,0,0,,%s"
        % (ass_time(end_seconds), text)
    ]
    lines = [
            "[Script Info]",
            "ScriptType: v4.00+",
            "PlayResX: %d" % width,
            "PlayResY: %d" % height,
            "WrapStyle: 0",
            "ScaledBorderAndShadow: yes",
            "YCbCr Matrix: TV.601",
            "",
            "[V4+ Styles]",
            "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,"
            "BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,"
            "BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding",
            style,
            "",
            "[Events]",
            "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
        ]
    lines.extend(event_lines)
    lines.append("")
    return "\ufeff" + "\n".join(lines)


def parse_fps(value: str) -> float:
    text = str(value).strip()
    if "/" in text:
        numerator, denominator = text.split("/", 1)
        result = float(numerator) / float(denominator)
    else:
        result = float(text)
    if result <= 0:
        raise ValueError("fps must be greater than 0")
    return result


def parse_framemd5(output: str) -> list[str]:
    hashes: list[str] = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fields = [field.strip() for field in line.split(",")]
        if len(fields) < 2:
            continue
        digest = fields[-1]
        if len(digest) != 32 or any(character not in "0123456789abcdefABCDEF" for character in digest):
            raise ValueError("framemd5 output contains an invalid frame hash: %s" % line)
        hashes.append(digest.lower())
    if not hashes:
        raise ValueError("framemd5 produced no frames")
    return hashes


def selected_frame_number(selection: FrameSelection, ordinal: int) -> int | None:
    remaining = ordinal
    for start, end in selection.ranges:
        length = end - start + 1
        if remaining < length:
            return start + remaining
        remaining -= length
    return None


def compare_frame_hashes(
    before: Sequence[str],
    after: Sequence[str],
    selection: FrameSelection,
) -> SequenceResult:
    total = max(len(before), len(after))
    changed = 0
    first_mismatch = None
    for index in range(total):
        if index >= len(before) or index >= len(after) or before[index] != after[index]:
            changed += 1
            if first_mismatch is None:
                first_mismatch = selected_frame_number(selection, index)
    status = "PASS" if changed == 0 and len(before) == len(after) else "DIFF"
    detail = ""
    if len(before) != len(after):
        detail = "Frame counts differ: %d vs %d" % (len(before), len(after))
    elif first_mismatch is not None:
        detail = "First mismatched source frame: %d" % first_mismatch
    return SequenceResult(
        renderer="",
        status=status,
        total_frames=total,
        changed_frames=changed,
        first_mismatch=first_mismatch,
        detail=detail,
    )


def ppm_pixels(path: Path) -> tuple[int, int, bytes]:
    data = path.read_bytes()
    index = 0

    def token() -> bytes:
        nonlocal index
        while index < len(data):
            if data[index] == 35:  # '#'
                newline = data.find(b"\n", index)
                index = len(data) if newline < 0 else newline + 1
            elif data[index] in b" \t\r\n":
                index += 1
            else:
                break
        start = index
        while index < len(data) and data[index] not in b" \t\r\n#":
            index += 1
        if start == index:
            raise ValueError("Incomplete PPM header: %s" % path)
        return data[start:index]

    magic = token()
    width = int(token())
    height = int(token())
    maximum = int(token())
    if magic != b"P6" or maximum != 255:
        raise ValueError("Only P6 PPM with maxval=255 is accepted: %s" % path)
    if data[index:index + 2] == b"\r\n":
        index += 2
    elif index < len(data) and data[index] in b" \t\r\n":
        index += 1
    pixels = data[index:]
    expected = width * height * 3
    if len(pixels) != expected:
        raise ValueError("Invalid PPM pixel length: expected %d, got %d" % (expected, len(pixels)))
    return width, height, pixels


def compare_ppm(
    before: Path,
    after: Path,
    channel_tolerance: int,
) -> tuple[int, int, int]:
    width_a, height_a, pixels_a = ppm_pixels(before)
    width_b, height_b, pixels_b = ppm_pixels(after)
    if (width_a, height_a) != (width_b, height_b):
        raise ValueError(
            "Image dimensions differ: %dx%d vs %dx%d"
            % (width_a, height_a, width_b, height_b)
        )
    changed = 0
    maximum = 0
    for offset in range(0, len(pixels_a), 3):
        deltas = (
            abs(pixels_a[offset] - pixels_b[offset]),
            abs(pixels_a[offset + 1] - pixels_b[offset + 1]),
            abs(pixels_a[offset + 2] - pixels_b[offset + 2]),
        )
        pixel_max = max(deltas)
        maximum = max(maximum, pixel_max)
        if pixel_max > channel_tolerance:
            changed += 1
    return changed, width_a * height_a, maximum


def write_diff_ppm(before: Path, after: Path, output: Path, channel_tolerance: int) -> None:
    width_a, height_a, pixels_a = ppm_pixels(before)
    width_b, height_b, pixels_b = ppm_pixels(after)
    if (width_a, height_a) != (width_b, height_b):
        raise ValueError("Difference-image dimensions differ: %dx%d vs %dx%d" % (width_a, height_a, width_b, height_b))
    pixels = bytearray(len(pixels_a))
    for offset in range(0, len(pixels_a), 3):
        deltas = (
            abs(pixels_a[offset] - pixels_b[offset]),
            abs(pixels_a[offset + 1] - pixels_b[offset + 1]),
            abs(pixels_a[offset + 2] - pixels_b[offset + 2]),
        )
        if max(deltas) > channel_tolerance:
            pixels[offset:offset + 3] = bytes((255, 0, 0))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(
        ("P6\n%d %d\n255\n" % (width_a, height_a)).encode("ascii") + pixels
    )


def parse_adapter(path: Path, timeout: float) -> ExternalRenderer:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Could not read xy adapter configuration: %s" % exc) from exc
    if data.get("renderer") != "xy-VSFilter":
        raise ValueError("The xy adapter must declare renderer=xy-VSFilter")
    if data.get("output_format") != "ppm-p6-rgb24":
        raise ValueError("The xy adapter must declare output_format=ppm-p6-rgb24")
    command = data.get("command")
    if not isinstance(command, list) or not all(isinstance(value, str) for value in command):
        raise ValueError("The xy adapter command must be an array of strings")
    return ExternalRenderer("xy-VSFilter (external adapter)", command, timeout)


def discover_xy_plugin(explicit: str | None, root: Path) -> Path | None:
    if explicit:
        return Path(explicit).resolve()
    environment = os.environ.get("CLEAN_REDUNDANT_TAGS_XY_VSFILTER_DLL")
    if environment:
        return Path(environment).resolve()
    # Keep portable installations working when this project is placed one or
    # two levels below a shared tools directory (for example
    # AssPythonScripts/AssCleanRedundantTags).
    search_roots = [root, *root.parents[:2]]
    candidates: list[Path] = []
    for search_root in search_roots:
        candidates.extend(
            search_root / name
            for name in ("xy-VSFilter.dll", "VSFilter.dll", "DirectVobSub.dll")
        )
    # Common portable distributions keep the AviSynth TextSub plugin in an
    # x64 subdirectory.  Search only one directory level and only folders whose
    # name explicitly mentions VSFilter; do not recursively load arbitrary DLLs.
    for search_root in search_roots:
        for directory in sorted(search_root.iterdir(), key=lambda path: path.name.lower()):
            if directory.is_dir() and "vsfilter" in directory.name.lower():
                candidates.extend(
                    directory / "x64" / name
                    for name in ("VSFilter.dll", "DirectVobSub.dll")
                )
    # A system-installed plugin can be exposed through PATH.  This is checked
    # after the script-local candidates so a portable copy beside the helper
    # remains deterministic.
    for name in ("xy-VSFilter.dll", "VSFilter.dll", "DirectVobSub.dll"):
        located = shutil.which(name)
        if located:
            candidates.append(Path(located))
        for entry in os.environ.get("PATH", "").split(os.pathsep):
            if entry:
                candidates.append(Path(entry) / name)
    return next((path.resolve() for path in candidates if path.is_file()), None)


def render_case(
    renderer: Renderer,
    case: Case,
    corpus: dict[str, Any],
    work: Path,
    channel_tolerance: int,
    pixel_tolerance: int,
    backgrounds: Sequence[str] = DEFAULT_BACKGROUNDS,
) -> list[Result]:
    canvas = corpus.get("canvas", {})
    width = int(canvas.get("width", 640))
    height = int(canvas.get("height", 360))
    end_seconds = max(max(case.times) + 0.5, float(corpus.get("duration", 2.0)))
    case_dir = work / safe_name(renderer.name) / safe_name(case.case_id)
    case_dir.mkdir(parents=True, exist_ok=True)
    original_ass = case_dir / "original.ass"
    cleaned_ass = case_dir / "cleaned.ass"
    original_ass.write_text(
        build_ass(corpus, case.original, end_seconds, case.original_dialogues),
        encoding="utf-8",
    )
    cleaned_ass.write_text(
        build_ass(corpus, case.cleaned, end_seconds, case.cleaned_dialogues),
        encoding="utf-8",
    )
    results: list[Result] = []
    for background in backgrounds:
        for ordinal, time_seconds in enumerate(case.times, start=1):
            before = case_dir / ("%s-%02d-before.ppm" % (background, ordinal))
            after = case_dir / ("%s-%02d-after.ppm" % (background, ordinal))
            try:
                renderer.render(original_ass, time_seconds, width, height, before, background)
                renderer.render(cleaned_ass, time_seconds, width, height, after, background)
                changed, total, maximum = compare_ppm(before, after, channel_tolerance)
                status = "PASS" if changed <= pixel_tolerance else "DIFF"
                detail = "" if status == "PASS" else "Exceeded the allowed changed-pixel count of %d" % pixel_tolerance
                artifacts: tuple[str, ...] = ()
                if status == "DIFF":
                    diff = case_dir / ("%s-%02d-diff.ppm" % (background, ordinal))
                    write_diff_ppm(before, after, diff, channel_tolerance)
                    artifacts = tuple(str(path.resolve()) for path in (before, after, diff))
                results.append(
                    Result(
                        renderer=renderer.name,
                        case_id=case.case_id,
                        time=time_seconds,
                        status=status,
                        changed_pixels=changed,
                        total_pixels=total,
                        max_channel_delta=maximum,
                        detail=detail,
                        background=background,
                        artifacts=artifacts,
                    )
                )
            except Exception as exc:  # A renderer failure belongs in the report.
                results.append(
                    Result(
                        renderer=renderer.name,
                        case_id=case.case_id,
                        time=time_seconds,
                        status="ERROR",
                        detail=str(exc),
                        background=background,
                    )
                )
    return results


def parse_ass_clock(value: str) -> float | None:
    parts = value.strip().split(":")
    if len(parts) != 3:
        return None
    try:
        hours = int(parts[0])
        seconds = float(parts[2])
        return max(0.0, hours * 3600.0 + int(parts[1]) * 60.0 + seconds)
    except (TypeError, ValueError):
        return None


def read_ass_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-16")


def parse_ass_dialogues(path: Path) -> tuple[AssDialogue, ...]:
    dialogues: list[AssDialogue] = []
    for line in read_ass_text(path).splitlines():
        if not line.lstrip().lower().startswith("dialogue:"):
            continue
        fields = line.split(",", 9)
        start = parse_ass_clock(fields[1]) if len(fields) >= 3 else None
        end = parse_ass_clock(fields[2]) if len(fields) >= 3 else None
        dialogues.append(AssDialogue(line, start, end))
    return tuple(dialogues)


def select_modified_dialogue_frames(
    original_path: Path,
    cleaned_path: Path,
    fps: float,
) -> FrameSelection:
    original = parse_ass_dialogues(original_path)
    cleaned = parse_ass_dialogues(cleaned_path)
    matcher = difflib.SequenceMatcher(
        None,
        [event.raw for event in original],
        [event.raw for event in cleaned],
        autojunk=False,
    )
    modified = 0
    frame_ranges: list[tuple[int, int]] = []
    for operation, before_start, before_end, after_start, after_end in matcher.get_opcodes():
        if operation == "equal":
            continue
        modified += max(before_end - before_start, after_end - after_start)
        affected = original[before_start:before_end] + cleaned[after_start:after_end]
        for event in affected:
            if event.start is None or event.end is None or event.end <= event.start:
                continue
            # ASS events are active on [Start, End).  Include exactly the CFR
            # frame timestamps that fall inside that half-open interval.
            first = max(0, int(math.ceil(event.start * fps - 1e-9)))
            last = int(math.ceil(event.end * fps - 1e-9)) - 1
            if last >= first:
                frame_ranges.append((first, last))

    merged: list[tuple[int, int]] = []
    for start, end in sorted(frame_ranges):
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    selected_count = sum(end - start + 1 for start, end in merged)
    source_count = merged[-1][1] + 1 if merged else 0
    return FrameSelection(modified, tuple(merged), source_count, selected_count)


def parse_ass_file(path: Path) -> AssFileInfo:
    text = read_ass_text(path)
    width = 1920
    height = 1080
    candidates: list[float] = []
    for line in text.splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip().lower() == "playresx":
            try:
                width = int(value.strip())
            except ValueError:
                pass
        elif separator and key.strip().lower() == "playresy":
            try:
                height = int(value.strip())
            except ValueError:
                pass
        elif line.startswith("Dialogue:"):
            fields = line.split(",", 9)
            if len(fields) < 10:
                continue
            start = parse_ass_clock(fields[1])
            end = parse_ass_clock(fields[2])
            if start is None or end is None or end <= start:
                continue
            candidates.extend((start, (start + end) / 2.0, max(start, end - 0.001)))
    duration = max(candidates, default=1.0)
    return AssFileInfo(width, height, duration, tuple(sorted(set(candidates))))


def select_ass_sample_times(info: AssFileInfo, sample_count: int) -> tuple[float, ...]:
    if sample_count <= 0 or len(info.event_times) <= sample_count:
        return info.event_times or (0.0,)
    # Keep the event-derived candidates, but spread the selected indices over
    # the complete timeline so a long episode is not dominated by its opening.
    selected: list[float] = []
    last_index = len(info.event_times) - 1
    for ordinal in range(sample_count):
        index = round(ordinal * last_index / max(1, sample_count - 1))
        value = info.event_times[index]
        if not selected or selected[-1] != value:
            selected.append(value)
    return tuple(selected)


def render_file_pair(
    renderer: Renderer,
    original_ass: Path,
    cleaned_ass: Path,
    times: Sequence[float],
    width: int,
    height: int,
    work: Path,
    channel_tolerance: int,
    pixel_tolerance: int,
    backgrounds: Sequence[str] = DEFAULT_BACKGROUNDS,
) -> list[Result]:
    case_dir = work / safe_name(renderer.name) / "full_subtitle"
    case_dir.mkdir(parents=True, exist_ok=True)
    results: list[Result] = []
    for background in backgrounds:
        for ordinal, time_seconds in enumerate(times, start=1):
            before = case_dir / ("%s-%04d-before.ppm" % (background, ordinal))
            after = case_dir / ("%s-%04d-after.ppm" % (background, ordinal))
            try:
                renderer.render(original_ass, time_seconds, width, height, before, background)
                renderer.render(cleaned_ass, time_seconds, width, height, after, background)
                changed, total, maximum = compare_ppm(before, after, channel_tolerance)
                status = "PASS" if changed <= pixel_tolerance else "DIFF"
                detail = "" if status == "PASS" else "Exceeded the allowed changed-pixel count of %d" % pixel_tolerance
                artifacts: tuple[str, ...] = ()
                if status == "DIFF":
                    diff = case_dir / ("%s-%04d-diff.ppm" % (background, ordinal))
                    write_diff_ppm(before, after, diff, channel_tolerance)
                    artifacts = tuple(str(path.resolve()) for path in (before, after, diff))
                results.append(
                    Result(
                        renderer=renderer.name,
                        case_id="full-subtitle",
                        time=time_seconds,
                        status=status,
                        changed_pixels=changed,
                        total_pixels=total,
                        max_channel_delta=maximum,
                        detail=detail,
                        background=background,
                        artifacts=artifacts,
                    )
                )
            except Exception as exc:
                results.append(
                    Result(
                        renderer=renderer.name,
                        case_id="full-subtitle",
                        time=time_seconds,
                        status="ERROR",
                        detail=str(exc),
                        background=background,
                    )
                )
    return results


def render_full_frame_sequence(
    renderer: Renderer,
    original_ass: Path,
    cleaned_ass: Path,
    fps: str,
    selection: FrameSelection,
    width: int,
    height: int,
    progress: Callable[[float, str], None] | None = None,
    backgrounds: Sequence[str] = DEFAULT_BACKGROUNDS,
    artifact_root: Path | None = None,
    channel_tolerance: int = 0,
    render_workers: int = 2,
) -> SequenceResult:
    return render_full_frame_sequences(
        [renderer],
        original_ass,
        cleaned_ass,
        fps,
        selection,
        width,
        height,
        progress,
        backgrounds,
        artifact_root,
        channel_tolerance,
        render_workers,
    )[0]


def render_full_frame_sequences(
    renderers: Sequence[Renderer],
    original_ass: Path,
    cleaned_ass: Path,
    fps: str,
    selection: FrameSelection,
    width: int,
    height: int,
    progress: Callable[[float, str], None] | None = None,
    backgrounds: Sequence[str] = DEFAULT_BACKGROUNDS,
    artifact_root: Path | None = None,
    channel_tolerance: int = 0,
    render_workers: int = 4,
) -> list[SequenceResult]:
    """Render all renderer, side, and background combinations in one task pool.

    With two renderers and the default black/white backgrounds this creates
    eight independent jobs. ``render_workers`` is the user-controlled upper
    bound on how many of those jobs may run at once.
    """
    if not renderers:
        return []
    total_render_tasks = max(1, len(backgrounds) * len(renderers) * 2)
    worker_count = max(1, min(int(render_workers), total_render_tasks))
    states: list[dict[str, Any]] = [
        {
            "mismatch_ordinals": set(),
            "first_mismatch": None,
            "mismatch_backgrounds": set(),
            "artifacts": [],
            "error": None,
        }
        for _ in renderers
    ]
    completed_render_tasks = 0

    def report_task_finished(renderer: Renderer, background: str, side: str) -> None:
        nonlocal completed_render_tasks
        completed_render_tasks += 1
        if progress:
            progress(
                min(0.92, 0.92 * completed_render_tasks / total_render_tasks),
                "%s: %s background %s render complete (concurrency %d)"
                % (renderer.name, background, side, worker_count),
            )

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=worker_count,
        thread_name_prefix="ass-render",
    ) as executor:
        futures: dict[
            concurrent.futures.Future[list[str]],
            tuple[int, str, str, str],
        ] = {}
        outputs: dict[tuple[int, str, str], list[str]] = {}
        for background in backgrounds:
            for renderer_index, renderer in enumerate(renderers):
                for side, ass_path, side_label in (
                    ("before", original_ass, "before cleanup"),
                    ("after", cleaned_ass, "after cleanup"),
                ):
                    future = executor.submit(
                        renderer.render_sequence,
                        ass_path,
                        fps,
                        selection,
                        width,
                        height,
                        background,
                    )
                    futures[future] = (
                        renderer_index,
                        background,
                        side,
                        side_label,
                    )
        if progress and futures:
            progress(
                0.0,
                "Submitted %d render tasks with a concurrency limit of %d" % (len(futures), worker_count),
            )

        for future in concurrent.futures.as_completed(futures):
            renderer_index, background, side, side_label = futures[future]
            renderer = renderers[renderer_index]
            try:
                outputs[(renderer_index, background, side)] = future.result()
            except Exception as exc:
                state = states[renderer_index]
                if state["error"] is None:
                    state["error"] = "%s background %s render failed: %s" % (
                        background,
                        side_label,
                        exc,
                    )
            report_task_finished(renderer, background, side_label)

        for background in backgrounds:
            for renderer_index, renderer in enumerate(renderers):
                state = states[renderer_index]
                if state["error"] is not None:
                    continue
                before = outputs.get((renderer_index, background, "before"))
                after = outputs.get((renderer_index, background, "after"))
                if before is None or after is None:
                    state["error"] = "%s background did not return complete before/after results" % background
                    continue
                if len(before) != selection.selected_frame_count:
                    state["error"] = (
                        "Invalid original frame count (%s background): expected %d, got %d"
                        % (background, selection.selected_frame_count, len(before))
                    )
                    continue
                if len(after) != selection.selected_frame_count:
                    state["error"] = (
                        "Invalid cleaned frame count (%s background): expected %d, got %d"
                        % (background, selection.selected_frame_count, len(after))
                    )
                    continue

                background_mismatches: set[int] = set()
                for ordinal in range(max(len(before), len(after))):
                    if (
                        ordinal >= len(before)
                        or ordinal >= len(after)
                        or before[ordinal] != after[ordinal]
                    ):
                        background_mismatches.add(ordinal)
                        state["mismatch_ordinals"].add(ordinal)
                if background_mismatches:
                    first = min(background_mismatches)
                    if state["first_mismatch"] is None or first < state["first_mismatch"]:
                        state["first_mismatch"] = first
                    state["mismatch_backgrounds"].add(background)
                if progress:
                    progress(
                        min(0.94, completed_render_tasks / total_render_tasks * 0.92 + 0.02),
                        "%s: comparing %s-background frame hashes" % (renderer.name, background),
                    )

                if background_mismatches and artifact_root is not None:
                    ordinal = min(background_mismatches)
                    source_frame = selected_frame_number(selection, ordinal)
                    if source_frame is not None:
                        artifact_dir = (
                            artifact_root
                            / safe_name(renderer.name)
                            / ("%s-frame-%06d" % (background, source_frame))
                        )
                        artifact_dir.mkdir(parents=True, exist_ok=True)
                        before_path = artifact_dir / "before.ppm"
                        after_path = artifact_dir / "after.ppm"
                        diff_path = artifact_dir / "diff.ppm"
                        time_seconds = source_frame / parse_fps(fps)
                        artifact_futures = (
                            executor.submit(
                                renderer.render,
                                original_ass,
                                time_seconds,
                                width,
                                height,
                                before_path,
                                background,
                            ),
                            executor.submit(
                                renderer.render,
                                cleaned_ass,
                                time_seconds,
                                width,
                                height,
                                after_path,
                                background,
                            ),
                        )
                        try:
                            for future in artifact_futures:
                                future.result()
                            write_diff_ppm(
                                before_path,
                                after_path,
                                diff_path,
                                channel_tolerance,
                            )
                            state["artifacts"].extend(
                                str(path.resolve())
                                for path in (before_path, after_path, diff_path)
                            )
                        except Exception as exc:
                            state["error"] = (
                                "%s-background difference image generation failed: %s" % (background, exc)
                            )

    results: list[SequenceResult] = []
    for renderer_index, renderer in enumerate(renderers):
        state = states[renderer_index]
        error = state["error"]
        if error is not None:
            result = SequenceResult(
                renderer=renderer.name,
                status="ERROR",
                detail=str(error),
                backgrounds=tuple(backgrounds),
            )
        else:
            mismatch_ordinals = state["mismatch_ordinals"]
            first_mismatch = state["first_mismatch"]
            mismatch_backgrounds = state["mismatch_backgrounds"]
            detail = ""
            if first_mismatch is not None:
                detail = "First mismatched source frame: %d; backgrounds: %s" % (
                    selected_frame_number(selection, first_mismatch) or 0,
                    ", ".join(sorted(mismatch_backgrounds)),
                )
            result = SequenceResult(
                renderer=renderer.name,
                status="PASS" if not mismatch_ordinals else "DIFF",
                total_frames=selection.selected_frame_count,
                changed_frames=len(mismatch_ordinals),
                first_mismatch=selected_frame_number(selection, first_mismatch)
                if first_mismatch is not None
                else None,
                detail=detail,
                backgrounds=tuple(backgrounds),
                artifacts=tuple(dict.fromkeys(state["artifacts"])),
            )
        results.append(result)
        if progress:
            progress(1.0, "%s: complete" % renderer.name)
    return results


def safe_name(value: str) -> str:
    result = "".join(character if character.isalnum() else "_" for character in value)
    return result.strip("_") or "item"


def markdown_escape(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def build_report(
    corpus_path: Path,
    probes: list[tuple[str, bool, str]],
    missing_xy: bool,
    missing_libass: bool,
    results: list[Result],
    channel_tolerance: int,
    pixel_tolerance: int,
    source_label: str | None = None,
    sequence_results: Sequence[SequenceResult] = (),
    sequence_fps: str | None = None,
    sequence_selection: FrameSelection | None = None,
    backgrounds: Sequence[str] = DEFAULT_BACKGROUNDS,
    render_workers: int = 1,
) -> tuple[str, str]:
    failures = [result for result in results if result.status != "PASS"]
    sequence_failures = [result for result in sequence_results if result.status != "PASS"]
    if failures or sequence_failures:
        overall = "FAIL"
    elif missing_xy or missing_libass:
        overall = "INCOMPLETE"
    else:
        overall = "PASS"
    sequence_mode = sequence_selection is not None
    tolerance_line = (
        "- Criterion: the full-frame RGB24 MD5 must match exactly for every frame and comparison background during every modified Dialogue interval."
        if sequence_mode
        else "- Tolerance: per-channel delta ≤ %d; changed pixels allowed per frame ≤ %d."
        % (channel_tolerance, pixel_tolerance)
    )
    output = [
        "# CleanRedundantTags Rendering Differential Report",
        "",
        "- Tool version: %s" % VERSION,
        "- Generated: %s" % _datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "- Input: `%s`" % markdown_escape(source_label or corpus_path),
        "- Overall status: **%s**" % overall,
        "- Comparison rule: before/after images are compared within each renderer; libass and xy-VSFilter are not compared against each other.",
        tolerance_line,
        "",
        "## Actual runtimes",
        "",
        "| Renderer | Status | Runtime |",
        "| --- | --- | --- |",
    ]
    for name, available, detail in probes:
        output.append(
            "| %s | %s | %s |"
            % (markdown_escape(name), "Available" if available else "Unavailable", markdown_escape(detail))
        )
    if missing_xy:
        output.extend(
            [
                "",
                "> xy-VSFilter was not run. A real VSFilter DLL loadable through AviSynth `TextSub`, "
                "or an external adapter declaring xy-VSFilter and emitting P6 PPM, is required. This state is not counted as a pass.",
            ]
        )
    if missing_libass:
        output.extend(
            [
                "",
                "> libass was not run. A real FFmpeg/libass runtime with the `ass` video filter is required. "
                "This state is not counted as a pass.",
            ]
        )
    if sequence_mode:
        assert sequence_selection is not None
        output.extend(
            [
                "",
                "## Frame-by-frame differential for modified intervals",
                "",
                "- Frame rate: `%s`" % markdown_escape(sequence_fps or "unknown"),
                "- Modified Dialogue rows: %d" % sequence_selection.modified_dialogues,
                "- Merged frame ranges: %d" % len(sequence_selection.ranges),
                "- Frames actually compared: %d" % sequence_selection.selected_frame_count,
                "- Comparison backgrounds: %s (configurable with `--backgrounds`)"
                % ", ".join("`%s`" % markdown_escape(item) for item in backgrounds),
                "- Concurrent rendering: configured limit %d processes; %d tasks in this run; effective limit %d"
                % (
                    render_workers,
                    len(sequence_results) * len(backgrounds) * 2,
                    min(
                        render_workers,
                        len(sequence_results) * len(backgrounds) * 2,
                    )
                    if sequence_results
                    else 0,
                ),
                "",
                "| Renderer | Backgrounds | Status | Compared frames | Mismatched frames | First mismatched source frame | Details |",
                "| --- | --- | --- | ---: | ---: | ---: | --- |",
            ]
        )
        if not sequence_results:
            output.append("| — | — | Not run | 0 | 0 | — | No actual renderer is available |")
        for result in sequence_results:
            artifacts = ", ".join("`%s`" % markdown_escape(path) for path in result.artifacts)
            detail = markdown_escape(result.detail)
            if artifacts:
                detail = (detail + "; " if detail else "") + "Difference images: " + artifacts
            output.append(
                "| %s | %s | %s | %d | %d | %s | %s |"
                % (
                    markdown_escape(result.renderer),
                    markdown_escape(", ".join(result.backgrounds) or "black"),
                    result.status,
                    result.total_frames,
                    result.changed_frames,
                    "—" if result.first_mismatch is None else str(result.first_mismatch),
                    detail,
                )
            )
    else:
        output.extend(
            [
                "",
                "## Differential results",
                "",
                "| Renderer | Background | Case | Time (seconds) | Status | Changed pixels | Maximum channel delta | Details |",
                "| --- | --- | --- | ---: | --- | ---: | ---: | --- |",
            ]
        )
        if not results:
            output.append("| — | — | — | — | Not run | — | — | No actual renderer is available |")
        for result in results:
            detail = markdown_escape(result.detail)
            if result.artifacts:
                artifact_text = ", ".join(
                    "`%s`" % markdown_escape(path) for path in result.artifacts
                )
                detail = (detail + "; " if detail else "") + "Difference images: " + artifact_text
            output.append(
                "| %s | %s | `%s` | %.3f | %s | %d / %d | %d | %s |"
                % (
                    markdown_escape(result.renderer),
                    markdown_escape(result.background),
                    markdown_escape(result.case_id),
                    result.time,
                    result.status,
                    result.changed_pixels,
                    result.total_pixels,
                    result.max_channel_delta,
                    detail,
                )
            )
    output.extend(
        [
            "",
            "## Verdict",
            "",
        ]
    )
    if overall == "PASS":
        executed_renderers = sorted({
            result.renderer for result in (sequence_results if sequence_mode else results)
        })
        subject = (
            markdown_escape(executed_renderers[0])
            if len(executed_renderers) == 1
            else "%d actual renderers" % len(executed_renderers)
        )
        if sequence_mode:
            output.append(
                "%s passed the before/after frame-hash equivalence check across every modified Dialogue interval."
                % subject
            )
        else:
            output.append("All before/after cases for %s passed the pixel-equivalence check." % subject)
    elif overall == "INCOMPLETE":
        output.append("The configured renderer cases passed, but at least one actual target runtime is unavailable, so two-renderer differential equivalence cannot be claimed.")
    else:
        output.append("At least one actual renderer produced an image difference or execution error. Review the table and retained artifacts above.")
    output.append("")
    return "\n".join(output), overall


def default_ffmpeg(root: Path) -> str:
    local = root / "ffmpeg.exe"
    return str(local) if local.is_file() else "ffmpeg"


def emit_progress(enabled: bool, percent: float, message: str) -> None:
    callback = globals().get("GUI_PROGRESS_CALLBACK")
    if callback:
        callback(percent, message)
    if not enabled:
        return
    value = max(0, min(100, int(round(percent))))
    clean_message = str(message).replace("\r", " ").replace("\n", " ")
    print("PROGRESS=%d|%s" % (value, clean_message), flush=True)


def parse_compare_args(argv: Sequence[str]) -> argparse.Namespace:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="Compare ASS pixels before and after cleanup independently with actual libass and xy-VSFilter runtimes."
    )
    parser.add_argument(
        "--corpus",
        help="Explicit corpus of original/cleaned cases; no default corpus is included in the release",
    )
    parser.add_argument(
        "--original-ass",
        help="Whole-subtitle pair mode: ASS file before cleanup",
    )
    parser.add_argument(
        "--cleaned-ass",
        help="Whole-subtitle pair mode: ASS file after cleanup",
    )
    parser.add_argument(
        "--sample-count",
        type=int,
        default=120,
        help="Maximum number of event-time candidates sampled uniformly in whole-subtitle mode; 0 uses all candidates",
    )
    parser.add_argument(
        "--full-frames",
        action="store_true",
        help="Compare every frame during every modified Dialogue interval at the specified FPS",
    )
    parser.add_argument(
        "--fps",
        default="24000/1001",
        help="Frame rate for --full-frames, such as 24000/1001, 23.976, or 25",
    )
    parser.add_argument(
        "--backgrounds",
        default=",".join(DEFAULT_BACKGROUNDS),
        help="Comma-separated comparison backgrounds; default: black,white; gray is also available",
    )
    parser.add_argument("--ffmpeg", default=default_ffmpeg(root))
    parser.add_argument(
        "--xy-vsfilter-dll",
        help="Actual xy-VSFilter/VSFilter DLL loadable through AviSynth LoadPlugin/TextSub",
    )
    parser.add_argument(
        "--xy-adapter",
        help="External xy-VSFilter P6 PPM adapter JSON; mutually exclusive with --xy-vsfilter-dll",
    )
    parser.add_argument(
        "--report",
        default=str(root / "differential_report.md"),
        help="Report path; .html/.htm produces HTML, other extensions produce Markdown",
    )
    parser.add_argument("--artifacts", help="Directory for retained ASS and PPM artifacts; a temporary directory is used when omitted")
    parser.add_argument("--channel-tolerance", type=int, default=0)
    parser.add_argument("--pixel-tolerance", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--render-workers",
        type=int,
        default=4,
        help=(
            "Concurrent render-process limit for full-frame mode; default: 4. "
            "All renderers, before/after sides, and backgrounds share one task pool"
        ),
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Exit with 0 when xy-VSFilter is unavailable; the report status remains INCOMPLETE",
    )
    parser.add_argument("--skip-libass", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--skip-xy-vsfilter", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--progress",
        action="store_true",
        help="Emit PROGRESS=percent|message to standard output",
    )
    return parser.parse_args(list(argv))


def compare_main(argv: Sequence[str]) -> int:
    args = parse_compare_args(argv)
    emit_progress(args.progress, 1, "Preparing differential arguments")
    if args.channel_tolerance < 0 or args.channel_tolerance > 255:
        print("ERROR: --channel-tolerance must be in the range 0..255", file=sys.stderr)
        return EXIT_CONFIGURATION
    if args.pixel_tolerance < 0 or args.timeout <= 0 or args.render_workers <= 0:
        print(
            "ERROR: pixel tolerance cannot be negative, and timeout and render-workers must be greater than 0",
            file=sys.stderr,
        )
        return EXIT_CONFIGURATION
    if args.xy_adapter and args.xy_vsfilter_dll:
        print("ERROR: --xy-adapter and --xy-vsfilter-dll are mutually exclusive", file=sys.stderr)
        return EXIT_CONFIGURATION
    if bool(args.original_ass) != bool(args.cleaned_ass):
        print("ERROR: --original-ass and --cleaned-ass must be provided together", file=sys.stderr)
        return EXIT_CONFIGURATION
    if not args.original_ass and not args.corpus:
        print(
            "ERROR: provide either --original-ass/--cleaned-ass or --corpus",
            file=sys.stderr,
        )
        return EXIT_CONFIGURATION
    if args.sample_count < 0:
        print("ERROR: --sample-count cannot be negative", file=sys.stderr)
        return EXIT_CONFIGURATION
    backgrounds = tuple(dict.fromkeys(
        item.strip().lower() for item in str(args.backgrounds).split(",") if item.strip()
    ))
    if not backgrounds or any(item not in BACKGROUND_COLORS for item in backgrounds):
        print("ERROR: --backgrounds may contain only black, white, and gray", file=sys.stderr)
        return EXIT_CONFIGURATION
    if args.full_frames and not (args.original_ass and args.cleaned_ass):
        print("ERROR: --full-frames can only be used with whole-ASS file-pair mode", file=sys.stderr)
        return EXIT_CONFIGURATION
    try:
        fps_value = parse_fps(args.fps)
    except (ValueError, ZeroDivisionError) as exc:
        print("ERROR: invalid --fps value: %s" % exc, file=sys.stderr)
        return EXIT_CONFIGURATION

    root = Path(__file__).resolve().parent
    full_ass_mode = bool(args.original_ass and args.cleaned_ass)
    frame_selection: FrameSelection | None = None
    if full_ass_mode:
        emit_progress(args.progress, 4, "Reading before/after subtitles")
        original_ass_path = Path(args.original_ass).resolve()
        cleaned_ass_path = Path(args.cleaned_ass).resolve()
        if not original_ass_path.is_file() or not cleaned_ass_path.is_file():
            print("ERROR: whole-ASS input file does not exist", file=sys.stderr)
            return EXIT_CONFIGURATION
        try:
            original_info = parse_ass_file(original_ass_path)
            cleaned_info = parse_ass_file(cleaned_ass_path)
        except (OSError, UnicodeError, ValueError) as exc:
            print("ERROR: could not read whole ASS file: %s" % exc, file=sys.stderr)
            return EXIT_CONFIGURATION
        width = original_info.width
        height = original_info.height
        if (width, height) != (cleaned_info.width, cleaned_info.height):
            print("ERROR: original and cleaned ASS files have different PlayRes dimensions", file=sys.stderr)
            return EXIT_CONFIGURATION
        sample_info = AssFileInfo(
            width,
            height,
            max(original_info.duration, cleaned_info.duration),
            tuple(sorted(set(original_info.event_times + cleaned_info.event_times))),
        )
        sample_times = select_ass_sample_times(sample_info, args.sample_count)
        if args.full_frames:
            try:
                frame_selection = select_modified_dialogue_frames(
                    original_ass_path,
                    cleaned_ass_path,
                    fps_value,
                )
            except (OSError, UnicodeError, ValueError) as exc:
                print("ERROR: could not determine frame ranges for modified Dialogue rows: %s" % exc, file=sys.stderr)
                return EXIT_CONFIGURATION
            if (
                frame_selection.modified_dialogues > 0
                and frame_selection.selected_frame_count == 0
            ):
                print(
                    "ERROR: modified Dialogue rows were detected, but none has a valid display interval to compare",
                    file=sys.stderr,
                )
                return EXIT_CONFIGURATION
            emit_progress(
                args.progress,
                9,
                "Located %d modified Dialogue rows in %d frame ranges"
                % (frame_selection.modified_dialogues, len(frame_selection.ranges)),
            )
        corpus = None
        cases = []
        corpus_path = original_ass_path
    else:
        corpus_path = Path(args.corpus).resolve()
        try:
            corpus, cases = load_corpus(corpus_path)
        except ValueError as exc:
            print("ERROR: %s" % exc, file=sys.stderr)
            return EXIT_CONFIGURATION

    renderers: list[Renderer] = []
    if not args.skip_libass:
        renderers.append(LibassFfmpegRenderer(args.ffmpeg, args.timeout))
    try:
        if not args.skip_xy_vsfilter:
            if args.xy_adapter:
                renderers.append(parse_adapter(Path(args.xy_adapter), args.timeout))
            else:
                plugin = discover_xy_plugin(args.xy_vsfilter_dll, root)
                if plugin:
                    renderers.append(AviSynthVsFilterRenderer(args.ffmpeg, plugin, args.timeout))
    except ValueError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return EXIT_CONFIGURATION

    probes: list[tuple[str, bool, str]] = []
    available: list[Renderer] = []
    emit_progress(args.progress, 11, "Probing actual renderers")
    for probe_index, renderer in enumerate(renderers):
        emit_progress(
            args.progress,
            12 + 5 * probe_index / max(1, len(renderers)),
            "Probing %s" % renderer.name,
        )
        ok, detail = renderer.probe()
        probes.append((renderer.name, ok, detail))
        if ok:
            available.append(renderer)

    has_xy = any(renderer.name.startswith("xy-VSFilter") for renderer in available)
    has_libass = any(renderer.name.startswith("libass") for renderer in available)
    if not args.skip_xy_vsfilter and not any(
        name.startswith("xy-VSFilter") for name, _, _ in probes
    ):
        probes.append(
            (
                "xy-VSFilter",
                False,
                "No DLL was found and --xy-adapter was not provided",
            )
        )
    missing_xy = not args.skip_xy_vsfilter and not has_xy
    missing_libass = not args.skip_libass and not has_libass

    temporary_work = not bool(args.artifacts)
    if args.artifacts:
        work = Path(args.artifacts).resolve()
        work.mkdir(parents=True, exist_ok=True)
    else:
        # TemporaryDirectory owns a finalizer and would delete failure images
        # again when Python exits.  Use mkdtemp so a failed comparison can
        # deliberately leave its before/after/diff artifacts on disk.
        work = Path(tempfile.mkdtemp(prefix="clean_tags_diff_"))

    results: list[Result] = []
    sequence_results: list[SequenceResult] = []
    keep_temporary = False
    try:
        if full_ass_mode and args.full_frames:
            assert frame_selection is not None

            def concurrent_progress(fraction: float, message: str) -> None:
                emit_progress(
                    args.progress,
                    18 + 76 * fraction,
                    message,
                )

            sequence_results.extend(
                render_full_frame_sequences(
                    available,
                    original_ass_path,
                    cleaned_ass_path,
                    args.fps,
                    frame_selection,
                    width,
                    height,
                    concurrent_progress,
                    backgrounds,
                    work,
                    args.channel_tolerance,
                    args.render_workers,
                )
            )
            keep_temporary = keep_temporary or any(
                result.status != "PASS" for result in sequence_results
            )
        else:
            for renderer in available:
                if full_ass_mode:
                    results.extend(
                        render_file_pair(
                            renderer,
                            original_ass_path,
                            cleaned_ass_path,
                            sample_times,
                            width,
                            height,
                            work,
                            args.channel_tolerance,
                            args.pixel_tolerance,
                            backgrounds,
                        )
                    )
                else:
                    for case in cases:
                        results.extend(
                            render_case(
                                renderer,
                                case,
                                corpus,
                                work,
                                args.channel_tolerance,
                                args.pixel_tolerance,
                                backgrounds,
                            )
                        )
        keep_temporary = keep_temporary or any(result.status != "PASS" for result in results)
    finally:
        if temporary_work and not keep_temporary:
            shutil.rmtree(work, ignore_errors=True)

    source_label = None
    if full_ass_mode:
        source_label = "%s → %s" % (original_ass_path, cleaned_ass_path)
    report, overall = build_report(
        corpus_path,
        probes,
        missing_xy,
        missing_libass,
        results,
        args.channel_tolerance,
        args.pixel_tolerance,
        source_label,
        sequence_results,
        args.fps if args.full_frames else None,
        frame_selection,
        backgrounds,
        args.render_workers,
    )
    report_path = Path(args.report).resolve()
    emit_progress(
        args.progress,
        96,
        "Writing %s differential report" % ("HTML" if report_is_html(report_path) else "Markdown"),
    )
    try:
        write_report(report_path, report)
    except OSError as exc:
        print("ERROR: could not write report: %s" % exc, file=sys.stderr)
        return EXIT_CONFIGURATION

    passed = sum(result.status == "PASS" for result in results)
    passed = passed + sum(result.status == "PASS" for result in sequence_results)
    failed = len(results) + len(sequence_results) - passed
    print("STATUS=%s" % overall)
    print("PASS=%d" % passed)
    print("FAIL=%d" % failed)
    if args.full_frames:
        assert frame_selection is not None
        print("MODIFIED_DIALOGUES=%d" % frame_selection.modified_dialogues)
        print("RANGES=%d" % len(frame_selection.ranges))
        print("FRAMES=%d" % frame_selection.selected_frame_count)
    print("REPORT=%s" % report_path)
    emit_progress(args.progress, 100, "Differential complete: %s" % overall)
    if overall == "FAIL":
        return EXIT_DIFFERENCE
    if overall == "INCOMPLETE" and not args.allow_partial:
        return EXIT_INCOMPLETE
    return EXIT_PASS


ProgressCallback = Callable[[float, str], None]
GUI_PROGRESS_CALLBACK: ProgressCallback | None = None


def operation_progress(percent: float, message: str) -> None:
    if GUI_PROGRESS_CALLBACK:
        GUI_PROGRESS_CALLBACK(max(0.0, min(100.0, percent)), message)


def load_clean_settings(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    settings_path = Path(path)
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Could not read JSON settings %s: %s" % (settings_path, exc)) from exc
    if not isinstance(data, dict):
        raise ValueError("The JSON settings root must be an object")
    return data


def _settings_bool(value: object, key: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError("JSON settings field %s must be a boolean" % key)
    return value


def _settings_int(value: object, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("JSON settings field %s must be an integer" % key)
    return value


def _settings_float(value: object, key: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("JSON settings field %s must be numeric" % key)
    return float(value)


def _settings_optional_string(value: object, key: str) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("JSON settings field %s must be a string or null" % key)
    if not value.strip():
        return None
    return value


def _settings_string_list(value: object, key: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError("JSON settings field %s must be a non-empty array of strings" % key)
    return list(value)


def resolve_clean_args(
    args: argparse.Namespace,
    settings: dict[str, Any] | None = None,
) -> argparse.Namespace:
    settings = load_clean_settings(getattr(args, "settings", None)) if settings is None else settings
    resolved = copy.copy(args)

    cli_inputs = list(getattr(args, "inputs", ()))
    resolved.inputs = (
        cli_inputs
        if cli_inputs
        else _settings_string_list(settings.get("inputs", []), "inputs")
    )
    if not resolved.inputs:
        raise ValueError("Provide at least one subtitle file or directory on the command line or in JSON inputs")

    setting_output = _settings_optional_string(settings.get("output"), "output")
    setting_output_dir = _settings_optional_string(
        settings.get("output_dir"), "output_dir"
    )
    if setting_output and setting_output_dir:
        raise ValueError("JSON settings output and output_dir are mutually exclusive")
    if args.output is not None:
        resolved.output = args.output
        resolved.output_dir = None
    elif args.output_dir is not None:
        resolved.output = None
        resolved.output_dir = args.output_dir
    else:
        resolved.output = setting_output
        resolved.output_dir = setting_output_dir

    raw_in_place = (
        args.in_place
        if args.in_place is not None
        else settings.get("in_place", False)
    )
    resolved.in_place = _settings_bool(raw_in_place, "in_place")
    if resolved.in_place:
        if args.in_place is not True and (resolved.output or resolved.output_dir):
            raise ValueError("JSON settings in_place cannot be combined with output or output_dir")
        if args.output is not None or args.output_dir is not None:
            raise ValueError("--in-place cannot be combined with --output or --output-dir")
        resolved.output = None
        resolved.output_dir = None

    setting_report = _settings_optional_string(settings.get("report"), "report")
    setting_report_dir = _settings_optional_string(
        settings.get("report_dir"), "report_dir"
    )
    if setting_report and setting_report_dir:
        raise ValueError("JSON settings report and report_dir are mutually exclusive")
    if args.report is not None:
        resolved.report = args.report
        resolved.report_dir = None
    elif args.report_dir is not None:
        resolved.report = None
        resolved.report_dir = args.report_dir
    else:
        resolved.report = setting_report
        resolved.report_dir = setting_report_dir

    boolean_fields = {
        "recursive": False,
        "safe_reorder": True,
        "merge_lines": True,
        "clean_comments": False,
        "remove_transparent_dialogues": False,
        "clean_unknown_tags": True,
        "clean_extradata_references": True,
        "clean_project_garbage": True,
        "clean_extradata": True,
        "write_reports": True,
        "compare_libass": True,
        "compare_vsfilter": False,
        "allow_partial": False,
    }
    for key, default in boolean_fields.items():
        cli_value = getattr(args, key)
        raw_value = cli_value if cli_value is not None else settings.get(key, default)
        setattr(resolved, key, _settings_bool(raw_value, key))

    if args.no_backup is not None:
        resolved.no_backup = args.no_backup
    else:
        resolved.no_backup = not _settings_bool(settings.get("backup", True), "backup")

    raw_report_format = (
        args.report_format
        if args.report_format is not None
        else settings.get("report_format", "html")
    )
    if not isinstance(raw_report_format, str) or raw_report_format.casefold() not in (
        "md",
        "html",
    ):
        raise ValueError("JSON settings field report_format must be md or html")
    resolved.report_format = raw_report_format.casefold()

    default_ffmpeg_value = default_ffmpeg(Path(__file__).resolve().parent)
    raw_ffmpeg = args.ffmpeg if args.ffmpeg is not None else settings.get("ffmpeg")
    resolved.ffmpeg = (
        _settings_optional_string(raw_ffmpeg, "ffmpeg") or default_ffmpeg_value
    )
    for attribute, key in (
        ("xy_vsfilter_dll", "xy_vsfilter_dll"),
        ("xy_adapter", "xy_adapter"),
    ):
        cli_value = getattr(args, attribute)
        raw_value = cli_value if cli_value is not None else settings.get(key)
        setattr(resolved, attribute, _settings_optional_string(raw_value, key))

    raw_fps = args.fps if args.fps is not None else settings.get("fps", "24000/1001")
    if not isinstance(raw_fps, str) or not raw_fps.strip():
        raise ValueError("JSON settings field fps must be a non-empty string")
    resolved.fps = raw_fps

    raw_backgrounds = (
        args.backgrounds
        if args.backgrounds is not None
        else settings.get("backgrounds", list(DEFAULT_BACKGROUNDS))
    )
    if isinstance(raw_backgrounds, list):
        background_items = _settings_string_list(raw_backgrounds, "backgrounds")
        resolved.backgrounds = ",".join(background_items)
    elif isinstance(raw_backgrounds, str) and raw_backgrounds.strip():
        resolved.backgrounds = raw_backgrounds
    else:
        raise ValueError("JSON settings field backgrounds must be a non-empty string or array of strings")

    numeric_fields = {
        "channel_tolerance": (0, _settings_int),
        "pixel_tolerance": (0, _settings_int),
        "timeout": (30.0, _settings_float),
        "render_workers": (4, _settings_int),
    }
    for key, (default, converter) in numeric_fields.items():
        cli_value = getattr(args, key)
        raw_value = cli_value if cli_value is not None else settings.get(key, default)
        setattr(resolved, key, converter(raw_value, key))
    if resolved.channel_tolerance < 0 or resolved.channel_tolerance > 255:
        raise ValueError("channel_tolerance must be in the range 0..255")
    if resolved.pixel_tolerance < 0:
        raise ValueError("pixel_tolerance cannot be negative")
    if resolved.timeout <= 0 or resolved.render_workers <= 0:
        raise ValueError("timeout and render_workers must be greater than 0")
    return resolved


def clean_settings_data(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "inputs": [str(value) for value in getattr(args, "inputs", ())],
        "output": str(args.output) if getattr(args, "output", None) else None,
        "output_dir": (
            str(args.output_dir) if getattr(args, "output_dir", None) else None
        ),
        "recursive": bool(args.recursive),
        "in_place": bool(args.in_place),
        "backup": not bool(args.no_backup),
        "safe_reorder": bool(args.safe_reorder),
        "merge_lines": bool(args.merge_lines),
        "clean_comments": bool(args.clean_comments),
        "remove_transparent_dialogues": bool(args.remove_transparent_dialogues),
        "clean_unknown_tags": bool(args.clean_unknown_tags),
        "clean_extradata_references": bool(args.clean_extradata_references),
        "clean_project_garbage": bool(args.clean_project_garbage),
        "clean_extradata": bool(args.clean_extradata),
        "report": str(args.report) if getattr(args, "report", None) else None,
        "report_dir": (
            str(args.report_dir) if getattr(args, "report_dir", None) else None
        ),
        "write_reports": bool(args.write_reports),
        "report_format": str(args.report_format),
        "compare_libass": bool(args.compare_libass),
        "compare_vsfilter": bool(args.compare_vsfilter),
        "ffmpeg": str(args.ffmpeg),
        "xy_vsfilter_dll": args.xy_vsfilter_dll,
        "xy_adapter": args.xy_adapter,
        "fps": str(args.fps),
        "backgrounds": [
            item.strip()
            for item in str(args.backgrounds).split(",")
            if item.strip()
        ],
        "allow_partial": bool(args.allow_partial),
        "channel_tolerance": int(args.channel_tolerance),
        "pixel_tolerance": int(args.pixel_tolerance),
        "timeout": float(args.timeout),
        "render_workers": int(args.render_workers),
    }


def save_clean_settings(path: str | Path, args: argparse.Namespace) -> None:
    Path(path).write_text(
        json.dumps(clean_settings_data(args), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def parse_clean_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Clean redundant ASS tags and editor metadata, with optional validation through two actual renderers."
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        help="One or more ASS/SSA files or directories; may also be provided by JSON inputs",
    )
    parser.add_argument("--settings", help="Read settings from a JSON file")
    parser.add_argument("--save-settings", help="Save the merged effective settings as JSON")
    parser.add_argument(
        "--recursive",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Scan all subdirectories of selected directories",
    )
    destination = parser.add_mutually_exclusive_group()
    destination.add_argument(
        "-o",
        "--output",
        help="Output subtitle for a single input; use --output-dir for batch input",
    )
    destination.add_argument(
        "--output-dir",
        help=(
            "Output directory. An absolute path is a shared root; a relative path is resolved from each input file "
            "or selected directory while preserving relative directory structure"
        ),
    )
    destination.add_argument(
        "--in-place",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Overwrite input files in place",
    )
    backup = parser.add_mutually_exclusive_group()
    backup.add_argument("--backup", dest="no_backup", action="store_false")
    backup.add_argument("--no-backup", dest="no_backup", action="store_true")
    parser.set_defaults(no_backup=None)
    parser.add_argument(
        "--safe-reorder",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Compatibility-safe tag reordering; enabled by default",
    )
    parser.add_argument(
        "--merge-lines",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Merge consecutive identical static Dialogue rows; enabled by default",
    )
    parser.add_argument(
        "--clean-comments",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Remove Comment event rows from [Events]; disabled by default",
    )
    parser.add_argument(
        "--remove-transparent-dialogues",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Remove always-transparent Dialogue rows only when collision-safe; "
            "disabled by default"
        ),
    )
    parser.add_argument(
        "--clean-unknown-tags",
        "--delete-unknown-tags",
        dest="clean_unknown_tags",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Remove override tags unknown to both libass and xy-VSFilter; enabled by default",
    )
    parser.add_argument(
        "--clean-extradata-references",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Remove leading Aegisub {=number} extradata references from events; enabled by default",
    )
    parser.add_argument(
        "--clean-project-garbage",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Remove the entire [Aegisub Project Garbage] section; enabled by default",
    )
    parser.add_argument(
        "--clean-extradata",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Remove [Aegisub Extradata] and all associated {=number} references; enabled by default",
    )
    report_destination = parser.add_mutually_exclusive_group()
    report_destination.add_argument(
        "--report",
        help="Single-file report; .html/.htm produces HTML, other extensions produce Markdown",
    )
    report_destination.add_argument(
        "--report-dir",
        help="Batch report directory; preserves relative directory structure under selected directories",
    )
    parser.add_argument(
        "--write-reports",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Generate a report for every input subtitle; enabled by default and written beside the source when no report directory is specified",
    )
    parser.add_argument(
        "--report-format",
        choices=("md", "html"),
        default=None,
        help="Batch report format; default: html",
    )
    parser.add_argument(
        "--compare-libass",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Run libass comparison; enabled by default",
    )
    parser.add_argument(
        "--compare-vsfilter",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Run xy-VSFilter comparison; disabled by default",
    )
    parser.add_argument("--ffmpeg")
    parser.add_argument("--xy-vsfilter-dll")
    parser.add_argument("--xy-adapter")
    parser.add_argument("--fps")
    parser.add_argument("--backgrounds")
    parser.add_argument(
        "--allow-partial", action=argparse.BooleanOptionalAction, default=None
    )
    parser.add_argument("--channel-tolerance", type=int)
    parser.add_argument("--pixel-tolerance", type=int)
    parser.add_argument("--timeout", type=float)
    parser.add_argument(
        "--render-workers",
        type=int,
        default=None,
        help="Concurrent render-process limit for full-frame differential; default: 4",
    )
    return parser.parse_args(list(argv))


def run_clean_namespace(args: argparse.Namespace) -> tuple[int, CleanResult, Path | None]:
    raw_input = getattr(args, "input", None)
    if raw_input is None:
        inputs = list(getattr(args, "inputs", ()))
        if len(inputs) != 1:
            raise ValueError("run_clean_namespace accepts one subtitle only; use the batch entry point for multiple inputs")
        raw_input = inputs[0]
    input_path = Path(raw_input).resolve()
    if not input_path.is_file():
        raise FileNotFoundError("Input subtitle does not exist: %s" % input_path)
    if input_path.suffix.casefold() not in (".ass", ".ssa"):
        raise ValueError("Only .ass or .ssa files are supported")
    output_path = (
        input_path
        if args.in_place
        else Path(args.output).resolve()
        if args.output
        else default_clean_output_path(input_path)
    )
    report_path = Path(args.report).resolve() if args.report else None
    if output_path == input_path and not args.in_place:
        raise ValueError("Output matches input; use --in-place explicitly to overwrite")
    if report_path and report_path in (input_path, output_path):
        raise ValueError("Report path cannot match the input or output subtitle path")
    compare_enabled = args.compare_libass or args.compare_vsfilter
    if compare_enabled and report_path is None:
        report_path = default_clean_report_path(
            input_path,
            getattr(args, "report_format", "html"),
        )

    operation_progress(2, "Reading and parsing ASS")
    with tempfile.TemporaryDirectory(prefix="clean_tags_original_") as temporary:
        original_snapshot = Path(temporary) / input_path.name
        shutil.copy2(input_path, original_snapshot)
        if args.in_place and not args.no_backup:
            backup = input_path.with_suffix(input_path.suffix + ".bak")
            suffix = 1
            while backup.exists():
                backup = input_path.with_suffix(input_path.suffix + ".bak.%d" % suffix)
                suffix += 1
            shutil.copy2(input_path, backup)
        options = CleanOptions(
            safe_reorder=args.safe_reorder,
            merge_lines=args.merge_lines,
            clean_comments=getattr(args, "clean_comments", False),
            remove_transparent_dialogues=getattr(
                args, "remove_transparent_dialogues", False
            ),
            clean_unknown_tags=getattr(args, "clean_unknown_tags", True),
            clean_extradata_refs=args.clean_extradata_references,
            clean_project_garbage=args.clean_project_garbage,
            clean_extradata_section=args.clean_extradata,
        )
        operation_progress(8, "Cleaning tags and file-level metadata")
        result = clean_ass_file(input_path, output_path, options)
        operation_progress(15, "Generating cleanup audit")
        clean_report = build_clean_report(result)
        compare_code = EXIT_PASS
        if compare_enabled:
            assert report_path is not None
            differential_path = Path(temporary) / "differential.md"
            compare_arguments = [
                "--original-ass",
                str(original_snapshot),
                "--cleaned-ass",
                str(output_path),
                "--full-frames",
                "--fps",
                args.fps,
                "--backgrounds",
                args.backgrounds,
                "--ffmpeg",
                args.ffmpeg,
                "--report",
                str(differential_path),
                "--channel-tolerance",
                str(args.channel_tolerance),
                "--pixel-tolerance",
                str(args.pixel_tolerance),
                "--timeout",
                str(args.timeout),
                "--render-workers",
                str(args.render_workers),
            ]
            if not args.compare_libass:
                compare_arguments.append("--skip-libass")
            if not args.compare_vsfilter:
                compare_arguments.append("--skip-xy-vsfilter")
            if args.xy_vsfilter_dll:
                compare_arguments.extend(["--xy-vsfilter-dll", args.xy_vsfilter_dll])
            if args.xy_adapter:
                compare_arguments.extend(["--xy-adapter", args.xy_adapter])
            if args.allow_partial:
                compare_arguments.append("--allow-partial")
            operation_progress(18, "Starting actual rendering differential")
            compare_code = compare_main(compare_arguments)
            if differential_path.is_file():
                clean_report = combine_markdown_reports(
                    clean_report,
                    differential_path.read_text(encoding="utf-8-sig"),
                )
        if report_path:
            operation_progress(
                97,
                "Writing %s report" % ("HTML" if report_is_html(report_path) else "Markdown"),
            )
            write_report(report_path, clean_report)
        operation_progress(100, "Complete")
        return compare_code, result, report_path


def run_clean_batch_namespace(
    args: argparse.Namespace,
) -> tuple[int, BatchCleanResult]:
    global GUI_PROGRESS_CALLBACK
    if any(
        getattr(args, key, None) is None
        for key in ("recursive", "safe_reorder", "report_format", "render_workers")
    ):
        args = resolve_clean_args(args)
    raw_inputs = list(getattr(args, "inputs", ()))
    if not raw_inputs and getattr(args, "input", None):
        raw_inputs = [args.input]
    if not raw_inputs:
        raise ValueError("At least one subtitle file or directory is required")

    recursive = bool(getattr(args, "recursive", False))
    selected_directory = any(Path(value).expanduser().is_dir() for value in raw_inputs)
    inputs = discover_subtitle_inputs(raw_inputs, recursive)
    batch_mode = selected_directory or len(inputs) > 1
    explicit_output = getattr(args, "output", None)
    output_dir_value = getattr(args, "output_dir", None)
    explicit_report = getattr(args, "report", None)
    report_dir_value = getattr(args, "report_dir", None)
    if batch_mode and explicit_output:
        raise ValueError("--output cannot be used with multiple subtitle or directory inputs; use --output-dir")
    if batch_mode and explicit_report:
        raise ValueError("--report cannot be used with multiple subtitle or directory inputs; use --report-dir")

    output_dir = output_dir_value if output_dir_value else None
    report_dir = Path(report_dir_value).resolve() if report_dir_value else None
    report_format = str(getattr(args, "report_format", "html")).casefold()
    if report_format not in ("md", "html"):
        raise ValueError("Report format must be md or html")
    compare_enabled = bool(args.compare_libass or args.compare_vsfilter)
    write_reports = bool(
        getattr(args, "write_reports", False)
        or explicit_report
        or report_dir
        or compare_enabled
    )

    planned: list[tuple[SubtitleInput, Path, Path | None]] = []
    for item in inputs:
        if args.in_place:
            output_path = item.path
        elif explicit_output:
            output_path = Path(explicit_output).resolve()
        else:
            output_path = batch_clean_output_path(item, output_dir)

        report_path: Path | None = None
        if explicit_report:
            report_path = Path(explicit_report).resolve()
        elif write_reports:
            report_path = batch_clean_report_path(item, report_dir, report_format)
        planned.append((item, output_path, report_path))

    input_keys = {os.path.normcase(str(item.path)) for item in inputs}
    output_keys: dict[str, Path] = {}
    report_keys: dict[str, Path] = {}
    for item, output_path, report_path in planned:
        output_key = os.path.normcase(str(output_path))
        if output_key in output_keys and output_keys[output_key] != item.path:
            raise ValueError("Multiple inputs would write to the same output subtitle: %s" % output_path)
        if (
            not args.in_place
            and output_key in input_keys
            and output_path != item.path
        ):
            raise ValueError("An output subtitle would overwrite another input file: %s" % output_path)
        output_keys[output_key] = item.path
        if report_path is not None:
            report_key = os.path.normcase(str(report_path))
            if report_key in report_keys:
                raise ValueError("Multiple inputs would write to the same report: %s" % report_path)
            if report_path in (item.path, output_path):
                raise ValueError("Report path cannot match a subtitle path: %s" % report_path)
            report_keys[report_key] = item.path

    batch = BatchCleanResult()
    outer_progress = GUI_PROGRESS_CALLBACK
    total = len(planned)
    try:
        for index, (item, output_path, report_path) in enumerate(planned):
            if outer_progress:
                start = index * 100.0 / total
                span = 100.0 / total

                def item_progress(
                    percent: float,
                    message: str,
                    item_start: float = start,
                    item_span: float = span,
                    item_index: int = index,
                    item_name: str = item.path.name,
                ) -> None:
                    outer_progress(
                        item_start + item_span * percent / 100.0,
                        "[%d/%d] %s: %s"
                        % (item_index + 1, total, item_name, message),
                    )

                GUI_PROGRESS_CALLBACK = item_progress

            child = copy.copy(args)
            child.input = str(item.path)
            child.output = None if args.in_place else str(output_path)
            child.report = str(report_path) if report_path is not None else None
            try:
                code, result, actual_report = run_clean_namespace(child)
                batch.items.append(
                    BatchCleanItem(
                        input_path=item.path,
                        output_path=result.output_path,
                        report_path=actual_report,
                        code=code,
                        result=result,
                    )
                )
            except (OSError, UnicodeError, ValueError) as exc:
                batch.items.append(
                    BatchCleanItem(
                        input_path=item.path,
                        output_path=output_path,
                        report_path=report_path,
                        code=EXIT_CONFIGURATION,
                        error=str(exc),
                    )
                )
    finally:
        GUI_PROGRESS_CALLBACK = outer_progress

    codes = {item.code for item in batch.items}
    if EXIT_CONFIGURATION in codes:
        overall = EXIT_CONFIGURATION
    elif EXIT_DIFFERENCE in codes:
        overall = EXIT_DIFFERENCE
    elif EXIT_INCOMPLETE in codes:
        overall = EXIT_INCOMPLETE
    else:
        overall = EXIT_PASS
    if outer_progress:
        outer_progress(100, "Batch processing complete: %d succeeded, %d failed" % (batch.succeeded, batch.failed))
    return overall, batch


def config_path() -> Path:
    return Path(__file__).resolve().with_name("ass_clean_redundant_tags.config.json")


def load_gui_config() -> dict[str, Any]:
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_gui_config(data: dict[str, Any]) -> None:
    try:
        config_path().write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except OSError:
        pass


def launch_gui() -> int:
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
    except ImportError as exc:
        print("ERROR: tkinter is unavailable in this Python installation; the GUI cannot start: %s" % exc, file=sys.stderr)
        return EXIT_CONFIGURATION

    root = tk.Tk()
    root.title("Clean Redundant ASS Tags")
    root.minsize(900, 820)
    config = load_gui_config()
    configured_inputs = config.get("inputs")
    if not isinstance(configured_inputs, list):
        legacy_input = str(config.get("input", "")).strip()
        configured_inputs = [legacy_input] if legacy_input else []
    input_items: list[str] = [
        str(value) for value in configured_inputs if str(value).strip()
    ]
    configured_report_path = str(config.get("report_path", ""))
    configured_report_format = str(config.get("report_format", "")).casefold()
    if configured_report_format not in ("md", "html"):
        configured_report_format = (
            "md"
            if Path(configured_report_path).suffix.casefold() in (".md", ".markdown")
            else "html"
        )
    configured_output_dir = str(config.get("output_dir", "")).strip()
    if not configured_output_dir:
        legacy_output = str(config.get("output", "")).strip()
        if legacy_output:
            legacy_path = Path(legacy_output)
            configured_output_dir = str(
                legacy_path.parent
                if legacy_path.suffix.casefold() in SUBTITLE_SUFFIXES
                else legacy_path
            )
    variables: dict[str, Any] = {
        "output_dir": tk.StringVar(value=configured_output_dir),
        "report_path": tk.StringVar(value=configured_report_path),
        "report_format": tk.StringVar(value=configured_report_format),
        "recursive": tk.BooleanVar(value=config.get("recursive", False)),
        "safe_reorder": tk.BooleanVar(value=config.get("safe_reorder", True)),
        "merge_lines": tk.BooleanVar(value=config.get("merge_lines", True)),
        "clean_comments": tk.BooleanVar(value=config.get("clean_comments", False)),
        "remove_transparent_dialogues": tk.BooleanVar(
            value=config.get("remove_transparent_dialogues", False)
        ),
        "clean_unknown_tags": tk.BooleanVar(
            value=config.get("clean_unknown_tags", True)
        ),
        "clean_refs": tk.BooleanVar(value=config.get("clean_refs", True)),
        "clean_project": tk.BooleanVar(value=config.get("clean_project", True)),
        "clean_extradata": tk.BooleanVar(value=config.get("clean_extradata", True)),
        "write_report": tk.BooleanVar(value=config.get("write_report", True)),
        "compare_libass": tk.BooleanVar(value=config.get("compare_libass", True)),
        "compare_vsfilter": tk.BooleanVar(value=config.get("compare_vsfilter", False)),
        "in_place": tk.BooleanVar(value=config.get("in_place", False)),
        "ffmpeg": tk.StringVar(value=config.get("ffmpeg", default_ffmpeg(Path(__file__).resolve().parent))),
        "xy_dll": tk.StringVar(value=config.get("xy_dll", "")),
        "fps": tk.StringVar(value=config.get("fps", "24000/1001")),
        "render_workers": tk.IntVar(value=config.get("render_workers", 4)),
    }
    frame = ttk.Frame(root, padding=12)
    frame.grid(sticky="nsew")
    root.columnconfigure(0, weight=1)
    root.rowconfigure(0, weight=1)
    frame.columnconfigure(1, weight=1)

    def input_key(value: str) -> str:
        try:
            return os.path.normcase(str(Path(value).resolve()))
        except OSError:
            return os.path.normcase(value)

    def is_batch_selection() -> bool:
        return len(input_items) != 1 or any(Path(value).is_dir() for value in input_items)

    def add_input_values(values: Sequence[str]) -> None:
        existing = {input_key(value) for value in input_items}
        for value in values:
            key = input_key(value)
            if key not in existing:
                input_items.append(value)
                existing.add(key)
        refresh_input_list()
        update_destination_mode()

    def add_files() -> None:
        selected = filedialog.askopenfilenames(
            title="Select one or more ASS/SSA subtitles",
            filetypes=[("ASS/SSA subtitles", "*.ass *.ssa"), ("All files", "*.*")],
        )
        if selected:
            add_input_values(list(selected))

    def add_folder() -> None:
        selected = filedialog.askdirectory(title="Select subtitle directory")
        if selected:
            add_input_values([selected])

    def remove_selected_inputs() -> None:
        selected = list(input_listbox.curselection())
        for index in reversed(selected):
            del input_items[index]
        refresh_input_list()
        update_destination_mode()

    def clear_inputs() -> None:
        input_items.clear()
        refresh_input_list()
        update_destination_mode()

    def refresh_input_list() -> None:
        input_listbox.delete(0, tk.END)
        for value in input_items:
            path = Path(value)
            label = "[Directory] " + str(path) if path.is_dir() else str(path)
            input_listbox.insert(tk.END, label)

    def choose_output() -> None:
        selected = filedialog.askdirectory(title="Select output directory")
        if selected:
            variables["output_dir"].set(selected)

    def update_destination_mode() -> None:
        batch = is_batch_selection()
        report_label.configure(text="Report output (batch directory)" if batch else "Report output")
        report_value = variables["report_path"].get().strip()
        if not input_items:
            return
        elif batch:
            if report_value and Path(report_value).suffix.casefold() in (
                ".md",
                ".markdown",
                ".html",
                ".htm",
            ):
                variables["report_path"].set(str(Path(report_value).parent))
        else:
            source = Path(input_items[0])
            if not report_value or Path(report_value).is_dir():
                variables["report_path"].set(
                    str(
                        default_clean_report_path(
                            source,
                            variables["report_format"].get(),
                        )
                    )
                )

    def sync_report_extension(event: object | None = None) -> None:
        del event
        if is_batch_selection():
            return
        value = variables["report_path"].get().strip()
        suffix = ".html" if variables["report_format"].get() == "html" else ".md"
        if value:
            path = Path(value)
            if path.suffix.casefold() in (".md", ".markdown", ".html", ".htm"):
                variables["report_path"].set(str(path.with_suffix(suffix)))
        elif len(input_items) == 1:
            variables["report_path"].set(
                str(
                    default_clean_report_path(
                        Path(input_items[0]),
                        variables["report_format"].get(),
                    )
                )
            )

    def choose_report() -> None:
        if is_batch_selection():
            selected = filedialog.askdirectory(title="Select batch report directory")
            if selected:
                variables["report_path"].set(selected)
            return
        html_selected = variables["report_format"].get() == "html"
        filetypes = (
            [("HTML", "*.html *.htm"), ("Markdown", "*.md")]
            if html_selected
            else [("Markdown", "*.md"), ("HTML", "*.html *.htm")]
        )
        selected = filedialog.asksaveasfilename(
            title="Select report",
            defaultextension=".html" if html_selected else ".md",
            filetypes=filetypes,
        )
        if selected:
            variables["report_path"].set(selected)
            variables["report_format"].set(
                "html"
                if Path(selected).suffix.casefold() in (".html", ".htm")
                else "md"
            )

    row = 0
    ttk.Label(frame, text="Input subtitles/directories").grid(
        row=row, column=0, sticky="nw", pady=3
    )
    input_box_frame = ttk.Frame(frame)
    input_box_frame.grid(row=row, column=1, sticky="nsew", padx=6)
    input_box_frame.columnconfigure(0, weight=1)
    input_listbox = tk.Listbox(
        input_box_frame,
        height=5,
        selectmode=tk.EXTENDED,
        exportselection=False,
    )
    input_scrollbar = ttk.Scrollbar(
        input_box_frame,
        orient="vertical",
        command=input_listbox.yview,
    )
    input_listbox.configure(yscrollcommand=input_scrollbar.set)
    input_listbox.grid(row=0, column=0, sticky="nsew")
    input_scrollbar.grid(row=0, column=1, sticky="ns")
    input_buttons = ttk.Frame(frame)
    input_buttons.grid(row=row, column=2, sticky="n")
    ttk.Button(input_buttons, text="Add files…", command=add_files).grid(
        row=0, column=0, sticky="ew", pady=1
    )
    ttk.Button(input_buttons, text="Add directory…", command=add_folder).grid(
        row=1, column=0, sticky="ew", pady=1
    )
    ttk.Button(input_buttons, text="Remove selected", command=remove_selected_inputs).grid(
        row=2, column=0, sticky="ew", pady=1
    )
    ttk.Button(input_buttons, text="Clear", command=clear_inputs).grid(
        row=3, column=0, sticky="ew", pady=1
    )
    row += 1
    ttk.Checkbutton(
        frame,
        text="Scan subdirectories",
        variable=variables["recursive"],
    ).grid(row=row, column=1, sticky="w", padx=6, pady=(2, 6))
    row += 1
    output_label = ttk.Label(frame, text="Output directory")
    output_label.grid(row=row, column=0, sticky="w", pady=3)
    output_entry = ttk.Entry(frame, textvariable=variables["output_dir"])
    output_entry.grid(row=row, column=1, sticky="ew", padx=6)
    output_button = ttk.Button(frame, text="Browse…", command=choose_output)
    output_button.grid(row=row, column=2)
    row += 1
    ttk.Label(
        frame,
        text=(
            "Leave blank to write beside each source subtitle. Relative paths use each input file "
            "or selected directory as their base; absolute paths are also accepted."
        ),
        foreground="#666666",
    ).grid(row=row, column=1, columnspan=2, sticky="w", padx=6, pady=(0, 4))
    row += 1

    def toggle_in_place() -> None:
        state = "disabled" if variables["in_place"].get() else "normal"
        output_entry.configure(state=state)
        output_button.configure(state=state)

    ttk.Checkbutton(
        frame,
        text="Overwrite in place (creates .bak automatically)",
        variable=variables["in_place"],
        command=toggle_in_place,
    ).grid(row=row, column=1, sticky="w", pady=(0, 8))
    row += 1
    ttk.Separator(frame).grid(row=row, column=0, columnspan=3, sticky="ew", pady=5)
    row += 1
    checks = (
        ("safe_reorder", "Compatibility-safe tag reordering"),
        ("merge_lines", "Merge consecutive identical static Dialogue rows"),
        ("clean_comments", "Remove Comment event rows from [Events]"),
        (
            "remove_transparent_dialogues",
            "Remove always-transparent Dialogue rows when collision-safe",
        ),
        (
            "clean_unknown_tags",
            "Remove override tags unknown to both libass and xy-VSFilter",
        ),
        ("clean_refs", "Remove leading {=number} extradata references from events"),
        ("clean_project", "Remove [Aegisub Project Garbage]"),
        ("clean_extradata", "Remove [Aegisub Extradata] and all references"),
        ("compare_libass", "Compare all active frames of modified Dialogue rows with libass"),
        ("compare_vsfilter", "Compare all active frames of modified Dialogue rows with xy-VSFilter"),
    )
    for key, label in checks:
        ttk.Checkbutton(frame, text=label, variable=variables[key]).grid(
            row=row, column=0, columnspan=3, sticky="w", pady=2
        )
        row += 1
    ttk.Label(
        frame,
        text=(
            "Unknown tags may be used by other renderers or automation scripts. Removing Extradata may also "
            "break script-specific restore, tracking, or similar features."
        ),
        foreground="#9A5A00",
    ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(2, 8))
    row += 1
    report_label = ttk.Checkbutton(
        frame, text="Write report", variable=variables["write_report"]
    )
    report_label.grid(row=row, column=0, sticky="w")
    ttk.Entry(frame, textvariable=variables["report_path"]).grid(
        row=row, column=1, sticky="ew", padx=6
    )
    ttk.Button(frame, text="Browse…", command=choose_report).grid(row=row, column=2)
    row += 1
    ttk.Label(frame, text="Report format").grid(row=row, column=0, sticky="w", pady=3)
    report_format_box = ttk.Combobox(
        frame,
        textvariable=variables["report_format"],
        values=("html", "md"),
        state="readonly",
        width=10,
    )
    report_format_box.grid(row=row, column=1, sticky="w", padx=6)
    report_format_box.bind("<<ComboboxSelected>>", sync_report_extension)
    ttk.Label(
        frame,
        text="HTML details expand on demand, which keeps large reports responsive",
        foreground="#666666",
    ).grid(row=row, column=2, sticky="e")
    row += 1
    ttk.Label(frame, text="FFmpeg").grid(row=row, column=0, sticky="w", pady=(8, 3))
    ttk.Entry(frame, textvariable=variables["ffmpeg"]).grid(
        row=row, column=1, columnspan=2, sticky="ew", padx=6
    )
    row += 1
    ttk.Label(frame, text="xy‑VSFilter DLL").grid(row=row, column=0, sticky="w", pady=3)
    ttk.Entry(frame, textvariable=variables["xy_dll"]).grid(
        row=row, column=1, columnspan=2, sticky="ew", padx=6
    )
    row += 1
    ttk.Label(frame, text="Frame rate").grid(row=row, column=0, sticky="w", pady=3)
    ttk.Entry(frame, textvariable=variables["fps"], width=18).grid(
        row=row, column=1, sticky="w", padx=6
    )
    row += 1
    ttk.Label(frame, text="Concurrent renders").grid(row=row, column=0, sticky="w", pady=3)
    ttk.Spinbox(
        frame,
        from_=1,
        to=64,
        textvariable=variables["render_workers"],
        width=8,
    ).grid(row=row, column=1, sticky="w", padx=6)
    ttk.Label(
        frame,
        text="2 renderers × before/after × 2 default backgrounds = 8 tasks",
        foreground="#666666",
    ).grid(row=row, column=2, sticky="e")
    row += 1
    progress = ttk.Progressbar(frame, mode="determinate", maximum=100)
    progress.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(12, 4))
    row += 1
    status = tk.StringVar(value="Ready")
    ttk.Label(frame, textvariable=status).grid(row=row, column=0, columnspan=3, sticky="w")
    row += 1

    def persist() -> None:
        data = {key: value.get() for key, value in variables.items()}
        data["inputs"] = list(input_items)
        data.pop("input", None)
        data.pop("output", None)
        save_gui_config(data)

    def update_progress(percent: float, message: str) -> None:
        root.after(0, lambda: (progress.configure(value=percent), status.set(message)))

    def namespace_from_gui() -> argparse.Namespace:
        batch_selection = is_batch_selection()
        output_value = variables["output_dir"].get().strip() or None
        report_value = variables["report_path"].get().strip() or None
        write_reports = variables["write_report"].get()
        return argparse.Namespace(
            inputs=list(input_items),
            recursive=variables["recursive"].get(),
            output=None,
            output_dir=output_value,
            in_place=variables["in_place"].get(),
            no_backup=False,
            safe_reorder=variables["safe_reorder"].get(),
            merge_lines=variables["merge_lines"].get(),
            clean_comments=variables["clean_comments"].get(),
            remove_transparent_dialogues=variables[
                "remove_transparent_dialogues"
            ].get(),
            clean_unknown_tags=variables["clean_unknown_tags"].get(),
            clean_extradata_references=variables["clean_refs"].get(),
            clean_project_garbage=variables["clean_project"].get(),
            clean_extradata=variables["clean_extradata"].get(),
            report=report_value if write_reports and not batch_selection else None,
            report_dir=report_value if write_reports and batch_selection else None,
            write_reports=write_reports,
            report_format=variables["report_format"].get(),
            compare_libass=variables["compare_libass"].get(),
            compare_vsfilter=variables["compare_vsfilter"].get(),
            ffmpeg=variables["ffmpeg"].get().strip() or "ffmpeg",
            xy_vsfilter_dll=variables["xy_dll"].get().strip() or None,
            xy_adapter=None,
            fps=variables["fps"].get().strip() or "24000/1001",
            backgrounds=",".join(DEFAULT_BACKGROUNDS),
            allow_partial=False,
            channel_tolerance=0,
            pixel_tolerance=0,
            timeout=30.0,
            render_workers=variables["render_workers"].get(),
        )

    def save_json_settings() -> None:
        selected = filedialog.asksaveasfilename(
            title="Save JSON settings",
            defaultextension=".json",
            initialfile="settings.json",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
        )
        if not selected:
            return
        include_paths = messagebox.askyesno(
            "Save input and output paths",
            "Include the current inputs, subtitle output, and report paths in the JSON file?\n\n"
            "Choose No to save empty paths that can be supplied on the command line at run time.",
            default="no",
        )
        namespace = namespace_from_gui()
        if not include_paths:
            namespace.inputs = []
            namespace.output = None
            namespace.output_dir = None
            namespace.in_place = False
            namespace.report = None
            namespace.report_dir = None
        try:
            save_clean_settings(Path(selected), namespace)
        except OSError as exc:
            messagebox.showerror("Save failed", str(exc))
            return
        messagebox.showinfo("Settings saved", "JSON settings saved to:\n%s" % selected)

    def start() -> None:
        if not input_items:
            messagebox.showerror("Missing input", "Add at least one ASS/SSA subtitle or directory.")
            return
        persist()
        run_button.configure(state="disabled")
        progress.configure(value=0)
        status.set("Preparing")

        namespace = namespace_from_gui()

        def worker() -> None:
            global GUI_PROGRESS_CALLBACK
            GUI_PROGRESS_CALLBACK = update_progress
            try:
                code, batch = run_clean_batch_namespace(namespace)
                message = (
                    "Complete: %d files succeeded, %d failed; %d rows modified, %d tags removed, "
                    "%d always-transparent Dialogue rows removed, %d Comment rows removed, "
                    "and %d extradata references removed."
                    % (
                        batch.succeeded,
                        batch.failed,
                        batch.changed_dialogues,
                        batch.removed_tags,
                        batch.removed_transparent_dialogues,
                        batch.removed_comment_lines,
                        batch.removed_extradata_references,
                    )
                )
                if code != EXIT_PASS:
                    message += "\nSome files failed or their rendering differential status was not PASS."
                errors = [item for item in batch.items if item.error]
                if errors:
                    message += "\n\nErrors:\n" + "\n".join(
                        "%s: %s" % (item.input_path.name, item.error)
                        for item in errors[:5]
                    )
                    if len(errors) > 5:
                        message += "\n...and %d more errors" % (len(errors) - 5)
                reports = [
                    item.report_path for item in batch.items if item.report_path is not None
                ]
                if len(reports) == 1:
                    message += "\nReport: " + str(reports[0])
                elif reports:
                    message += "\nGenerated %d reports." % len(reports)
                root.after(0, lambda: messagebox.showinfo("Cleanup complete", message))
            except Exception:
                detail = traceback.format_exc()
                root.after(0, lambda: messagebox.showerror("Execution failed", detail))
            finally:
                GUI_PROGRESS_CALLBACK = None
                root.after(0, lambda: run_button.configure(state="normal"))

        threading.Thread(target=worker, daemon=True).start()

    ttk.Button(frame, text="Save as JSON settings…", command=save_json_settings).grid(
        row=row, column=1, sticky="e", padx=6, pady=(10, 0)
    )
    run_button = ttk.Button(frame, text="Start cleanup", command=start)
    run_button.grid(row=row, column=2, sticky="e", pady=(10, 0))
    refresh_input_list()
    update_destination_mode()
    toggle_in_place()
    root.mainloop()
    return EXIT_PASS


def main(argv: Sequence[str]) -> int:
    arguments = list(argv)
    if not arguments:
        return launch_gui()
    if arguments[0] in ("-V", "--version"):
        print(VERSION)
        return EXIT_PASS
    command = arguments.pop(0) if arguments[0] in ("clean", "compare") else "clean"
    if command == "compare":
        return compare_main(arguments)
    if command == "clean":
        try:
            args = resolve_clean_args(parse_clean_args(arguments))
            if args.save_settings:
                save_clean_settings(Path(args.save_settings).resolve(), args)
            code, batch = run_clean_batch_namespace(args)
        except (OSError, UnicodeError, ValueError) as exc:
            print("ERROR: %s" % exc, file=sys.stderr)
            return EXIT_CONFIGURATION
        print("STATUS=%s" % ("PASS" if code == EXIT_PASS else "CHECK_REPORT"))
        print("FILES=%d" % len(batch.items))
        print("SUCCEEDED=%d" % batch.succeeded)
        print("FAILED=%d" % batch.failed)
        print("CHANGED_DIALOGUES=%d" % batch.changed_dialogues)
        print("REMOVED_TAGS=%d" % batch.removed_tags)
        print(
            "REMOVED_TRANSPARENT_DIALOGUES=%d"
            % batch.removed_transparent_dialogues
        )
        print("REMOVED_COMMENT_LINES=%d" % batch.removed_comment_lines)
        print(
            "REMOVED_EXTRADATA_REFERENCES=%d"
            % batch.removed_extradata_references
        )
        if len(batch.items) == 1:
            only = batch.items[0]
            if only.output_path:
                print("OUTPUT=%s" % only.output_path)
            if only.report_path:
                print("REPORT=%s" % only.report_path)
        for index, item in enumerate(batch.items, start=1):
            print("INPUT_%d=%s" % (index, item.input_path))
            if item.output_path:
                print("OUTPUT_%d=%s" % (index, item.output_path))
            if item.report_path:
                print("REPORT_%d=%s" % (index, item.report_path))
            if item.error:
                print("ERROR_%d=%s" % (index, item.error))
        return code
    raise AssertionError("Unreachable command dispatch: %s" % command)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
