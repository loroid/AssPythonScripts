#!/usr/bin/env python3
"""Standalone optical centering for ASS subtitles, filtered by Style."""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import json
import math
import os
import re
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Optional

if os.name == "nt":
    from ctypes import wintypes

PROGRAM_NAME = "AssGlyphOpticalCenter"
VERSION = "1.6.0"
AUTHOR = "Ioroid"
OUTPUT_SUFFIX = ".centered.ass"
DEFAULT_THRESHOLD = 0.20
DEFAULT_LARGE_MARGIN_THRESHOLD = 100
DEFAULT_RUBY_DISTANCE_THRESHOLD_PERCENT = 3.0
MAX_RUBY_DISTANCE_THRESHOLD_PERCENT = 100.0

OVERRIDE_BLOCK_RE = re.compile(r"\{([^}]*)\}")
TAG_RE = re.compile(
    r"\\(fscx|fscy|fsp|fax|fay|fn|fs(?![A-Za-z])|"
    r"b(?![A-Za-z])|i(?![A-Za-z])|u(?![A-Za-z])|"
    r"s(?![A-Za-z])|p(?![A-Za-z])|r)([^\\}]*)",
    re.IGNORECASE,
)
POS_RE = re.compile(
    r"\\pos\s*\(\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*,\s*"
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*\)",
    re.IGNORECASE,
)
POS_COMPONENT_RE = re.compile(
    r"(\\pos\s*\(\s*)([+-]?(?:\d+(?:\.\d*)?|\.\d+))(\s*,\s*)"
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))(\s*\))",
    re.IGNORECASE,
)
MOVE_RE = re.compile(r"\\move\s*\(", re.IGNORECASE)
AN_RE = re.compile(r"\\an([1-9])", re.IGNORECASE)
CLIP_RE = re.compile(r"\\clip\s*\([^)]*\)", re.IGNORECASE)


class UserError(RuntimeError):
    """An input or configuration error suitable for direct display."""


@dataclass
class Style:
    name: str
    fontname: str
    fontsize: float
    bold: bool
    italic: bool
    underline: bool
    strikeout: bool
    scale_x: float
    scale_y: float
    spacing: float
    alignment: int
    margin_l: int
    margin_r: int
    margin_v: int


@dataclass
class Event:
    line_index: int
    prefix: str
    fields: list[str]
    field_index: dict[str, int]
    newline: str
    dirty: bool = False

    @property
    def style_name(self) -> str:
        return self.get("style").strip()

    @property
    def text(self) -> str:
        return self.get("text")

    @text.setter
    def text(self, value: str) -> None:
        self.set("text", value)

    @property
    def is_comment(self) -> bool:
        return self.prefix.casefold() == "comment"

    @property
    def timing_key(self) -> tuple[str, str]:
        return self.get("start").strip(), self.get("end").strip()

    def get(self, name: str, default: str = "") -> str:
        index = self.field_index.get(name.casefold())
        return self.fields[index] if index is not None else default

    def set(self, name: str, value: str) -> None:
        index = self.field_index.get(name.casefold())
        if index is None:
            raise UserError(f"The Events format has no {name!r} field.")
        self.fields[index] = value
        self.dirty = True

    def get_int(self, name: str, default: int = 0) -> int:
        return parse_int(self.get(name), default)

    def set_margin(self, name: str, value: int) -> None:
        old_value = self.get(name)
        rounded = int(value)
        if old_value.isdigit() and len(old_value) > 1:
            rendered = f"{rounded:0{len(old_value)}d}"
        else:
            rendered = str(rounded)
        self.set(name, rendered)

    def serialize(self) -> str:
        return f"{self.prefix}: {','.join(self.fields)}{self.newline}"


@dataclass
class ASSDocument:
    lines: list[str]
    styles: dict[str, Style]
    events: list[Event]
    play_res_x: int
    play_res_y: int
    encoding: str
    had_bom: bool

    def render(self) -> str:
        output = list(self.lines)
        for event in self.events:
            if event.dirty:
                output[event.line_index] = event.serialize()
        return "".join(output)


@dataclass
class StyleRules:
    match: list[str] = field(default_factory=list)
    match_exact: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    exclude_exact: list[str] = field(default_factory=list)
    ignore_case: bool = False

    def validate(self) -> None:
        if not self.match and not self.match_exact:
            raise UserError(
                "At least one --match-style or --match-style-exact value is required."
            )
        for group in (self.match, self.match_exact, self.exclude, self.exclude_exact):
            if any(value == "" for value in group):
                raise UserError("Style filter values cannot be empty.")

    def accepts(self, style_name: str) -> bool:
        candidate = style_name.casefold() if self.ignore_case else style_name

        def normalized(values: Iterable[str]) -> Iterable[str]:
            if self.ignore_case:
                return (value.casefold() for value in values)
            return values

        included = any(value in candidate for value in normalized(self.match))
        included = included or any(
            value == candidate for value in normalized(self.match_exact)
        )
        if not included:
            return False

        excluded = any(value in candidate for value in normalized(self.exclude))
        excluded = excluded or any(
            value == candidate for value in normalized(self.exclude_exact)
        )
        return not excluded


@dataclass
class RubyStyleRules:
    match: list[str] = field(default_factory=list)
    match_exact: list[str] = field(default_factory=list)
    ignore_case: bool = False

    @property
    def enabled(self) -> bool:
        return bool(self.match or self.match_exact)

    def validate(self) -> None:
        if any(value == "" for value in (*self.match, *self.match_exact)):
            raise UserError("Ruby Style filter values cannot be empty.")

    def accepts(self, style_name: str) -> bool:
        if not self.enabled:
            return False
        candidate = style_name.casefold() if self.ignore_case else style_name
        match = (
            (value.casefold() if self.ignore_case else value)
            for value in self.match
        )
        match_exact = (
            (value.casefold() if self.ignore_case else value)
            for value in self.match_exact
        )
        return any(value in candidate for value in match) or any(
            value == candidate for value in match_exact
        )


@dataclass
class Options:
    mode: str = "margin"
    in_place: bool = False
    threshold: float = DEFAULT_THRESHOLD
    large_margin_threshold: int = DEFAULT_LARGE_MARGIN_THRESHOLD
    preserve_large_margins: bool = True
    process_comments: bool = False
    debug_clip: bool = False
    rules: StyleRules = field(default_factory=StyleRules)
    ruby_rules: RubyStyleRules = field(default_factory=RubyStyleRules)
    ruby_distance_threshold_percent: float = DEFAULT_RUBY_DISTANCE_THRESHOLD_PERCENT
    font_dirs: list[str] = field(default_factory=list)


@dataclass
class ExecutionOptions:
    inputs: list[Path] = field(default_factory=list)
    output: Optional[Path] = None
    output_dir: Optional[Path] = None
    recursive: bool = False
    overwrite: bool = False
    dry_run: bool = False
    list_styles: bool = False


@dataclass
class TextState:
    fontname: str
    bold: bool
    italic: bool
    underline: bool
    strikeout: bool
    fontsize: float
    scale_x: float
    scale_y: float
    spacing: float
    shear_x: float = 0.0
    shear_y: float = 0.0
    drawing: bool = False


@dataclass
class InkBounds:
    advance_width: float
    min_x: float
    max_x: float
    min_y: float
    max_y: float
    ascent: float
    descent: float

    @property
    def center_x(self) -> float:
        return (self.min_x + self.max_x) / 2.0


@dataclass
class RenderRecord:
    event: Event
    style: Style
    bounds: InkBounds
    left: float
    right: float
    top: float
    bottom: float

    @property
    def center_x(self) -> float:
        return (self.left + self.right) / 2.0

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2.0


@dataclass
class ProcessingStats:
    total_events: int = 0
    style_matched: int = 0
    modified: int = 0
    comments_skipped: int = 0
    missing_style: int = 0
    measurement_failed: int = 0
    position_skipped: int = 0


