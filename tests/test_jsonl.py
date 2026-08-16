from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from echobot.jsonl import append_jsonl, read_jsonl


class JsonlTests(unittest.TestCase):
    def test_append_repairs_incomplete_final_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "events.jsonl"
            append_jsonl(path, [{"type": "first", "text": "中文"}])
            with path.open("ab") as handle:
                handle.write(b'{"type":"torn"')

            append_jsonl(path, [{"type": "second"}])

            self.assertEqual(
                [{"type": "first", "text": "中文"}, {"type": "second"}],
                read_jsonl(path, source=path.name),
            )

    def test_append_preserves_valid_record_without_newline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "events.jsonl"
            path.write_text('{"type":"first"}', encoding="utf-8")

            append_jsonl(path, [{"type": "second"}])

            self.assertEqual(
                [{"type": "first"}, {"type": "second"}],
                read_jsonl(path, source=path.name),
            )
