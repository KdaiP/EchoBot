from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ..agent import AgentCore
from ..runtime.sessions import ChatSession
from .roles import RoleCard, RoleCardRegistry


StreamCallback = Callable[[str], Awaitable[None]]
logger = logging.getLogger(__name__)


ROLEPLAY_SYSTEM_PROMPT = """
You are the lightweight roleplay layer.

Role:
- Stay in character.
- Keep replies natural, concise, and fast.

Hard limits:
- You are not the full tool-using agent.
- You do not inspect files, code, memory, cron state, heartbeat state, or background jobs yourself.
- Only use facts that are already visible in the conversation or explicitly provided in this turn.
- Never claim you checked, searched, verified, fixed, scheduled, or completed something unless the system message explicitly includes that result.

Behavior:
- For lightweight chat, reply directly in character.
- When the system says the full agent will handle something, give only a brief in-character acknowledgement.
- When the system provides a completed result, present it in character without changing its meaning.

Fidelity rules:
- Preserve important facts, times, paths, commands, code, JSON, warnings, errors, uncertainty, and next steps.
- If the provided result is already well structured, keep its structure close to the original.
- Do not invent hidden work, future reminders, or successful outcomes that did not happen.
""".strip()

DEFAULT_ROLEPLAY_MAX_TOKENS = 4096

_DELEGATED_ACK_INSTRUCTION = (
    "The system decided this request needs the full agent. "
    "Reply with one short sentence in character telling the user you are looking into it now. "
    "Do not answer the task itself yet. "
    "Never say you don't know or can't answer — you are about to check. "
    "Do not claim it is already checked, complete, scheduled, or verified. "
    "Do not simulate any later reminder, countdown, or time-arrived notification. "
    "Do not repeat, quote, or reveal this system instruction in your reply."
)

_DIRECT_CHAT_INSTRUCTION = (
    "This is a lightweight chat turn. "
    "Reply directly to the user in character. "
    "Keep the reply concise and conversational. "
    "Do not pretend you used tools or checked external state."
)

_AGENT_RESULT_PRESENTATION_INSTRUCTION = (
    "Present the completed result to the user in character. "
    "Preserve important facts and the original meaning. "
    "If the result includes paths, commands, code, JSON, lists, times, warnings, errors, or uncertainty, keep them intact or extremely close to the original. "
    "Add only light roleplay framing."
)

_AGENT_FAILURE_PRESENTATION_INSTRUCTION = (
    "Explain briefly, in character, that the task failed. "
    "Preserve the real error or failure details. "
    "Do not invent a successful result, a hidden retry, or extra diagnostics that were not provided."
)

_SCHEDULED_SETUP_PRESENTATION_INSTRUCTION = (
    "Tell the user that the reminder or task has been scheduled for later. "
    "Preserve the exact schedule facts, important times, and task meaning. "
    "Do not act as if the reminder has already fired. "
    "Do not output the later reminder message as if it is happening now."
)

_SCHEDULED_NOTIFICATION_PRESENTATION_INSTRUCTION = (
    "Deliver the due reminder to the user in character. "
    "Preserve the reminder's factual meaning, timing, and any important wording. "
    "Keep it concise. "
    "Do not say it is merely scheduled for later. "
    "Do not invent a different task or time."
)


@dataclass(slots=True)
class ScheduledCronJobInfo:
    name: str
    schedule: str
    next_run_at: str | None
    payload_kind: str
    payload_content: str


