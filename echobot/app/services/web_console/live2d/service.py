from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

from .annotations import Live2DAnnotationsRepository
from .catalog import Live2DModelCatalog
from .constants import (
    DEFAULT_LIP_SYNC_PARAMETER_IDS,
    DEFAULT_MOUTH_FORM_PARAMETER_IDS,
    LIVE2D_SOURCE_WORKSPACE,
)
from .metadata import Live2DMetadataService
from .models import Live2DDiscoveredHotkey, Live2DModelCandidate, Live2DUploadFile
from .uploads import Live2DUploadManager


class Live2DService:
    def __init__(self, workspace_root: Path, builtin_root: Path) -> None:
        self._catalog = Live2DModelCatalog(workspace_root, builtin_root)
        self._annotations_repository = Live2DAnnotationsRepository()
        self._metadata = Live2DMetadataService(self._annotations_repository)
        self._uploads = Live2DUploadManager(workspace_root)

    async def build_config(
        self,
        *,
        selected_selection_key: str | None = None,
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(
            self._build_config_sync,
            selected_selection_key,
        )

    async def render_model_json(self, asset_path: str) -> str:
        return await asyncio.to_thread(self._render_model_json_sync, asset_path)

    async def save_directory(
        self,
        uploaded_files: list[Live2DUploadFile],
    ) -> dict[str, Any]:
        return await asyncio.to_thread(self._save_directory_sync, uploaded_files)

    async def save_annotation(
        self,
        *,
        selection_key: str,
        kind: str,
        file: str,
        note: str,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._save_annotation_sync,
            selection_key,
            kind,
            file,
            note,
        )

    async def save_hotkey(
        self,
        *,
        selection_key: str,
        hotkey_key: str,
        shortcut_tokens: list[str],
        restore_default: bool = False,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._save_hotkey_sync,
            selection_key,
            hotkey_key,
            shortcut_tokens,
            restore_default,
        )

    def empty_config(self) -> dict[str, object]:
        return self._catalog.empty_config()

    def resolve_asset(self, asset_path: str) -> Path:
        return self._catalog.resolve_asset(asset_path)

    async def save_selection(
        self,
        selection_key: str,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(self._save_selection_sync, selection_key)

    def _build_config_sync(
        self,
        selected_selection_key: str | None = None,
    ) -> dict[str, Any] | None:
        candidates = self._catalog.discover_model_candidates()
        if not candidates:
            return None

        model_options = [self._build_model_option(candidate) for candidate in candidates]
        selected_candidate = None
        if selected_selection_key:
            selected_candidate = self._catalog.candidate_from_selection_key(
                selected_selection_key,
            )
        if selected_candidate is None:
            selected_candidate = self._catalog.select_default_candidate(candidates)
        selected_selection_key = self._catalog.selection_key_for(selected_candidate)
        selected_option = next(
            option
            for option in model_options
            if option["selection_key"] == selected_selection_key
        )
        return {
            "available": True,
            **selected_option,
            "models": model_options,
        }

    def _save_selection_sync(self, selection_key: str) -> dict[str, Any]:
        candidate = self._catalog.candidate_from_selection_key(selection_key)
        if candidate is None:
            raise ValueError(f"Unknown Live2D model: {selection_key}")
        return {
            "selection_key": self._catalog.selection_key_for(candidate),
        }

    def _render_model_json_sync(self, asset_path: str) -> str:
        candidate = self._catalog.candidate_for_model_asset(asset_path)
        if candidate is None:
            raise FileNotFoundError(asset_path)

        model_data = self._metadata.load_model_data(candidate)
        metadata = self._metadata.discover_metadata(candidate, model_data)
        patched_model_data = self._metadata.patch_model_data(
            candidate,
            model_data,
            metadata,
        )
        return json.dumps(patched_model_data, ensure_ascii=False)

    def _save_directory_sync(
        self,
        uploaded_files: list[Live2DUploadFile],
    ) -> dict[str, Any]:
        target_directory = self._uploads.save_directory(uploaded_files)
        try:
            live2d_config = self._build_config_sync()
            if live2d_config is None:
                raise ValueError("No Live2D model was found after upload")
            return live2d_config
        except Exception:
            shutil.rmtree(target_directory, ignore_errors=True)
            raise

    def _save_annotation_sync(
        self,
        selection_key: str,
        kind: str,
        file: str,
        note: str,
    ) -> dict[str, Any]:
        candidate = self._catalog.candidate_from_selection_key(selection_key)
        if candidate is None:
            raise ValueError(f"Unknown Live2D model: {selection_key}")
        self._ensure_workspace_model(candidate)

        normalized_kind = str(kind or "").strip().lower()
        if normalized_kind not in {"expression", "motion"}:
            raise ValueError("Live2D annotation kind must be expression or motion")

        normalized_file = self._metadata.normalize_annotation_file(file)
        model_data = self._metadata.load_model_data(candidate)
        metadata = self._metadata.discover_metadata(candidate, model_data)
        available_files = {
            item.file
            for item in (
                metadata.expressions
                if normalized_kind == "expression"
                else metadata.motions
            )
        }
        if normalized_file not in available_files:
            raise ValueError(f"Unknown Live2D {normalized_kind}: {normalized_file}")

        normalized_note = str(note or "").strip()
        self._annotations_repository.save_annotation(
            candidate.runtime_root,
            kind=normalized_kind,
            file=normalized_file,
            note=normalized_note,
        )
        return {
            "selection_key": self._catalog.selection_key_for(candidate),
            "kind": normalized_kind,
            "file": normalized_file,
            "note": normalized_note,
        }

    def _save_hotkey_sync(
        self,
        selection_key: str,
        hotkey_key: str,
        shortcut_tokens: list[str],
        restore_default: bool = False,
    ) -> dict[str, Any]:
        candidate = self._catalog.candidate_from_selection_key(selection_key)
        if candidate is None:
            raise ValueError(f"Unknown Live2D model: {selection_key}")
        self._ensure_workspace_model(candidate)

        model_data = self._metadata.load_model_data(candidate)
        metadata = self._metadata.discover_metadata(candidate, model_data)
        normalized_hotkey_key = str(hotkey_key or "").strip()
        discovered_hotkey = self._find_hotkey(metadata.hotkeys, normalized_hotkey_key)
        if discovered_hotkey is None:
            raise ValueError(f"Unknown Live2D hotkey: {hotkey_key}")

        normalized_shortcut_tokens = self._metadata.normalize_shortcut_tokens(shortcut_tokens)
        self._annotations_repository.save_hotkey(
            candidate.runtime_root,
            hotkey_key=normalized_hotkey_key,
            shortcut_tokens=normalized_shortcut_tokens,
            restore_default=restore_default,
        )

        refreshed_metadata = self._metadata.discover_metadata(candidate, model_data)
        updated_hotkey = self._find_hotkey(refreshed_metadata.hotkeys, normalized_hotkey_key)
        if updated_hotkey is None:
            raise ValueError(f"Unknown Live2D hotkey: {hotkey_key}")

        return {
            "selection_key": self._catalog.selection_key_for(candidate),
            **self._metadata.hotkey_payload(updated_hotkey),
        }

    def _build_model_option(self, candidate: Live2DModelCandidate) -> dict[str, Any]:
        model_data = self._metadata.load_model_data(candidate)
        metadata = self._metadata.discover_metadata(candidate, model_data)
        parameter_ids = self._metadata.load_parameter_ids(candidate, model_data)
        lip_sync_parameter_ids = self._resolve_lip_sync_parameter_ids(model_data, parameter_ids)
        mouth_form_parameter_id = self._resolve_mouth_form_parameter_id(parameter_ids)

        return {
            "source": candidate.source,
            "selection_key": self._catalog.selection_key_for(candidate),
            "model_name": candidate.model_name,
            "model_url": self._catalog.asset_url_for(
                candidate,
                candidate.model_relative_path.as_posix(),
            ),
            "directory_name": self._catalog.directory_name_for(candidate),
            "lip_sync_parameter_ids": lip_sync_parameter_ids,
            "mouth_form_parameter_id": mouth_form_parameter_id,
            "expressions": [
                {
                    "name": expression.name,
                    "file": expression.file,
                    "url": self._catalog.asset_url_for(candidate, expression.asset_relative_path),
                    "note": expression.note,
                }
                for expression in metadata.expressions
            ],
            "motions": [
                {
                    "name": motion.name,
                    "file": motion.file,
                    "url": self._catalog.asset_url_for(candidate, motion.asset_relative_path),
                    "note": motion.note,
                    "group": motion.group,
                    "index": motion.index,
                }
                for motion in metadata.motions
            ],
            "hotkeys": [
                self._metadata.hotkey_payload(hotkey)
                for hotkey in metadata.hotkeys
            ],
            "annotations_writable": metadata.annotations_writable,
        }

    def _resolve_lip_sync_parameter_ids(
        self,
        model_data: dict[str, Any],
        parameter_ids: list[str],
    ) -> list[str]:
        group_ids = self._metadata.load_group_parameter_ids(model_data, "LipSync")
        if group_ids:
            return group_ids

        inferred_ids = [
            parameter_id
            for parameter_id in parameter_ids
            if "MouthOpen" in parameter_id
        ]
        if inferred_ids:
            return inferred_ids

        fallback_ids = [
            parameter_id
            for parameter_id in DEFAULT_LIP_SYNC_PARAMETER_IDS
            if parameter_id in parameter_ids
        ]
        if fallback_ids:
            return fallback_ids

        return DEFAULT_LIP_SYNC_PARAMETER_IDS[:]

    @staticmethod
    def _resolve_mouth_form_parameter_id(parameter_ids: list[str]) -> str | None:
        for parameter_id in parameter_ids:
            if "MouthForm" in parameter_id:
                return parameter_id

        for parameter_id in DEFAULT_MOUTH_FORM_PARAMETER_IDS:
            if parameter_id in parameter_ids:
                return parameter_id
        return None

    @staticmethod
    def _find_hotkey(
        hotkeys: tuple[Live2DDiscoveredHotkey, ...],
        hotkey_key: str,
    ) -> Live2DDiscoveredHotkey | None:
        return next(
            (
                hotkey
                for hotkey in hotkeys
                if hotkey.hotkey_key == hotkey_key
            ),
            None,
        )

    @staticmethod
    def _ensure_workspace_model(candidate: Live2DModelCandidate) -> None:
        if candidate.source != LIVE2D_SOURCE_WORKSPACE:
            raise ValueError("Built-in Live2D models are read-only")
