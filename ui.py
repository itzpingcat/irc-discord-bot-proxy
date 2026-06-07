"""
ui.py — IRC-style terminal UI using prompt_toolkit.
Incoming messages never clobber the input bar.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Callable, Awaitable

from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import InMemoryHistory

_STYLE = Style.from_dict({
    "prompt-nick":    "#00ff88 bold",
    "prompt-channel": "#aaaaaa",
})


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M")


def print_message(author: str, content: str, *, is_self: bool = False) -> None:
    ts = _now()
    nick_color = "\x1b[92m" if is_self else "\x1b[94m"
    print(f"\x1b[90m[{ts}]\x1b[0m \x1b[1m<\x1b[0m{nick_color}{author}\x1b[0m\x1b[1m>\x1b[0m {content}")


def print_action(author: str, content: str) -> None:
    ts = _now()
    print(f"\x1b[90m[{ts}]\x1b[0m \x1b[93m* {author} {content}\x1b[0m")


def print_status(text: str) -> None:
    ts = _now()
    print(f"\x1b[90m[{ts}]\x1b[0m \x1b[33m*** {text}\x1b[0m")


def print_error(text: str) -> None:
    ts = _now()
    print(f"\x1b[90m[{ts}]\x1b[0m \x1b[31m!!! {text}\x1b[0m")


def print_topic(channel: str, topic: str) -> None:
    ts = _now()
    print(f"\x1b[90m[{ts}]\x1b[0m \x1b[35m--- Topic for #{channel}: {topic or '(none)'}\x1b[0m")


def print_separator(label: str = "") -> None:
    width = 60
    if label:
        pad = (width - len(label) - 2) // 2
        right = width - pad - len(label) - 2
        print(f"\x1b[90m{'─' * pad} {label} {'─' * right}\x1b[0m")
    else:
        print(f"\x1b[90m{'─' * width}\x1b[0m")


def print_history_message(author: str, content: str, dt: datetime) -> None:
    ts = dt.strftime("%H:%M")
    print(f"\x1b[90m[{ts}] <{author}> {content}\x1b[0m")


async def ainput(prompt: str = "") -> str:
    """Non-blocking input() via executor — safe to await in async code."""
    loop = asyncio.get_running_loop()
    if prompt:
        print(prompt, end="", flush=True)
    return await loop.run_in_executor(None, input)


async def pick_from_list(prompt: str, items: list[str]) -> int:
    """Print numbered list, return 0-based index. Fully async-safe."""
    for i, name in enumerate(items, 1):
        print(f"  \x1b[90m{i:3d}.\x1b[0m {name}")
    while True:
        try:
            raw = await ainput(f"\n{prompt}: ")
            idx = int(raw.strip()) - 1
            if 0 <= idx < len(items):
                return idx
            print_error("Out of range.")
        except ValueError:
            print_error("Enter a number.")


async def ask_int(prompt: str, lo: int, hi: int, default: int = 0) -> int:
    while True:
        try:
            raw = await ainput(f"{prompt} [{lo}-{hi}, default {default}]: ")
            raw = raw.strip()
            if not raw:
                return default
            val = int(raw)
            if lo <= val <= hi:
                return val
            print_error(f"Must be {lo}-{hi}.")
        except ValueError:
            print_error("Enter a number.")


CommandHandler = Callable[[str, list[str]], Awaitable[None]]


async def input_loop(
    self_nick: str,
    channel_name: str,
    on_message: Callable[[str], Awaitable[None]],
    on_command: CommandHandler,
) -> None:
    session: PromptSession = PromptSession(
        history=InMemoryHistory(),
        style=_STYLE,
    )

    def _prompt() -> HTML:
        return HTML(
            f"<prompt-nick>[{self_nick}]</prompt-nick>"
            f"<prompt-channel> #{channel_name}&gt;</prompt-channel> "
        )

    with patch_stdout():
        while True:
            try:
                line: str = await session.prompt_async(_prompt)
            except (EOFError, KeyboardInterrupt):
                await on_command("quit", [])
                return

            line = line.strip()
            if not line:
                continue

            if line.startswith("/"):
                parts = line[1:].split(None, 1)
                cmd = parts[0].lower() if parts else ""
                args = parts[1].split() if len(parts) > 1 else []
                await on_command(cmd, args)
            else:
                await on_message(line)