def parse_int(value: object, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def parse_float(value: object, default: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def parse_bool_number(value: object) -> bool:
    return parse_int(value, 0) != 0


def split_ass_fields(content: str, field_count: int) -> list[str]:
    if field_count <= 1:
        return [content]
    parts = content.split(",", field_count - 1)
    if len(parts) < field_count:
        parts.extend([""] * (field_count - len(parts)))
    return parts


def detect_text_encoding(data: bytes) -> tuple[str, bool]:
    if data.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig", True
    try:
        data.decode("utf-8")
        return "utf-8", False
    except UnicodeDecodeError as error:
        raise UserError(
            "The ASS file is not UTF-8. Convert it to UTF-8 in Aegisub first."
        ) from error


def parse_ass(path: Path) -> ASSDocument:
    data = path.read_bytes()
    encoding, had_bom = detect_text_encoding(data)
    text = data.decode(encoding)
    lines = text.splitlines(keepends=True)
    if text and (not lines or not text.endswith(("\n", "\r"))):
        # splitlines(keepends=True) already retains the final non-newline line.
        pass

    section = ""
    style_fields: list[str] = []
    event_fields: list[str] = []
    styles: dict[str, Style] = {}
    events: list[Event] = []
    play_res_x = 0
    play_res_y = 0

    for index, raw_line in enumerate(lines):
        body = raw_line.rstrip("\r\n")
        newline = raw_line[len(body):]
        stripped = body.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1].strip().casefold()
            continue

        if section == "script info" and ":" in stripped:
            key, value = stripped.split(":", 1)
            if key.strip().casefold() == "playresx":
                play_res_x = parse_int(value)
            elif key.strip().casefold() == "playresy":
                play_res_y = parse_int(value)

        elif section in {"v4+ styles", "v4 styles"}:
            if stripped.casefold().startswith("format:"):
                style_fields = [
                    value.strip() for value in stripped.split(":", 1)[1].split(",")
                ]
            elif stripped.casefold().startswith("style:") and style_fields:
                values = split_ass_fields(stripped.split(":", 1)[1].lstrip(), len(style_fields))
                mapping = {
                    name.casefold(): values[position].strip()
                    for position, name in enumerate(style_fields)
                }
                style = Style(
                    name=mapping.get("name", ""),
                    fontname=mapping.get("fontname", "Arial"),
                    fontsize=parse_float(mapping.get("fontsize"), 48.0),
                    bold=parse_bool_number(mapping.get("bold")),
                    italic=parse_bool_number(mapping.get("italic")),
                    underline=parse_bool_number(mapping.get("underline")),
                    strikeout=parse_bool_number(mapping.get("strikeout")),
                    scale_x=parse_float(mapping.get("scalex"), 100.0),
                    scale_y=parse_float(mapping.get("scaley"), 100.0),
                    spacing=parse_float(mapping.get("spacing"), 0.0),
                    alignment=parse_int(mapping.get("alignment"), 2),
                    margin_l=parse_int(mapping.get("marginl")),
                    margin_r=parse_int(mapping.get("marginr")),
                    margin_v=parse_int(mapping.get("marginv")),
                )
                if style.name:
                    styles[style.name] = style

        elif section == "events":
            if stripped.casefold().startswith("format:"):
                event_fields = [
                    value.strip() for value in stripped.split(":", 1)[1].split(",")
                ]
                if event_fields and event_fields[-1].casefold() != "text":
                    raise UserError("The Events Format must place Text last.")
            elif event_fields and re.match(r"^(Dialogue|Comment):", stripped, re.I):
                prefix, content = stripped.split(":", 1)
                values = split_ass_fields(content.lstrip(), len(event_fields))
                events.append(
                    Event(
                        line_index=index,
                        prefix=prefix,
                        fields=values,
                        field_index={
                            name.casefold(): position
                            for position, name in enumerate(event_fields)
                        },
                        newline=newline,
                    )
                )

    if not styles:
        raise UserError("No styles were found in the ASS file.")
    if not events:
        raise UserError("No Dialogue or Comment events were found in the ASS file.")
    if play_res_x <= 0:
        raise UserError("PlayResX is missing or invalid.")
    if play_res_y <= 0:
        play_res_y = 1080

    return ASSDocument(
        lines=lines,
        styles=styles,
        events=events,
        play_res_x=play_res_x,
        play_res_y=play_res_y,
        encoding=encoding,
        had_bom=had_bom,
    )


FONT_SUFFIXES = {".ttf", ".otf", ".ttc", ".otc"}


def collect_font_paths(directories: Iterable[str]) -> list[Path]:
    paths: set[Path] = set()
    for value in directories:
        directory = Path(value).expanduser()
        if not directory.is_dir():
            continue
        paths.update(
            path.resolve()
            for path in directory.rglob("*")
            if path.is_file() and path.suffix.casefold() in FONT_SUFFIXES
        )
    return sorted(paths, key=lambda item: str(item).casefold())


if os.name == "nt":
    class _GDISize(ctypes.Structure):
        _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]


    class _GDIPoint(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


    class _GDITextMetric(ctypes.Structure):
        _fields_ = [
            ("tmHeight", wintypes.LONG),
            ("tmAscent", wintypes.LONG),
            ("tmDescent", wintypes.LONG),
            ("tmInternalLeading", wintypes.LONG),
            ("tmExternalLeading", wintypes.LONG),
            ("tmAveCharWidth", wintypes.LONG),
            ("tmMaxCharWidth", wintypes.LONG),
            ("tmWeight", wintypes.LONG),
            ("tmOverhang", wintypes.LONG),
            ("tmDigitizedAspectX", wintypes.LONG),
            ("tmDigitizedAspectY", wintypes.LONG),
            ("tmFirstChar", wintypes.WCHAR),
            ("tmLastChar", wintypes.WCHAR),
            ("tmDefaultChar", wintypes.WCHAR),
            ("tmBreakChar", wintypes.WCHAR),
            ("tmItalic", wintypes.BYTE),
            ("tmUnderlined", wintypes.BYTE),
            ("tmStruckOut", wintypes.BYTE),
            ("tmPitchAndFamily", wintypes.BYTE),
            ("tmCharSet", wintypes.BYTE),
        ]


class WindowsGDITextMeasurer:
    """Measure the same Windows outline paths used by Yutils."""

    PRECISION = 64

    def __init__(self, font_paths: Iterable[Path] = ()) -> None:
        if os.name != "nt":
            raise OSError("Windows GDI is unavailable on this platform.")
        self.gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        self._configure_functions()
        self.registered_fonts: list[Path] = []
        for path in font_paths:
            if not self.gdi32.AddFontResourceExW(str(path), 0x10, None):
                self.close()
                raise OSError(f"Could not register private font: {path}")
            self.registered_fonts.append(path)
        self.dc = self.gdi32.CreateCompatibleDC(None)
        if not self.dc:
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error
        self.gdi32.SetMapMode(self.dc, 1)  # MM_TEXT
        self.gdi32.SetBkMode(self.dc, 1)  # TRANSPARENT

    def _configure_functions(self) -> None:
        handle = ctypes.c_void_p
        self.gdi32.CreateCompatibleDC.argtypes = [handle]
        self.gdi32.CreateCompatibleDC.restype = handle
        self.gdi32.DeleteDC.argtypes = [handle]
        self.gdi32.DeleteDC.restype = wintypes.BOOL
        self.gdi32.SetMapMode.argtypes = [handle, ctypes.c_int]
        self.gdi32.SetMapMode.restype = ctypes.c_int
        self.gdi32.SetBkMode.argtypes = [handle, ctypes.c_int]
        self.gdi32.SetBkMode.restype = ctypes.c_int
        self.gdi32.CreateFontW.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPCWSTR,
        ]
        self.gdi32.CreateFontW.restype = handle
        self.gdi32.SelectObject.argtypes = [handle, handle]
        self.gdi32.SelectObject.restype = handle
        self.gdi32.DeleteObject.argtypes = [handle]
        self.gdi32.DeleteObject.restype = wintypes.BOOL
        self.gdi32.AddFontResourceExW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_void_p,
        ]
        self.gdi32.AddFontResourceExW.restype = ctypes.c_int
        self.gdi32.RemoveFontResourceExW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_void_p,
        ]
        self.gdi32.RemoveFontResourceExW.restype = wintypes.BOOL
        self.gdi32.GetTextMetricsW.argtypes = [
            handle,
            ctypes.POINTER(_GDITextMetric),
        ]
        self.gdi32.GetTextMetricsW.restype = wintypes.BOOL
        self.gdi32.GetTextExtentPoint32W.argtypes = [
            handle,
            wintypes.LPCWSTR,
            ctypes.c_int,
            ctypes.POINTER(_GDISize),
        ]
        self.gdi32.GetTextExtentPoint32W.restype = wintypes.BOOL
        self.gdi32.BeginPath.argtypes = [handle]
        self.gdi32.BeginPath.restype = wintypes.BOOL
        self.gdi32.EndPath.argtypes = [handle]
        self.gdi32.EndPath.restype = wintypes.BOOL
        self.gdi32.AbortPath.argtypes = [handle]
        self.gdi32.AbortPath.restype = wintypes.BOOL
        self.gdi32.ExtTextOutW.argtypes = [
            handle,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
            ctypes.c_void_p,
            wintypes.LPCWSTR,
            wintypes.UINT,
            ctypes.POINTER(ctypes.c_int),
        ]
        self.gdi32.ExtTextOutW.restype = wintypes.BOOL
        self.gdi32.GetPath.argtypes = [
            handle,
            ctypes.POINTER(_GDIPoint),
            ctypes.POINTER(wintypes.BYTE),
            ctypes.c_int,
        ]
        self.gdi32.GetPath.restype = ctypes.c_int

    def close(self) -> None:
        dc = getattr(self, "dc", None)
        if dc:
            self.gdi32.DeleteDC(dc)
            self.dc = None
        registered_fonts = getattr(self, "registered_fonts", [])
        for path in reversed(registered_fonts):
            self.gdi32.RemoveFontResourceExW(str(path), 0x10, None)
        self.registered_fonts = []

    def __del__(self) -> None:  # pragma: no cover - interpreter cleanup
        try:
            self.close()
        except Exception:
            pass

    @staticmethod
    def _utf16_buffer(text: str):
        encoded = text.encode("utf-16-le")
        buffer = ctypes.create_string_buffer(encoded + b"\0\0")
        return buffer, ctypes.cast(buffer, wintypes.LPCWSTR), len(encoded) // 2

    def _extent(self, pointer, length: int) -> Optional[_GDISize]:
        size = _GDISize()
        if not self.gdi32.GetTextExtentPoint32W(
            self.dc, pointer, length, ctypes.byref(size)
        ):
            return None
        return size

    def measure_run(
        self, text: str, state: TextState
    ) -> Optional[tuple[float, float, float, float, float, float, float]]:
        font = self.gdi32.CreateFontW(
            int(state.fontsize * self.PRECISION),
            0,
            0,
            0,
            700 if state.bold else 400,
            1 if state.italic else 0,
            1 if state.underline else 0,
            1 if state.strikeout else 0,
            1,  # DEFAULT_CHARSET
            4,  # OUT_TT_PRECIS
            0,  # CLIP_DEFAULT_PRECIS
            4,  # ANTIALIASED_QUALITY
            0,  # DEFAULT_PITCH | FF_DONTCARE
            state.fontname,
        )
        if not font:
            return None

        previous_font = self.gdi32.SelectObject(self.dc, font)
        if not previous_font:
            self.gdi32.DeleteObject(font)
            return None

        points: list[tuple[int, int]] = []
        try:
            buffer, pointer, text_length = self._utf16_buffer(text)
            extent = self._extent(pointer, text_length)
            if extent is None:
                return None

            metrics = _GDITextMetric()
            if not self.gdi32.GetTextMetricsW(self.dc, ctypes.byref(metrics)):
                return None

            character_widths = None
            widths_pointer = None
            if state.spacing != 0 and text_length > 0:
                character_widths = (ctypes.c_int * text_length)()
                extra_space = state.spacing * self.PRECISION
                for index in range(text_length):
                    character_pointer = ctypes.cast(
                        ctypes.byref(buffer, index * 2), wintypes.LPCWSTR
                    )
                    character_extent = self._extent(character_pointer, 1)
                    if character_extent is None:
                        return None
                    character_widths[index] = int(
                        character_extent.cx + extra_space
                    )
                widths_pointer = ctypes.cast(
                    character_widths, ctypes.POINTER(ctypes.c_int)
                )

            if not self.gdi32.BeginPath(self.dc):
                return None
            try:
                if not self.gdi32.ExtTextOutW(
                    self.dc,
                    0,
                    0,
                    0,
                    None,
                    pointer,
                    text_length,
                    widths_pointer,
                ):
                    return None
                if not self.gdi32.EndPath(self.dc):
                    return None
                point_count = self.gdi32.GetPath(self.dc, None, None, 0)
                if point_count > 0:
                    point_buffer = (_GDIPoint * point_count)()
                    type_buffer = (wintypes.BYTE * point_count)()
                    copied = self.gdi32.GetPath(
                        self.dc, point_buffer, type_buffer, point_count
                    )
                    if copied > 0:
                        points = [
                            (point_buffer[index].x, point_buffer[index].y)
                            for index in range(copied)
                        ]
            finally:
                self.gdi32.AbortPath(self.dc)

            scale_x = state.scale_x / 100.0
            scale_y = state.scale_y / 100.0
            advance = (
                extent.cx / self.PRECISION + state.spacing * text_length
            ) * scale_x
            ascent = metrics.tmAscent / self.PRECISION * scale_y
            descent = metrics.tmDescent / self.PRECISION * scale_y
            if not points:
                return (
                    advance,
                    math.inf,
                    -math.inf,
                    math.inf,
                    -math.inf,
                    ascent,
                    descent,
                )

            effective_x = (
                state.shear_x * state.scale_x / state.scale_y
                if state.scale_y != 0
                else 0.0
            )
            effective_y = (
                state.shear_y * state.scale_y / state.scale_x
                if state.scale_x != 0
                else 0.0
            )
            transformed: list[tuple[float, float]] = []
            for raw_x, raw_y in points:
                x = raw_x / self.PRECISION * scale_x
                y = raw_y / self.PRECISION * scale_y
                transformed.append(
                    (x + effective_x * y, y + effective_y * x)
                )
            min_x = min(value[0] for value in transformed)
            max_x = max(value[0] for value in transformed)
            # Convert GDI's top-relative path coordinates to the baseline-
            # relative coordinates used by the rest of this implementation.
            min_y = min(value[1] for value in transformed) - ascent
            max_y = max(value[1] for value in transformed) - ascent
            return advance, min_x, max_x, min_y, max_y, ascent, descent
        finally:
            self.gdi32.SelectObject(self.dc, previous_font)
            self.gdi32.DeleteObject(font)


