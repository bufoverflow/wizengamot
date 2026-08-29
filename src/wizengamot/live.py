from __future__ import annotations

import asyncio
import shutil
import sys
import time
from dataclasses import dataclass
from typing import Any, TextIO


SPINNER = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


def _single_line(value: Any, *, limit: int = 96) -> str:
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 1)]}…"


def _tool_activity(name: str, value: Any) -> str:
    if not isinstance(value, dict):
        return name

    keys_by_tool = {
        "Read": ("file_path", "path"),
        "Grep": ("pattern", "path"),
        "Glob": ("pattern", "path"),
        "WebSearch": ("query",),
        "WebFetch": ("url",),
    }
    keys = keys_by_tool.get(name)
    if keys is None:
        # Unknown/future tools expose only the tool name. Never guess which
        # arguments are safe to surface in the transient terminal view.
        return name

    pieces: list[str] = []
    for key in keys:
        item = value.get(key)
        if item is not None and str(item).strip():
            pieces.append(_single_line(item, limit=72))

    return f"{name} {' · '.join(pieces)}" if pieces else name


def describe_sdk_message(message: Any) -> str | None:
    """Return a terse activity label without exposing model reasoning text.

    The Agent SDK message classes can evolve independently of Wizengamot, so
    this function uses attribute inspection rather than importing every
    concrete message/content-block type. Tool names and their small identifying
    arguments are useful live activity. Free-form assistant text is deliberately
    reduced to "analyzing" rather than echoed into the terminal.
    """

    class_name = type(message).__name__.lower()
    content = getattr(message, "content", None)
    if isinstance(content, (list, tuple)):
        for block in content:
            tool_name = getattr(block, "name", None)
            if isinstance(tool_name, str) and tool_name:
                return _tool_activity(tool_name, getattr(block, "input", None))
        if content:
            return "analyzing"

    if "resultmessage" in class_name or class_name == "result":
        return "finalizing structured report"
    if "system" in class_name:
        return "initializing session"
    if "user" in class_name:
        return "processing tool result"
    if "assistant" in class_name:
        return "analyzing"
    return None


@dataclass
class _AgentActivity:
    started_at: float
    activity: str


class LiveTail:
    """Transient multi-agent terminal activity view.

    The renderer writes only to stderr and only when stderr is a TTY. It owns a
    small ANSI redraw region while the run is active, then erases that region
    and restores the cursor. Persistent CLI output therefore remains stdout.
    """

    def __init__(
        self,
        *,
        total: int,
        stream: TextIO | None = None,
        enabled: bool = True,
        refresh_interval: float = 0.12,
    ) -> None:
        self.total = total
        self.stream = stream or sys.stderr
        is_tty = bool(getattr(self.stream, "isatty", lambda: False)())
        self.enabled = bool(enabled and is_tty)
        self.refresh_interval = refresh_interval
        self._running: dict[str, _AgentActivity] = {}
        self._completed_names: set[str] = set()
        self._frame = 0
        self._rendered_lines = 0
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def rendered_lines(self) -> int:
        return self._rendered_lines

    async def start(self) -> None:
        if not self.enabled or self._task is not None:
            return
        self.stream.write("\x1b[?25l")
        self.stream.flush()
        self._render()
        self._task = asyncio.create_task(self._spin())

    async def stop(self) -> None:
        if not self.enabled:
            return
        self._stop.set()
        try:
            if self._task is not None:
                await self._task
        finally:
            self._task = None
            try:
                self._clear()
            finally:
                # Cursor restoration is a terminal-safety invariant even when
                # the spinner task or redraw path fails.
                self.stream.write("\x1b[?25h")
                self.stream.flush()

    def start_agent(self, name: str, activity: str = "starting") -> None:
        if not self.enabled:
            return
        self._running[name] = _AgentActivity(
            started_at=time.monotonic(),
            activity=_single_line(activity),
        )

    def update_agent(self, name: str, activity: str) -> None:
        if not self.enabled:
            return
        state = self._running.get(name)
        if state is None:
            self.start_agent(name, activity)
            return
        state.activity = _single_line(activity)

    def complete_agent(self, name: str) -> None:
        if not self.enabled:
            return
        self._running.pop(name, None)
        self._completed_names.add(name)

    async def _spin(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(self.refresh_interval)
            self._frame += 1
            self._render()

    def _render(self) -> None:
        if not self.enabled:
            return

        width, height = shutil.get_terminal_size((120, 24))
        completed = len(self._completed_names)
        active = len(self._running)
        queued = max(0, self.total - completed - active)
        spinner = SPINNER[self._frame % len(SPINNER)]

        lines = [
            self._clip(
                f"{spinner} Wizengamot · {completed}/{self.total} complete"
                f" · {active} active · {queued} queued",
                width,
            )
        ]

        max_agent_lines = max(1, height - 4)
        states = sorted(self._running.items(), key=lambda item: item[1].started_at)
        visible = states[:max_agent_lines]
        for name, state in visible:
            elapsed = max(0, int(time.monotonic() - state.started_at))
            minutes, seconds = divmod(elapsed, 60)
            line = (
                f"  {spinner} {name} · {minutes:02d}:{seconds:02d}"
                f" · {state.activity}"
            )
            lines.append(self._clip(line, width))

        hidden = len(states) - len(visible)
        if hidden > 0:
            lines.append(self._clip(f"  … {hidden} more active agents", width))

        self._replace_region(lines)

    @staticmethod
    def _clip(value: str, width: int) -> str:
        width = max(20, width)
        if len(value) <= width:
            return value
        return f"{value[: max(0, width - 1)]}…"

    def _replace_region(self, lines: list[str]) -> None:
        previous = self._rendered_lines
        if previous:
            self.stream.write(f"\x1b[{previous}F")

        for line in lines:
            self.stream.write(f"\x1b[2K{line}\n")

        stale = max(0, previous - len(lines))
        for _ in range(stale):
            self.stream.write("\x1b[2K\n")
        if stale:
            self.stream.write(f"\x1b[{stale}F")

        self._rendered_lines = len(lines)
        self.stream.flush()

    def _clear(self) -> None:
        count = self._rendered_lines
        if not count:
            return

        self.stream.write(f"\x1b[{count}F")
        for index in range(count):
            self.stream.write("\x1b[2K")
            if index < count - 1:
                self.stream.write("\n")
        if count > 1:
            self.stream.write(f"\x1b[{count - 1}F")
        self.stream.write("\r")
        self._rendered_lines = 0
