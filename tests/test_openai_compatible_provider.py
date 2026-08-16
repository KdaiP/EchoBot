from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from openai import PermissionDeniedError

from echobot.models import LLMMessage, LLMTool, ToolCall
from echobot.providers.openai_compatible import (
    OpenAICompatibleProvider,
    OpenAICompatibleSettings,
)


class _DumpableResponse:
    def __init__(self, data: dict[str, object]) -> None:
        self._data = data

    def model_dump(self, *, mode: str) -> dict[str, object]:
        if mode != "json":
            raise AssertionError(f"unexpected dump mode: {mode}")
        return self._data


class _FakeAsyncStream:
    def __init__(self, chunks: list[object]) -> None:
        self._chunks = chunks
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        self.closed = True

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for chunk in self._chunks:
            yield chunk


class _FakeAsyncCompletions:
    def __init__(self, response: object) -> None:
        self.response = response
        self.create_kwargs: dict[str, object] = {}

    async def create(self, **kwargs):
        self.create_kwargs = kwargs
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


class OpenAICompatibleProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        settings = OpenAICompatibleSettings(
            api_key="test-key",
            model="test-model",
            base_url="https://example.com/v1",
        )
        self.provider = OpenAICompatibleProvider(settings)

    def test_client_configuration_is_delegated_to_openai_sdk(self) -> None:
        settings = OpenAICompatibleSettings(
            api_key="test-key",
            model="test-model",
            base_url="https://example.com/v1/",
            timeout=30,
            max_retries=4,
            extra_headers={"X-Test": "enabled"},
        )
        provider = OpenAICompatibleProvider(settings)
        fake_client = object()

        with patch(
            "echobot.providers.openai_compatible.AsyncOpenAI",
            return_value=fake_client,
        ) as constructor:
            first_client = provider._get_client()
            second_client = provider._get_client()

        self.assertIs(fake_client, first_client)
        self.assertIs(first_client, second_client)
        constructor.assert_called_once_with(
            api_key="test-key",
            base_url="https://example.com/v1",
            timeout=30,
            max_retries=4,
            default_headers={"X-Test": "enabled"},
        )

    def test_build_payload_keeps_supported_options(self) -> None:
        payload = self.provider._build_payload_sync(
            messages=[LLMMessage(role="user", content="hi")],
            tools=[
                LLMTool(
                    name="search_weather",
                    description="Search weather",
                    parameters={"type": "object", "properties": {}},
                )
            ],
            tool_choice="auto",
            temperature=0.3,
            max_tokens=200,
        )

        self.assertEqual("test-model", payload["model"])
        self.assertEqual("hi", payload["messages"][0]["content"])
        self.assertEqual("search_weather", payload["tools"][0]["function"]["name"])
        self.assertEqual("auto", payload["tool_choice"])
        self.assertEqual(0.3, payload["temperature"])
        self.assertEqual(200, payload["max_tokens"])

    def test_parse_response_supports_tools_reasoning_and_usage(self) -> None:
        response = self.provider._parse_response(
            {
                "model": "test-model",
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "reasoning_content": "Need current weather.",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "function": {
                                        "name": "search_weather",
                                        "arguments": '{"city":"Shanghai"}',
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                    "prompt_cache_hit_tokens": 6,
                    "prompt_cache_miss_tokens": 4,
                },
            }
        )

        self.assertEqual("tool_calls", response.finish_reason)
        self.assertEqual("Need current weather.", response.reasoning_content)
        self.assertEqual("search_weather", response.tool_calls[0].name)
        self.assertEqual(15, response.usage.total_tokens)
        self.assertEqual(6, response.usage.prompt_cache_hit_tokens)

    def test_build_payload_relays_structured_reasoning(self) -> None:
        payload = self.provider._build_payload_sync(
            messages=[
                LLMMessage(role="user", content="weather"),
                LLMMessage(
                    role="assistant",
                    content="",
                    reasoning_content="Need to activate the weather skill.",
                    tool_calls=[
                        ToolCall(
                            id="call_1",
                            name="activate_skill",
                            arguments='{"name":"weather"}',
                        )
                    ],
                ),
                LLMMessage(
                    role="tool",
                    content='{"ok":true}',
                    tool_call_id="call_1",
                ),
            ],
            tools=None,
            tool_choice=None,
            temperature=None,
            max_tokens=None,
        )

        assistant_payload = payload["messages"][1]
        self.assertEqual(
            "Need to activate the weather skill.",
            assistant_payload["reasoning_content"],
        )
        self.assertEqual("call_1", assistant_payload["tool_calls"][0]["id"])


class OpenAICompatibleProviderAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_generate_uses_sdk_and_keeps_extra_body_separate(self) -> None:
        completion = _DumpableResponse(
            {
                "model": "test-model",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "hello"},
                    }
                ],
            }
        )
        completions = _FakeAsyncCompletions(completion)
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        provider = OpenAICompatibleProvider(
            OpenAICompatibleSettings(
                api_key="test-key",
                model="test-model",
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            ),
            client=client,
        )

        response = await provider.generate([LLMMessage(role="user", content="hi")])

        self.assertEqual("hello", response.message.content)
        self.assertEqual("test-model", completions.create_kwargs["model"])
        self.assertEqual(
            {"chat_template_kwargs": {"enable_thinking": False}},
            completions.create_kwargs["extra_body"],
        )

    async def test_stream_generate_uses_sdk_stream(self) -> None:
        stream = _FakeAsyncStream(
            [
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(content="hel"),
                            finish_reason=None,
                        )
                    ]
                ),
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(content="lo"),
                            finish_reason=None,
                        )
                    ]
                ),
            ]
        )
        completions = _FakeAsyncCompletions(stream)
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        provider = OpenAICompatibleProvider(
            OpenAICompatibleSettings(api_key="test-key", model="test-model"),
            client=client,
        )

        chunks = [
            chunk
            async for chunk in provider.stream_generate(
                [LLMMessage(role="user", content="hi")]
            )
        ]

        self.assertEqual(["hel", "lo"], chunks)
        self.assertTrue(completions.create_kwargs["stream"])
        self.assertTrue(stream.closed)

    async def test_generate_preserves_403_error_details(self) -> None:
        request = httpx.Request("POST", "https://example.com/v1/chat/completions")
        error = PermissionDeniedError(
            "blocked",
            response=httpx.Response(403, request=request),
            body={"error": {"code": 1010}},
        )
        completions = _FakeAsyncCompletions(error)
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        provider = OpenAICompatibleProvider(
            OpenAICompatibleSettings(api_key="test-key", model="test-model"),
            client=client,
        )

        with self.assertRaisesRegex(RuntimeError, r"status=403.*1010"):
            await provider.generate([LLMMessage(role="user", content="hi")])


class OpenAICompatibleSettingsTests(unittest.TestCase):
    def test_from_env_reads_provider_options(self) -> None:
        settings = OpenAICompatibleSettings.from_env(
            env={
                "LLM_API_KEY": "test-key",
                "LLM_MODEL": "test-model",
                "LLM_BASE_URL": "https://example.com/v1",
                "LLM_TIMEOUT": "30",
                "LLM_MAX_RETRIES": "4",
                "LLM_EXTRA_HEADERS": '{"X-Test":"enabled"}',
                "LLM_EXTRA_BODY": '{"reasoning":{"enabled":true}}',
            }
        )

        self.assertEqual("test-key", settings.api_key)
        self.assertEqual("test-model", settings.model)
        self.assertEqual("https://example.com/v1", settings.base_url)
        self.assertEqual(30.0, settings.timeout)
        self.assertEqual(4, settings.max_retries)
        self.assertEqual({"X-Test": "enabled"}, settings.extra_headers)
        self.assertEqual({"reasoning": {"enabled": True}}, settings.extra_body)

    def test_from_env_requires_api_key_and_model(self) -> None:
        with self.assertRaisesRegex(ValueError, "LLM_API_KEY"):
            OpenAICompatibleSettings.from_env(env={"LLM_MODEL": "test-model"})

        with self.assertRaisesRegex(ValueError, "LLM_MODEL"):
            OpenAICompatibleSettings.from_env(env={"LLM_API_KEY": "test-key"})

    def test_base_url_accepts_common_chat_endpoint_forms(self) -> None:
        base_urls = {
            "https://example.com/v1": "https://example.com/v1",
            "https://example.com/v1/": "https://example.com/v1",
            "https://example.com/v1/chat": "https://example.com/v1",
            "https://example.com/v1/chat/": "https://example.com/v1",
            "https://example.com/v1/chat/completions": "https://example.com/v1",
            "https://example.com/v1/chat/completions/": "https://example.com/v1",
        }

        for configured_url, expected_url in base_urls.items():
            with self.subTest(base_url=configured_url):
                settings = OpenAICompatibleSettings(
                    api_key="test-key",
                    model="test-model",
                    base_url=configured_url,
                )
                self.assertEqual(expected_url, settings.base_url)

    def test_from_env_validates_numbers(self) -> None:
        invalid_values = {
            "LLM_TIMEOUT": "not-a-number",
            "LLM_MAX_RETRIES": "-1",
        }
        for name, value in invalid_values.items():
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, name):
                OpenAICompatibleSettings.from_env(
                    env={
                        "LLM_API_KEY": "test-key",
                        "LLM_MODEL": "test-model",
                        name: value,
                    }
                )

    def test_from_env_validates_json_options(self) -> None:
        invalid_values = {
            "LLM_EXTRA_HEADERS": '{"X-Retry":3}',
            "LLM_EXTRA_BODY": "[]",
        }
        for name, value in invalid_values.items():
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, name):
                OpenAICompatibleSettings.from_env(
                    env={
                        "LLM_API_KEY": "test-key",
                        "LLM_MODEL": "test-model",
                        name: value,
                    }
                )
