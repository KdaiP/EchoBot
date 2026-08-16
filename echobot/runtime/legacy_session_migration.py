from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..jsonl import append_jsonl, read_jsonl
from .sessions import SESSION_SCHEMA_VERSION, SessionStore, normalize_session_id


_CURRENT_ROUTE_BINDING_SCHEMA_VERSION = 1


@dataclass(slots=True, frozen=True)
class LegacySessionMigrationReport:
    sessions_migrated: int = 0
    agent_contexts_migrated: int = 0
    current_session_migrated: bool = False
    route_bindings_migrated: int = 0

    @property
    def changed(self) -> bool:
        return any(
            (
                self.sessions_migrated,
                self.agent_contexts_migrated,
                self.current_session_migrated,
                self.route_bindings_migrated,
            )
        )


@dataclass(slots=True)
class _LegacySession:
    session_id: str
    updated_at: str
    messages: list[dict[str, Any]]
    summary: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class _LegacyRouteSession:
    session_id: str
    title: str
    created_at: str
    updated_at: str


@dataclass(slots=True)
class _LegacyRoute:
    current_session_id: str | None
    sessions: list[_LegacyRouteSession]


def migrate_legacy_session_data(
    workspace: str | Path,
) -> LegacySessionMigrationReport:
    """Convert pre-v1 session stores once, before the current stores are opened.

    The current Session and RouteBinding stores remain unaware of old schemas.
    Successful conversion consumes the old sources, so later starts are no-ops
    and this module plus its single bootstrap call can eventually be removed.
    """

    data_dir = Path(workspace) / ".echobot"
    sessions_dir = data_dir / "sessions"
    agent_sessions_dir = data_dir / "agent_sessions"
    route_source = _legacy_route_source(data_dir)
    routes = _read_legacy_routes(route_source) if route_source else {}
    route_titles = _route_titles(routes)

    sessions_migrated = 0
    agent_contexts_migrated = 0

    if sessions_dir.exists():
        for source in sorted(sessions_dir.glob("*.jsonl")):
            if source.name in {"index.jsonl", "state.jsonl"}:
                continue
            legacy_session = _read_legacy_session(source)
            if legacy_session is None:
                continue
            agent_source = agent_sessions_dir / f"{legacy_session.session_id}.jsonl"
            legacy_agent = _read_legacy_session(agent_source)
            if not agent_source.exists():
                legacy_agent = None
            title_info = route_titles.get(legacy_session.session_id)
            title = title_info.title if title_info else legacy_session.session_id
            created_at = (
                title_info.created_at if title_info else legacy_session.updated_at
            )
            target = sessions_dir / f"{legacy_session.session_id}.jsonl"
            if target != source and target.exists():
                raise ValueError(
                    f"Cannot migrate legacy session {source.name}: "
                    f"target {target.name} already exists"
                )
            records = _current_session_records(
                legacy_session,
                legacy_agent,
                title=title,
                created_at=created_at,
            )
            _write_current_session_atomically(target, records)
            if target != source:
                source.unlink()
            if legacy_agent is not None:
                agent_source.unlink()
                agent_contexts_migrated += 1
            sessions_migrated += 1

    if agent_sessions_dir.exists():
        for source in sorted(agent_sessions_dir.glob("*.jsonl")):
            legacy_agent = _read_legacy_session(source)
            if legacy_agent is None:
                continue
            target = sessions_dir / f"{legacy_agent.session_id}.jsonl"
            if target.exists():
                if not _is_current_session_log(target):
                    raise ValueError(
                        f"Cannot migrate legacy agent context {source.name}: "
                        f"target {target.name} is not a current session log"
                    )
                _add_agent_context_if_missing(target, legacy_agent)
            else:
                records = _current_session_records(
                    _LegacySession(
                        session_id=legacy_agent.session_id,
                        updated_at=legacy_agent.updated_at,
                        messages=[],
                        metadata=legacy_agent.metadata,
                    ),
                    legacy_agent,
                    title=legacy_agent.session_id,
                    created_at=legacy_agent.updated_at,
                )
                _write_current_session_atomically(target, records)
                sessions_migrated += 1
            source.unlink()
            agent_contexts_migrated += 1
        _remove_directory_if_empty(agent_sessions_dir)

    current_session_migrated = _migrate_current_session(sessions_dir)
    route_bindings_migrated = 0
    if route_source is not None:
        route_bindings_migrated = _migrate_route_bindings(
            data_dir / "route_bindings.jsonl",
            sessions_dir,
            routes,
        )
        route_source.unlink()

    return LegacySessionMigrationReport(
        sessions_migrated=sessions_migrated,
        agent_contexts_migrated=agent_contexts_migrated,
        current_session_migrated=current_session_migrated,
        route_bindings_migrated=route_bindings_migrated,
    )


