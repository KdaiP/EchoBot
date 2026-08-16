from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


def read_jsonl(path: Path, *, source: str) -> list[dict[str, Any]]:
    """Read JSON objects, tolerating only an incomplete final record."""

    lines = path.read_text(encoding="utf-8").splitlines()
    records: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            if index == len(lines) - 1:
                break
            raise ValueError(f"Invalid JSONL record in {source} at line {index + 1}")
        if not isinstance(value, dict):
            raise ValueError(f"JSONL record must be an object in {source}")
        records.append(value)
    return records


def append_jsonl(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    """Durably append JSON objects and repair a torn final write first."""

    if not records:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    _repair_final_record(path)
    payload = "".join(
        json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        for record in records
    )
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _repair_final_record(path: Path) -> None:
    if not path.exists() or path.stat().st_size == 0:
        return
    with path.open("rb+") as handle:
        handle.seek(-1, 2)
        if handle.read(1) == b"\n":
            return

        handle.seek(0)
        content = handle.read()
        last_newline = content.rfind(b"\n")
        tail_start = last_newline + 1
        try:
            tail_record = json.loads(content[tail_start:].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            handle.truncate(tail_start)
            return
        if not isinstance(tail_record, dict):
            handle.truncate(tail_start)
            return
        handle.seek(0, 2)
        handle.write(b"\n")
        handle.flush()
        os.fsync(handle.fileno())
