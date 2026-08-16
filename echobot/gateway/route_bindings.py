from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..jsonl import append_jsonl, read_jsonl
from ..runtime.sessions import normalize_session_id


ROUTE_BINDING_SCHEMA_VERSION = 1


@dataclass(slots=True)
class RouteBinding:
    session_id: str
    created_at: str
    updated_at: str


@dataclass(slots=True)
class RouteState:
    current_session_id: str | None = None
    bindings: list[RouteBinding] = field(default_factory=list)


@dataclass(slots=True, frozen=True)
class RemovedRouteBinding:
    session_id: str
    replacement_session_id: str | None


class RouteBindingStore:
    """Append-only route-to-session bindings.

    A route identifies a chat transport endpoint. Session titles and histories
    deliberately do not live here.
    """

    def __init__(self, path: str | Path = ".echobot/route_bindings.jsonl") -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    def current_session_id(self, route_key: str) -> str | None:
        with self._lock:
            return self._state_for(_normalize_route_key(route_key)).current_session_id

    def list_session_ids(self, route_key: str) -> list[str]:
        with self._lock:
            state = self._state_for(_normalize_route_key(route_key))
            ordered = self._ordered_bindings(state)
            return [binding.session_id for binding in ordered]

    def bind_session(self, route_key: str, session_id: str) -> None:
        with self._lock:
            normalized_route_key = _normalize_route_key(route_key)
            normalized_id = normalize_session_id(session_id)
            state = self._state_for(normalized_route_key)
            existing = self._find(state, normalized_id)
            now = _now_text()
            if existing is None:
                self._append(
                    {
                        "type": "route.session_bound",
                        "schema_version": ROUTE_BINDING_SCHEMA_VERSION,
                        "route_key": normalized_route_key,
                        "session_id": normalized_id,
                        "created_at": now,
                    }
                )
            self._append(
                {
                    "type": "route.session_selected",
                    "schema_version": ROUTE_BINDING_SCHEMA_VERSION,
                    "route_key": normalized_route_key,
                    "session_id": normalized_id,
                    "created_at": now,
                }
            )

    def select_session(self, route_key: str, index: int) -> str:
        with self._lock:
            normalized_route_key = _normalize_route_key(route_key)
            ordered = self._ordered_bindings(self._state_for(normalized_route_key))
            if index < 1 or index > len(ordered):
                raise ValueError("Session number is out of range")
            selected = ordered[index - 1]
            self._append(
                {
                    "type": "route.session_selected",
                    "schema_version": ROUTE_BINDING_SCHEMA_VERSION,
                    "route_key": normalized_route_key,
                    "session_id": selected.session_id,
                    "created_at": _now_text(),
                }
            )
            return selected.session_id

    def touch_session(
        self,
        route_key: str,
        session_id: str,
        *,
        updated_at: str | None = None,
    ) -> None:
        with self._lock:
            normalized_route_key = _normalize_route_key(route_key)
            state = self._state_for(normalized_route_key)
            if self._find(state, session_id) is None:
                return
            self._append(
                {
                    "type": "route.session_touched",
                    "schema_version": ROUTE_BINDING_SCHEMA_VERSION,
                    "route_key": normalized_route_key,
                    "session_id": normalize_session_id(session_id),
                    "created_at": updated_at or _now_text(),
                }
            )

    def remove_current(self, route_key: str) -> RemovedRouteBinding | None:
        with self._lock:
            normalized_route_key = _normalize_route_key(route_key)
            state = self._state_for(normalized_route_key)
            current_id = state.current_session_id
            if current_id is None:
                return None
            remaining = [
                binding
                for binding in state.bindings
                if binding.session_id != current_id
            ]
            replacement_id = (
                max(remaining, key=lambda item: item.updated_at).session_id
                if remaining
                else None
            )
            now = _now_text()
            records = [
                {
                    "type": "route.session_unbound",
                    "schema_version": ROUTE_BINDING_SCHEMA_VERSION,
                    "route_key": normalized_route_key,
                    "session_id": current_id,
                    "created_at": now,
                }
            ]
            if replacement_id is not None:
                records.append(
                    {
                        "type": "route.session_selected",
                        "schema_version": ROUTE_BINDING_SCHEMA_VERSION,
                        "route_key": normalized_route_key,
                        "session_id": replacement_id,
                        "created_at": now,
                    }
                )
            self._append_many(records)
            return RemovedRouteBinding(current_id, replacement_id)

    def remove_session(self, session_id: str) -> bool:
        with self._lock:
            normalized_id = normalize_session_id(session_id)
            states = self._load_states()
            records: list[dict[str, object]] = []
            now = _now_text()
            for route_key, state in states.items():
                if self._find(state, normalized_id) is None:
                    continue
                records.append(
                    {
                        "type": "route.session_unbound",
                        "schema_version": ROUTE_BINDING_SCHEMA_VERSION,
                        "route_key": route_key,
                        "session_id": normalized_id,
                        "created_at": now,
                    }
                )
                if state.current_session_id != normalized_id:
                    continue
                remaining = [
                    binding
                    for binding in state.bindings
                    if binding.session_id != normalized_id
                ]
                if remaining:
                    replacement = max(remaining, key=lambda item: item.updated_at)
                    records.append(
                        {
                            "type": "route.session_selected",
                            "schema_version": ROUTE_BINDING_SCHEMA_VERSION,
                            "route_key": route_key,
                            "session_id": replacement.session_id,
                            "created_at": now,
                        }
                    )
            self._append_many(records)
            return bool(records)

    def _state_for(self, route_key: str) -> RouteState:
        return self._load_states().get(_normalize_route_key(route_key), RouteState())

    def _load_states(self) -> dict[str, RouteState]:
        states: dict[str, RouteState] = {}
        if not self.path.exists():
            return states
        for record in _read_records(self.path):
            if record.get("schema_version") != ROUTE_BINDING_SCHEMA_VERSION:
                raise ValueError("Unsupported route binding schema")
            route_key = _normalize_route_key(str(record.get("route_key", "")))
            session_id = normalize_session_id(str(record.get("session_id", "")))
            state = states.setdefault(route_key, RouteState())
            event_type = record.get("type")
            if event_type == "route.session_bound":
                if self._find(state, session_id) is None:
                    created_at = str(record.get("created_at", "")) or _now_text()
                    state.bindings.append(
                        RouteBinding(session_id, created_at, created_at)
                    )
            elif event_type == "route.session_selected":
                binding = self._find(state, session_id)
                if binding is None:
                    raise ValueError("Selected route session is not bound")
                state.current_session_id = session_id
                binding.updated_at = str(record.get("created_at", "")) or binding.updated_at
            elif event_type == "route.session_touched":
                binding = self._find(state, session_id)
                if binding is not None:
                    binding.updated_at = str(record.get("created_at", "")) or binding.updated_at
            elif event_type == "route.session_unbound":
                state.bindings = [
                    item for item in state.bindings if item.session_id != session_id
                ]
                if state.current_session_id == session_id:
                    state.current_session_id = None
            else:
                raise ValueError(f"Unsupported route binding event: {event_type!r}")
        return states

    @staticmethod
    def _find(state: RouteState, session_id: str) -> RouteBinding | None:
        for binding in state.bindings:
            if binding.session_id == session_id:
                return binding
        return None

    def _ordered_bindings(self, state: RouteState) -> list[RouteBinding]:
        current = self._find(state, state.current_session_id or "")
        remaining = [
            binding
            for binding in state.bindings
            if current is None or binding.session_id != current.session_id
        ]
        remaining.sort(key=lambda item: item.updated_at, reverse=True)
        return remaining if current is None else [current, *remaining]

    def _append(self, record: dict[str, object]) -> None:
        self._append_many([record])

    def _append_many(self, records: list[dict[str, object]]) -> None:
        append_jsonl(self.path, records)


def _read_records(path: Path) -> list[dict[str, object]]:
    return read_jsonl(path, source=path.name)


def _normalize_route_key(route_key: str) -> str:
    value = str(route_key or "").strip()
    if not value:
        raise ValueError("Route key cannot be empty")
    return value


def _now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="microseconds")
