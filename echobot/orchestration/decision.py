from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from ..agent import AgentCore
from ..models import LLMMessage, message_content_to_text
from .route_modes import DEFAULT_ROUTE_MODE, RouteMode

logger = logging.getLogger(__name__)


DECISION_SYSTEM_PROMPT = """
You are the decision layer for a three-layer real-time assistant.

Your job is to choose the fastest safe route for the current turn.

Choose one route:
- "chat": a direct conversational reply is enough. Use this for casual conversation, roleplay, opinions, brainstorming, rewriting, translation, emotional support, or general discussion that can be answered from the current message plus recent chat context alone.
- "agent": the assistant must inspect, change, search, verify, schedule, or execute anything beyond the current visible chat reply.

Always choose "agent" for:
- Any tool use, project or file inspection, code review or edits, shell commands, skill use, or background work.
- Any memory lookup, including "do you remember..." questions about user preferences, prior topics, previous tasks, or earlier decisions.
- Any scheduling, reminders, cron, heartbeat, timers, or checking existing scheduled jobs.
- Any request that depends on external state such as workspace files, saved memory, schedule state, prior tool output, or background job status.
- Any follow-up that modifies, continues, retries, or asks about an earlier actionable task, for example "do that", "continue", "try again", "change it to tomorrow at 9", or "what was the result?"

Choose "chat" only when no lookup, tool call, memory search, scheduling action, or workspace inspection is needed.

If the request is ambiguous, prefer "agent" when there is a meaningful chance the user is referring to prior agent work or stored state. Otherwise prefer "chat".

Return JSON only:
{"route":"chat"|"agent","reason":"short reason"}
""".strip()

DEFAULT_DECISION_MAX_TOKENS = 4096


ENGLISH_REQUEST_PREFIX = (
    r"^\s*(?:(?:please|kindly)\s+)?"
    r"(?:(?:can|could|would)\s+you\s+)?"
    r"(?:help\s+me\s+)?"
)
CHINESE_REQUEST_PREFIX = r"^\s*(?:请|请帮我|帮我|麻烦你|麻烦帮我)?\s*"

AGENT_PATTERNS = (
    # scheduling and background execution
    rf"{ENGLISH_REQUEST_PREFIX}(set|create|add|schedule)\s+(a\s+)?(cron|reminder|timer|task)\b",
    rf"{ENGLISH_REQUEST_PREFIX}(start|stop|enable|disable|check|show)\s+(the\s+)?(cron|heartbeat)\b",
    rf"{ENGLISH_REQUEST_PREFIX}(run in the background|background task)\b",
    rf"{ENGLISH_REQUEST_PREFIX}remind\s+me\s+to\b",
    rf"{ENGLISH_REQUEST_PREFIX}(set\s+(a\s+)?(reminder|timer)|schedule\s+(a\s+)?(reminder|task))\b.*\b(in|after)\s+\d+\s*(seconds?|minutes?|hours?|days?)\b",
    rf"{ENGLISH_REQUEST_PREFIX}\b(in|after)\s+\d+\s*(seconds?|minutes?|hours?|days?)\b.*\b(remind\s+me\s+to|set\s+(a\s+)?(reminder|timer)|schedule\s+(a\s+)?(reminder|task))\b",
    rf"{CHINESE_REQUEST_PREFIX}(设置|创建|添加|安排).*(cron|提醒|定时|计划任务)",
    rf"{CHINESE_REQUEST_PREFIX}(开启|关闭|启动|停止|检查|查看).*(心跳|cron)",
    rf"{CHINESE_REQUEST_PREFIX}(提醒我.*(后|在|每|去|做)|设置提醒|定时提醒|计划任务|后台任务|后台执行)",
    rf"{CHINESE_REQUEST_PREFIX}\d+\s*(秒|分钟|小时|天)后.*提醒我",
    rf"{CHINESE_REQUEST_PREFIX}(每[天周月年]).*(提醒|执行|运行)",
    # workspace, files, code, tools, and memory operations
    rf"{ENGLISH_REQUEST_PREFIX}(open|read|view|inspect|check)\s+(the\s+)?(file|files|folder|directory|repo|repository|project|workspace|codebase)\b",
    rf"{ENGLISH_REQUEST_PREFIX}(open|read|view|inspect|edit|modify|delete)\s+\S+\.\w+\b",
    rf"{ENGLISH_REQUEST_PREFIX}(search|scan|inspect|check)\s+(the\s+)?(repo|repository|project|workspace|codebase)\b",
    rf"{ENGLISH_REQUEST_PREFIX}(search|find|look\s+up)\s+(in|through)\s+(the\s+)?(file|files|code|repo|repository|project|codebase|directory|workspace|memory|memories)\b",
    rf"{ENGLISH_REQUEST_PREFIX}(edit|modify|delete|remove|rename|move)\s+(the\s+|a\s+)?(file|files|folder|directory|repo|repository|project|workspace|code|script|function|class|module|test|command|program)\b",
    rf"{ENGLISH_REQUEST_PREFIX}(create|write|generate)\s+(a\s+|the\s+)?(file|script|function|class|module|test|command|program)\b",
    rf"{ENGLISH_REQUEST_PREFIX}(run|execute)\s+(a\s+|the\s+)?(script|command|test|program|process|shell command|terminal command)\b",
    rf"{ENGLISH_REQUEST_PREFIX}(use|activate|install|list)\s+(a\s+|the\s+)?skills?\b",
    rf"{ENGLISH_REQUEST_PREFIX}(use|run|call|list)\s+(a\s+|the\s+)?tools?\b",
    rf"{ENGLISH_REQUEST_PREFIX}(remember|save|store|log)\s+(this|that|it)\b",
    rf"{ENGLISH_REQUEST_PREFIX}(look up|search|check|recall)\s+(the\s+)?memories?\b",
    rf"{CHINESE_REQUEST_PREFIX}(打开|查看|读取|检查|搜索|查找).*(文件|代码|项目|仓库|目录|工作区|记忆)",
    rf"{CHINESE_REQUEST_PREFIX}(修改|编辑|删除|移除|重命名|移动).*(文件|代码|脚本|函数|类|模块|测试|命令|目录|项目)",
    rf"{CHINESE_REQUEST_PREFIX}(创建|新建|生成|写).*(文件|脚本|函数|类|模块|测试|命令|程序)",
    rf"{CHINESE_REQUEST_PREFIX}(运行|执行).*(脚本|命令|测试|程序|进程)",
    rf"{CHINESE_REQUEST_PREFIX}(使用|启用|安装|列出).*(技能|skill)",
    rf"{CHINESE_REQUEST_PREFIX}(使用|调用|运行|列出).*(工具|tool)",
    rf"{CHINESE_REQUEST_PREFIX}(记住|记下来|保存到记忆|存到记忆|查记忆|查一下记忆|搜索记忆)",
    # 人脸识别相关
    rf"{CHINESE_REQUEST_PREFIX}(记住我|记住我叫|我叫|这是|把我绑定|绑定.*人脸|绑定.*脸|人脸绑定|记下我的脸|记住我的脸)",
    rf"{ENGLISH_REQUEST_PREFIX}(bind|remember|recognize).*(face|name|person)",
    rf"{ENGLISH_REQUEST_PREFIX}(my name is|i am|i'm|this is)\s+\w+",
)