def _read_legacy_session(path: Path) -> _LegacySession | None:
    if not path.exists():
        return None
    records = read_jsonl(path, source=path.name)
    if not records or records[0].get("type") != "session":
        return None
    header = records[0]
    session_id = normalize_session_id(str(header.get("name") or path.stem))
    updated_at = str(header.get("updated_at", "")).strip() or _now_text()
    messages = []
    for record in records[1:]:
        if record.get("type") != "message":
            continue
        message = dict(record)
        message.pop("type", None)
        messages.append(message)
    metadata = header.get("metadata")
    return _LegacySession(
        session_id=session_id,
        updated_at=updated_at,
        messages=messages,
        summary=str(header.get("compressed_summary", "")),
        metadata=dict(metadata) if isinstance(metadata, dict) else {},
    )


def _current_session_records(
    visible: _LegacySession,
    agent: _LegacySession | None,
    *,
    title: str,
    created_at: str,
) -> list[dict[str, Any]]:
    event_time = visible.updated_at or created_at
    records: list[dict[str, Any]] = [
        {
            "type": "session.created",
            "schema_version": SESSION_SCHEMA_VERSION,
            "session_id": visible.session_id,
            "title": _normalize_legacy_title(title, visible.session_id),
            "kind": "user",
            "created_at": created_at,
        }
    ]
    if visible.metadata:
        records.append(
            {
                "type": "session.metadata_replaced",
                "metadata": dict(visible.metadata),
                "created_at": event_time,
            }
        )
    if visible.messages:
        records.append(
            {
                "type": "visible.context_replaced",
                "messages": list(visible.messages),
                "created_at": event_time,
            }
        )
    agent_messages = agent.messages if agent is not None else []
    summary = (agent.summary if agent is not None else "") or visible.summary
    if agent_messages or summary:
        records.append(
            {
                "type": "agent.context_replaced",
                "messages": list(agent_messages),
                "summary": summary,
                "created_at": agent.updated_at if agent is not None else event_time,
            }
        )
    created_record = records[0]
    later_events = records[1:]
    later_events.sort(
        key=lambda record: _timestamp_sort_key(str(record.get("created_at", "")))
    )
    return [created_record, *later_events]


def _add_agent_context_if_missing(path: Path, agent: _LegacySession) -> None:
    records = read_jsonl(path, source=path.name)
    if any(str(record.get("type", "")).startswith("agent.") for record in records):
        return
    records.append(
        {
            "type": "agent.context_replaced",
            "messages": list(agent.messages),
            "summary": agent.summary,
            "created_at": agent.updated_at,
        }
    )
    _write_current_session_atomically(path, records)


