# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
"""Pilot helpers shared by the UI tests."""

import asyncio
import html
import re
import time
from collections.abc import Awaitable, Callable

from textual.pilot import Pilot
from textual.screen import ModalScreen

from llmdup.tui.app import LlmdupApp
from llmdup.tui.screens.base import ViewScreen

from .fakecore import FakeCore

TEXT = re.compile(r"<text[^>]*>(.*?)</text>", re.DOTALL)
PLACED = re.compile(r'<text[^>]*?x="([\d.]+)"[^>]*?y="([\d.]+)"[^>]*>(.*?)</text>', re.DOTALL)


def make_app(core: FakeCore | None = None) -> LlmdupApp:
    return LlmdupApp(core or FakeCore())


def drive(scenario: Callable[[Pilot], Awaitable[None]], app: LlmdupApp | None = None, size: tuple[int, int] = (120, 36)) -> LlmdupApp:
    """Run the app headless, let it load, then play a scenario."""
    app = app or make_app()

    async def main() -> None:
        async with app.run_test(size=size) as pilot:
            await settle(pilot)
            await scenario(pilot)

    asyncio.run(main())
    return app


async def settle(pilot: Pilot) -> None:
    """Wait for loading workers, not for a run nor for a worker that waits on a dialog, then for the screen."""
    app = pilot.app
    for _ in range(3):
        await pilot.pause()
        waiting = isinstance(app.screen, ModalScreen)
        pending = [w for w in app.workers if w.group not in {"run", *(["prepare"] if waiting else [])} and not w.is_finished]
        if pending:
            await asyncio.wait_for(app.workers.wait_for_complete(pending), timeout=20)
    await pilot.pause()


async def command(pilot: Pilot, text: str) -> None:
    """Type a `:` command and run it."""
    await pilot.press(":", *text, "enter")
    await settle(pilot)


async def until(pilot: Pilot, done: Callable[[], bool], timeout: float = 10) -> None:
    """Pause until a condition holds."""
    deadline = time.monotonic() + timeout
    while not done():
        if time.monotonic() > deadline:
            msg = "condition not reached"
            raise TimeoutError(msg)
        await pilot.pause(0.05)


async def run_done(pilot: Pilot, timeout: float = 10) -> None:
    """Wait until the app's run has ended."""
    app = pilot.app
    assert isinstance(app, LlmdupApp)
    await until(pilot, lambda: app.deploy_run is not None and not app.deploy_run.running, timeout)
    await settle(pilot)


def view(app: LlmdupApp) -> ViewScreen:
    screen = app.screen
    assert isinstance(screen, ViewScreen), screen
    return screen


def drawn(app: LlmdupApp) -> str:
    """The text on the terminal now, as the SVG screenshot holds it."""
    return "".join(html.unescape(t) for t in TEXT.findall(app.export_screenshot())).replace("\xa0", " ")


def screen_lines(app: LlmdupApp) -> list[str]:
    """The terminal as lines of text, rebuilt from the SVG screenshot."""
    rows: dict[float, list[tuple[float, str]]] = {}
    for x, y, text in PLACED.findall(app.export_screenshot()):
        rows.setdefault(float(y), []).append((float(x), html.unescape(text).replace("\xa0", " ")))
    return ["".join(t for _, t in sorted(parts)) for _, parts in sorted(rows.items())]
