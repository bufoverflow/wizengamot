from __future__ import annotations

import asyncio
import io
import os
import unittest
from unittest.mock import patch

from wizengamot.live import LiveTail, SessionWaitDisplay, describe_sdk_message, style_status_line


class TTYBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


class NonTTYBuffer(io.StringIO):
    def isatty(self) -> bool:
        return False


class FakeToolUse:
    name = "WebSearch"
    input = {"query": "authoritative protocol capability documentation"}


class FakeAssistantMessage:
    content = [FakeToolUse()]


class FakeUnknownToolUse:
    name = "FutureTool"
    input = {"opaque_argument": "sensitive-value-that-must-not-be-shown"}


class FakeUnknownToolMessage:
    content = [FakeUnknownToolUse()]


class FakeTextBlock:
    text = "private model reasoning that should not be echoed"


class FakeTextAssistantMessage:
    content = [FakeTextBlock()]


class LiveDescriptionTests(unittest.TestCase):
    def test_tool_activity_surfaces_tool_without_reasoning_text(self):
        activity = describe_sdk_message(FakeAssistantMessage())
        self.assertEqual(
            activity,
            "WebSearch authoritative protocol capability documentation",
        )

    def test_unknown_tool_arguments_are_not_echoed(self):
        activity = describe_sdk_message(FakeUnknownToolMessage())
        self.assertEqual(activity, "FutureTool")
        self.assertNotIn("sensitive-value", activity)

    def test_free_form_assistant_text_is_not_echoed(self):
        activity = describe_sdk_message(FakeTextAssistantMessage())
        self.assertEqual(activity, "analyzing")
        self.assertNotIn("private model reasoning", activity)


class LiveTailTests(unittest.IsolatedAsyncioTestCase):
    async def test_tty_renderer_is_transient_and_restores_cursor(self):
        stream = TTYBuffer()
        with patch.dict(os.environ, {"TERM": "xterm-256color"}, clear=True):
            tail = LiveTail(
                total=2,
                stream=stream,
                enabled=True,
                refresh_interval=0.01,
            )

        await tail.start()
        tail.start_agent("alpha", "starting")
        tail.update_agent("alpha", "Read knowledge/example.md")
        await asyncio.sleep(0.03)
        tail.complete_agent("alpha")
        tail.start_agent("beta", "WebSearch current protocol docs")
        await asyncio.sleep(0.02)
        tail.complete_agent("beta")
        await tail.stop()

        output = stream.getvalue()
        self.assertIn("\x1b[?25l", output)
        self.assertIn("\x1b[?25h", output)
        self.assertIn("\x1b[36m", output)
        self.assertIn("\x1b[34m", output)
        self.assertIn("alpha", output)
        self.assertIn("Read knowledge/example.md", output)
        self.assertIn("beta", output)
        self.assertEqual(tail.rendered_lines, 0)

    async def test_non_tty_renderer_is_silent(self):
        stream = NonTTYBuffer()
        tail = LiveTail(
            total=1,
            stream=stream,
            enabled=True,
            refresh_interval=0.01,
        )

        self.assertFalse(tail.enabled)
        await tail.start()
        tail.start_agent("alpha", "Read knowledge/example.md")
        tail.update_agent("alpha", "WebSearch example")
        tail.complete_agent("alpha")
        await tail.stop()

        self.assertEqual(stream.getvalue(), "")

    def test_status_colors_are_tty_only_and_honor_no_color(self):
        tty = TTYBuffer()
        non_tty = NonTTYBuffer()
        with patch.dict(os.environ, {"TERM": "xterm-256color"}, clear=True):
            colored = style_status_line("complete example", "complete", stream=tty)
            plain = style_status_line("complete example", "complete", stream=non_tty)
        self.assertIn("\x1b[32m", colored)
        self.assertEqual(plain, "complete example")

        with patch.dict(os.environ, {"TERM": "xterm-256color", "NO_COLOR": ""}, clear=True):
            no_color = style_status_line("complete example", "complete", stream=tty)
        self.assertEqual(no_color, "complete example")


class SessionWaitDisplayTests(unittest.IsolatedAsyncioTestCase):
    async def test_tty_wait_message_has_colored_countdown_and_cleans_up(self):
        stream = TTYBuffer()
        with patch.dict(os.environ, {"TERM": "xterm-256color"}, clear=True):
            display = SessionWaitDisplay(
                run_id="research-001",
                resume_at="2026-08-30T05:00:00+00:00",
                wait_seconds=0.04,
                reset_count=2,
                completed=17,
                retrying=1,
                deferred=12,
                stream=stream,
                refresh_interval=0.01,
            )
            await display.start()
            await asyncio.sleep(0.025)
            await display.stop(resuming=True)

        output = stream.getvalue()
        self.assertIn("WAITING FOR CLAUDE USAGE LIMIT RESET", output)
        self.assertIn("research-001", output)
        self.assertIn("automatic resume", output)
        self.assertIn("remaining", output)
        self.assertIn("reset #2", output)
        self.assertIn("\x1b[33m", output)
        self.assertIn("\x1b[36m", output)
        self.assertIn("\x1b[32m", output)
        self.assertIn("\x1b[?25l", output)
        self.assertIn("\x1b[?25h", output)
        self.assertIn("RESUMING:", output)
        self.assertEqual(display.rendered_lines, 0)

    async def test_non_tty_wait_message_is_clear_and_ansi_free(self):
        stream = NonTTYBuffer()
        display = SessionWaitDisplay(
            run_id="research-002",
            resume_at="2026-08-30T05:00:00+00:00",
            wait_seconds=90,
            reset_count=1,
            completed=5,
            retrying=1,
            deferred=8,
            stream=stream,
        )

        await display.start()
        await display.stop(resuming=True)

        output = stream.getvalue()
        self.assertIn("WAITING: Claude usage limit reached", output)
        self.assertIn("run research-002 is checkpointed", output)
        self.assertIn("Automatic resume at", output)
        self.assertIn("reset #1", output)
        self.assertIn("RESUMING:", output)
        self.assertNotIn("\x1b[", output)


if __name__ == "__main__":
    unittest.main()