class _PangoRectangle(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_int),
        ("y", ctypes.c_int),
        ("width", ctypes.c_int),
        ("height", ctypes.c_int),
    ]


class _CairoPathHeader(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("length", ctypes.c_int)]


class _CairoPathPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class _CairoPathData(ctypes.Union):
    _fields_ = [("header", _CairoPathHeader), ("point", _CairoPathPoint)]


class _CairoPath(ctypes.Structure):
    _fields_ = [
        ("status", ctypes.c_int),
        ("data", ctypes.POINTER(_CairoPathData)),
        ("num_data", ctypes.c_int),
    ]


def load_shared_library(*names: str):
    candidates: list[str] = []
    for name in names:
        located = ctypes.util.find_library(name)
        if located:
            candidates.append(located)
        candidates.append(name)
    errors: list[str] = []
    for candidate in dict.fromkeys(candidates):
        try:
            return ctypes.CDLL(
                candidate,
                mode=getattr(ctypes, "RTLD_GLOBAL", 0),
            )
        except OSError as error:
            errors.append(str(error))
    raise OSError(errors[-1] if errors else f"Could not load {names[0]}")


def yutils_round(value: float, decimals: int = 3) -> float:
    factor = 10 ** max(0, math.floor(decimals))
    return math.floor(value * factor + 0.5) / factor


