from __future__ import annotations

import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from ..jsonl import append_jsonl, read_jsonl
from ..models import LLMMessage, ToolCall, normalize_message_content


SESSION_SCHEMA_VERSION = 1
SessionKind = Literal["user", "system"]
_SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class _UnrecognizedSessionLog(ValueError):
    """A JSONL file in the sessions directory that is not a current session log."""


@dataclass(slots=True)
class Session:
    """One durable conversation with two explicit message surfaces.

    ``history`` is the user-visible conversation. ``agent_history`` is the
    internal tool-capable agent context. Keeping both surfaces in one aggregate
    gives them one identity and one lifecycle without pretending they are the
    same history.
    """

    id: str
    title: str
    history: list[LLMMessage]
    agent_history: list[LLMMessage]
    created_at: str
    updated_at: str
    agent_summary: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    kind: SessionKind = "user"


@dataclass(slots=True, frozen=True)
class SessionInfo:
    id: str
    title: str
    message_count: int
    updated_at: str


class SessionStore:
    """Append-only JSONL repository for session aggregates."""

    def __init__(self, base_dir: str | Path = ".echobot/sessions") -> None:
        self.base_dir = Path(base_dir)
        self.state_file = self.base_dir / "state.jsonl"
        self._lock = threading.RLock()

    def create_session(
        self,
        title: str | None = None,
        *,
        session_id: str | None = None,
        kind: SessionKind = "user",
    ) -> Session:
        with self._lock:
            resolved_id = normalize_session_id(session_id or uuid.uuid4().hex)
            path = self._session_path(resolved_id)
            if path.exists():
                raise ValueError(f"Session already exists: {resolved_id}")
            if kind not in {"user", "system"}:
                raise ValueError(f"Unsupported session kind: {kind}")

            now = _now_text()
            normalized_title = _normalize_title(title)
            resolved_title = (
                _require_title(normalized_title)
                if normalized_title
                else _default_title(now)
            )
            session = Session(
                id=resolved_id,
                title=resolved_title,
                history=[],
                agent_history=[],
                created_at=now,
                updated_at=now,
                kind=kind,
            )
            self._append_records(
                path,
                [
                    {
                        "type": "session.created",
                        "schema_version": SESSION_SCHEMA_VERSION,
                        "session_id": session.id,
                        "title": session.title,
                        "kind": session.kind,
                        "created_at": now,
                    }
                ],
            )
            if kind == "user":
                self.set_current_session(session.id)
            return session

    def ensure_system_session(self, session_id: str, title: str) -> Session:
        with self._lock:
            normalized_id = normalize_session_id(session_id)
            if self.has_session(normalized_id):
                session = self.load_session(normalized_id)
                if session.kind != "system":
                    raise ValueError(f"Session ID is already used: {normalized_id}")
                return session
            return self.create_session(
                title,
                session_id=normalized_id,
                kind="system",
            )

    def load_session(self, session_id: str) -> Session:
        with self._lock:
            normalized_id = normalize_session_id(session_id)
            path = self._session_path(normalized_id)
            if not path.exists():
                raise ValueError(f"Session not found: {normalized_id}")
            return self._fold_session(path)

    def load_current_session(self) -> Session:
        with self._lock:
            current_id = self.get_current_session_id()
            if current_id is not None and self.has_session(current_id):
                session = self.load_session(current_id)
                if session.kind == "user":
                    return session

            sessions = self.list_sessions()
            if sessions:
                self.set_current_session(sessions[0].id)
                return self.load_session(sessions[0].id)
            return self.create_session("New session")

    def save_session(self, session: Session) -> None:
        """Persist visible history and session metadata as append-only events."""

        with self._lock:
            stored = self.load_session(session.id)
            now = _now_text()
            records: list[dict[str, Any]] = []

            if session.title != stored.title:
                records.append(
                    {
                        "type": "session.title_changed",
                        "title": _require_title(session.title),
                        "created_at": now,
                    }
                )
            if session.metadata != stored.metadata:
                records.append(
                    {
                        "type": "session.metadata_replaced",
                        "metadata": dict(session.metadata),
                        "created_at": now,
                    }
                )
            records.extend(
                _surface_records(
                    "visible",
                    stored.history,
                    session.history,
                    created_at=now,
                )
            )
            self._append_records(self._session_path(session.id), records)
            if records:
                session.updated_at = now

    def save_agent_context(self, session: Session) -> None:
        """Persist the internal agent context without touching visible history."""

        with self._lock:
            stored = self.load_session(session.id)
            now = _now_text()
            if session.agent_summary != stored.agent_summary:
                records = [
                    {
                        "type": "agent.context_replaced",
                        "messages": [
                            message_to_dict(message)
                            for message in session.agent_history
                        ],
                        "summary": session.agent_summary,
                        "created_at": now,
                    }
                ]
            else:
                records = _surface_records(
                    "agent",
                    stored.agent_history,
                    session.agent_history,
                    created_at=now,
                )
                for record in records:
                    if record["type"] == "agent.context_replaced":
                        record["summary"] = session.agent_summary
            self._append_records(self._session_path(session.id), records)
            if records:
                session.updated_at = now

    def clear_agent_context(self, session_id: str) -> None:
        with self._lock:
            session = self.load_session(session_id)
            if not session.agent_history and not session.agent_summary:
                return
            self._append_records(
                self._session_path(session.id),
                [
                    {
                        "type": "agent.context_replaced",
                        "messages": [],
                        "summary": "",
                        "created_at": _now_text(),
                    }
                ],
            )

    def rename_session(self, session_id: str, title: str) -> Session:
        with self._lock:
            session = self.load_session(session_id)
            session.title = _require_title(title)
            self.save_session(session)
            return session

    def delete_session(self, session_id: str) -> None:
        with self._lock:
            path = self._session_path(session_id)
            if path.exists():
                path.unlink()

    def set_current_session(self, session_id: str) -> None:
        with self._lock:
            normalized_id = normalize_session_id(session_id)
            session = self.load_session(normalized_id)
            if session.kind != "user":
                raise ValueError("System sessions cannot be selected")
            self._append_records(
                self.state_file,
                [
                    {
                        "type": "current_session.changed",
                        "session_id": normalized_id,
                        "created_at": _now_text(),
                    }
                ],
            )

    def get_current_session_id(self) -> str | None:
        with self._lock:
            if not self.state_file.exists():
                return None
            current_id: str | None = None
            for record in _read_jsonl_records(self.state_file):
                if record.get("type") == "current_session.changed":
                    value = str(record.get("session_id", "")).strip()
                    current_id = value or None
            return current_id

    def list_sessions(self) -> list[SessionInfo]:
        with self._lock:
            if not self.base_dir.exists():
                return []
            sessions: list[SessionInfo] = []
            for path in self.base_dir.glob("*.jsonl"):
                if path == self.state_file:
                    continue
                try:
                    session = self._fold_session(path)
                except _UnrecognizedSessionLog:
                    # Old session files and unrelated JSONL files may remain in
                    # the directory after an upgrade. They are not migrated or
                    # exposed as sessions.
                    continue
                if session.kind != "user":
                    continue
                sessions.append(
                    SessionInfo(
                        id=session.id,
                        title=session.title,
                        message_count=len(session.history),
                        updated_at=session.updated_at,
                    )
                )
            sessions.sort(key=lambda item: item.updated_at, reverse=True)
            return sessions

    def has_session(self, session_id: str) -> bool:
        return self._session_path(session_id).exists()

    def _session_path(self, session_id: str) -> Path:
        return self.base_dir / f"{normalize_session_id(session_id)}.jsonl"

    def _fold_session(self, path: Path) -> Session:
        records = _read_jsonl_records(path)
        if not records or records[0].get("type") != "session.created":
            raise _UnrecognizedSessionLog(f"Unrecognized session log: {path.name}")

        created = records[0]
        version = created.get("schema_version")
        if version != SESSION_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported session schema {version!r}: {path.name}"
            )

        session_id = normalize_session_id(str(created.get("session_id", "")))
        created_at = str(created.get("created_at", "")) or _now_text()
        kind_value = str(created.get("kind", "user"))
        if kind_value not in {"user", "system"}:
            raise ValueError(f"Invalid session kind in {path.name}")
        session = Session(
            id=session_id,
            title=_require_title(str(created.get("title", ""))),
            history=[],
            agent_history=[],
            created_at=created_at,
            updated_at=created_at,
            kind=kind_value,  # type: ignore[arg-type]
        )

        for record in records[1:]:
            event_type = record.get("type")
            if event_type == "session.title_changed":
                session.title = _require_title(str(record.get("title", "")))
            elif event_type == "session.metadata_replaced":
                session.metadata = _read_metadata(record.get("metadata"))
            elif event_type == "visible.message":
                session.history.append(_message_from_record(record))
            elif event_type == "visible.context_replaced":
                session.history = _messages_from_record(record)
            elif event_type == "agent.message":
                session.agent_history.append(_message_from_record(record))
            elif event_type == "agent.context_replaced":
                session.agent_history = _messages_from_record(record)
                session.agent_summary = str(record.get("summary", ""))
            else:
                raise ValueError(
                    f"Unsupported session event {event_type!r} in {path.name}"
                )
            session.updated_at = str(record.get("created_at", "")) or session.updated_at
        return session

    def _append_records(self, path: Path, records: list[dict[str, Any]]) -> None:
        append_jsonl(path, records)


