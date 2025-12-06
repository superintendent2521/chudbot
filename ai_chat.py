"""AI chat listener service."""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from typing import Dict, List, Optional

import aiohttp
from interactions import listen
from interactions.api.events.discord import MessageCreate


class AiChatService:
    def __init__(
        self,
        *,
        logger: logging.Logger,
        openrouter_api_url: str,
        openrouter_api_key: Optional[str],
        openrouter_site_url: str,
        openrouter_app_name: str,
        system_prompt: str,
        model_id: str,
        max_memory_messages: int,
    ) -> None:
        self.logger = logger
        self.openrouter_api_url = openrouter_api_url
        self.openrouter_api_key = openrouter_api_key
        self.openrouter_site_url = openrouter_site_url
        self.openrouter_app_name = openrouter_app_name
        self.system_prompt = system_prompt
        self.max_memory_messages = max_memory_messages
        self.model_id = model_id
        self.user_memories: Dict[int, deque] = defaultdict(lambda: deque(maxlen=self.max_memory_messages))

    def create_listener(self):
        service = self

        @listen(MessageCreate)
        async def handle_ai_conversation(event: MessageCreate):
            await service._handle_message(event)

        return handle_ai_conversation

    async def _handle_message(self, event: MessageCreate) -> None:
        message = event.message
        if not message or not message.content:
            return
        if not self.openrouter_api_key:
            return
        if message.author.bot:
            return

        bot_user = event.client.user
        if not bot_user:
            return

        bot_id = bot_user.id
        if not self._bot_was_mentioned(message.content, bot_id):
            return

        cleaned_content = self._strip_bot_mentions(message.content, bot_id)
        if not cleaned_content:
            cleaned_content = "Hello!"

        self.logger.info(
            "Incoming mention from %s (%s): %s",
            getattr(message.author, "username", "unknown"),
            message.author.id,
            cleaned_content,
        )

        user_id = int(message.author.id)
        history = self.user_memories.get(user_id, [])
        trimmed_history = history[-self.max_memory_messages :]
        self.logger.debug("History length for %s: %d", user_id, len(trimmed_history))

        request_messages = [{"role": "system", "content": self.system_prompt}]
        request_messages.extend(trimmed_history)
        request_messages.append({"role": "user", "content": cleaned_content})

        try:
            ai_response = await self._query_openrouter(request_messages)
        except Exception as error:
            self.logger.error("Failed to fetch AI response for %s: %s", user_id, error)
            await message.reply(
                "I couldn't reach my AI brain right now. Please try again later."
            )
            return

        self._append_memory(user_id, "user", cleaned_content)
        self._append_memory(user_id, "assistant", ai_response)
        preview = ai_response if len(ai_response) <= 200 else f"{ai_response[:200]}..."
        self.logger.info(
            "AI response to %s (%s): %s",
            getattr(message.author, "username", "unknown"),
            message.author.id,
            preview,
        )

        chunks = self._chunk_response(ai_response)
        first_message = True
        for chunk in chunks:
            if first_message:
                await message.reply(chunk)
                first_message = False
            else:
                await message.channel.send(chunk)

    def _bot_was_mentioned(self, content: str, bot_id: int) -> bool:
        mention_patterns = (f"<@{bot_id}>", f"<@!{bot_id}>")
        return any(pattern in content for pattern in mention_patterns)

    def _strip_bot_mentions(self, content: str, bot_id: int) -> str:
        mention_patterns = (f"<@{bot_id}>", f"<@!{bot_id}>")
        for pattern in mention_patterns:
            content = content.replace(pattern, "")
        return content.strip()

    def _append_memory(self, user_id: int, role: str, message: str) -> None:
        self.user_memories[user_id].append({"role": role, "content": message})

    async def _query_openrouter(self, messages: List[dict]) -> str:
        if not self.openrouter_api_key:
            raise RuntimeError("OPENROUTER_API_KEY missing")

        headers = {
            "Authorization": f"Bearer {self.openrouter_api_key}",
            "Content-Type": "application/json",
        }
        if self.openrouter_site_url:
            headers["HTTP-Referer"] = self.openrouter_site_url
        if self.openrouter_app_name:
            headers["X-Title"] = self.openrouter_app_name

        payload = {
            "model": self.model_id,
            "messages": messages,
            "temperature": 0.7,
        }

        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                self.openrouter_api_url,
                json=payload,
                headers=headers,
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise RuntimeError(
                        f"OpenRouter error {response.status}: {error_text}"
                    )
                data = await response.json()

        choices = data.get("choices")
        if not choices:
            raise RuntimeError("OpenRouter returned no choices")

        message = choices[0].get("message", {})
        content = message.get("content", "").strip()
        if not content:
            raise RuntimeError("OpenRouter returned empty content")
        return content

    def _chunk_response(self, content: str, limit: int = 1800) -> List[str]:
        if len(content) <= limit:
            return [content]

        chunks = []
        current = []
        current_len = 0
        for paragraph in content.split("\n"):
            piece = paragraph + "\n"
            if current_len + len(piece) > limit and current:
                chunks.append("".join(current).rstrip())
                current = [piece]
                current_len = len(piece)
            else:
                current.append(piece)
                current_len += len(piece)
        if current:
            chunks.append("".join(current).rstrip())
        return [chunk for chunk in chunks if chunk]
