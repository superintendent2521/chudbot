"""Mention-triggered OpenRouter assistant with short follow-up sessions."""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

import aiohttp
from interactions import listen
from interactions.api.events.discord import MessageCreate


FOLLOW_UP_SECONDS = 30
MAX_TOOL_ROUNDS = 5
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
AI_STUDIO_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
MENTION_RE = re.compile(r"<@!?\d+>\s*")
SYSTEM_PROMPT_FILE = Path(__file__).with_name("ai_system_prompt.txt")

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for current information.",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "Evaluate a basic arithmetic expression.",
            "parameters": {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory",
            "description": "Recall or save a fact about the user. Use save to remember durable preferences or facts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["recall", "save"]},
                    "note": {"type": "string"},
                },
                "required": ["action"],
            },
        },
    },
]


def _calculate(expression: str) -> str:
    """Evaluate only numeric arithmetic; never execute arbitrary Python."""
    allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Add, ast.Sub,
               ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.USub, ast.UAdd, ast.FloorDiv)
    tree = ast.parse(expression, mode="eval")
    if any(not isinstance(node, allowed) or (isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)))
           for node in ast.walk(tree)):
        raise ValueError("only arithmetic is allowed")
    value = eval(compile(tree, "<calculator>", "eval"), {"__builtins__": {}}, {})
    if not isinstance(value, (int, float)) or abs(value) > 1e100:
        raise ValueError("result is out of range")
    return str(value)


async def _web_search(session: aiohttp.ClientSession, query: str) -> str:
    async with session.get(
        "https://api.duckduckgo.com/",
        params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
    ) as response:
        response.raise_for_status()
        data = await response.json(content_type=None)
    results = []
    if data.get("AbstractText"):
        results.append(f"{data.get('Heading', 'Result')}: {data['AbstractText']} ({data.get('AbstractURL', '')})")
    for item in data.get("RelatedTopics", [])[:5]:
        if isinstance(item, dict) and item.get("Text"):
            results.append(f"{item['Text']} ({item.get('FirstURL', '')})")
    return "\n".join(results) or "No results found."


def _snowflake(value: Any) -> int | None:
    try:
        value = getattr(value, "id", value)
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def create_ai_listener(store: Any, logger: logging.Logger):
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    gemini_model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()
    openrouter_model = os.getenv("OPENROUTER_MODEL", "google/gemini-3.5-flash-lite").strip()
    prompt = SYSTEM_PROMPT_FILE.read_text(encoding="utf-8").strip()
    sessions: dict[tuple[int, int], float] = {}

    async def ask(user_id: int, question: str) -> str:
        memory = await store.get_ai_memory(user_id)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": prompt},
            {"role": "system", "content": f"Stored user memory:\n{memory or '(none)'}"},
            {"role": "user", "content": question},
        ]
        providers = []
        if gemini_key:
            providers.append(("AI Studio", AI_STUDIO_URL, gemini_key, gemini_model))
        if openrouter_key:
            providers.append(("OpenRouter", OPENROUTER_URL, openrouter_key, openrouter_model))
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for provider_name, provider_url, provider_key, provider_model in providers:
                try:
                    provider_messages = list(messages)
                    headers = {"Authorization": f"Bearer {provider_key}", "Content-Type": "application/json"}
                    if provider_name == "OpenRouter":
                        if os.getenv("OPENROUTER_SITE_URL"):
                            headers["HTTP-Referer"] = os.environ["OPENROUTER_SITE_URL"]
                        if os.getenv("OPENROUTER_APP_NAME"):
                            headers["X-Title"] = os.environ["OPENROUTER_APP_NAME"]
                    for _ in range(MAX_TOOL_ROUNDS):
                        async with session.post(provider_url, headers=headers, json={"model": provider_model, "messages": provider_messages, "tools": TOOLS, "tool_choice": "auto", "max_tokens": 128}) as response:
                            response.raise_for_status()
                            choice = (await response.json())["choices"][0]["message"]
                        provider_messages.append(choice)
                        calls = choice.get("tool_calls") or []
                        if not calls:
                            return (choice.get("content") or "I couldn't produce a response.").strip()
                        for call in calls:
                            name = call["function"]["name"]
                            args = json.loads(call["function"].get("arguments") or "{}")
                            try:
                                if name == "calculator":
                                    result = _calculate(str(args.get("expression", "")))
                                elif name == "web_search":
                                    result = await _web_search(session, str(args.get("query", "")))
                                elif name == "memory":
                                    result = await (store.get_ai_memory(user_id) if args.get("action") == "recall" else store.append_ai_memory(user_id, str(args.get("note", ""))))
                                else:
                                    result = "Unknown tool."
                            except Exception as error:
                                result = f"Tool error: {error}"
                            provider_messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
                except (aiohttp.ClientError, KeyError, TypeError, ValueError) as error:
                    logger.warning("%s AI provider failed; trying next provider: %s", provider_name, error)
        return "I couldn't finish that request."

    @listen(MessageCreate)
    async def on_message(event: MessageCreate):
        message = getattr(event, "message", None)
        author = getattr(message, "author", None)
        if not message or not author or getattr(author, "bot", False):
            return
        user_id = _snowflake(author)
        channel_id = _snowflake(getattr(message, "channel", None)) or _snowflake(getattr(message, "channel_id", None))
        bot_id = _snowflake(getattr(getattr(event, "client", None), "user", None))
        content = (getattr(message, "content", None) or "").strip()
        mentioned = bool(bot_id and re.search(rf"<@!?{bot_id}>", content))
        if not mentioned and bot_id:
            mentioned = any(_snowflake(mention) == bot_id for mention in (getattr(message, "mentions", None) or []))
        key = (user_id, channel_id) if user_id is not None and channel_id is not None else None
        if mentioned:
            if not gemini_key and not openrouter_key:
                logger.warning("GEMINI_API_KEY and OPENROUTER_API_KEY are not configured")
                return
            question = MENTION_RE.sub("", content).strip()
            if not question:
                question = "What can you help me with?"
            if key:
                sessions[key] = time.monotonic() + FOLLOW_UP_SECONDS
        elif not key or sessions.get(key, 0) < time.monotonic():
            return
        else:
            question = content
        try:
            answer = await ask(user_id, question)
            for start in range(0, len(answer), 1900):
                await message.reply(answer[start:start + 1900])
        except Exception:
            logger.exception("AI response failed for user %s", user_id)

    return (on_message,)