def normalize_session_id(session_id: str) -> str:
    value = str(session_id or "").strip()
    if not _SESSION_ID_PATTERN.fullmatch(value):
        raise ValueError(
            "Session ID must be 1-128 letters, digits, dots, hyphens, or underscores"
        )
    return value


def message_to_dict(message: LLMMessage) -> dict[str, Any]:
    data: dict[str, Any] = {
        "role": message.role,
        "content": normalize_message_content(message.content),
        "name": message.name,
        "tool_call_id": message.tool_call_id,
        "tool_calls": [
            {
                "id": tool_call.id,
                "name": tool_call.name,
                "arguments": tool_call.arguments,
            }
            for tool_call in message.tool_calls
        ],
    }
    if message.role == "assistant" and message.reasoning_content:
        data["reasoning_content"] = message.reasoning_content
        data["reasoning_field"] = message.reasoning_field
    return data


def message_from_dict(data: dict[str, Any]) -> LLMMessage:
    raw_tool_calls = data.get("tool_calls", [])
    tool_calls = raw_tool_calls if isinstance(raw_tool_calls, list) else []
    return LLMMessage(
        role=str(data.get("role", "user")),  # type: ignore[arg-type]
        content=normalize_message_content(data.get("content", "")),
        name=_read_optional_text(data.get("name")),
        tool_call_id=_read_optional_text(data.get("tool_call_id")),
        tool_calls=[
            ToolCall(
                id=str(item.get("id", "")),
                name=str(item.get("name", "")),
                arguments=str(item.get("arguments", "")),
            )
            for item in tool_calls
            if isinstance(item, dict)
        ],
        reasoning_content=str(
            data.get("reasoning_content") or data.get("reasoning") or ""
        ),
        reasoning_field=_read_reasoning_field(data.get("reasoning_field")),
    )


