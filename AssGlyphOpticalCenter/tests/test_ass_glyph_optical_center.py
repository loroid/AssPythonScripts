from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "AssGlyphOpticalCenter.py"
SPEC = importlib.util.spec_from_file_location("ass_glyph_optical_center", SCRIPT)
assert SPEC and SPEC.loader
goc = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = goc
SPEC.loader.exec_module(goc)


SAMPLE_ASS = """[Script Info]\nPlayResX: 1920\nPlayResY: 1080\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: Body Chinese,Arial,60,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,20,20,30,1\nStyle: Body Notes,Arial,60,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,20,20,30,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\nDialogue: 0,0:00:00.00,0:00:05.00,Body Chinese,,0000,0000,0000,,Test)\nComment: 0,0:00:00.00,0:00:05.00,Body Chinese,,0000,0000,0000,,Comment)\nDialogue: 0,0:00:00.00,0:00:05.00,Body Notes,,0000,0000,0000,,Excluded)\n"""

RUBY_ASS = """[Script Info]\nPlayResX: 200\nPlayResY: 120\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: Body Chinese,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,2,10,10,10,1\nStyle: Body Japanese,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,2,10,10,0,1\nStyle: Text - Ruby,Arial,10,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,2,10,10,18,1\nStyle: Ruby Margin,Arial,10,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,2,10,10,18,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\nDialogue: 0,0:00:00.00,0:00:01.00,Body Chinese,,0000,0000,0000,,Body CN\nDialogue: 0,0:00:00.00,0:00:01.00,Body Japanese,,0000,0000,0000,,Body JP\nDialogue: 1,0:00:00.00,0:00:01.00,Text - Ruby,,0000,0000,0000,,{\\pos (120.0, 102.00)}Ruby Pos\nDialogue: 0,0:00:02.00,0:00:03.00,Body Chinese,,0000,0000,0000,,Body CN 2\nDialogue: 1,0:00:02.00,0:00:03.00,Ruby Margin,,0000,0000,0000,,Ruby Margin\n"""


class FakeMeasurer:
    def measure_text(self, text, style):
        if style.name == "Body Japanese":
            return goc.InkBounds(40, 8, 44, -8, 2, 8, 2)
        if "Ruby" in style.name:
            return goc.InkBounds(20, 0, 20, -8, 2, 8, 2)
        return goc.InkBounds(40, 4, 40, -8, 2, 8, 2)


class StyleRuleTests(unittest.TestCase):
    def test_contains_exact_and_exclusion_precedence(self):
        rules = goc.StyleRules(
            match=["Body"],
            match_exact=["Sign"],
            exclude=["Notes"],
            exclude_exact=["Body JP"],
        )
        rules.validate()
        self.assertTrue(rules.accepts("Body Chinese"))
        self.assertTrue(rules.accepts("Sign"))
        self.assertFalse(rules.accepts("Body Notes 1"))
        self.assertFalse(rules.accepts("Body JP"))
        self.assertFalse(rules.accepts("Signs"))

    def test_include_rule_is_required(self):
        with self.assertRaises(goc.UserError):
            goc.StyleRules(exclude=["Notes"]).validate()

    def test_case_insensitive_mode(self):
        rules = goc.StyleRules(match_exact=["BODY"], ignore_case=True)
        rules.validate()
        self.assertTrue(rules.accepts("body"))

    def test_ruby_rules_are_optional_and_support_contains_and_exact(self):
        disabled = goc.RubyStyleRules()
        self.assertFalse(disabled.enabled)
        self.assertFalse(disabled.accepts("Text - Ruby"))

        rules = goc.RubyStyleRules(
            match=["Ruby"], match_exact=["Furigana"], ignore_case=True
        )
        rules.validate()
        self.assertTrue(rules.accepts("TEXT - RUBY"))
        self.assertTrue(rules.accepts("furigana"))
        self.assertFalse(rules.accepts("Body"))


