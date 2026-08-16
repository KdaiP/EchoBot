from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .orchestration.route_modes import RouteMode, normalize_route_mode


def resolve_attachment_images(
    attachment_store,
    attachment_inputs: Iterable[object],
) -> list[dict[str, str]]:
    images: list[dict[str, str]] = []
    for attachment_id in attachment_ids_from_inputs(attachment_inputs):
        attachment = attachment_store.get_image_attachment(attachment_id)
        images.append(attachment.to_message_image())
    return images


def resolve_attachment_files(
    attachment_store,
    workspace: Path,
    attachment_inputs: Iterable[object],
) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for attachment_id in attachment_ids_from_inputs(attachment_inputs):
        files.append(
            attachment_store.file_attachment_message_content(
                attachment_id,
                workspace=workspace,
            )
        )
    return files


def attachment_ids_from_inputs(values: Iterable[object]) -> list[str]:
    unique_values: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned_value = _attachment_id_from_input(value)
        if not cleaned_value or cleaned_value in seen:
            continue
        unique_values.append(cleaned_value)
        seen.add(cleaned_value)
    return unique_values


def resolve_file_attachment_route_mode(
    *,
    requested_route_mode: RouteMode | None,
    current_route_mode: RouteMode | None,
    has_file_attachments: bool,
    can_process_files: bool,
) -> RouteMode | None:
    if requested_route_mode in {"chat_only", "force_agent"}:
        return requested_route_mode

    if not has_file_attachments:
        return requested_route_mode

    base_route_mode = requested_route_mode or current_route_mode
    if normalize_route_mode(base_route_mode) != "auto":
        return requested_route_mode

    if can_process_files:
        return normalize_route_mode("force_agent")
    return requested_route_mode


def has_file_processing_capability(
    skill_registry,
    tool_registry_factory,
    session_id: str,
) -> bool:
    if skill_registry is not None and skill_registry.has_skills():
        return True

    if not callable(tool_registry_factory):
        return False

    tool_registry = tool_registry_factory(session_id, False)
    return tool_registry is not None and bool(tool_registry.names())


def _attachment_id_from_input(value: object) -> str:
    if isinstance(value, Mapping):
        return str(value.get("attachment_id", "")).strip()

    attachment_id = getattr(value, "attachment_id", None)
    if attachment_id is not None:
        return str(attachment_id).strip()

    return str(value or "").strip()
