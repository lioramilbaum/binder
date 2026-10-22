"""Node.js-based unit tests for the pure calendar helper functions in app.js."""

import json
import os
import shutil
import subprocess
import unittest

from binder.config import STATIC_DIR
from binder.i18n import Translator


def _extract_function(script, name):
    start = script.index(f"function {name}(")
    depth = 0
    i = start
    while i < len(script):
        ch = script[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return script[start:i + 1]
        i += 1
    raise ValueError(f"Could not find end of function {name}")


def _run_node(code, tz="UTC"):
    env = {**os.environ, "TZ": tz}
    result = subprocess.run(
        ["node", "-e", code],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def _build_harness():
    script = Translator("en").fill_script((STATIC_DIR / "app.js").read_text(encoding="utf-8"))
    fns = ["documentDateKey", "toDateKey", "buildCalendarWeeks", "filterDocumentsByDate", "nextCalendarFocus"]
    parts = [_extract_function(script, fn) for fn in fns]
    return "\n".join(parts)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class CalendarJsTests(unittest.TestCase):
    harness = None

    @classmethod
    def setUpClass(cls):
        cls.harness = _build_harness()

    def _run(self, expr, tz="UTC"):
        code = self.harness + f"\nconsole.log(JSON.stringify({expr}));"
        return _run_node(code, tz)

    def test_document_date_key_iso(self):
        for tz in ("America/Los_Angeles", "Asia/Jerusalem"):
            with self.subTest(tz=tz):
                result = _run_node(self.harness + '\nconsole.log(JSON.stringify(documentDateKey("2024-03-01")));', tz)
                self.assertEqual(result, "2024-03-01")

    def test_document_date_key_variations(self):
        for tz in ("America/Los_Angeles", "Asia/Jerusalem"):
            with self.subTest(tz=tz):
                code_template = self.harness + '\nconsole.log(JSON.stringify({iso: documentDateKey("2024-03-01"), datetime: documentDateKey("2024-03-01T10:00:00"), compact: documentDateKey("20240301"), whatsapp: documentDateKey("13/05/2026"), empty: documentDateKey(""), null_val: documentDateKey(null), impossible: documentDateKey("2024-02-30"), week: documentDateKey("2024-W10-2")}));'
                result = _run_node(code_template, tz)
                self.assertEqual(result["iso"], "2024-03-01")
                self.assertEqual(result["datetime"], "2024-03-01")
                self.assertEqual(result["compact"], "2024-03-01")
                self.assertIsNone(result["whatsapp"])
                self.assertIsNone(result["empty"])
                self.assertIsNone(result["null_val"])
                self.assertIsNone(result["impossible"])
                self.assertIsNone(result["week"])

    def test_build_calendar_weeks_september_2024_sunday_start(self):
        result = self._run("buildCalendarWeeks(2024, 8, 0)")
        self.assertEqual(len(result), 5)
        self.assertEqual(result[0], [1, 2, 3, 4, 5, 6, 7])
        self.assertEqual(result[4][:2], [29, 30])
        self.assertTrue(all(x is None for x in result[4][2:]))

    def test_build_calendar_weeks_september_2024_monday_start(self):
        result = self._run("buildCalendarWeeks(2024, 8, 1)")
        self.assertEqual(len(result), 6)
        self.assertEqual(result[0][:6], [None]*6)
        self.assertEqual(result[0][6], 1)

    def test_build_calendar_weeks_february_2026(self):
        # Feb 2026 starts on Sunday, 28 days → exactly 4 weeks with Sunday start
        result = self._run("buildCalendarWeeks(2026, 1, 0)")
        self.assertEqual(len(result), 4)

    def test_filter_documents_by_date(self):
        docs = [
            {"id": "a", "date": "2024-03-01"},
            {"id": "b", "date": "2024-03-02"},
            {"id": "c", "date": "20240301"},
            {"id": "d", "date": "garbage"},
        ]
        docs_json = json.dumps(docs)
        result = _run_node(
            self.harness + f'\nconsole.log(JSON.stringify(filterDocumentsByDate({docs_json}, "2024-03-01").map(d => d.id)));'
        )
        self.assertEqual(result, ["a", "c"])

    def test_filter_documents_null_returns_all(self):
        docs = [{"id": "a", "date": "2024-03-01"}, {"id": "b", "date": "garbage"}]
        result = _run_node(
            self.harness + f'\nconsole.log(JSON.stringify(filterDocumentsByDate({json.dumps(docs)}, null).length));'
        )
        self.assertEqual(result, 2)

    def test_keyboard_arrow_right_ltr(self):
        result = _run_node(self.harness + '\nconsole.log(JSON.stringify(nextCalendarFocus("2024-03-01", "ArrowRight", false, 1)));')
        self.assertEqual(result, "2024-03-02")

    def test_keyboard_arrow_right_rtl(self):
        result = _run_node(self.harness + '\nconsole.log(JSON.stringify(nextCalendarFocus("2024-03-01", "ArrowRight", true, 1)));')
        self.assertEqual(result, "2024-02-29")

    def test_keyboard_arrow_down(self):
        result = _run_node(self.harness + '\nconsole.log(JSON.stringify(nextCalendarFocus("2024-03-01", "ArrowDown", false, 1)));')
        self.assertEqual(result, "2024-03-08")

    def test_keyboard_page_down_clamps(self):
        result = _run_node(self.harness + '\nconsole.log(JSON.stringify(nextCalendarFocus("2024-01-31", "PageDown", false, 1)));')
        self.assertEqual(result, "2024-02-29")

    def test_keyboard_home_sunday_start(self):
        # 2024-03-06 is a Wednesday (dow=3); weekStart=0 → go back to Sunday 2024-03-03
        result = _run_node(self.harness + '\nconsole.log(JSON.stringify(nextCalendarFocus("2024-03-06", "Home", false, 0)));')
        self.assertEqual(result, "2024-03-03")

    def test_keyboard_home_monday_start(self):
        # weekStart=1 → go back to Monday 2024-03-04
        result = _run_node(self.harness + '\nconsole.log(JSON.stringify(nextCalendarFocus("2024-03-06", "Home", false, 1)));')
        self.assertEqual(result, "2024-03-04")

    def test_keyboard_end_monday_start(self):
        result = _run_node(self.harness + '\nconsole.log(JSON.stringify(nextCalendarFocus("2024-03-06", "End", false, 1)));')
        self.assertEqual(result, "2024-03-10")

    def test_keyboard_tab_returns_null(self):
        result = _run_node(self.harness + '\nconsole.log(JSON.stringify(nextCalendarFocus("2024-03-06", "Tab", false, 1)));')
        self.assertIsNone(result)
