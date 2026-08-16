from __future__ import annotations

import unittest

from echobot.models import LLMUsage


class LLMUsageTests(unittest.TestCase):
    def test_to_dict_keeps_cache_metrics(self) -> None:
        usage = LLMUsage(
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            prompt_cache_hit_tokens=6,
            prompt_cache_miss_tokens=4,
        )

        self.assertEqual(
            {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "prompt_cache_hit_tokens": 6,
                "prompt_cache_miss_tokens": 4,
                "prompt_cache_hit_rate_percent": 60.0,
            },
            usage.to_dict(),
        )

    def test_hit_rate_handles_zero_prompt_tokens(self) -> None:
        self.assertIsNone(LLMUsage().prompt_cache_hit_rate_percent())

    def test_from_openai_prompt_token_details(self) -> None:
        usage = LLMUsage.from_dict(
            {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "prompt_tokens_details": {"cached_tokens": 6},
            }
        )

        self.assertEqual(10, usage.prompt_tokens)
        self.assertEqual(5, usage.completion_tokens)
        self.assertEqual(15, usage.total_tokens)
        self.assertEqual(6, usage.prompt_cache_hit_tokens)
        self.assertEqual(4, usage.prompt_cache_miss_tokens)
        self.assertEqual(60.0, usage.prompt_cache_hit_rate_percent())

    def test_from_input_output_tokens(self) -> None:
        usage = LLMUsage.from_dict(
            {
                "input_tokens": 12,
                "output_tokens": 3,
                "input_tokens_details": {"cached_tokens": 8},
            }
        )

        self.assertEqual(12, usage.prompt_tokens)
        self.assertEqual(3, usage.completion_tokens)
        self.assertEqual(15, usage.total_tokens)
        self.assertEqual(8, usage.prompt_cache_hit_tokens)
        self.assertEqual(4, usage.prompt_cache_miss_tokens)
        self.assertEqual(66.67, usage.prompt_cache_hit_rate_percent())