class PangoCairoTextMeasurer:
    """Measure the same Pango/Cairo outline paths used by Yutils on Unix."""

    PRECISION = 64
    PANGO_SCALE = 1024

    def __init__(self, font_paths: Iterable[Path] = ()) -> None:
        if os.name == "nt":
            raise OSError("Pango/Cairo is not selected on Windows.")
        try:
            self.cairo = load_shared_library(
                "cairo", "libcairo.so.2", "libcairo.2.dylib"
            )
            self.pango = load_shared_library(
                "pango-1.0", "libpango-1.0.so.0", "libpango-1.0.0.dylib"
            )
            self.pangocairo = load_shared_library(
                "pangocairo-1.0",
                "libpangocairo-1.0.so.0",
                "libpangocairo-1.0.0.dylib",
            )
            self.gobject = load_shared_library(
                "gobject-2.0",
                "libgobject-2.0.so.0",
                "libgobject-2.0.0.dylib",
            )
        except OSError as error:
            raise OSError(f"Pango/Cairo libraries could not be loaded: {error}") from error
        self._configure_functions()
        paths = list(font_paths)
        if paths:
            self._register_fonts(paths)

    def _configure_functions(self) -> None:
        pointer = ctypes.c_void_p

        self.cairo.cairo_image_surface_create.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        ]
        self.cairo.cairo_image_surface_create.restype = pointer
        self.cairo.cairo_surface_destroy.argtypes = [pointer]
        self.cairo.cairo_create.argtypes = [pointer]
        self.cairo.cairo_create.restype = pointer
        self.cairo.cairo_destroy.argtypes = [pointer]
        self.cairo.cairo_save.argtypes = [pointer]
        self.cairo.cairo_restore.argtypes = [pointer]
        self.cairo.cairo_scale.argtypes = [
            pointer,
            ctypes.c_double,
            ctypes.c_double,
        ]
        self.cairo.cairo_new_path.argtypes = [pointer]
        self.cairo.cairo_copy_path.argtypes = [pointer]
        self.cairo.cairo_copy_path.restype = ctypes.POINTER(_CairoPath)
        self.cairo.cairo_path_destroy.argtypes = [ctypes.POINTER(_CairoPath)]

        self.pangocairo.pango_cairo_create_layout.argtypes = [pointer]
        self.pangocairo.pango_cairo_create_layout.restype = pointer
        self.pangocairo.pango_cairo_layout_path.argtypes = [pointer, pointer]

        self.gobject.g_object_unref.argtypes = [pointer]

        self.pango.pango_font_description_new.restype = pointer
        self.pango.pango_font_description_free.argtypes = [pointer]
        self.pango.pango_font_description_set_family.argtypes = [
            pointer,
            ctypes.c_char_p,
        ]
        self.pango.pango_font_description_set_weight.argtypes = [
            pointer,
            ctypes.c_int,
        ]
        self.pango.pango_font_description_set_style.argtypes = [
            pointer,
            ctypes.c_int,
        ]
        self.pango.pango_font_description_set_absolute_size.argtypes = [
            pointer,
            ctypes.c_double,
        ]
        self.pango.pango_layout_set_font_description.argtypes = [pointer, pointer]
        self.pango.pango_attr_list_new.restype = pointer
        self.pango.pango_attr_list_unref.argtypes = [pointer]
        self.pango.pango_attr_list_insert.argtypes = [pointer, pointer]
        self.pango.pango_attr_underline_new.argtypes = [ctypes.c_int]
        self.pango.pango_attr_underline_new.restype = pointer
        self.pango.pango_attr_strikethrough_new.argtypes = [ctypes.c_int]
        self.pango.pango_attr_strikethrough_new.restype = pointer
        self.pango.pango_attr_letter_spacing_new.argtypes = [ctypes.c_int]
        self.pango.pango_attr_letter_spacing_new.restype = pointer
        self.pango.pango_layout_set_attributes.argtypes = [pointer, pointer]
        self.pango.pango_layout_get_context.argtypes = [pointer]
        self.pango.pango_layout_get_context.restype = pointer
        self.pango.pango_layout_get_font_description.argtypes = [pointer]
        self.pango.pango_layout_get_font_description.restype = pointer
        self.pango.pango_context_get_metrics.argtypes = [
            pointer,
            pointer,
            pointer,
        ]
        self.pango.pango_context_get_metrics.restype = pointer
        self.pango.pango_font_metrics_unref.argtypes = [pointer]
        self.pango.pango_font_metrics_get_ascent.argtypes = [pointer]
        self.pango.pango_font_metrics_get_ascent.restype = ctypes.c_int
        self.pango.pango_font_metrics_get_descent.argtypes = [pointer]
        self.pango.pango_font_metrics_get_descent.restype = ctypes.c_int
        self.pango.pango_layout_set_text.argtypes = [
            pointer,
            ctypes.c_char_p,
            ctypes.c_int,
        ]
        self.pango.pango_layout_get_pixel_extents.argtypes = [
            pointer,
            ctypes.POINTER(_PangoRectangle),
            ctypes.POINTER(_PangoRectangle),
        ]

    def _register_fonts(self, paths: list[Path]) -> None:
        try:
            fontconfig = load_shared_library(
                "fontconfig", "libfontconfig.so.1", "libfontconfig.1.dylib"
            )
        except OSError as error:
            raise OSError(
                "Fontconfig is required when --font-dir is used on this platform."
            ) from error
        pointer = ctypes.c_void_p
        fontconfig.FcConfigGetCurrent.restype = pointer
        fontconfig.FcInitLoadConfigAndFonts.restype = pointer
        fontconfig.FcConfigAppFontAddFile.argtypes = [pointer, ctypes.c_char_p]
        fontconfig.FcConfigAppFontAddFile.restype = ctypes.c_int
        fontconfig.FcConfigBuildFonts.argtypes = [pointer]
        fontconfig.FcConfigBuildFonts.restype = ctypes.c_int
        config = fontconfig.FcConfigGetCurrent()
        if not config:
            config = fontconfig.FcInitLoadConfigAndFonts()
        if not config:
            raise OSError("Fontconfig could not initialize its current configuration.")
        for path in paths:
            if not fontconfig.FcConfigAppFontAddFile(config, os.fsencode(path)):
                raise OSError(f"Could not register application font: {path}")
        if not fontconfig.FcConfigBuildFonts(config):
            raise OSError("Fontconfig could not rebuild its font set.")
        self.fontconfig = fontconfig

    def _font_metrics(self, layout) -> Optional[tuple[int, int, float]]:
        context = self.pango.pango_layout_get_context(layout)
        description = self.pango.pango_layout_get_font_description(layout)
        metrics = self.pango.pango_context_get_metrics(
            context, description, None
        )
        if not metrics:
            return None
        try:
            ascent = self.pango.pango_font_metrics_get_ascent(metrics)
            descent = self.pango.pango_font_metrics_get_descent(metrics)
        finally:
            self.pango.pango_font_metrics_unref(metrics)
        natural_height = (
            (ascent + descent) / self.PANGO_SCALE / self.PRECISION
        )
        return ascent, descent, natural_height

    @staticmethod
    def _path_points(path: _CairoPath) -> list[tuple[float, float]]:
        points: list[tuple[float, float]] = []
        index = 0
        while index < path.num_data:
            header = path.data[index].header
            if header.length <= 0:
                break
            if header.type in {0, 1} and index + 1 < path.num_data:
                point = path.data[index + 1].point
                points.append((yutils_round(point.x), yutils_round(point.y)))
            elif header.type == 2 and index + 3 < path.num_data:
                for offset in (1, 2, 3):
                    point = path.data[index + offset].point
                    points.append((yutils_round(point.x), yutils_round(point.y)))
            index += header.length
        return points

    def measure_run(
        self, text: str, state: TextState
    ) -> Optional[tuple[float, float, float, float, float, float, float]]:
        if state.fontsize <= 0:
            return None
        surface = self.cairo.cairo_image_surface_create(2, 1, 1)  # CAIRO_FORMAT_A8
        if not surface:
            return None
        context = self.cairo.cairo_create(surface)
        if not context:
            self.cairo.cairo_surface_destroy(surface)
            return None
        layout = self.pangocairo.pango_cairo_create_layout(context)
        if not layout:
            self.cairo.cairo_destroy(context)
            self.cairo.cairo_surface_destroy(surface)
            return None
        description = self.pango.pango_font_description_new()
        attributes = self.pango.pango_attr_list_new()
        if not description or not attributes:
            if attributes:
                self.pango.pango_attr_list_unref(attributes)
            if description:
                self.pango.pango_font_description_free(description)
            self.gobject.g_object_unref(layout)
            self.cairo.cairo_destroy(context)
            self.cairo.cairo_surface_destroy(surface)
            return None

        try:
            self.pango.pango_font_description_set_family(
                description, state.fontname.encode("utf-8")
            )
            self.pango.pango_font_description_set_weight(
                description, 700 if state.bold else 400
            )
            self.pango.pango_font_description_set_style(
                description, 2 if state.italic else 0
            )
            self.pango.pango_font_description_set_absolute_size(
                description,
                state.fontsize * self.PANGO_SCALE * self.PRECISION,
            )
            self.pango.pango_layout_set_font_description(layout, description)
            self.pango.pango_attr_list_insert(
                attributes,
                self.pango.pango_attr_underline_new(1 if state.underline else 0),
            )
            self.pango.pango_attr_list_insert(
                attributes,
                self.pango.pango_attr_strikethrough_new(
                    1 if state.strikeout else 0
                ),
            )
            self.pango.pango_attr_list_insert(
                attributes,
                self.pango.pango_attr_letter_spacing_new(
                    int(state.spacing * self.PANGO_SCALE * self.PRECISION)
                ),
            )
            self.pango.pango_layout_set_attributes(layout, attributes)

            metrics = self._font_metrics(layout)
            if metrics is None or metrics[2] <= 0:
                return None
            raw_ascent, raw_descent, natural_height = metrics
            font_hack_scale = state.fontsize / natural_height
            downscale = 1.0 / self.PRECISION
            scale_x = state.scale_x / 100.0
            scale_y = state.scale_y / 100.0
            ascent = (
                raw_ascent
                / self.PANGO_SCALE
                * downscale
                * scale_y
                * font_hack_scale
            )
            descent = (
                raw_descent
                / self.PANGO_SCALE
                * downscale
                * scale_y
                * font_hack_scale
            )

            encoded_text = text.encode("utf-8")
            self.pango.pango_layout_set_text(layout, encoded_text, -1)
            logical = _PangoRectangle()
            self.pango.pango_layout_get_pixel_extents(
                layout, None, ctypes.byref(logical)
            )
            advance = (
                logical.width * downscale * scale_x * font_hack_scale
            )

            self.cairo.cairo_save(context)
            self.cairo.cairo_scale(
                context,
                downscale * scale_x * font_hack_scale,
                downscale * scale_y * font_hack_scale,
            )
            self.pangocairo.pango_cairo_layout_path(context, layout)
            self.cairo.cairo_restore(context)
            path_pointer = self.cairo.cairo_copy_path(context)
            try:
                points = (
                    self._path_points(path_pointer.contents)
                    if path_pointer and path_pointer.contents.status == 0
                    else []
                )
            finally:
                if path_pointer:
                    self.cairo.cairo_path_destroy(path_pointer)
                self.cairo.cairo_new_path(context)

            if not points:
                return (
                    advance,
                    math.inf,
                    -math.inf,
                    math.inf,
                    -math.inf,
                    ascent,
                    descent,
                )

            effective_x = (
                state.shear_x * state.scale_x / state.scale_y
                if state.scale_y != 0
                else 0.0
            )
            effective_y = (
                state.shear_y * state.scale_y / state.scale_x
                if state.scale_x != 0
                else 0.0
            )
            transformed = [
                (x + effective_x * y, y + effective_y * x)
                for x, y in points
            ]
            min_x = min(point[0] for point in transformed)
            max_x = max(point[0] for point in transformed)
            min_y = min(point[1] for point in transformed) - ascent
            max_y = max(point[1] for point in transformed) - ascent
            return advance, min_x, max_x, min_y, max_y, ascent, descent
        finally:
            self.pango.pango_attr_list_unref(attributes)
            self.pango.pango_font_description_free(description)
            self.gobject.g_object_unref(layout)
            self.cairo.cairo_destroy(context)
            self.cairo.cairo_surface_destroy(surface)