def _surface_records(
    surface: Literal["visible", "agent"],
    previous: list[LLMMessage],
    current: list[LLMMessage],
    *,
    created_at: str,
) -> list[dict[str, Any]]:
    previous_data = [message_to_dict(message) for message in previous]
    current_data = [message_to_dict(message) for message in current]
    if current_data[: len(previous_data)] == previous_data:
        return [
            {
                "type": f"{surface}.message",
                "message": message,
                "created_at": created_at,
            }
            for message in current_data[len(previous_data) :]
        ]
    return [
        {
            "type": f"{surface}.context_replaced",
            "messages": current_data,
            "created_at": created_at,
        }
    ]


def _message_from_record(record: dict[str, Any]) -> LLMMessage:
    message = record.get("message")
    if not isinstance(message, dict):
        raise ValueError("Session message event must contain an object")
    return message_from_dict(message)


def _messages_from_record(record: dict[str, Any]) -> list[LLMMessage]:
    messages = record.get("messages")
    if not isinstance(messages, list):
        raise ValueError("Session context event must contain a message list")
    return [message_from_dict(item) for item in messages if isinstance(item, dict)]


def _read_jsonl_records(path: Path) -> list[dict[str, Any]]:
    return read_jsonl(path, source=path.name)


def _normalize_title(title: str | None) -> str:
    return " ".join(str(title or "").split()).strip()


def _require_title(title: str) -> str:
    value = _normalize_title(title)
    if not value:
        raise ValueError("Session title cannot be empty")
    if len(value) > 200:
        raise ValueError("Session title cannot exceed 200 characters")
    return value


def _default_title(created_at: str) -> str:
    timestamp = datetime.fromisoformat(created_at).strftime("Session %Y-%m-%d %H:%M")
    return timestamp


def _read_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _read_reasoning_field(value: Any) -> str:
    return "reasoning" if str(value or "").strip() == "reasoning" else "reasoning_content"


def _read_metadata(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="microseconds")