ROUTE_FIELD_PATTERN = re.compile(
    r"\broute\b\s*[:=]\s*['\"]?(chat|agent)['\"]?",
    flags=re.IGNORECASE,
)
ROUTE_TOKEN_PATTERN = re.compile(
    r"^\s*['\"]?(chat|agent)['\"]?\s*$",
    flags=re.IGNORECASE,
)


@dataclass(slots=True)
class RouteDecision:
    route: str
    reason: str

    @property
    def requires_agent(self) -> bool:
        return self.route == "agent"


class DecisionEngine:
    def __init__(
        self,
        decider_agent: AgentCore | None = None,
        *,
        max_tokens: int = DEFAULT_DECISION_MAX_TOKENS,
    ) -> None:
        self._decider_agent = decider_agent
        self._max_tokens = max(int(max_tokens), 1)

    async def decide(
        self,
        user_input: str,
        *,
        history: list[LLMMessage] | None = None,
        route_mode: RouteMode = DEFAULT_ROUTE_MODE,
    ) -> RouteDecision:
        if route_mode == "chat_only":
            return RouteDecision(route="chat", reason="Forced chat-only route")
        if route_mode == "force_agent":
            return RouteDecision(route="agent", reason="Forced full-agent route")

        rule_decision = _rule_based_decision(user_input)
        if rule_decision is not None:
            return rule_decision

        if self._decider_agent is None:
            return RouteDecision(route="chat", reason="Fallback to lightweight chat")

        response = await self._decider_agent.ask(
            user_input,
            history=_trim_history(history, max_messages=6),
            extra_system_messages=[DECISION_SYSTEM_PROMPT],
            temperature=0,
            max_tokens=self._max_tokens,
        )
        decision = _parse_decision_response(
            message_content_to_text(response.message.content),
        )
        if response.finish_reason == "length":
            logger.warning(
                "Decision layer hit max_tokens limit and returned route='%s' from truncated output",
                decision.route,
            )
        return decision


def _trim_history(
    history: list[LLMMessage] | None,
    *,
    max_messages: int,
) -> list[LLMMessage]:
    if not history:
        return []
    return list(history[-max_messages:])


def _rule_based_decision(user_input: str) -> RouteDecision | None:
    cleaned = user_input.strip()
    if not cleaned:
        return RouteDecision(route="chat", reason="Empty input")

    if _matches_any_pattern(cleaned, AGENT_PATTERNS):
        return RouteDecision(route="agent", reason="Likely workspace or tool task")

    return None


def _parse_decision_response(text: str) -> RouteDecision:
    parsed = _try_parse_json_object(text)
    if parsed is not None:
        route = str(parsed.get("route", "")).strip().lower()
        reason = str(parsed.get("reason", "")).strip() or "LLM decision"
        if route in {"chat", "agent"}:
            return RouteDecision(route=route, reason=reason)

    fallback_route = _extract_route_from_text(text)
    if fallback_route is not None:
        return RouteDecision(route=fallback_route, reason="LLM fallback parse")
    return RouteDecision(route="chat", reason="LLM fallback parse")


def _matches_any_pattern(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _extract_route_from_text(text: str) -> str | None:
    route_match = ROUTE_FIELD_PATTERN.search(text)
    if route_match is not None:
        return route_match.group(1).lower()

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        token_match = ROUTE_TOKEN_PATTERN.fullmatch(stripped)
        if token_match is not None:
            return token_match.group(1).lower()
        break

    return None


def _try_parse_json_object(text: str) -> dict[str, object] | None:
    cleaned = text.strip()
    if not cleaned:
        return None

    candidates = [cleaned]
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and start < end:
        candidates.append(cleaned[start : end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None
