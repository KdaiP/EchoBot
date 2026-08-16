from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..models import LLMMessage
from ..skill_support import SkillRegistry
from ..tools import ToolRegistry


@dataclass(slots=True)
class PreparedSkills:
    tool_registry: ToolRegistry | None
    extra_system_messages: list[str]
    persistent_messages: list[LLMMessage]


def prepare_skills(
    skill_registry: SkillRegistry | None,
    tool_registry: ToolRegistry | None,
    *,
    prompt: str,
    history: Sequence[LLMMessage],
    extra_system_messages: Sequence[str],
) -> PreparedSkills:
    if skill_registry is None:
        return PreparedSkills(
            tool_registry=tool_registry,
            extra_system_messages=list(extra_system_messages),
            persistent_messages=[],
        )

    active_names = skill_registry.active_skill_names_from_history(history)
    explicit_names = skill_registry.explicit_skill_names(prompt)
    activation_messages = skill_registry.build_explicit_activation_messages(
        prompt,
        active_skill_names=active_names,
    )
    all_active_names = list(active_names)
    for name in explicit_names:
        if name not in all_active_names:
            all_active_names.append(name)

    system_messages = list(extra_system_messages)
    catalog = skill_registry.build_catalog_prompt(active_skill_names=all_active_names)
    if catalog:
        system_messages.append(catalog)

    combined_registry = tool_registry.copy() if tool_registry is not None else ToolRegistry()
    combined_registry.register_many(
        skill_registry.create_tools(active_skill_names=all_active_names)
    )
    return PreparedSkills(
        tool_registry=combined_registry if combined_registry.names() else None,
        extra_system_messages=system_messages,
        persistent_messages=[
            LLMMessage(role="system", content=content)
            for content in activation_messages
        ],
    )