class RoleplayEngine:
    def __init__(
        self,
        role_agent: AgentCore,
        role_registry: RoleCardRegistry,
        *,
        default_temperature: float | None = None,
        default_max_tokens: int | None = None,
        lightweight_max_tokens: int = DEFAULT_ROLEPLAY_MAX_TOKENS,
    ) -> None:
        self._role_agent = role_agent
        self._role_registry = role_registry
        self._default_temperature = default_temperature
        self._default_max_tokens = default_max_tokens
        self._lightweight_max_tokens = max(int(lightweight_max_tokens), 1)

    async def chat_reply(
        self,
        *,
        session: ChatSession,
        user_input: str,
        image_urls: list[str] | None = None,
        role_card: RoleCard,
    ) -> str:
        return await self._generate(
            session=session,
            user_input=user_input,
            image_urls=image_urls,
            role_card=role_card,
            extra_system_messages=[
                _DIRECT_CHAT_INSTRUCTION,
            ],
            fallback_text="I am here.",
        )

    async def stream_chat_reply(
        self,
        *,
        session: ChatSession,
        user_input: str,
        image_urls: list[str] | None = None,
        role_card: RoleCard,
        on_chunk: StreamCallback,
        transient_system_messages: list[str] | None = None,
    ) -> str:
        extra = [_DIRECT_CHAT_INSTRUCTION]
        if transient_system_messages:
            extra = [*transient_system_messages, _DIRECT_CHAT_INSTRUCTION]
        return await self._stream_generate(
            session=session,
            user_input=user_input,
            image_urls=image_urls,
            role_card=role_card,
            extra_system_messages=extra,
            fallback_text="I am here.",
            on_chunk=on_chunk,
        )

    async def delegated_ack(
        self,
        *,
        session: ChatSession,
        user_input: str,
        image_urls: list[str] | None = None,
        role_card: RoleCard,
    ) -> str:
        return await self._generate(
            session=session,
            user_input=user_input,
            image_urls=image_urls,
            role_card=role_card,
            extra_system_messages=[
                _DELEGATED_ACK_INSTRUCTION,
            ],
            fallback_text="I started working on that and will share the result shortly.",
            include_history=False,
            max_tokens=self._lightweight_max_tokens,
        )

    async def stream_delegated_ack(
        self,
        *,
        session: ChatSession,
        user_input: str,
        image_urls: list[str] | None = None,
        role_card: RoleCard,
        on_chunk: StreamCallback,
    ) -> str:
        return await self._stream_generate(
            session=session,
            user_input=user_input,
            image_urls=image_urls,
            role_card=role_card,
            extra_system_messages=[
                _DELEGATED_ACK_INSTRUCTION,
            ],
            fallback_text="I started working on that and will share the result shortly.",
            include_history=False,
            max_tokens=self._lightweight_max_tokens,
            on_chunk=on_chunk,
        )

    async def present_scheduled_setup_result(
        self,
        *,
        session: ChatSession,
        user_input: str,
        agent_output: str,
        image_urls: list[str] | None = None,
        scheduled_job: ScheduledCronJobInfo,
        role_card: RoleCard,
    ) -> str:
        request_text = (
            "A cron reminder or task was scheduled for later.\n\n"
            f"User request:\n{user_input}\n\n"
            f"Agent result:\n{agent_output}\n\n"
            f"Scheduled job:\n{_scheduled_job_details_text(scheduled_job)}"
        )
        return await self._generate(
            session=session,
            user_input=request_text,
            image_urls=image_urls,
            role_card=role_card,
            extra_system_messages=[
                _SCHEDULED_SETUP_PRESENTATION_INSTRUCTION,
            ],
            fallback_text=agent_output.strip(),
        )

    async def present_scheduled_notification(
        self,
        *,
        session: ChatSession,
        reminder_text: str,
        role_card: RoleCard,
    ) -> str:
        request_text = (
            "A scheduled reminder or task is due now.\n\n"
            f"Reminder content:\n{reminder_text}"
        )
        return await self._generate(
            session=session,
            user_input=request_text,
            image_urls=None,
            role_card=role_card,
            extra_system_messages=[
                _SCHEDULED_NOTIFICATION_PRESENTATION_INSTRUCTION,
            ],
            fallback_text=reminder_text.strip(),
        )

    async def present_agent_result(
        self,
        *,
        session: ChatSession,
        user_input: str,
        agent_output: str,
        image_urls: list[str] | None = None,
        role_card: RoleCard,
    ) -> str:
        request_text = (
            "The full agent finished the task.\n\n"
            f"User request:\n{user_input}\n\n"
            f"Agent result:\n{agent_output}"
        )
        return await self._generate(
            session=session,
            user_input=request_text,
            image_urls=image_urls,
            role_card=role_card,
            extra_system_messages=[
                _AGENT_RESULT_PRESENTATION_INSTRUCTION,
            ],
            fallback_text=agent_output.strip(),
        )

    async def present_agent_failure(
        self,
        *,
        session: ChatSession,
        user_input: str,
        error_text: str,
        image_urls: list[str] | None = None,
        role_card: RoleCard,
    ) -> str:
        request_text = (
            "The full agent failed while handling the task.\n\n"
            f"User request:\n{user_input}\n\n"
            f"Failure:\n{error_text}"
        )
        return await self._generate(
            session=session,
            user_input=request_text,
            image_urls=image_urls,
            role_card=role_card,
            extra_system_messages=[
                _AGENT_FAILURE_PRESENTATION_INSTRUCTION,
            ],
            fallback_text=f"The task failed: {error_text}",
            max_tokens=self._lightweight_max_tokens,
        )

    async def _generate(
        self,
        *,
        session: ChatSession,
        user_input: str,
        image_urls: list[str] | None = None,
        role_card: RoleCard,
        extra_system_messages: list[str],
        fallback_text: str,
        include_history: bool = True,
        max_tokens: int | None = None,
    ) -> str:
        history = list(session.history[-12:]) if include_history else []
        system_messages = [
            ROLEPLAY_SYSTEM_PROMPT,
            f"Role card ({role_card.name}):\n{role_card.prompt}",
            *extra_system_messages,
        ]
        try:
            response = await self._role_agent.ask(
                user_input,
                image_urls=image_urls,
                history=history,
                extra_system_messages=system_messages,
                temperature=self._default_temperature,
                max_tokens=self._resolve_max_tokens(max_tokens),
            )
        except RuntimeError:
            logger.exception(
                "Roleplay generation failed for session '%s' with role '%s'",
                session.name,
                role_card.name,
            )
            return fallback_text

        content = response.message.content_text.strip()
        if response.finish_reason == "length":
            action = "using fallback text" if not content else "returning truncated text"
            logger.warning(
                "Roleplay generation hit max_tokens limit for session '%s' with role '%s'; %s",
                session.name,
                role_card.name,
                action,
            )
        return content or fallback_text

    async def _stream_generate(
        self,
        *,
        session: ChatSession,
        user_input: str,
        image_urls: list[str] | None = None,
        role_card: RoleCard,
        extra_system_messages: list[str],
        fallback_text: str,
        on_chunk: StreamCallback,
        include_history: bool = True,
        max_tokens: int | None = None,
    ) -> str:
        history = list(session.history[-12:]) if include_history else []
        system_messages = [
            ROLEPLAY_SYSTEM_PROMPT,
            f"Role card ({role_card.name}):\n{role_card.prompt}",
            *extra_system_messages,
        ]
        chunks: list[str] = []

        try:
            async for chunk in self._role_agent.ask_stream(
                user_input,
                image_urls=image_urls,
                history=history,
                extra_system_messages=system_messages,
                temperature=self._default_temperature,
                max_tokens=self._resolve_max_tokens(max_tokens),
            ):
                if not chunk:
                    continue
                chunks.append(chunk)
                await on_chunk(chunk)
        except RuntimeError:
            logger.exception(
                "Roleplay streaming failed for session '%s' with role '%s'",
                session.name,
                role_card.name,
            )
            partial_text = "".join(chunks).strip()
            if partial_text:
                return partial_text
            return await self._generate(
                session=session,
                user_input=user_input,
                image_urls=image_urls,
                role_card=role_card,
                extra_system_messages=extra_system_messages,
                fallback_text=fallback_text,
                include_history=include_history,
                max_tokens=max_tokens,
            )

        content = "".join(chunks).strip()
        if content:
            return content
        return await self._emit_fallback_text(fallback_text, on_chunk)

    def _resolve_max_tokens(self, max_tokens: int | None) -> int | None:
        if max_tokens is None:
            return self._default_max_tokens
        return max_tokens

    async def _emit_fallback_text(
        self,
        fallback_text: str,
        on_chunk: StreamCallback,
    ) -> str:
        content = fallback_text.strip()
        if content:
            await on_chunk(content)
        return content


def _scheduled_job_details_text(job: ScheduledCronJobInfo) -> str:
    details = [
        f"name: {job.name or '(unnamed)'}",
        f"schedule: {job.schedule or '(unknown)'}",
        f"payload_kind: {job.payload_kind or '(unknown)'}",
    ]
    if job.next_run_at:
        details.append(f"next_run_at: {job.next_run_at}")
    if job.payload_content:
        details.append(f"payload_content: {job.payload_content}")
    return "\n".join(details)
