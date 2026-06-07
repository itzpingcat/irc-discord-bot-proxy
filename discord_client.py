"""
discord_client.py — Discord connection and event handling.
UI concerns handled via callbacks; knows nothing about prompt_toolkit.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Callable

import discord


class DiscordProxy:
    def __init__(self, token: str) -> None:
        self.token = token
        self._channel: discord.TextChannel | None = None
        self._self_id: int | None = None
        self._ready_event = asyncio.Event()

        # set by main.py before start()
        self.on_message_cb: Callable[[str, str, bool, bool], None] | None = None
        self.on_connected_cb: Callable[[str], None] | None = None

        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.messages = True

        self.client = discord.Client(intents=intents)
        self.client.event(self._on_ready)
        self.client.event(self._on_message)

    # ── internal events ───────────────────────────────────────────────────────

    async def _on_ready(self) -> None:
        self._self_id = self.client.user.id
        if self.on_connected_cb:
            self.on_connected_cb(str(self.client.user))
        self._ready_event.set()

    async def _on_message(self, message: discord.Message) -> None:
        if self._channel is None or message.channel.id != self._channel.id:
            return
        if self.on_message_cb is None:
            return

        is_self = message.author.id == self._self_id
        author = message.author.display_name
        content = message.content or ""

        if message.attachments:
            content += " [file: " + " ".join(a.url for a in message.attachments) + "]"
        if message.embeds:
            for e in message.embeds:
                parts = filter(None, [e.title, e.description])
                content += " [embed: " + " — ".join(parts) + "]"

        # /me actions arrive as italic markdown in Discord; detect *text* or _text_
        is_action = False
        if (content.startswith("*") and content.endswith("*") and len(content) > 2) or \
           (content.startswith("_") and content.endswith("_") and len(content) > 2):
            content = content[1:-1]
            is_action = True

        self.on_message_cb(author, content, is_self, is_action)

    # ── public API ────────────────────────────────────────────────────────────

    async def wait_ready(self) -> None:
        await self._ready_event.wait()

    @property
    def guilds(self) -> list[discord.Guild]:
        return sorted(self.client.guilds, key=lambda g: g.name.lower())

    def text_channels(self, guild: discord.Guild) -> list[discord.TextChannel]:
        return [
            c for c in guild.text_channels
            if c.permissions_for(guild.me).view_channel
        ]

    async def attach(self, channel: discord.TextChannel) -> None:
        self._channel = channel

    @property
    def channel(self) -> discord.TextChannel | None:
        return self._channel

    @property
    def channel_topic(self) -> str:
        return (self._channel.topic or "") if self._channel else ""

    async def fetch_history(self, limit: int) -> list[tuple[str, str, datetime]]:
        """Returns (author, content, created_at) tuples, oldest first."""
        if not self._channel or limit == 0:
            return []
        msgs = []
        async for m in self._channel.history(limit=limit):
            content = m.content or ""
            if m.attachments:
                content += " [file: " + " ".join(a.url for a in m.attachments) + "]"
            msgs.append((m.author.display_name, content, m.created_at))
        return list(reversed(msgs))

    async def send(self, text: str) -> None:
        if self._channel:
            await self._channel.send(text)

    async def send_action(self, text: str) -> None:
        """Send a /me action as Discord italics."""
        if self._channel:
            await self._channel.send(f"*{text}*")

    async def start(self) -> None:
        await self.client.start(self.token)

    async def close(self) -> None:
        await self.client.close()

    @property
    def self_nick(self) -> str:
        u = self.client.user
        return u.display_name if u else "???"

    @property
    def self_tag(self) -> str:
        u = self.client.user
        return str(u) if u else "???"