class TextMeasurer:
    def __init__(
        self,
        font_paths: Iterable[Path] = (),
    ) -> None:
        self.gdi: Optional[WindowsGDITextMeasurer] = None
        self.pango: Optional[PangoCairoTextMeasurer] = None
        try:
            if os.name == "nt":
                self.gdi = WindowsGDITextMeasurer(font_paths)
                self.native = self.gdi
            else:
                self.pango = PangoCairoTextMeasurer(font_paths)
                self.native = self.pango
        except OSError as error:
            backend = "Windows GDI" if os.name == "nt" else "Pango/Cairo"
            raise UserError(
                f"The required {backend} measurement backend is unavailable: "
                f"{error}"
            ) from error

    def measure_run(
        self, text: str, state: TextState
    ) -> Optional[tuple[float, float, float, float, float, float, float]]:
        return self.native.measure_run(text, state)

    def measure_text(self, text: str, style: Style) -> Optional[InkBounds]:
        state = state_from_style(style)
        cursor = 0.0
        min_x = math.inf
        max_x = -math.inf
        min_y = math.inf
        max_y = -math.inf
        ascent = 0.0
        descent = 0.0
        has_ink = False
        position = 0

        for match in OVERRIDE_BLOCK_RE.finditer(text):
            plain = text[position:match.start()]
            cursor, min_x, max_x, min_y, max_y, ascent, descent, has_ink = (
                self._consume_text(
                    plain, state, cursor, min_x, max_x, min_y, max_y,
                    ascent, descent, has_ink
                )
            )
            apply_override_block(state, style, match.group(1))
            position = match.end()
        cursor, min_x, max_x, min_y, max_y, ascent, descent, has_ink = (
            self._consume_text(
                text[position:], state, cursor, min_x, max_x, min_y, max_y,
                ascent, descent, has_ink
            )
        )

        if not has_ink:
            return None
        return InkBounds(
            cursor, min_x, max_x, min_y, max_y, ascent, descent
        )

    def _consume_text(
        self,
        text: str,
        state: TextState,
        cursor: float,
        min_x: float,
        max_x: float,
        min_y: float,
        max_y: float,
        ascent: float,
        descent: float,
        has_ink: bool,
    ) -> tuple[float, float, float, float, float, float, float, bool]:
        visible = text.replace(r"\N", "").replace(r"\n", "").replace(r"\h", " ")
        if state.drawing or not visible:
            return cursor, min_x, max_x, min_y, max_y, ascent, descent, has_ink
        measured = self.measure_run(visible, state)
        if measured is None:
            return cursor, min_x, max_x, min_y, max_y, ascent, descent, has_ink
        (
            advance, run_min_x, run_max_x, run_min_y, run_max_y,
            run_ascent, run_descent,
        ) = measured
        run_has_ink = run_min_x < run_max_x and run_min_y < run_max_y
        if run_has_ink:
            min_x = min(min_x, cursor + run_min_x)
            max_x = max(max_x, cursor + run_max_x)
            min_y = min(min_y, run_min_y)
            max_y = max(max_y, run_max_y)
        ascent = max(ascent, run_ascent)
        descent = max(descent, run_descent)
        return (
            cursor + advance, min_x, max_x, min_y, max_y,
            ascent, descent, has_ink or run_has_ink,
        )


def state_from_style(style: Style) -> TextState:
    return TextState(
        fontname=style.fontname,
        bold=style.bold,
        italic=style.italic,
        underline=style.underline,
        strikeout=style.strikeout,
        fontsize=style.fontsize,
        scale_x=style.scale_x,
        scale_y=style.scale_y,
        spacing=style.spacing,
    )


def tag_enabled(value: str) -> bool:
    stripped = value.strip()
    if stripped == "1":
        return True
    try:
        return float(stripped) > 0
    except ValueError:
        return False


def reset_state(state: TextState, style: Style) -> None:
    fresh = state_from_style(style)
    state.__dict__.update(fresh.__dict__)


def apply_override_block(state: TextState, style: Style, block: str) -> None:
    for match in TAG_RE.finditer(block):
        name = match.group(1).casefold()
        value = match.group(2).strip()
        if name == "r":
            reset_state(state, style)
        elif name == "fn" and value:
            state.fontname = value
        elif name == "b":
            state.bold = tag_enabled(value)
        elif name == "i":
            state.italic = tag_enabled(value)
        elif name == "u":
            state.underline = tag_enabled(value)
        elif name == "s":
            state.strikeout = tag_enabled(value)
        elif name == "fs":
            state.fontsize = parse_float(value, state.fontsize)
        elif name == "fscx":
            state.scale_x = parse_float(value, state.scale_x)
        elif name == "fscy":
            state.scale_y = parse_float(value, state.scale_y)
        elif name == "fsp":
            state.spacing = parse_float(value, state.spacing)
        elif name == "fax":
            state.shear_x = parse_float(value)
        elif name == "fay":
            state.shear_y = parse_float(value)
        elif name == "p":
            state.drawing = parse_float(value) > 0


def alignment_value(text: str, style: Style) -> int:
    match = AN_RE.search(text)
    return int(match.group(1)) if match else style.alignment


def horizontal_alignment(text: str, style: Style) -> int:
    alignment = alignment_value(text, style)
    horizontal = alignment % 3
    return 3 if horizontal == 0 else horizontal


def origin_offset(horizontal: int, advance_width: float) -> float:
    if horizontal == 2:
        return -advance_width / 2.0
    if horizontal == 3:
        return -advance_width
    return 0.0


def effective_margins(event: Event, style: Style) -> tuple[int, int]:
    event_left = event.get_int("marginl")
    event_right = event.get_int("marginr")
    return (
        event_left if event_left != 0 else style.margin_l,
        event_right if event_right != 0 else style.margin_r,
    )


def margin_anchor(horizontal: int, width: int, left: int, right: int) -> float:
    if horizontal == 1:
        return float(left)
    if horizontal == 3:
        return float(width - right)
    return left + (width - left - right) / 2.0


def read_pos(text: str) -> Optional[tuple[float, str]]:
    match = POS_RE.search(text)
    if not match:
        return None
    return float(match.group(1)), match.group(2)


def format_ass_number(value: float) -> str:
    if abs(value) < 0.0000005:
        value = 0.0
    return f"{value:.6f}".rstrip("0").rstrip(".")


def shift_pos_x(text: str, delta_x: float) -> tuple[str, bool]:
    current = read_pos(text)
    if current is None:
        return text, False
    new_x = format_ass_number(current[0] + delta_x)

    def replacement(match: re.Match[str]) -> str:
        return f"{match.group(1)}{new_x}{match.group(3)}{match.group(4)}{match.group(5)}"

    shifted, count = POS_COMPONENT_RE.subn(replacement, text, count=1)
    return shifted, count > 0


def prepend_tag(text: str, tag: str) -> str:
    if text.startswith("{"):
        return "{" + tag + text[1:]
    return "{" + tag + "}" + text


def fallback_y(style: Style, event: Event, resolution_y: int) -> float:
    alignment = alignment_value(event.text, style)
    margin_v = event.get_int("marginv") or style.margin_v
    if alignment <= 3:
        return float(resolution_y - margin_v)
    if alignment <= 6:
        return resolution_y / 2.0
    return float(margin_v)


def line_anchor_x(
    event: Event,
    style: Style,
    document: ASSDocument,
    use_explicit_pos: bool,
) -> float:
    horizontal = horizontal_alignment(event.text, style)
    left, right = effective_margins(event, style)
    anchor = margin_anchor(horizontal, document.play_res_x, left, right)
    if use_explicit_pos:
        current = read_pos(event.text)
        if current is not None:
            anchor = current[0]
    return anchor


def make_render_record(
    event: Event,
    style: Style,
    document: ASSDocument,
    bounds: InkBounds,
) -> RenderRecord:
    horizontal = horizontal_alignment(event.text, style)
    anchor_x = line_anchor_x(event, style, document, use_explicit_pos=True)
    origin_x = origin_offset(horizontal, bounds.advance_width)
    left = anchor_x + origin_x + bounds.min_x
    right = anchor_x + origin_x + bounds.max_x

    current_pos = read_pos(event.text)
    anchor_y = (
        float(current_pos[1])
        if current_pos is not None
        else fallback_y(style, event, document.play_res_y)
    )
    alignment = alignment_value(event.text, style)
    if alignment <= 3:
        baseline_y = anchor_y - bounds.descent
    elif alignment <= 6:
        baseline_y = anchor_y + (bounds.ascent - bounds.descent) / 2.0
    else:
        baseline_y = anchor_y + bounds.ascent
    top = baseline_y + bounds.min_y
    bottom = baseline_y + bounds.max_y

    return RenderRecord(
        event=event,
        style=style,
        bounds=bounds,
        left=min(left, right),
        right=max(left, right),
        top=min(top, bottom),
        bottom=max(top, bottom),
    )


