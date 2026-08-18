from __future__ import annotations

import os
from pathlib import Path
from pathlib import PurePosixPath
from typing import Iterable
from urllib.parse import quote

from .constants import (
    DEFAULT_LIP_SYNC_PARAMETER_IDS,
    LIVE2D_SOURCE_BUILTIN,
    LIVE2D_SOURCE_WORKSPACE,
)
from .models import Live2DModelCandidate


class Live2DModelCatalog:
    def __init__(self, workspace_root: Path, builtin_root: Path) -> None:
        self._workspace_root = workspace_root
        self._builtin_root = builtin_root

    def empty_config(self) -> dict[str, object]:
        return {
            "available": False,
            "source": "",
            "selection_key": "",
            "model_name": "",
            "model_url": "",
            "directory_name": "",
            "lip_sync_parameter_ids": DEFAULT_LIP_SYNC_PARAMETER_IDS[:],
            "mouth_form_parameter_id": None,
            "expressions": [],
            "motions": [],
            "hotkeys": [],
            "annotations_writable": False,
            "models": [],
        }

    def discover_model_candidates(self) -> list[Live2DModelCandidate]:
        candidates: list[Live2DModelCandidate] = []
        for source, root in self._roots():
            if not root.exists():
                continue

            model_paths = sorted(
                root.rglob("*.model3.json"),
                key=lambda path: (len(path.parts), path.as_posix()),
            )
            for model_path in model_paths:
                candidate = self._candidate_from_path(source, root, model_path)
                if candidate is not None:
                    candidates.append(candidate)
        return candidates

    def select_default_candidate(
        self,
        candidates: list[Live2DModelCandidate],
    ) -> Live2DModelCandidate:
        preferred_model = os.environ.get("ECHOBOT_WEB_LIVE2D_MODEL", "").strip()
        if not preferred_model:
            return candidates[0]

        normalized_preference = preferred_model.replace("\\", "/").strip("/").casefold()
        for candidate in candidates:
            if self._matches_preferred_model(candidate, normalized_preference):
                return candidate
        return candidates[0]

    def resolve_asset(self, asset_path: str) -> Path:
        source, relative_path = self.parse_asset_path(asset_path)
        resolved_path = self._resolve_under_root(self._root_for(source), relative_path)
        if resolved_path is None:
            raise ValueError(f"Invalid live2d asset path: {asset_path}")
        if not resolved_path.is_file():
            raise FileNotFoundError(asset_path)
        return resolved_path

    def candidate_from_selection_key(
        self,
        selection_key: str,
    ) -> Live2DModelCandidate | None:
        normalized_selection_key = str(selection_key or "").strip()
        if not normalized_selection_key:
            return None

        source = LIVE2D_SOURCE_WORKSPACE
        model_path_text = normalized_selection_key
        if ":" in normalized_selection_key:
            source, model_path_text = normalized_selection_key.split(":", 1)

        if source not in {LIVE2D_SOURCE_WORKSPACE, LIVE2D_SOURCE_BUILTIN}:
            return None

        relative_path = self._normalize_relative_path(model_path_text)
        if relative_path is None:
            return None

        root = self._root_for(source)
        resolved_path = self._resolve_under_root(root, relative_path)
        if resolved_path is None:
            return None
        if not resolved_path.is_file() or not resolved_path.name.endswith(".model3.json"):
            return None

        return self._candidate_from_path(source, root, resolved_path)

    def candidate_for_model_asset(self, asset_path: str) -> Live2DModelCandidate | None:
        try:
            source, relative_path = self.parse_asset_path(asset_path)
        except ValueError:
            return None

        if not relative_path.name.endswith(".model3.json"):
            return None

        root = self._root_for(source)
        resolved_path = self._resolve_under_root(root, relative_path)
        if resolved_path is None or not resolved_path.is_file():
            return None

        return self._candidate_from_path(source, root, resolved_path)

    def selection_key_for(self, candidate: Live2DModelCandidate) -> str:
        return f"{candidate.source}:{candidate.model_relative_path.as_posix()}"

    def asset_url_for(self, candidate: Live2DModelCandidate, relative_path: str) -> str:
        # 注意：此处 replace('\\', '/') 不能直接写在 f-string 的表达式内，
        # 因为 Python < 3.12 不允许 f-string 表达式部分含有反斜杠（PEP 701）
        normalized = str(relative_path or "").replace("\\", "/")
        return (
            f"/api/web/live2d/{candidate.source}/"
            f"{quote(normalized, safe='/')}"
        )

    def directory_name_for(self, candidate: Live2DModelCandidate) -> str:
        runtime_relative_path = candidate.runtime_relative_path
        if len(runtime_relative_path.parts) > 1:
            return runtime_relative_path.parts[0]
        if len(runtime_relative_path.parts) == 1:
            return runtime_relative_path.name
        return candidate.model_name

    def parse_asset_path(self, asset_path: str) -> tuple[str, Path]:
        raw_path = str(asset_path or "").replace("\\", "/").strip()
        if not raw_path:
            raise ValueError("Live2D asset path must not be empty")

        normalized_path = self._normalize_relative_path(raw_path)
        if normalized_path is None:
            raise ValueError(f"Invalid live2d asset path: {asset_path}")

        source = normalized_path.parts[0]
        if source in {LIVE2D_SOURCE_WORKSPACE, LIVE2D_SOURCE_BUILTIN}:
            relative_path = Path(*normalized_path.parts[1:])
            if not relative_path.parts:
                raise ValueError(f"Invalid live2d asset path: {asset_path}")
            return source, relative_path

        return LIVE2D_SOURCE_WORKSPACE, normalized_path

    def _roots(self) -> Iterable[tuple[str, Path]]:
        return (
            (LIVE2D_SOURCE_WORKSPACE, self._workspace_root),
            (LIVE2D_SOURCE_BUILTIN, self._builtin_root),
        )

    def _root_for(self, source: str) -> Path:
        if source == LIVE2D_SOURCE_BUILTIN:
            return self._builtin_root
        return self._workspace_root

    def _candidate_from_path(
        self,
        source: str,
        root: Path,
        model_path: Path,
    ) -> Live2DModelCandidate | None:
        resolved_root = root.resolve()
        resolved_model_path = model_path.resolve()
        if resolved_model_path != resolved_root and resolved_root not in resolved_model_path.parents:
            return None
        if not resolved_model_path.name.endswith(".model3.json"):
            return None

        return Live2DModelCandidate(
            source=source,
            source_root=resolved_root,
            model_path=resolved_model_path,
            runtime_root=resolved_model_path.parent,
        )

    @staticmethod
    def _normalize_relative_path(path_text: str) -> Path | None:
        normalized_text = str(path_text or "").replace("\\", "/").strip()
        if not normalized_text or normalized_text.startswith("/"):
            return None

        normalized_path = PurePosixPath(normalized_text)
        if not normalized_path.parts:
            return None
        if any(part in {"", ".", ".."} for part in normalized_path.parts):
            return None
        if any(":" in part for part in normalized_path.parts):
            return None

        return Path(*normalized_path.parts)

    @staticmethod
    def _resolve_under_root(root: Path, relative_path: Path) -> Path | None:
        resolved_root = root.resolve()
        resolved_path = (resolved_root / relative_path).resolve()
        if resolved_path != resolved_root and resolved_root not in resolved_path.parents:
            return None
        return resolved_path

    def _matches_preferred_model(
        self,
        candidate: Live2DModelCandidate,
        normalized_preference: str,
    ) -> bool:
        model_relative_path = candidate.model_relative_path.as_posix().casefold()
        model_parent_path = candidate.model_relative_path.parent.as_posix().casefold()
        runtime_relative_path = candidate.runtime_relative_path.as_posix().casefold()
        directory_name = self.directory_name_for(candidate).casefold()
        model_name = candidate.model_name.casefold()

        comparable_values = {
            model_relative_path,
            model_parent_path,
            runtime_relative_path,
            directory_name,
            model_name,
            f"{candidate.source}:{model_relative_path}",
            f"{candidate.source}:{runtime_relative_path}",
            f"{candidate.source}:{directory_name}",
            f"{candidate.source}/{model_relative_path}",
            f"{candidate.source}/{runtime_relative_path}",
            f"{candidate.source}/{directory_name}",
        }
        return normalized_preference in comparable_values
