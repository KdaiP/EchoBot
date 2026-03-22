from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..models import LLMMessage
from ..tools.base import BaseTool
from .models import Skill, SkillRuntimeState
from .parsing import (
    extract_active_skill_names_from_history,
    extract_explicit_skill_tokens,
    parse_skill_file,
)
from .tools import ActivateSkillTool, ListSkillResourcesTool, ReadSkillResourceTool


class SkillRegistry:
    def __init__(
        self,
        skills: list[Skill] | None = None,
        *,
        search_roots: list[Path] | None = None,
        warnings: list[str] | None = None,
    ) -> None:
        self._skills: dict[str, Skill] = {}
        self.search_roots = search_roots or []
        self.warnings = warnings or []

        if skills:
            for skill in skills:
                self.register(skill)

    @classmethod
    def discover(
        cls,
        *,
        project_root: str | Path = ".",
        client_name: str = "echobot",
        extra_roots: list[str | Path] | None = None,
        include_user_roots: bool = True,
    ) -> SkillRegistry:
        project_path = Path(project_root).resolve()
        search_roots = _build_default_search_roots(
            project_root=project_path,
            client_name=client_name,
            include_user_roots=include_user_roots,
        )
        if extra_roots:
            for root in reversed(extra_roots):
                search_roots.insert(0, Path(root).resolve())

        skills: list[Skill] = []
        warnings: list[str] = []
        seen_names: dict[str, Path] = {}

        for root in search_roots:
            if not root.exists():
                continue

            for skill_file in sorted(root.rglob("SKILL.md")):
                try:
                    skill = parse_skill_file(skill_file)
                except ValueError as exc:
                    warnings.append(f"{skill_file}: {exc}")
                    continue

                previous_file = seen_names.get(skill.name)
                if previous_file is not None:
                    warnings.append(
                        "Duplicate skill ignored: "
                        f"{skill.name} from {skill_file} "
                        f"(already loaded from {previous_file})"
                    )
                    continue

                seen_names[skill.name] = skill_file
                skills.append(skill)

        return cls(skills, search_roots=search_roots, warnings=warnings)

    def register(self, skill: Skill) -> None:
        if skill.name in self._skills:
            raise ValueError(f"Duplicate skill name: {skill.name}")

        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def require_skill(self, raw_name: Any) -> Skill:
        skill_name = str(raw_name or "").strip()
        if not skill_name:
            raise ValueError("name is required")

        skill = self.get(skill_name)
        if skill is None:
            raise ValueError(f"Unknown skill: {skill_name}")

        return skill

    def require_active_skill(
        self,
        raw_name: Any,
        *,
        runtime_state: SkillRuntimeState,
    ) -> Skill:
        skill = self.require_skill(raw_name)
        if not runtime_state.is_active(skill.name):
            raise ValueError(
                f"Skill is not active yet: {skill.name}. Activate it first with activate_skill."
            )

        return skill

    def names(self) -> list[str]:
        return sorted(self._skills)

    def has_skills(self) -> bool:
        return bool(self._skills)

    def create_activate_tool(
        self,
        *,
        active_skill_names: Sequence[str] | None = None,
    ) -> ActivateSkillTool | None:
        if not self._skills:
            return None

        runtime_state = SkillRuntimeState(list(active_skill_names or []))
        return ActivateSkillTool(self, runtime_state)

    def create_tools(
        self,
        *,
        active_skill_names: Sequence[str] | None = None,
    ) -> list[BaseTool]:
        if not self._skills:
            return []

        runtime_state = SkillRuntimeState(list(active_skill_names or []))
        return [
            ActivateSkillTool(self, runtime_state),
            ListSkillResourcesTool(self, runtime_state),
            ReadSkillResourceTool(self, runtime_state),
        ]

    def build_catalog_prompt(
        self,
        *,
        active_skill_names: Sequence[str] | None = None,
    ) -> str:
        if not self._skills:
            return ""

        lines = [
            "You can use project skills for specialized workflows.",
            "Only activate a skill when the task clearly benefits from its instructions.",
            "If the user explicitly mentions /skill-name or $skill-name, treat that skill as already active.",
            "If a skill is already active in the context, do not activate it again.",
        ]

        current_active_names = list(active_skill_names or [])
        if current_active_names:
            lines.append("Already active skills: " + ", ".join(sorted(current_active_names)))

        lines.extend(
            [
                "Available skills:",
                "<available_skills>",
            ]
        )
        for skill_name in self.names():
            lines.append(self._skills[skill_name].to_catalog_entry())
        lines.append("</available_skills>")
        lines.append("Use activate_skill to load a skill's main instructions.")
        lines.append("Use list_skill_resources only after a skill is active.")
        lines.append("Use read_skill_resource to load one bundled file only when needed.")
        return "\n".join(lines)

    def build_activation_message(self, skill_name: str) -> str:
        skill = self.require_skill(skill_name)
        return "The user explicitly activated this skill.\n" + skill.to_activation_text()

    def build_explicit_activation_messages(
        self,
        user_input: str,
        *,
        active_skill_names: Sequence[str] | None = None,
    ) -> list[str]:
        messages: list[str] = []
        seen_names = set(active_skill_names or [])

        for skill_name in self.explicit_skill_names(user_input):
            if skill_name in seen_names:
                continue

            messages.append(self.build_activation_message(skill_name))
            seen_names.add(skill_name)

        return messages

    def active_skill_names_from_history(
        self,
        history: Sequence[LLMMessage] | None,
    ) -> list[str]:
        return extract_active_skill_names_from_history(
            history,
            available_skill_names=set(self._skills),
        )

    def explicit_skill_names(self, text: str) -> list[str]:
        found_names: list[str] = []
        for token in extract_explicit_skill_tokens(text):
            if token in self._skills and token not in found_names:
                found_names.append(token)

        return found_names


def _build_default_search_roots(
    *,
    project_root: Path,
    client_name: str,
    include_user_roots: bool,
) -> list[Path]:
    roots = [
        project_root / "skills",
        project_root / f".{client_name}" / "skills",
        project_root / ".agents" / "skills",
        project_root / "echobot" / "skills",
    ]

    # PyInstaller: data files are extracted under sys._MEIPASS
    _meipass = getattr(sys, "_MEIPASS", None)
    if _meipass:
        roots.append(Path(_meipass) / "echobot" / "skills")

    if include_user_roots:
        home = Path.home()
        roots.extend(
            [
                home / f".{client_name}" / "skills",
                home / ".agents" / "skills",
            ]
        )

    return roots


__all__ = [
    "ActivateSkillTool",
    "ListSkillResourcesTool",
    "ReadSkillResourceTool",
    "Skill",
    "SkillRegistry",
    "parse_skill_file",
]
