from __future__ import annotations

import copy
import json
import threading
from dataclasses import dataclass, field
from pathlib import Path

from ..channels.types import ChannelAddress, DeliveryTarget


DEFAULT_DELIVERY_STORE_PATH = Path(".echobot/delivery.json")


@dataclass(slots=True)
class DeliveryState:
    routes: dict[str, DeliveryTarget] = field(default_factory=dict)
    latest_session_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "routes": {
                session_id: target.to_dict()
                for session_id, target in self.routes.items()
            },
            "latest_session_id": self.latest_session_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "DeliveryState":
        raw_routes = data.get("routes", {})
        routes: dict[str, DeliveryTarget] = {}
        if isinstance(raw_routes, dict):
            for session_id, target_data in raw_routes.items():
                if isinstance(session_id, str) and isinstance(target_data, dict):
                    routes[session_id] = DeliveryTarget.from_dict(target_data)
        latest_session_id = data.get("latest_session_id")
        if not isinstance(latest_session_id, str):
            latest_session_id = None
        return cls(
            routes=routes,
            latest_session_id=latest_session_id,
        )


class DeliveryStore:
    def __init__(
        self,
        path: str | Path = DEFAULT_DELIVERY_STORE_PATH,
    ) -> None:
        self.path = Path(path)
        self._state = DeliveryState()
        self._loaded = False
        self._lock = threading.RLock()

    def remember(
        self,
        session_id: str,
        address: ChannelAddress,
        metadata: dict[str, object] | None = None,
    ) -> None:
        with self._lock:
            self._ensure_loaded()
            self._state.routes[session_id] = DeliveryTarget(
                address=copy.deepcopy(address),
                metadata=dict(metadata or {}),
            )
            self._state.latest_session_id = session_id
            self._save()

    def get_session_target(self, session_id: str) -> DeliveryTarget | None:
        with self._lock:
            self._ensure_loaded()
            target = self._state.routes.get(session_id)
            return copy.deepcopy(target) if target is not None else None

    def get_latest_target(self) -> DeliveryTarget | None:
        with self._lock:
            self._ensure_loaded()
            if not self._state.latest_session_id:
                return None
            return self.get_session_target(self._state.latest_session_id)

    def forget(self, session_id: str) -> None:
        with self._lock:
            self._ensure_loaded()
            removed = self._state.routes.pop(session_id, None)
            if removed is None:
                return
            if self._state.latest_session_id == session_id:
                if self._state.routes:
                    self._state.latest_session_id = list(self._state.routes)[-1]
                else:
                    self._state.latest_session_id = None
            self._save()

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.path.exists():
            self._state = DeliveryState()
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            self._state = DeliveryState()
            return
        if not isinstance(data, dict):
            self._state = DeliveryState()
            return
        self._state = DeliveryState.from_dict(data)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                self._state.to_dict(),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
