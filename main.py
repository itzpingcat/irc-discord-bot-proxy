#!/usr/bin/env python3
"""
main.py — IRC-style Discord terminal proxy.

Commands once connected:
    /me <text>   — send an action
    /topic       — show channel topic
    /list        — list channels
    /join        — switch channel
    /clear       — clear screen
    /help        — list commands
    /quit        — disconnect and exit
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from datetime import datetime, timezone

import discord
from prompt_toolkit import PromptSession, print_formatted_text
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.formatted_text import ANSI, HTML
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.styles import Style

TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
if not TOKEN:
    print("Error: DISCORD_BOT_TOKEN environment variable not set.")
    sys.exit(1)

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.messages = True
intents.members = True

client = discord.Client(intents=intents)
selected_channel: discord.TextChannel | None = None
selected_guild: discord.Guild | None = None

_STYLE = Style.from_dict({
    "prompt-nick":    "#00ff88 bold",
    "prompt-channel": "#aaaaaa",
})


# ── mention helpers ───────────────────────────────────────────────────────────

def resolve_mentions(content: str) -> str:
    if selected_guild is None:
        return content

    def replace_user(m: re.Match) -> str:
        member = selected_guild.get_member(int(m.group(1)))
        return f"@{member.display_name}" if member else f"@{m.group(1)}"

    def replace_channel(m: re.Match) -> str:
        ch = selected_guild.get_channel(int(m.group(1)))
        return f"#{ch.name}" if ch else f"#{m.group(1)}"

    def replace_role(m: re.Match) -> str:
        role = selected_guild.get_role(int(m.group(1)))
        return f"@{role.name}" if role else f"@{m.group(1)}"

    content = re.sub(r"<@!?(\d+)>", replace_user, content)
    content = re.sub(r"<#(\d+)>", replace_channel, content)
    content = re.sub(r"<@&(\d+)>", replace_role, content)
    return content


def encode_mentions(text: str) -> str:
    if selected_guild is None:
        return text
    name_map = {m.display_name.lower(): m for m in selected_guild.members}

    def replace(m: re.Match) -> str:
        member = name_map.get(m.group(1).lower())
        return f"<@{member.id}>" if member else m.group(0)

    return re.sub(r"@([\w][\w ]{0,30}[\w]|[\w]+)", replace, text)


# ── display helpers ───────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M")


def _print(ansi: str) -> None:
    print_formatted_text(ANSI(ansi))


def print_msg(author: str, content: str) -> None:
    _print(f"\x1b[90m[{_now()}]\x1b[0m \x1b[1m<\x1b[0m\x1b[94m{author}\x1b[0m\x1b[1m>\x1b[0m {content}")


def print_action(author: str, content: str) -> None:
    _print(f"\x1b[90m[{_now()}]\x1b[0m \x1b[93m* {author} {content}\x1b[0m")


def print_status(text: str) -> None:
    _print(f"\x1b[90m[{_now()}]\x1b[0m \x1b[33m*** {text}\x1b[0m")


def print_error(text: str) -> None:
    _print(f"\x1b[90m[{_now()}]\x1b[0m \x1b[31m!!! {text}\x1b[0m")


def print_topic(channel: str, topic: str) -> None:
    _print(f"\x1b[90m[{_now()}]\x1b[0m \x1b[35m--- Topic for #{channel}: {topic or '(none)'}\x1b[0m")


def print_sep(label: str = "") -> None:
    width = 60
    if label:
        pad = (width - len(label) - 2) // 2
        right = width - pad - len(label) - 2
        _print(f"\x1b[90m{'─' * pad} {label} {'─' * right}\x1b[0m")
    else:
        _print(f"\x1b[90m{'─' * width}\x1b[0m")


# ── discord events ────────────────────────────────────────────────────────────

@client.event
async def on_ready():
    global selected_channel, selected_guild

    print_status(f"Connected as {client.user}")

    guilds = sorted(client.guilds, key=lambda g: g.name.lower())
    if not guilds:
        print_error("Bot is not in any servers.")
        await client.close()
        return

    print_sep("SERVERS")
    for i, g in enumerate(guilds, 1):
        print(f"  {i:3d}. {g.name}")
    while True:
        try:
            idx = int(input("\nSelect server: ")) - 1
            selected_guild = guilds[idx]
            break
        except (ValueError, IndexError):
            print_error("Invalid selection.")

    await _pick_channel()


async def _pick_channel():
    global selected_channel

    channels = [
        c for c in selected_guild.text_channels
        if c.permissions_for(selected_guild.me).view_channel
    ]
    if not channels:
        print_error("No accessible text channels.")
        await client.close()
        return

    print_sep(f"{selected_guild.name} — CHANNELS")
    for i, c in enumerate(channels, 1):
        topic = f"  — {c.topic[:50]}" if c.topic else ""
        print(f"  {i:3d}. #{c.name}{topic}")

    while True:
        try:
            idx = int(input("\nSelect channel: ")) - 1
            selected_channel = channels[idx]
            break
        except (ValueError, IndexError):
            print_error("Invalid selection.")

    while True:
        try:
            raw = input("Messages to backfill [0-1000, default 50]: ").strip()
            backfill = int(raw) if raw else 50
            if 0 <= backfill <= 1000:
                break
            print_error("Must be 0-1000.")
        except ValueError:
            print_error("Enter a number.")

    if backfill:
        print_sep("HISTORY")
        msgs = []
        async for m in selected_channel.history(limit=backfill):
            content = resolve_mentions(m.content or "")
            if m.attachments:
                content += " [file: " + " ".join(a.url for a in m.attachments) + "]"
            msgs.append((m.author.display_name, content, m.created_at))
        for author, content, dt in reversed(msgs):
            print(f"[{dt.strftime('%H:%M')}] <{author}> {content}")

    print_sep("LIVE")
    print_status(f"Joined #{selected_channel.name} in {selected_guild.name}")
    if selected_channel.topic:
        print_topic(selected_channel.name, selected_channel.topic)
    print_status("Type /help for commands.")

    asyncio.create_task(stdin_loop())


@client.event
async def on_message(message):
    if selected_channel is None or message.channel.id != selected_channel.id:
        return
    if message.author.id == client.user.id:
        return

    author = message.author.display_name
    content = resolve_mentions(message.content or "")

    if message.attachments:
        content += " [file: " + " ".join(a.url for a in message.attachments) + "]"

    is_action = content.startswith("*") and content.endswith("*") and len(content) > 2
    if is_action:
        print_action(author, content[1:-1])
    else:
        print_msg(author, content)


# ── live input loop ───────────────────────────────────────────────────────────

async def stdin_loop():
    session: PromptSession = PromptSession(
        history=InMemoryHistory(),
        style=_STYLE,
    )

    def _prompt() -> HTML:
        nick = client.user.display_name if client.user else "?"
        chan = selected_channel.name if selected_channel else "?"
        return HTML(
            f"<prompt-nick>[{nick}]</prompt-nick>"
            f"<prompt-channel> #{chan}&gt;</prompt-channel> "
        )

    with patch_stdout():
        while True:
            try:
                line: str = await session.prompt_async(_prompt)
            except (EOFError, KeyboardInterrupt):
                await client.close()
                return

            line = line.strip()
            if not line:
                continue

            if line.startswith("/"):
                parts = line[1:].split(None, 1)
                cmd = parts[0].lower()
                args = parts[1].split() if len(parts) > 1 else []
                await handle_command(cmd, args)
            else:
                try:
                    await selected_channel.send(encode_mentions(line))
                except Exception as e:
                    print_error(f"Send failed: {e}")


async def handle_command(cmd: str, args: list[str]) -> None:
    if cmd in ("quit", "exit"):
        print_status("Disconnecting...")
        await client.close()

    elif cmd == "me":
        if not args:
            print_error("Usage: /me <action>")
            return
        await selected_channel.send(f"*{encode_mentions(' '.join(args))}*")

    elif cmd == "topic":
        if selected_channel:
            print_topic(selected_channel.name, selected_channel.topic or "")
        else:
            print_error("Not in a channel.")

    elif cmd == "list":
        channels = [
            c for c in selected_guild.text_channels
            if c.permissions_for(selected_guild.me).view_channel
        ]
        print_sep("CHANNELS")
        for c in channels:
            marker = " ◀" if (selected_channel and c.id == selected_channel.id) else ""
            topic = f"  — {c.topic[:60]}" if c.topic else ""
            _print(f"\x1b[94m  #{c.name}\x1b[0m\x1b[90m{topic}{marker}\x1b[0m")
        print_sep()

    elif cmd == "join":
        await _pick_channel()

    elif cmd == "clear":
        print("\x1b[2J\x1b[H", end="")

    elif cmd == "help":
        print_sep("COMMANDS")
        for line in [
            "/me <text>   — send an action",
            "/topic       — show channel topic",
            "/list        — list channels",
            "/join        — switch channel",
            "/clear       — clear screen",
            "/quit        — disconnect",
        ]:
            print(f"  {line}")
        print_sep()

    else:
        print_error(f"Unknown command: /{cmd}  (try /help)")


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print_sep("discord IRC proxy")
    client.run(TOKEN)