def point_to_rectangle_distance(x: float, y: float, rectangle: RenderRecord) -> float:
    delta_x = max(rectangle.left - x, 0.0, x - rectangle.right)
    delta_y = max(rectangle.top - y, 0.0, y - rectangle.bottom)
    return math.hypot(delta_x, delta_y)


def rectangle_center_distance(x: float, y: float, rectangle: RenderRecord) -> float:
    return math.hypot(x - rectangle.center_x, y - rectangle.center_y)


def round_to_integer(value: float) -> int:
    return math.floor(value + 0.5) if value >= 0 else math.ceil(value - 0.5)


def explicit_margin_value(value: float, style_margin: int) -> int:
    rounded = round_to_integer(value)
    return 1 if rounded == 0 and style_margin != 0 else rounded


def shift_ruby_event(event: Event, style: Style, delta_x: float) -> bool:
    if abs(delta_x) < 0.0000005 or MOVE_RE.search(event.text):
        return False

    shifted_text, used_pos = shift_pos_x(event.text, delta_x)
    if used_pos:
        event.text = shifted_text
        return True

    horizontal = horizontal_alignment(event.text, style)
    left, right = effective_margins(event, style)
    if horizontal == 1:
        target = explicit_margin_value(left + delta_x, style.margin_l)
        if target == left:
            return False
        event.set_margin("marginl", target)
    elif horizontal == 3:
        target = explicit_margin_value(right - delta_x, style.margin_r)
        if target == right:
            return False
        event.set_margin("marginr", target)
    elif delta_x > 0:
        target = explicit_margin_value(left + delta_x * 2.0, style.margin_l)
        if target == left:
            return False
        event.set_margin("marginl", target)
    else:
        target = explicit_margin_value(right - delta_x * 2.0, style.margin_r)
        if target == right:
            return False
        event.set_margin("marginr", target)
    return True


def set_pos(text: str, x: float, y: float) -> str:
    current = read_pos(text)
    rendered_y = current[1] if current else f"{y:.1f}"
    tag = rf"\pos({x:.1f},{rendered_y})"
    if POS_RE.search(text):
        return POS_RE.sub(lambda _: tag, text, count=1)
    return prepend_tag(text, tag)


def set_debug_clip(text: str, x1: int, x2: int, resolution_y: int) -> str:
    tag = rf"\clip({x1},0,{x2},{resolution_y})"
    if CLIP_RE.search(text):
        return CLIP_RE.sub(lambda _: tag, text, count=1)
    return prepend_tag(text, tag)


def process_event(
    event: Event,
    style: Style,
    document: ASSDocument,
    options: Options,
    measurer: TextMeasurer,
    bounds: Optional[InkBounds] = None,
) -> tuple[bool, str, float]:
    if MOVE_RE.search(event.text):
        return False, "position", 0.0
    if options.mode == "margin" and POS_RE.search(event.text):
        return False, "position", 0.0

    bounds = bounds or measurer.measure_text(event.text, style)
    if bounds is None:
        return False, "measurement", 0.0

    horizontal = horizontal_alignment(event.text, style)
    origin_x = origin_offset(horizontal, bounds.advance_width)
    anchor_x = line_anchor_x(
        event, style, document, use_explicit_pos=options.mode == "pos"
    )
    initial_anchor_x = anchor_x

    visual_center = anchor_x + origin_x + bounds.center_x
    event_left = event.get_int("marginl")
    event_right = event.get_int("marginr")
    large_left = (
        options.preserve_large_margins
        and event_left > options.large_margin_threshold
    )
    large_right = (
        options.preserve_large_margins
        and event_right > options.large_margin_threshold
    )
    use_large = options.mode == "margin" and (large_left or large_right)
    if use_large:
        shift = bounds.advance_width / 2.0 - bounds.center_x
    else:
        shift = document.play_res_x / 2.0 - visual_center

    modified = False
    shift_percent = abs(shift) / document.play_res_x * 100.0
    if shift_percent > options.threshold:
        if options.mode == "pos":
            target_x = document.play_res_x / 2.0 - origin_x - bounds.center_x
            event.text = set_pos(
                event.text,
                target_x,
                fallback_y(style, event, document.play_res_y),
            )
        elif use_large:
            if large_left:
                delta = shift if horizontal == 1 else shift * 2.0 if horizontal == 2 else 0.0
                event.set_margin("marginl", math.floor(event_left + delta + 0.5))
            elif large_right:
                delta = -shift if horizontal == 3 else -shift * 2.0 if horizontal == 2 else 0.0
                event.set_margin("marginr", math.floor(event_right + delta + 0.5))
        else:
            base_anchor = margin_anchor(
                horizontal, document.play_res_x, style.margin_l, style.margin_r
            )
            base_center = base_anchor + origin_x + bounds.center_x
            standard_shift = document.play_res_x / 2.0 - base_center
            event.set_margin("marginl", 0)
            event.set_margin("marginr", 0)
            if horizontal == 1:
                event.set_margin("marginl", math.floor(style.margin_l + standard_shift + 0.5))
            elif horizontal == 3:
                event.set_margin("marginr", math.floor(style.margin_r - standard_shift + 0.5))
            elif standard_shift > 0:
                event.set_margin("marginl", math.floor(style.margin_l + standard_shift * 2.0 + 0.5))
            else:
                event.set_margin("marginr", math.floor(style.margin_r + abs(standard_shift) * 2.0 + 0.5))
        modified = True

    if options.debug_clip:
        if options.mode == "pos":
            final_pos = read_pos(event.text)
            final_anchor = final_pos[0] if final_pos else anchor_x
        else:
            final_left, final_right = effective_margins(event, style)
            final_anchor = margin_anchor(
                horizontal, document.play_res_x, final_left, final_right
            )
        final_origin = final_anchor + origin_x
        event.text = set_debug_clip(
            event.text,
            math.floor(final_origin + bounds.min_x),
            math.ceil(final_origin + bounds.max_x),
            document.play_res_y,
        )
        modified = True

    final_anchor_x = line_anchor_x(
        event, style, document, use_explicit_pos=options.mode == "pos"
    )
    return modified, "ok", final_anchor_x - initial_anchor_x


def process_document(
    document: ASSDocument,
    options: Options,
    measurer: Optional[TextMeasurer] = None,
) -> ProcessingStats:
    if measurer is None:
        font_paths = collect_font_paths(options.font_dirs)
        measurer = TextMeasurer(font_paths=font_paths)
    stats = ProcessingStats(total_events=len(document.events))
    body_records: list[RenderRecord] = []
    bodies_by_timing: dict[tuple[str, str], list[RenderRecord]] = {}

    for event in document.events:
        # A Ruby Style is never also processed as body text, even if the body
        # include rules are broad enough to match it.
        if options.ruby_rules.accepts(event.style_name):
            continue
        if not options.rules.accepts(event.style_name):
            continue
        stats.style_matched += 1
        if event.is_comment and not options.process_comments:
            stats.comments_skipped += 1
            continue
        style = document.styles.get(event.style_name)
        if style is None:
            stats.missing_style += 1
            continue
        if MOVE_RE.search(event.text) or (
            options.mode == "margin" and POS_RE.search(event.text)
        ):
            stats.position_skipped += 1
            continue
        bounds = measurer.measure_text(event.text, style)
        if bounds is None:
            stats.measurement_failed += 1
            continue
        record = make_render_record(event, style, document, bounds)
        body_records.append(record)
        bodies_by_timing.setdefault(event.timing_key, []).append(record)

    ruby_matches: list[tuple[RenderRecord, RenderRecord]] = []
    if options.ruby_rules.enabled:
        maximum_distance = (
            document.play_res_y * options.ruby_distance_threshold_percent / 100.0
        )
        for event in document.events:
            if not options.ruby_rules.accepts(event.style_name):
                continue
            if event.is_comment and not options.process_comments:
                stats.comments_skipped += 1
                continue
            style = document.styles.get(event.style_name)
            if style is None:
                stats.missing_style += 1
                continue
            if MOVE_RE.search(event.text):
                stats.position_skipped += 1
                continue
            candidates = bodies_by_timing.get(event.timing_key)
            if not candidates:
                continue
            bounds = measurer.measure_text(event.text, style)
            if bounds is None:
                stats.measurement_failed += 1
                continue
            ruby_record = make_render_record(event, style, document, bounds)

            ranked = sorted(
                (
                    point_to_rectangle_distance(
                        ruby_record.center_x, ruby_record.center_y, body
                    ),
                    rectangle_center_distance(
                        ruby_record.center_x, ruby_record.center_y, body
                    ),
                    body.event.line_index,
                    body,
                )
                for body in candidates
            )
            best = ranked[0]
            if best[0] > maximum_distance:
                continue

            # The body rectangle distance is the primary criterion and the
            # center distance breaks common overlaps. If both are identical,
            # there is no reliable way to identify the corresponding body.
            if len(ranked) > 1:
                second = ranked[1]
                if math.isclose(best[0], second[0], abs_tol=1e-6) and math.isclose(
                    best[1], second[1], abs_tol=1e-6
                ):
                    continue
            ruby_matches.append((ruby_record, best[3]))

    body_shifts: dict[int, float] = {}
    for record in body_records:
        modified, reason, delta_x = process_event(
            record.event,
            record.style,
            document,
            options,
            measurer,
            record.bounds,
        )
        body_shifts[record.event.line_index] = delta_x
        if modified:
            stats.modified += 1
        elif reason == "measurement":
            stats.measurement_failed += 1
        elif reason == "position":
            stats.position_skipped += 1

    for ruby, body in ruby_matches:
        delta_x = body_shifts.get(body.event.line_index, 0.0)
        if shift_ruby_event(ruby.event, ruby.style, delta_x):
            stats.modified += 1
    return stats