class ParserTests(unittest.TestCase):
    def parse_sample(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.ass"
            path.write_bytes(SAMPLE_ASS.encode("utf-8"))
            return goc.parse_ass(path)

    def test_parse_styles_events_and_comments(self):
        document = self.parse_sample()
        self.assertEqual(document.play_res_x, 1920)
        self.assertIn("Body Chinese", document.styles)
        self.assertEqual(len(document.events), 3)
        self.assertTrue(document.events[1].is_comment)

    def test_unmodified_lines_are_preserved_exactly(self):
        document = self.parse_sample()
        self.assertEqual(document.render(), SAMPLE_ASS)


class BatchModeTests(unittest.TestCase):
    def test_json_execution_settings_and_cli_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings_path = root / "settings.json"
            settings_path.write_text(
                json.dumps(
                    {
                        "inputs": ["json-one.ass", "json-two.ass"],
                        "output": None,
                        "output_dir": "json-output",
                        "recursive": True,
                        "overwrite": True,
                        "dry_run": True,
                        "list_styles": True,
                    }
                ),
                encoding="utf-8",
            )
            parser = goc.build_parser()
            settings = goc.load_settings(settings_path)
            from_json = goc.execution_from_args(
                parser.parse_args(["--settings", str(settings_path)]), settings
            )
            overridden = goc.execution_from_args(
                parser.parse_args(
                    [
                        "cli.ass",
                        "--settings",
                        str(settings_path),
                        "--output",
                        "cli-output.ass",
                        "--no-recursive",
                        "--no-overwrite",
                        "--no-dry-run",
                        "--no-list-styles",
                    ]
                ),
                settings,
            )

        self.assertEqual(
            [str(path) for path in from_json.inputs],
            ["json-one.ass", "json-two.ass"],
        )
        self.assertEqual(from_json.output_dir, Path("json-output"))
        self.assertTrue(from_json.recursive)
        self.assertTrue(from_json.overwrite)
        self.assertTrue(from_json.dry_run)
        self.assertTrue(from_json.list_styles)
        self.assertEqual(overridden.inputs, [Path("cli.ass")])
        self.assertEqual(overridden.output, Path("cli-output.ass"))
        self.assertIsNone(overridden.output_dir)
        self.assertFalse(overridden.recursive)
        self.assertFalse(overridden.overwrite)
        self.assertFalse(overridden.dry_run)
        self.assertFalse(overridden.list_styles)

    def test_main_can_run_entirely_from_json(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "input.ass"
            settings_path = root / "settings.json"
            input_path.write_text(SAMPLE_ASS, encoding="utf-8")
            settings_path.write_text(
                json.dumps(
                    {
                        "inputs": [str(input_path)],
                        "dry_run": True,
                        "style_rules": {"match": ["Body"]},
                    }
                ),
                encoding="utf-8",
            )
            stats = goc.ProcessingStats(total_events=3, style_matched=1)

            with mock.patch.object(goc, "TextMeasurer", return_value=object()), \
                    mock.patch.object(goc, "process_document", return_value=stats), \
                    redirect_stdout(io.StringIO()):
                result = goc.main(["--settings", str(settings_path)])

        self.assertEqual(result, 0)

    def test_json_in_place_can_be_overridden_from_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            settings_path = Path(directory) / "settings.json"
            settings_path.write_text(
                json.dumps(
                    {
                        "in_place": True,
                        "style_rules": {"match": ["Body"]},
                    }
                ),
                encoding="utf-8",
            )
            parser = goc.build_parser()
            from_json = goc.options_from_args(
                parser.parse_args(
                    ["input.ass", "--settings", str(settings_path)]
                )
            )
            overridden = goc.options_from_args(
                parser.parse_args(
                    [
                        "input.ass",
                        "--settings",
                        str(settings_path),
                        "--no-in-place",
                    ]
                )
            )

        self.assertTrue(from_json.in_place)
        self.assertFalse(overridden.in_place)

    def test_saved_settings_include_in_place(self):
        with tempfile.TemporaryDirectory() as directory:
            settings_path = Path(directory) / "settings.json"
            options = goc.Options(
                in_place=True,
                rules=goc.StyleRules(match=["Body"]),
            )
            execution = goc.ExecutionOptions(
                inputs=[Path("one.ass"), Path("two.ass")],
                output_dir=Path("output"),
                recursive=True,
                overwrite=True,
                dry_run=True,
                list_styles=False,
            )
            goc.save_settings(settings_path, options, execution)
            saved = json.loads(settings_path.read_text(encoding="utf-8"))
        self.assertIs(saved["in_place"], True)
        self.assertEqual(saved["inputs"], ["one.ass", "two.ass"])
        self.assertEqual(saved["output_dir"], "output")
        self.assertIs(saved["recursive"], True)
        self.assertIs(saved["overwrite"], True)
        self.assertIs(saved["dry_run"], True)
        self.assertIs(saved["list_styles"], False)

    def test_directory_discovery_filters_outputs_and_supports_recursion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nested = root / "nested"
            nested.mkdir()
            first = root / "one.ass"
            second = root / "two.ASS"
            generated = root / "one.centered.ass"
            nested_file = nested / "three.ass"
            for path in (first, second, generated, nested_file):
                path.write_text(SAMPLE_ASS, encoding="utf-8")

            shallow = goc.discover_input_paths([root], recursive=False)
            recursive = goc.discover_input_paths([root], recursive=True)
            deduplicated = goc.discover_input_paths(
                [first, root], recursive=False
            )

        self.assertEqual({path.name for path in shallow}, {"one.ass", "two.ASS"})
        self.assertEqual(
            {path.name for path in recursive},
            {"one.ass", "two.ASS", "three.ass"},
        )
        self.assertEqual(
            [path.name for path in deduplicated].count("one.ass"), 1
        )

    def test_output_directory_rejects_same_name_collisions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = (root / "a" / "episode.ass").resolve()
            second = (root / "b" / "episode.ass").resolve()
            with self.assertRaises(goc.UserError):
                goc.build_output_paths(
                    [first, second], None, root / "output"
                )
            with self.assertRaises(goc.UserError):
                goc.build_output_paths(
                    [first, second], root / "single.ass", None
                )

    def test_in_place_uses_inputs_and_rejects_output_options(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = [(root / "one.ass").resolve(), (root / "two.ass").resolve()]
            self.assertEqual(
                goc.build_output_paths(inputs, None, None, in_place=True),
                inputs,
            )
            with self.assertRaises(goc.UserError):
                goc.build_output_paths(
                    inputs, None, root / "output", in_place=True
                )

    def test_main_atomically_replaces_original_file(self):
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "input.ass"
            input_path.write_text(SAMPLE_ASS, encoding="utf-8")

            def modify_document(document, _options, _measurer):
                document.events[0].text = "Centered"
                return goc.ProcessingStats(total_events=3, style_matched=1, modified=1)

            with mock.patch.object(goc, "TextMeasurer", return_value=object()), \
                    mock.patch.object(goc, "process_document", side_effect=modify_document), \
                    redirect_stdout(io.StringIO()):
                result = goc.main(
                    [
                        str(input_path),
                        "--match-style",
                        "Body",
                        "--in-place",
                    ]
                )

            self.assertEqual(result, 0)
            self.assertIn("Centered", input_path.read_text(encoding="utf-8"))
            self.assertFalse(any(input_path.parent.glob(f".{input_path.name}.*.tmp")))

    def test_main_processes_multiple_files_into_output_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "one.ass"
            second = root / "two.ass"
            output_dir = root / "output"
            first.write_text(SAMPLE_ASS, encoding="utf-8")
            second.write_text(SAMPLE_ASS, encoding="utf-8")
            stats = goc.ProcessingStats(total_events=3, style_matched=1)

            with mock.patch.object(goc, "TextMeasurer", return_value=object()), \
                    mock.patch.object(goc, "process_document", return_value=stats), \
                    redirect_stdout(io.StringIO()):
                result = goc.main(
                    [
                        str(first),
                        str(second),
                        "--match-style",
                        "Body",
                        "--output-dir",
                        str(output_dir),
                    ]
                )

            self.assertEqual(result, 0)
            self.assertTrue(
                (output_dir / "one.centered.ass").is_file()
            )
            self.assertTrue(
                (output_dir / "two.centered.ass").is_file()
            )


class ProcessingTests(unittest.TestCase):
    def parse_sample(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.ass"
            path.write_bytes(SAMPLE_ASS.encode("utf-8"))
            return goc.parse_ass(path)

    def test_default_filtering_skips_comments(self):
        document = self.parse_sample()
        options = goc.Options(
            rules=goc.StyleRules(match=["Body"], exclude_exact=["Body Notes"]),
            process_comments=False,
        )

        original = goc.process_event
        calls = []

        def fake_process(event, style, document, options, measurer, bounds=None):
            calls.append(event.style_name)
            return False, "ok", 0.0

        goc.process_event = fake_process
        try:
            stats = goc.process_document(document, options, FakeMeasurer())
        finally:
            goc.process_event = original

        self.assertEqual(calls, ["Body Chinese"])
        self.assertEqual(stats.style_matched, 2)
        self.assertEqual(stats.comments_skipped, 1)

    def test_comment_option_processes_comments(self):
        document = self.parse_sample()
        options = goc.Options(
            rules=goc.StyleRules(match_exact=["Body Chinese"]),
            process_comments=True,
        )
        original = goc.process_event
        calls = []

        def fake_process(event, style, document, options, measurer, bounds=None):
            calls.append(event.is_comment)
            return False, "ok", 0.0

        goc.process_event = fake_process
        try:
            goc.process_document(document, options, FakeMeasurer())
        finally:
            goc.process_event = original
        self.assertEqual(calls, [False, True])

    def test_inline_font_and_shear_tags(self):
        style = goc.Style(
            "Body", "Arial", 50, False, False, False, False,
            100, 100, 0, 2, 20, 20, 30,
        )
        state = goc.state_from_style(style)
        goc.apply_override_block(state, style, r"\fnNoto Sans CJK JP\fax0.2\fay-0.1")
        self.assertEqual(state.fontname, "Noto Sans CJK JP")
        self.assertEqual(state.shear_x, 0.2)
        self.assertEqual(state.shear_y, -0.1)

    def test_ruby_follows_nearest_body_by_rendered_rectangle(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ruby.ass"
            path.write_bytes(RUBY_ASS.encode("utf-8"))
            document = goc.parse_ass(path)

        options = goc.Options(
            threshold=0,
            rules=goc.StyleRules(match=["Body"]),
            ruby_rules=goc.RubyStyleRules(
                match_exact=["Text - Ruby", "Ruby Margin"]
            ),
            ruby_distance_threshold_percent=3,
        )
        stats = goc.process_document(document, options, FakeMeasurer())

        ruby_pos = document.events[2]
        ruby_margin = document.events[4]
        self.assertIn(r"\pos (118, 102.00)", ruby_pos.text)
        self.assertEqual(ruby_margin.get_int("marginr"), 14)
        self.assertEqual(stats.modified, 5)

    def test_ruby_outside_distance_threshold_is_not_moved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ruby.ass"
            path.write_bytes(RUBY_ASS.encode("utf-8"))
            document = goc.parse_ass(path)

        options = goc.Options(
            threshold=0,
            rules=goc.StyleRules(match=["Body"]),
            ruby_rules=goc.RubyStyleRules(match_exact=["Text - Ruby"]),
            ruby_distance_threshold_percent=0.1,
        )
        goc.process_document(document, options, FakeMeasurer())
        self.assertIn(r"\pos (120.0, 102.00)", document.events[2].text)

    @unittest.skipUnless(sys.platform == "win32", "Windows GDI only")
    def test_windows_gdi_outline_backend_returns_ink_bounds(self):
        backend = goc.WindowsGDITextMeasurer()
        try:
            state = goc.TextState(
                fontname="Arial",
                bold=False,
                italic=False,
                underline=False,
                strikeout=False,
                fontsize=40,
                scale_x=100,
                scale_y=100,
                spacing=0.1,
            )
            measured = backend.measure_run("Glyph!", state)
        finally:
            backend.close()
        self.assertIsNotNone(measured)
        advance, min_x, max_x, min_y, max_y, _, _ = measured
        self.assertGreater(advance, 0)
        self.assertLess(min_x, max_x)
        self.assertLess(min_y, max_y)


class NativeBackendTests(unittest.TestCase):
    def test_yutils_round_matches_lua_for_positive_and_negative_values(self):
        self.assertEqual(goc.yutils_round(1.2345), 1.235)
        self.assertEqual(goc.yutils_round(-1.2345), -1.234)

    def test_cairo_path_control_points_are_collected(self):
        data = (goc._CairoPathData * 7)()
        data[0].header = goc._CairoPathHeader(0, 2)
        data[1].point = goc._CairoPathPoint(1.2345, -1.2345)
        data[2].header = goc._CairoPathHeader(2, 4)
        data[3].point = goc._CairoPathPoint(2, 3)
        data[4].point = goc._CairoPathPoint(4, 5)
        data[5].point = goc._CairoPathPoint(6, 7)
        data[6].header = goc._CairoPathHeader(3, 1)
        path = goc._CairoPath(0, data, len(data))
        self.assertEqual(
            goc.PangoCairoTextMeasurer._path_points(path),
            [(1.235, -1.234), (2.0, 3.0), (4.0, 5.0), (6.0, 7.0)],
        )

    def test_non_windows_selects_pango_backend(self):
        class FakePango:
            def __init__(self, font_paths):
                self.font_paths = list(font_paths)

        paths = [Path("font.ttf")]
        with mock.patch.object(goc.os, "name", "posix"), mock.patch.object(
            goc, "PangoCairoTextMeasurer", FakePango
        ):
            measurer = goc.TextMeasurer(font_paths=paths)
        self.assertIsInstance(measurer.native, FakePango)
        self.assertEqual(measurer.native.font_paths, paths)

    def test_missing_pango_is_an_error(self):
        with mock.patch.object(goc.os, "name", "posix"), mock.patch.object(
            goc.PangoCairoTextMeasurer,
            "__init__",
            side_effect=OSError("missing"),
        ):
            with self.assertRaises(goc.UserError):
                goc.TextMeasurer()

    def test_font_paths_are_collected_recursively(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nested = root / "nested"
            nested.mkdir()
            (root / "one.ttf").write_bytes(b"")
            (nested / "two.OTF").write_bytes(b"")
            (nested / "ignore.txt").write_bytes(b"")
            paths = goc.collect_font_paths([str(root)])
        self.assertEqual(
            {path.name for path in paths},
            {"one.ttf", "two.OTF"},
        )


if __name__ == "__main__":
    unittest.main()
