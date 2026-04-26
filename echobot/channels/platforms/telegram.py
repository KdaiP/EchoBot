from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from ..base import BaseChannel
from ..types import OutboundMessage
from ...models import (
    FILE_ATTACHMENT_CONTENT_BLOCK_TYPE,
    IMAGE_URL_CONTENT_BLOCK_TYPE,
    TEXT_CONTENT_BLOCK_TYPE,
    message_content_blocks,
)

try:
    from telegram import BotCommand, Update
    from telegram.error import Conflict, TelegramError
    from telegram.ext import Application, ContextTypes, MessageHandler, filters
    from telegram.request import HTTPXRequest

    TELEGRAM_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised in runtime environments
    BotCommand = Any
    Conflict = Any
    TelegramError = Any
    Application = Any
    ContextTypes = Any
    HTTPXRequest = Any
    MessageHandler = Any
    Update = Any
    filters = None
    TELEGRAM_AVAILABLE = False

if TYPE_CHECKING:  # pragma: no cover
    from telegram.ext import Application as TelegramApplication


logger = logging.getLogger(__name__)

_MAX_MESSAGE_LENGTH = 4000
if TELEGRAM_AVAILABLE:
    _BOT_COMMANDS = [
        BotCommand("new", "Start a new session"),
        BotCommand("ls", "List sessions"),
        BotCommand("switch", "Switch to another session"),
        BotCommand("rename", "Rename the current session"),
        BotCommand("delete", "Delete the current session"),
        BotCommand("current", "Show current session"),
        BotCommand("route", "Show or switch route mode"),
        BotCommand("help", "Show all commands"),
    ]
else:
    _BOT_COMMANDS = []