def write_document(document: ASSDocument, output: Path) -> None:
    text = document.render()
    encoding = "utf-8-sig" if document.had_bom else "utf-8"
    output.write_text(text, encoding=encoding, newline="")


def replace_document(document: ASSDocument, input_path: Path) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{input_path.name}.",
        suffix=".tmp",
        dir=input_path.parent,
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        write_document(document, temporary_path)
        try:
            os.chmod(temporary_path, input_path.stat().st_mode)
        except OSError:
            pass
        os.replace(temporary_path, input_path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def validate_options(options: Options) -> None:
    options.rules.validate()
    options.ruby_rules.validate()
    if options.mode not in {"margin", "pos"}:
        raise UserError("Mode must be 'margin' or 'pos'.")
    if not math.isfinite(options.threshold) or not 0 <= options.threshold <= 100:
        raise UserError("Threshold must be between 0 and 100.")
    if not 0 <= options.large_margin_threshold <= 10000:
        raise UserError("Large-margin threshold must be between 0 and 10000.")
    if (
        not math.isfinite(options.ruby_distance_threshold_percent)
        or not 0 <= options.ruby_distance_threshold_percent
        <= MAX_RUBY_DISTANCE_THRESHOLD_PERCENT
    ):
        raise UserError("Ruby distance threshold must be between 0 and 100 percent.")


def load_settings(path: Optional[Path]) -> dict:
    if path is None:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise UserError(f"Could not load settings file {path}: {error}") from error
    if not isinstance(data, dict):
        raise UserError("The settings JSON root must be an object.")
    return data


def choose(cli_value, settings: dict, key: str, default):
    return cli_value if cli_value is not None else settings.get(key, default)


def string_list(value: object, key: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise UserError(f"{key} must be an array of strings in the settings file.")
    return list(value)


def options_from_args(
    args: argparse.Namespace,
    settings: Optional[dict] = None,
) -> Options:
    settings = load_settings(args.settings) if settings is None else settings
    rules_data = settings.get("style_rules", {})
    if not isinstance(rules_data, dict):
        raise UserError("style_rules in the settings file must be an object.")
    ruby_rules_data = settings.get("ruby_style_rules", {})
    if not isinstance(ruby_rules_data, dict):
        raise UserError("ruby_style_rules in the settings file must be an object.")
    rules = StyleRules(
        match=string_list(choose(args.match_style, rules_data, "match", []), "style_rules.match"),
        match_exact=string_list(
            choose(args.match_style_exact, rules_data, "match_exact", []),
            "style_rules.match_exact",
        ),
        exclude=string_list(
            choose(args.exclude_style, rules_data, "exclude", []),
            "style_rules.exclude",
        ),
        exclude_exact=string_list(
            choose(args.exclude_style_exact, rules_data, "exclude_exact", []),
            "style_rules.exclude_exact",
        ),
        ignore_case=bool(
            choose(args.ignore_style_case, rules_data, "ignore_case", False)
        ),
    )
    ruby_rules = RubyStyleRules(
        match=string_list(
            choose(args.match_ruby_style, ruby_rules_data, "match", []),
            "ruby_style_rules.match",
        ),
        match_exact=string_list(
            choose(
                args.match_ruby_style_exact,
                ruby_rules_data,
                "match_exact",
                [],
            ),
            "ruby_style_rules.match_exact",
        ),
        ignore_case=rules.ignore_case,
    )
    try:
        options = Options(
            mode=str(choose(args.mode, settings, "mode", "margin")),
            in_place=bool(choose(args.in_place, settings, "in_place", False)),
            threshold=float(choose(args.threshold, settings, "threshold", DEFAULT_THRESHOLD)),
            large_margin_threshold=int(
                choose(
                    args.large_margin_threshold,
                    settings,
                    "large_margin_threshold",
                    DEFAULT_LARGE_MARGIN_THRESHOLD,
                )
            ),
            preserve_large_margins=bool(
                choose(
                    args.preserve_large_margins,
                    settings,
                    "preserve_large_margins",
                    True,
                )
            ),
            process_comments=bool(
                choose(args.process_comments, settings, "process_comments", False)
            ),
            debug_clip=bool(choose(args.debug_clip, settings, "debug_clip", False)),
            rules=rules,
            ruby_rules=ruby_rules,
            ruby_distance_threshold_percent=float(
                choose(
                    args.ruby_distance_threshold_percent,
                    settings,
                    "ruby_distance_threshold_percent",
                    DEFAULT_RUBY_DISTANCE_THRESHOLD_PERCENT,
                )
            ),
            font_dirs=string_list(
                choose(args.font_dir, settings, "font_dirs", []), "font_dirs"
            ),
        )
    except (TypeError, ValueError) as error:
        raise UserError(f"Invalid value in settings file: {error}") from error
    validate_options(options)
    return options


def optional_path(value: object, key: str) -> Optional[Path]:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise UserError(f"{key} must be a non-empty string or null in the settings file.")
    return Path(value)


def execution_from_args(
    args: argparse.Namespace,
    settings: Optional[dict] = None,
) -> ExecutionOptions:
    settings = load_settings(args.settings) if settings is None else settings
    if args.input:
        inputs = list(args.input)
    else:
        inputs = [Path(value) for value in string_list(settings.get("inputs", []), "inputs")]
    if any(not str(path).strip() for path in inputs):
        raise UserError("inputs cannot contain empty paths.")

    if args.output is not None:
        output = args.output
        output_dir = None
    elif args.output_dir is not None:
        output = None
        output_dir = args.output_dir
    else:
        output = optional_path(settings.get("output"), "output")
        output_dir = optional_path(settings.get("output_dir"), "output_dir")

    execution = ExecutionOptions(
        inputs=inputs,
        output=output,
        output_dir=output_dir,
        recursive=bool(choose(args.recursive, settings, "recursive", False)),
        overwrite=bool(choose(args.overwrite, settings, "overwrite", False)),
        dry_run=bool(choose(args.dry_run, settings, "dry_run", False)),
        list_styles=bool(choose(args.list_styles, settings, "list_styles", False)),
    )
    if not execution.inputs:
        raise UserError(
            "At least one input file or directory is required on the command line "
            "or in the JSON inputs array."
        )
    return execution


def save_settings(
    path: Path,
    options: Options,
    execution: Optional[ExecutionOptions] = None,
) -> None:
    execution = execution or ExecutionOptions()
    data = {
        "inputs": [str(input_path) for input_path in execution.inputs],
        "output": str(execution.output) if execution.output is not None else None,
        "output_dir": (
            str(execution.output_dir) if execution.output_dir is not None else None
        ),
        "recursive": execution.recursive,
        "overwrite": execution.overwrite,
        "dry_run": execution.dry_run,
        "list_styles": execution.list_styles,
        "mode": options.mode,
        "in_place": options.in_place,
        "threshold": options.threshold,
        "large_margin_threshold": options.large_margin_threshold,
        "preserve_large_margins": options.preserve_large_margins,
        "style_rules": asdict(options.rules),
        "ruby_style_rules": {
            "match": options.ruby_rules.match,
            "match_exact": options.ruby_rules.match_exact,
        },
        "ruby_distance_threshold_percent": (
            options.ruby_distance_threshold_percent
        ),
        "process_comments": options.process_comments,
        "debug_clip": options.debug_clip,
        "font_dirs": options.font_dirs,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Optically center Style-selected ASS events and optionally move matching "
            "Ruby events with their nearest time-matched body event."
        )
    )
    parser.add_argument(
        "input",
        type=Path,
        nargs="*",
        help="Input ASS file or directory; may instead be supplied by JSON",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output ASS file (single input only)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Write all generated ASS files to this directory",
    )
    parser.add_argument(
        "--in-place",
        "--replace-original",
        dest="in_place",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Atomically replace each input ASS instead of creating output files",
    )
    parser.add_argument(
        "--recursive",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Search input directories recursively",
    )
    parser.add_argument(
        "--overwrite",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Allow replacing output files",
    )
    parser.add_argument(
        "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Analyze without writing output",
    )
    parser.add_argument(
        "--list-styles",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="List styles and exit",
    )
    parser.add_argument("--settings", type=Path, help="Load options from a JSON settings file")
    parser.add_argument("--save-settings", type=Path, help="Save the effective options as JSON")

    parser.add_argument("--mode", choices=("margin", "pos"), default=None)
    parser.add_argument("--threshold", type=float, default=None)
    parser.add_argument("--large-margin-threshold", type=int, default=None)
    parser.add_argument(
        "--preserve-large-margins",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    parser.add_argument(
        "--process-comments",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    parser.add_argument(
        "--debug-clip",
        action=argparse.BooleanOptionalAction,
        default=None,
    )

    parser.add_argument(
        "--match-style",
        action="append",
        default=None,
        metavar="TEXT",
        help="Include styles containing TEXT; repeat for multiple values",
    )
    parser.add_argument(
        "--match-style-exact",
        action="append",
        default=None,
        metavar="STYLE",
        help="Include a complete Style name; repeat for multiple values",
    )
    parser.add_argument(
        "--exclude-style",
        action="append",
        default=None,
        metavar="TEXT",
        help="Exclude styles containing TEXT; repeat for multiple values",
    )
    parser.add_argument(
        "--exclude-style-exact",
        action="append",
        default=None,
        metavar="STYLE",
        help="Exclude a complete Style name; repeat for multiple values",
    )
    parser.add_argument(
        "--ignore-style-case",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Make all four Style rule groups case-insensitive",
    )
    parser.add_argument(
        "--match-ruby-style",
        action="append",
        default=None,
        metavar="TEXT",
        help="Match Ruby styles containing TEXT; repeat for multiple values",
    )
    parser.add_argument(
        "--match-ruby-style-exact",
        action="append",
        default=None,
        metavar="STYLE",
        help="Match a complete Ruby Style name; repeat for multiple values",
    )
    parser.add_argument(
        "--ruby-distance-threshold-percent",
        "--ruby-distance-threshold",
        dest="ruby_distance_threshold_percent",
        type=float,
        default=None,
        metavar="PERCENT",
        help="Maximum Ruby-to-body distance as a percentage of PlayResY (default: 3)",
    )
    parser.add_argument(
        "--font-dir",
        action="append",
        default=None,
        metavar="DIR",
        help="Additional font directory; repeat for multiple directories",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser


def default_output_path(input_path: Path) -> Path:
    return input_path.with_name(input_path.stem + OUTPUT_SUFFIX)


def path_key(path: Path) -> str:
    return os.path.normcase(str(path))


def discover_input_paths(sources: Iterable[Path], recursive: bool) -> list[Path]:
    inputs: list[Path] = []
    seen: set[str] = set()

    for source in sources:
        resolved = source.expanduser().resolve()
        if resolved.is_file():
            candidates = [resolved]
        elif resolved.is_dir():
            iterator = resolved.rglob("*") if recursive else resolved.iterdir()
            candidates = sorted(
                (
                    path.resolve()
                    for path in iterator
                    if path.is_file()
                    and path.suffix.casefold() == ".ass"
                    and not path.name.casefold().endswith(OUTPUT_SUFFIX)
                ),
                key=lambda path: str(path).casefold(),
            )
        else:
            raise UserError(f"Input path does not exist: {resolved}")

        for candidate in candidates:
            key = path_key(candidate)
            if key not in seen:
                seen.add(key)
                inputs.append(candidate)

    if not inputs:
        raise UserError("No ASS files were found in the supplied input paths.")
    return inputs


def build_output_paths(
    input_paths: list[Path],
    output: Optional[Path],
    output_dir: Optional[Path],
    in_place: bool = False,
) -> list[Path]:
    if in_place and (output is not None or output_dir is not None):
        raise UserError(
            "--in-place cannot be used with --output or --output-dir."
        )
    if output is not None and output_dir is not None:
        raise UserError("--output and --output-dir cannot be used together.")
    if output is not None and len(input_paths) != 1:
        raise UserError("--output can only be used with one input file.")

    resolved_output_dir = output_dir.expanduser().resolve() if output_dir else None
    outputs: list[Path] = []
    owners: dict[str, Path] = {}
    for input_path in input_paths:
        if in_place:
            output_path = input_path
        elif output is not None:
            output_path = output.expanduser().resolve()
        elif resolved_output_dir is not None:
            output_path = (
                resolved_output_dir / default_output_path(input_path).name
            ).resolve()
        else:
            output_path = default_output_path(input_path).resolve()

        key = path_key(output_path)
        if key in owners:
            raise UserError(
                "Multiple inputs would write to the same output file: "
                f"{owners[key]} and {input_path} -> {output_path}"
            )
        owners[key] = input_path
        outputs.append(output_path)
    return outputs


def print_styles(document: ASSDocument) -> None:
    counts: dict[str, int] = {name: 0 for name in document.styles}
    for event in document.events:
        counts[event.style_name] = counts.get(event.style_name, 0) + 1
    for name in sorted(counts, key=str.casefold):
        print(f"{name}\t{counts[name]}")


def print_stats(
    stats: ProcessingStats,
    output: Optional[Path],
    replaced_original: bool = False,
) -> None:
    print(f"Events: {stats.total_events}")
    print(f"Style matched: {stats.style_matched}")
    print(f"Modified: {stats.modified}")
    print(f"Comment skipped: {stats.comments_skipped}")
    print(f"Missing style: {stats.missing_style}")
    print(f"Measurement failed: {stats.measurement_failed}")
    print(f"Position skipped: {stats.position_skipped}")
    if output is not None:
        label = "Replaced" if replaced_original else "Output"
        print(f"{label}: {output}")


def print_batch_summary(total: int, succeeded: int, failed: int) -> None:
    print("Batch summary:")
    print(f"Files: {total}")
    print(f"Succeeded: {succeeded}")
    print(f"Failed: {failed}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args.settings)
        execution = execution_from_args(args, settings)
        requested_batch = len(execution.inputs) > 1 or any(
            path.expanduser().is_dir() for path in execution.inputs
        )
        input_paths = discover_input_paths(
            execution.inputs, execution.recursive
        )
        batch_mode = requested_batch or len(input_paths) > 1

        if execution.list_styles:
            succeeded = 0
            failures: list[tuple[Path, str]] = []
            for index, input_path in enumerate(input_paths, start=1):
                if batch_mode:
                    if index > 1:
                        print()
                    print(f"[{index}/{len(input_paths)}] {input_path}")
                try:
                    print_styles(parse_ass(input_path))
                    succeeded += 1
                except (UserError, OSError) as error:
                    failures.append((input_path, str(error)))
                    print(f"Error: {error}", file=sys.stderr)
            if batch_mode:
                print()
                print_batch_summary(len(input_paths), succeeded, len(failures))
            return 1 if failures else 0

        options = options_from_args(args, settings)
        if args.save_settings:
            save_settings(args.save_settings.resolve(), options, execution)

        output_paths = build_output_paths(
            input_paths,
            execution.output,
            execution.output_dir,
            options.in_place,
        )
        font_paths = collect_font_paths(options.font_dirs)
        measurer = TextMeasurer(font_paths=font_paths)
        succeeded = 0
        failures: list[tuple[Path, str]] = []

        for index, (input_path, planned_output) in enumerate(
            zip(input_paths, output_paths), start=1
        ):
            if batch_mode:
                if index > 1:
                    print()
                print(f"[{index}/{len(input_paths)}] {input_path}")
            try:
                if not execution.dry_run:
                    if (
                        planned_output == input_path
                        and not options.in_place
                        and not execution.overwrite
                    ):
                        raise UserError(
                            "Refusing to overwrite the input file without --overwrite."
                        )
                    if (
                        not options.in_place
                        and planned_output.exists()
                        and not execution.overwrite
                    ):
                        raise UserError(
                            f"Output already exists: {planned_output}. Use --overwrite."
                        )

                document = parse_ass(input_path)
                stats = process_document(document, options, measurer)
                if stats.style_matched == 0:
                    raise UserError("No event Style matched the include rules.")

                output_path: Optional[Path] = None
                if not execution.dry_run:
                    output_path = planned_output
                    if options.in_place:
                        replace_document(document, input_path)
                    else:
                        output_path.parent.mkdir(parents=True, exist_ok=True)
                        write_document(document, output_path)

                print_stats(stats, output_path, options.in_place)
                succeeded += 1
            except (UserError, OSError) as error:
                if not batch_mode:
                    raise UserError(str(error)) from error
                failures.append((input_path, str(error)))
                print(f"Error: {error}", file=sys.stderr)

        if batch_mode:
            print()
            print_batch_summary(len(input_paths), succeeded, len(failures))
        return 1 if failures else 0
    except (UserError, OSError) as error:
        parser.error(str(error))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
