#!/usr/bin/env python3
"""
main.py — IRC-style Discord terminal proxy.

Commands once connected:
    /me <text>       — send an action
    /topic           — show channel topic
    /list            — list channels
    /join <name|id>  — join a channel (optional: /join <name> <backfill>)
    /clear           — clear screen
    /help            — list commands
    /quit            — disconnect and exit
    /translate <lang> — translate outgoing messages ending in -r to <lang> (e.g. ru, de, ja)
    /translate off   — disable outgoing translation
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

try:
    from deep_translator import GoogleTranslator
    _TRANSLATE_AVAILABLE = True
except ImportError:
    _TRANSLATE_AVAILABLE = False

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
outgoing_lang: str | None = None  # e.g. "ru"; None = disabled

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


# ── translation helpers ───────────────────────────────────────────────────────

def maybe_translate(content: str) -> str:
    """Translate incoming non-English messages to English."""
    if not _TRANSLATE_AVAILABLE or not content.strip():
        return content
    try:
        translated = GoogleTranslator(source="auto", target="en").translate(content)
        if translated and translated.lower() != content.lower():
            return f"{content}\x1b[90m  [{translated}]\x1b[0m"
    except Exception:
        pass
    return content


def translate_outgoing(text: str, lang: str) -> str | None:
    """Translate text to lang. Returns translated string or None on failure."""
    try:
        return GoogleTranslator(source="auto", target=lang).translate(text)
    except Exception as e:
        print_error(f"Translation failed: {e}")
        return None


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
    if not _TRANSLATE_AVAILABLE:
        print_status("Auto-translate disabled (run: pip install deep-translator)")

    if not client.guilds:
        print_error("Bot is not in any servers.")
        await client.close()
        return

    print_status("Type /join <channel> or /join <server>/<channel> to get started, /list to see all channels.")
    asyncio.create_task(stdin_loop())


def _fuzzy_channel(channels: list[discord.TextChannel], query: str) -> discord.TextChannel | list[discord.TextChannel] | None:
    """Return a single match, a list of ambiguous matches, or None."""
    q = query.lower().lstrip("#")
    for c in channels:
        if str(c.id) == q:
            return c
    for c in channels:
        if c.name.lower() == q:
            return c
    hits = [c for c in channels if c.name.lower().startswith(q)]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        return hits
    hits = [c for c in channels if q in c.name.lower()]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        return hits
    return None


async def _join_channel(query: str, backfill: int = 50) -> None:
    global selected_channel, selected_guild

    # parse optional server/channel syntax
    guild_query: str | None = None
    chan_query = query
    if "/" in query:
        guild_query, chan_query = query.split("/", 1)

    # resolve guild
    all_guilds = sorted(client.guilds, key=lambda g: g.name.lower())
    if guild_query:
        gq = guild_query.lower()
        target_guild: discord.Guild | None = None
        for g in all_guilds:
            if g.name.lower() == gq or str(g.id) == gq:
                target_guild = g
                break
        if target_guild is None:
            ghits = [g for g in all_guilds if g.name.lower().startswith(gq)]
            if len(ghits) == 1:
                target_guild = ghits[0]
            elif len(ghits) > 1:
                print_error(f"Ambiguous server: {', '.join(g.name for g in ghits)}")
                return
        if target_guild is None:
            print_error(f"No server matching '{guild_query}'.")
            return
        search_guilds = [target_guild]
    else:
        search_guilds = all_guilds

    # gather all visible channels across target guild(s)
    candidates: list[tuple[discord.Guild, discord.TextChannel]] = []
    for g in search_guilds:
        for c in g.text_channels:
            if c.permissions_for(g.me).view_channel:
                candidates.append((g, c))

    result = _fuzzy_channel([c for _, c in candidates], chan_query)
    if result is None:
        print_error(f"No channel matching '{chan_query}'.")
        return
    if isinstance(result, list):
        print_error(f"Ambiguous: {', '.join(g.name + '/#' + c.name for g, c in candidates if c in result)}")
        return

    match = result
    selected_guild = match.guild
    selected_channel = match

    if backfill:
        print_sep("HISTORY")
        msgs = []
        async for m in selected_channel.history(limit=backfill):
            msgs.append(m)
        for m in sorted(msgs, key=lambda m: m.created_at):
            content = resolve_mentions(m.content or "")
            if m.attachments:
                content += " [file: " + " ".join(a.url for a in m.attachments) + "]"
            content = maybe_translate(content)
            _print(f"\x1b[90m[{m.created_at.strftime('%H:%M')}]\x1b[0m \x1b[1m<\x1b[0m\x1b[94m{m.author.display_name}\x1b[0m\x1b[1m>\x1b[0m {content}")

    print_sep("LIVE")
    print_status(f"Joined #{selected_channel.name} in {selected_guild.name}")
    if selected_channel.topic:
        print_topic(selected_channel.name, selected_channel.topic)


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

    content = maybe_translate(content)

    is_action = content.startswith("*") and content.endswith("*") and len(content) > 2
    if is_action:
        print_action(author, content[1:-1])
    else:
        print_msg(author, content)


# ── live input loop ───────────────────────────────────────────────────────────

async def stdin_loop():
    global outgoing_lang

    session: PromptSession = PromptSession(
        history=InMemoryHistory(),
        style=_STYLE,
    )

    def _prompt() -> HTML:
        nick = client.user.display_name if client.user else "?"
        chan = selected_channel.name if selected_channel else "?"
        lang_tag = f" →{outgoing_lang}" if outgoing_lang else ""
        return HTML(
            f"<prompt-nick>[{nick}]</prompt-nick>"
            f"<prompt-channel> #{chan}{lang_tag}&gt;</prompt-channel> "
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
                # outgoing translation: message ending with -r triggers translate
                if outgoing_lang and line.endswith("-r"):
                    text = line[:-2].rstrip()
                    translated = translate_outgoing(text, outgoing_lang)
                    if translated is None:
                        continue  # error already printed
                    print_status(f"→ {translated}")
                    line = translated
                try:
                    await selected_channel.send(encode_mentions(line))
                except Exception as e:
                    print_error(f"Send failed: {e}")


async def handle_command(cmd: str, args: list[str]) -> None:
    global outgoing_lang

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
        print_sep("CHANNELS")
        for g in sorted(client.guilds, key=lambda g: g.name.lower()):
            _print(f"\x1b[33m  {g.name}\x1b[0m")
            for c in g.text_channels:
                if not c.permissions_for(g.me).view_channel:
                    continue
                marker = " ◀" if (selected_channel and c.id == selected_channel.id) else ""
                topic = f"  — {c.topic[:50]}" if c.topic else ""
                _print(f"\x1b[94m    #{c.name}\x1b[0m\x1b[90m{topic}{marker}\x1b[0m")
        print_sep()

    elif cmd == "join":
        if not args:
            print_error("Usage: /join <channel name or id>  (optional: /join <name> <backfill>)")
            return
        backfill_arg = 50
        if len(args) >= 2 and args[-1].isdigit():
            backfill_arg = min(int(args[-1]), 1000)
            args = args[:-1]
        await _join_channel(" ".join(args), backfill=backfill_arg)

    elif cmd == "clear":
        print("\x1b[2J\x1b[H", end="")

    elif cmd == "translate":
        if not _TRANSLATE_AVAILABLE:
            print_error("deep-translator not installed.")
            return
        if not args or args[0].lower() == "off":
            outgoing_lang = None
            print_status("Outgoing translation disabled.")
        else:
            outgoing_lang = args[0].lower()
            print_status(f"Outgoing translation enabled → {outgoing_lang}  (end messages with -r to translate)")

    elif cmd == "help":
        print_sep("COMMANDS")
        for line in [
            "/me <text>          — send an action",
            "/topic              — show channel topic",
            "/list               — list channels",
            "/join <name|id>     — join a channel (optional: /join <name> <backfill>)",
            "/clear              — clear screen",
            "/translate <lang>   — translate outgoing msgs ending in -r (e.g. ru, de, ja)",
            "/translate off      — disable outgoing translation",
            "/quit               — disconnect",
        ]:
            print(f"  {line}")
        print_sep()

    else:
        print_error(f"Unknown command: /{cmd}  (try /help)")


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print_sep("discord IRC proxy")
    client.run(TOKEN)