class TelegramChannel(BaseChannel):
    name = "telegram"

    def __init__(self, config: Any, bus, attachment_store=None) -> None:
        super().__init__(config, bus, attachment_store=attachment_store)
        self._app: "TelegramApplication | None" = None

    async def start(self) -> None:
        if not TELEGRAM_AVAILABLE:
            logger.error(
                "Telegram channel requires python-telegram-bot. "
                "Install it before enabling telegram.",
            )
            return
        if not self.config.bot_token:
            logger.error("Telegram channel is missing bot_token")
            return

        self._running = True
        request = HTTPXRequest(
            connection_pool_size=16,
            pool_timeout=5.0,
            connect_timeout=30.0,
            read_timeout=30.0,
        )
        builder = (
            Application.builder()
            .token(self.config.bot_token)
            .request(request)
            .get_updates_request(request)
        )
        if self.config.proxy:
            builder = builder.proxy(self.config.proxy).get_updates_proxy(
                self.config.proxy,
            )
        self._app = builder.build()
        self._app.add_error_handler(self._on_error)
        self._app.add_handler(
            MessageHandler(
                filters.ALL,
                self._on_message,
            ),
        )
        await self._app.initialize()
        await self._app.start()
        try:
            await self._app.bot.set_my_commands(_BOT_COMMANDS)
        except Exception:
            logger.debug("Telegram command registration failed", exc_info=True)
        await self._app.updater.start_polling(
            allowed_updates=["message"],
            drop_pending_updates=True,
            error_callback=self._on_polling_error,
        )
        logger.info("Telegram channel started")
        try:
            while self._running:
                await asyncio.sleep(1)
        finally:
            await self.stop()

    async def stop(self) -> None:
        self._running = False
        if self._app is None:
            return
        logger.info("Stopping Telegram channel")
        updater = getattr(self._app, "updater", None)
        try:
            if updater is not None:
                await updater.stop()
        except Exception:
            logger.debug("Telegram updater stop failed", exc_info=True)
        try:
            await self._app.stop()
        except Exception:
            logger.debug("Telegram app stop failed", exc_info=True)
        try:
            await self._app.shutdown()
        except Exception:
            logger.debug("Telegram app shutdown failed", exc_info=True)
        self._app = None

    async def send(self, message: OutboundMessage) -> None:
        if self._app is None:
            logger.warning("Telegram channel is not running")
            return
        chat_id = _as_chat_id(message.address.chat_id)
        reply_to_message_id = None
        if self.config.reply_to_message and not message.metadata.get("scheduled"):
            raw_message_id = message.metadata.get("message_id")
            if raw_message_id is not None:
                try:
                    reply_to_message_id = int(raw_message_id)
                except (TypeError, ValueError):
                    reply_to_message_id = None
        blocks = message_content_blocks(message.content or message.text)
        if not blocks:
            return

        for block in blocks:
            block_type = str(block.get("type", "")).strip()
            if block_type == TEXT_CONTENT_BLOCK_TYPE:
                for chunk in _split_text(str(block.get("text", ""))):
                    await self._app.bot.send_message(
                        chat_id=chat_id,
                        text=chunk,
                        **_reply_kwargs(reply_to_message_id),
                    )
                continue
            if block_type == IMAGE_URL_CONTENT_BLOCK_TYPE:
                await self._send_image_block(
                    chat_id,
                    block.get("image_url"),
                    reply_to_message_id=reply_to_message_id,
                )
                continue
            if block_type == FILE_ATTACHMENT_CONTENT_BLOCK_TYPE:
                await self._send_file_block(
                    chat_id,
                    block.get("file_attachment"),
                    reply_to_message_id=reply_to_message_id,
                )

    async def _send_image_block(
        self,
        chat_id: int | str,
        image_payload: Any,
        *,
        reply_to_message_id: int | None,
    ) -> None:
        if self._app is None:
            return

        attachment_id = ""
        image_url = ""
        if isinstance(image_payload, dict):
            attachment_id = str(image_payload.get("attachment_id", "")).strip()
            image_url = str(
                image_payload.get("preview_url") or image_payload.get("url") or ""
            ).strip()

        photo: str | None = None
        if attachment_id and self.attachment_store is not None:
            try:
                photo = str(
                    await asyncio.to_thread(
                        self.attachment_store.image_attachment_path,
                        attachment_id,
                    )
                )
            except ValueError:
                logger.warning(
                    "Telegram image attachment is missing: %s",
                    attachment_id,
                )
        elif image_url.startswith(("http://", "https://")):
            photo = image_url

        if not photo:
            fallback_text = image_url or "[image]"
            for chunk in _split_text(fallback_text):
                await self._app.bot.send_message(
                    chat_id=chat_id,
                    text=chunk,
                    **_reply_kwargs(reply_to_message_id),
                )
            return

        await self._app.bot.send_photo(
            chat_id=chat_id,
            photo=photo,
            **_reply_kwargs(reply_to_message_id),
        )

    async def _send_file_block(
        self,
        chat_id: int | str,
        file_payload: Any,
        *,
        reply_to_message_id: int | None,
    ) -> None:
        if self._app is None:
            return

        attachment_id = ""
        download_url = ""
        file_name = "file"
        if isinstance(file_payload, dict):
            attachment_id = str(file_payload.get("attachment_id", "")).strip()
            download_url = str(file_payload.get("download_url", "")).strip()
            file_name = str(file_payload.get("name", "")).strip() or "file"

        document: str | None = None
        if attachment_id and self.attachment_store is not None:
            try:
                document = str(
                    await asyncio.to_thread(
                        self.attachment_store.file_attachment_path,
                        attachment_id,
                    )
                )
            except ValueError:
                logger.warning(
                    "Telegram file attachment is missing: %s",
                    attachment_id,
                )
        elif download_url.startswith(("http://", "https://")):
            document = download_url

        if not document:
            fallback_text = download_url or f"file: {file_name}"
            for chunk in _split_text(fallback_text):
                await self._app.bot.send_message(
                    chat_id=chat_id,
                    text=chunk,
                    **_reply_kwargs(reply_to_message_id),
                )
            return

        await self._app.bot.send_document(
            chat_id=chat_id,
            document=document,
            filename=file_name,
            **_reply_kwargs(reply_to_message_id),
        )

    async def _on_message(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        del context
        if not update.message or not update.effective_user:
            return
        user = update.effective_user
        if getattr(user, "is_bot", False):
            return
        sender_id = _sender_id(user)
        if not self.should_accept_sender(sender_id):
            return
        text = (update.message.text or update.message.caption or "").strip()
        image_urls = await self._extract_image_urls(update.message)
        file_inputs = await self._extract_file_inputs(update.message)
        if not text and not image_urls and not file_inputs:
            return
        await self._publish_inbound_message(
            sender_id=sender_id,
            chat_id=str(update.message.chat_id),
            user_id=str(user.id),
            text=text,
            image_urls=image_urls,
            files=file_inputs,
            metadata={
                "message_id": update.message.message_id,
                "username": user.username,
                "first_name": user.first_name,
                "is_group": update.message.chat.type != "private",
            },
        )

    async def _on_error(
        self,
        update: object,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        del update
        logger.error("Telegram error: %s", context.error)

    def _on_polling_error(self, error: TelegramError) -> None:
        if isinstance(error, Conflict):
            logger.error(
                "Telegram polling conflict: another bot instance is already "
                "using getUpdates for this token. Stop the other instance or "
                "run only one gateway process.",
            )
            if self._running:
                asyncio.get_running_loop().create_task(self.stop())
            return
        logger.error(
            "Telegram polling error: %s",
            error,
            exc_info=(type(error), error, error.__traceback__),
        )

    async def _extract_image_urls(self, message: Any) -> list[dict[str, str]]:
        image_urls: list[dict[str, str]] = []

        photo_sizes = list(getattr(message, "photo", []) or [])
        if photo_sizes:
            image_url = await self._download_telegram_image(
                photo_sizes[-1],
                content_type="image/jpeg",
                filename="telegram-photo.jpg",
            )
            if image_url:
                image_urls.append(image_url)

        document = getattr(message, "document", None)
        document_content_type = str(getattr(document, "mime_type", "") or "").strip()
        if document is not None and document_content_type.startswith("image/"):
            image_url = await self._download_telegram_image(
                document,
                content_type=document_content_type,
                filename=getattr(document, "file_name", None),
            )
            if image_url:
                image_urls.append(image_url)

        return image_urls

    async def _extract_file_inputs(self, message: Any) -> list[dict[str, str]]:
        file_inputs: list[dict[str, str]] = []

        document = getattr(message, "document", None)
        document_content_type = str(getattr(document, "mime_type", "") or "").strip()
        if document is not None and not document_content_type.startswith("image/"):
            file_input = await self._download_telegram_file_attachment(
                document,
                content_type=document_content_type,
                filename=getattr(document, "file_name", None),
            )
            if file_input:
                file_inputs.append(file_input)

        audio = getattr(message, "audio", None)
        if audio is not None:
            file_input = await self._download_telegram_file_attachment(
                audio,
                content_type=getattr(audio, "mime_type", None),
                filename=getattr(audio, "file_name", None) or "telegram-audio",
            )
            if file_input:
                file_inputs.append(file_input)

        video = getattr(message, "video", None)
        if video is not None:
            file_input = await self._download_telegram_file_attachment(
                video,
                content_type=getattr(video, "mime_type", None),
                filename=getattr(video, "file_name", None) or "telegram-video.mp4",
            )
            if file_input:
                file_inputs.append(file_input)

        voice = getattr(message, "voice", None)
        if voice is not None:
            file_input = await self._download_telegram_file_attachment(
                voice,
                content_type=getattr(voice, "mime_type", None) or "audio/ogg",
                filename="telegram-voice.ogg",
            )
            if file_input:
                file_inputs.append(file_input)

        return file_inputs

    async def _download_telegram_image(
        self,
        attachment: Any,
        *,
        content_type: str | None,
        filename: str | None,
    ) -> dict[str, str] | None:
        if self.attachment_store is None:
            logger.warning("Telegram channel is missing attachment_store")
            return None

        if _attachment_exceeds_size_limit(
            attachment,
            max_bytes=self.attachment_store.image_budget.max_input_bytes,
        ):
            logger.warning("Telegram image attachment exceeds size limit")
            return None

        try:
            telegram_file = await attachment.get_file()
            image_bytes = bytes(await telegram_file.download_as_bytearray())
        except Exception:
            logger.warning("Failed to download Telegram image attachment", exc_info=True)
            return None

        if not image_bytes:
            return None

        try:
            image_attachment = await asyncio.to_thread(
                self.attachment_store.create_image_attachment,
                image_bytes,
                content_type=content_type,
                filename=filename,
            )
            return image_attachment.to_message_image()
        except ValueError:
            logger.warning("Failed to normalize Telegram image attachment", exc_info=True)
            return None

    async def _download_telegram_file_attachment(
        self,
        attachment: Any,
        *,
        content_type: str | None,
        filename: str | None,
    ) -> dict[str, str] | None:
        if self.attachment_store is None:
            logger.warning("Telegram channel is missing attachment_store")
            return None

        if _attachment_exceeds_size_limit(
            attachment,
            max_bytes=self.attachment_store.file_budget.max_input_bytes,
        ):
            logger.warning("Telegram file attachment exceeds size limit")
            return None

        try:
            telegram_file = await attachment.get_file()
            file_bytes = bytes(await telegram_file.download_as_bytearray())
        except Exception:
            logger.warning("Failed to download Telegram file attachment", exc_info=True)
            return None

        if not file_bytes:
            return None

        try:
            file_attachment = await asyncio.to_thread(
                self.attachment_store.create_file_attachment,
                file_bytes,
                content_type=content_type,
                filename=filename,
            )
            return {"attachment_id": file_attachment.attachment_id}
        except ValueError:
            logger.warning("Failed to persist Telegram file attachment", exc_info=True)
            return None


def _split_text(text: str) -> list[str]:
    cleaned = text.strip()
    if not cleaned:
        return []
    if len(cleaned) <= _MAX_MESSAGE_LENGTH:
        return [cleaned]

    chunks: list[str] = []
    remaining = cleaned
    while remaining:
        if len(remaining) <= _MAX_MESSAGE_LENGTH:
            chunks.append(remaining)
            break
        boundary = remaining.rfind("\n", 0, _MAX_MESSAGE_LENGTH)
        if boundary < _MAX_MESSAGE_LENGTH // 2:
            boundary = remaining.rfind(" ", 0, _MAX_MESSAGE_LENGTH)
        if boundary < _MAX_MESSAGE_LENGTH // 2:
            boundary = _MAX_MESSAGE_LENGTH
        chunks.append(remaining[:boundary].strip())
        remaining = remaining[boundary:].strip()
    return [chunk for chunk in chunks if chunk]


def _reply_kwargs(reply_to_message_id: int | None) -> dict[str, Any]:
    if reply_to_message_id is None:
        return {}
    return {"reply_to_message_id": reply_to_message_id}


def _sender_id(user: Any) -> str:
    sender_id = str(getattr(user, "id", "")).strip() or "unknown"
    username = str(getattr(user, "username", "") or "").strip()
    if username:
        return f"{sender_id}|{username}"
    return sender_id


def _as_chat_id(raw_value: str) -> int | str:
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return raw_value


def _attachment_exceeds_size_limit(attachment: Any, *, max_bytes: int) -> bool:
    raw_size = getattr(attachment, "file_size", None)
    try:
        size_bytes = int(raw_size)
    except (TypeError, ValueError):
        return False
    return size_bytes > max_bytes
