from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from echobot.channels import ChannelAddress
from echobot.gateway import DeliveryStore, GatewaySessionService, RouteBindingStore
from echobot.runtime.session_service import SessionLifecycleService
from echobot.runtime.sessions import SessionStore


class RouteBindingStoreTests(unittest.TestCase):
    def test_bind_select_and_remove_use_stable_session_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "route_bindings.jsonl"
            store = RouteBindingStore(path)
            store.bind_session("telegram:1", "first")
            store.bind_session("telegram:1", "second")

            self.assertEqual("second", store.current_session_id("telegram:1"))
            self.assertEqual(["second", "first"], store.list_session_ids("telegram:1"))

            selected = store.select_session("telegram:1", 2)
            removed = store.remove_current("telegram:1")

            self.assertEqual("first", selected)
            self.assertIsNotNone(removed)
            assert removed is not None
            self.assertEqual("first", removed.session_id)
            self.assertEqual("second", removed.replacement_session_id)
            self.assertEqual("second", store.current_session_id("telegram:1"))
            records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertTrue(all(record["schema_version"] == 1 for record in records))

    def test_bindings_reload_from_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "route_bindings.jsonl"
            store = RouteBindingStore(path)
            store.bind_session("qq:1", "alpha")
            store.bind_session("qq:1", "beta")

            reloaded = RouteBindingStore(path)

            self.assertEqual("beta", reloaded.current_session_id("qq:1"))
            self.assertEqual(["beta", "alpha"], reloaded.list_session_ids("qq:1"))


class GatewaySessionServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_title_is_owned_by_canonical_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            session_store = SessionStore(workspace / "sessions")
            bindings = RouteBindingStore(workspace / "route_bindings.jsonl")
            service = GatewaySessionService(
                SessionLifecycleService(session_store),
                route_binding_store=bindings,
            )
            routed = await service.create_routed_session(
                "telegram:1",
                title="原始标题",
            )

            renamed = await service.rename_current_routed_session(
                "telegram:1",
                "新的标题",
            )

            self.assertEqual(routed.session_id, renamed.session_id)
            self.assertEqual("新的标题", renamed.title)
            self.assertEqual(
                "新的标题",
                session_store.load_session(routed.session_id).title,
            )
            self.assertEqual(
                routed.session_id,
                bindings.current_session_id("telegram:1"),
            )

    async def test_deleting_last_routed_session_creates_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            session_store = SessionStore(workspace / "sessions")
            delivery_store = DeliveryStore(workspace / "delivery.json")
            bindings = RouteBindingStore(workspace / "route_bindings.jsonl")
            service = GatewaySessionService(
                SessionLifecycleService(session_store),
                route_binding_store=bindings,
                delivery_store=delivery_store,
            )
            current = await service.create_routed_session("telegram:1")
            await service.remember_delivery_target(
                current.session_id,
                ChannelAddress(channel="telegram", chat_id="1"),
            )

            result = await service.delete_current_routed_session("telegram:1")

            self.assertTrue(result.created_replacement)
            self.assertNotEqual(current.session_id, result.current.session_id)
            self.assertFalse(session_store.has_session(current.session_id))
            self.assertIsNone(await service.get_session_target(current.session_id))
            self.assertEqual(
                result.current.session_id,
                bindings.current_session_id("telegram:1"),
            )