def _migrate_current_session(sessions_dir: Path) -> bool:
    index_path = sessions_dir / "index.jsonl"
    if not index_path.exists():
        return False
    records = read_jsonl(index_path, source=index_path.name)
    if not records or "current_session" not in records[0]:
        return False

    state_path = sessions_dir / "state.jsonl"
    state_records = (
        read_jsonl(state_path, source=state_path.name) if state_path.exists() else []
    )
    has_current_state = any(
        record.get("type") == "current_session.changed" for record in state_records
    )
    migrated = False
    if not has_current_state:
        session_id = normalize_session_id(str(records[0].get("current_session", "")))
        target = sessions_dir / f"{session_id}.jsonl"
        if target.exists() and _is_current_session_log(target):
            append_jsonl(
                state_path,
                [
                    {
                        "type": "current_session.changed",
                        "session_id": session_id,
                        "created_at": _now_text(),
                    }
                ],
            )
            migrated = True
    index_path.unlink()
    return migrated


def _legacy_route_source(data_dir: Path) -> Path | None:
    current_old_path = data_dir / "route_sessions.json"
    if current_old_path.exists():
        return current_old_path
    older_path = data_dir / "conversations.json"
    return older_path if older_path.exists() else None


def _read_legacy_routes(path: Path) -> dict[str, _LegacyRoute]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid legacy route session file: {path.name}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid legacy route session file: {path.name}")
    raw_routes = payload.get("routes", {})
    if not isinstance(raw_routes, dict):
        return {}

    routes: dict[str, _LegacyRoute] = {}
    for raw_route_key, raw_state in raw_routes.items():
        route_key = str(raw_route_key).strip()
        if not route_key or not isinstance(raw_state, dict):
            continue
        raw_sessions = raw_state.get("sessions", raw_state.get("conversations", []))
        sessions: list[_LegacyRouteSession] = []
        if isinstance(raw_sessions, list):
            for raw_session in raw_sessions:
                if not isinstance(raw_session, dict):
                    continue
                raw_id = raw_session.get(
                    "session_name",
                    raw_session.get("conversation_name", ""),
                )
                try:
                    session_id = normalize_session_id(str(raw_id))
                except ValueError:
                    continue
                created_at = (
                    str(raw_session.get("created_at", "")).strip() or _now_text()
                )
                updated_at = (
                    str(raw_session.get("updated_at", "")).strip() or created_at
                )
                sessions.append(
                    _LegacyRouteSession(
                        session_id=session_id,
                        title=str(raw_session.get("title", "")).strip() or "Session",
                        created_at=created_at,
                        updated_at=updated_at,
                    )
                )
        raw_current = raw_state.get(
            "current_session_name",
            raw_state.get("current_conversation_name"),
        )
        try:
            current_session_id = (
                normalize_session_id(str(raw_current))
                if raw_current is not None
                else None
            )
        except ValueError:
            current_session_id = None
        routes[route_key] = _LegacyRoute(current_session_id, sessions)
    return routes


def _route_titles(
    routes: dict[str, _LegacyRoute],
) -> dict[str, _LegacyRouteSession]:
    titles: dict[str, _LegacyRouteSession] = {}
    for route in routes.values():
        for session in route.sessions:
            titles.setdefault(session.session_id, session)
    return titles


