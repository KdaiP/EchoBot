from __future__ import annotations

import argparse
import asyncio

from ..commands.saved_sessions import (
    SavedSessionCommandResult as SessionCommandResult,
    execute_saved_session_command,
    format_saved_session_help_lines,
    format_saved_session_list_lines,
    is_saved_session_command,
    parse_saved_session_command,
)
from ..runtime.session_service import SessionLifecycleService
from ..runtime.sessions import Session, SessionStore


def load_initial_session(
    session_store: SessionStore,
    args: argparse.Namespace,
) -> Session:
    if args.new_session:
        return session_store.create_session(args.new_session)

    if args.session:
        session = session_store.load_session(args.session)
        session_store.set_current_session(session.id)
        return session

    return session_store.load_current_session()


def is_session_command(prompt: str) -> bool:
    return is_saved_session_command(prompt)


async def handle_session_command_async(
    prompt: str,
    *,
    session_service: SessionLifecycleService,
    current_session: Session,
) -> SessionCommandResult:
    command = parse_saved_session_command(prompt)
    if command is None:
        raise ValueError("Unknown session command. Use /session help")
    return await execute_saved_session_command(
        session_service=session_service,
        current_session=current_session,
        command=command,
    )


def handle_session_command(
    prompt: str,
    *,
    session_store: SessionStore,
    current_session: Session,
) -> Session:
    result = asyncio.run(
        handle_session_command_async(
            prompt,
            session_service=SessionLifecycleService(session_store),
            current_session=current_session,
        )
    )
    print_session_command_result(result)
    return result.session


def print_session_help() -> None:
    _print_lines(format_saved_session_help_lines())


def print_sessions(
    session_store: SessionStore,
    *,
    current_session_id: str,
) -> None:
    _print_lines(
        format_saved_session_list_lines(
            session_store.list_sessions(),
            current_session_id=current_session_id,
        )
    )


def print_session_command_result(result: SessionCommandResult) -> None:
    _print_lines(result.lines)


def save_session_state(session_store: SessionStore, session: Session) -> None:
    session_store.save_session(session)
    session_store.set_current_session(session.id)


def clear_history(session_store: SessionStore, session: Session) -> None:
    session.history.clear()
    save_session_state(session_store, session)


def _print_lines(lines: list[str]) -> None:
    for line in lines:
        print(line)