def _migrate_route_bindings(
    target: Path,
    sessions_dir: Path,
    routes: dict[str, _LegacyRoute],
) -> int:
    existing_routes = _read_current_route_bindings(target)
    records: list[dict[str, object]] = []
    migrated = 0
    for route_key, route in routes.items():
        existing_ids, existing_current = existing_routes.get(
            route_key,
            (set(), None),
        )
        existing_ids = set(existing_ids)
        available_legacy: list[_LegacyRouteSession] = []
        for session in route.sessions:
            session_path = sessions_dir / f"{session.session_id}.jsonl"
            if not session_path.exists() or not _is_current_session_log(session_path):
                continue
            available_legacy.append(session)
            if session.session_id in existing_ids:
                continue
            records.extend(
                [
                    {
                        "type": "route.session_bound",
                        "schema_version": _CURRENT_ROUTE_BINDING_SCHEMA_VERSION,
                        "route_key": route_key,
                        "session_id": session.session_id,
                        "created_at": session.created_at,
                    },
                    {
                        "type": "route.session_touched",
                        "schema_version": _CURRENT_ROUTE_BINDING_SCHEMA_VERSION,
                        "route_key": route_key,
                        "session_id": session.session_id,
                        "created_at": session.updated_at,
                    },
                ]
            )
            existing_ids.add(session.session_id)
            migrated += 1
        if existing_current is not None:
            continue
        selected_id = route.current_session_id
        if selected_id not in existing_ids:
            selected_id = (
                max(available_legacy, key=lambda item: item.updated_at).session_id
                if available_legacy
                else None
            )
        if selected_id is not None:
            records.append(
                {
                    "type": "route.session_selected",
                    "schema_version": _CURRENT_ROUTE_BINDING_SCHEMA_VERSION,
                    "route_key": route_key,
                    "session_id": selected_id,
                    "created_at": _now_text(),
                }
            )
    append_jsonl(target, records)
    migrated_routes = _read_current_route_bindings(target)
    for route_key, route in routes.items():
        expected = {
            session.session_id
            for session in route.sessions
            if (
                (sessions_dir / f"{session.session_id}.jsonl").exists()
                and _is_current_session_log(
                    sessions_dir / f"{session.session_id}.jsonl"
                )
            )
        }
        migrated_ids, _current = migrated_routes.get(route_key, (set(), None))
        if not expected.issubset(migrated_ids):
            raise ValueError(f"Failed to validate migrated route: {route_key}")
    return migrated


def _read_current_route_bindings(
    path: Path,
) -> dict[str, tuple[set[str], str | None]]:
    if not path.exists():
        return {}
    states: dict[str, tuple[set[str], str | None]] = {}
    for record in read_jsonl(path, source=path.name):
        if record.get("schema_version") != _CURRENT_ROUTE_BINDING_SCHEMA_VERSION:
            raise ValueError("Unsupported route binding schema")
        route_key = str(record.get("route_key", "")).strip()
        if not route_key:
            raise ValueError("Route key cannot be empty")
        session_id = normalize_session_id(str(record.get("session_id", "")))
        session_ids, current_id = states.setdefault(route_key, (set(), None))
        event_type = record.get("type")
        if event_type == "route.session_bound":
            session_ids.add(session_id)
        elif event_type == "route.session_selected":
            if session_id not in session_ids:
                raise ValueError("Selected route session is not bound")
            current_id = session_id
        elif event_type == "route.session_unbound":
            session_ids.discard(session_id)
            if current_id == session_id:
                current_id = None
        elif event_type != "route.session_touched":
            raise ValueError(f"Unsupported route binding event: {event_type!r}")
        states[route_key] = (session_ids, current_id)
    return states


def _is_current_session_log(path: Path) -> bool:
    records = read_jsonl(path, source=path.name)
    return bool(records and records[0].get("type") == "session.created")


def _validate_current_session_log(path: Path) -> None:
    records = read_jsonl(path, source=path.name)
    if not records or records[0].get("type") != "session.created":
        raise ValueError(f"Failed to migrate session log: {path.name}")
    if records[0].get("schema_version") != SESSION_SCHEMA_VERSION:
        raise ValueError(f"Failed to migrate session schema: {path.name}")
    SessionStore(path.parent).load_session(path.stem)


def _write_current_session_atomically(
    path: Path,
    records: list[dict[str, Any]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = path.parent / f".session-migration-{uuid.uuid4().hex}"
    staging_dir.mkdir()
    temporary_path = staging_dir / path.name
    payload = "".join(
        json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        for record in records
    )
    try:
        with temporary_path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _validate_current_session_log(temporary_path)
        temporary_path.replace(path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
        _remove_directory_if_empty(staging_dir)


def _normalize_legacy_title(title: str, fallback: str) -> str:
    value = " ".join(str(title or "").split()).strip() or fallback
    return value[:200]


def _remove_directory_if_empty(path: Path) -> None:
    try:
        path.rmdir()
    except OSError:
        pass


def _timestamp_sort_key(value: str) -> float:
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return 0.0


def _now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="microseconds")
