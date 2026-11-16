# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
"""Snapshots of every view at 120 by 36, and of the guides view at 100 by 30."""

import re
from collections.abc import Awaitable, Callable
from pathlib import Path

from textual.pilot import Pilot

from .helpers import command, make_app, run_done, settle

type Steps = Callable[[Pilot], Awaitable[None]]
type SnapCompare = Callable[..., bool]

WIDE = (120, 36)
NARROW = (100, 30)
SNAPSHOTS = Path(__file__).parent / "__snapshots__"
TOKEN_LIKE = re.compile(r"hf_[A-Za-z0-9]{8,}")


def steps(*commands: str, keys: tuple[str, ...] = (), deploy: bool = False) -> Steps:
    async def run(pilot: Pilot) -> None:
        await settle(pilot)
        for text in commands:
            await command(pilot, text)
        if deploy:
            await pilot.press("u")
            await settle(pilot)
            await pilot.press("y")
            await run_done(pilot)
        if keys:
            await pilot.press(*keys)
        await settle(pilot)

    return run


def test_guides(snap_compare: SnapCompare) -> None:
    assert snap_compare(make_app(), terminal_size=WIDE, run_before=steps())


def test_guides_narrow(snap_compare: SnapCompare) -> None:
    assert snap_compare(make_app(), terminal_size=NARROW, run_before=steps())


def test_search(snap_compare: SnapCompare) -> None:
    assert snap_compare(make_app(), terminal_size=WIDE, run_before=steps(keys=("slash", *"cache")))


def test_doctor(snap_compare: SnapCompare) -> None:
    assert snap_compare(make_app(), terminal_size=WIDE, run_before=steps("doctor optimized-baseline", keys=("G",)))


def test_plan(snap_compare: SnapCompare) -> None:
    assert snap_compare(make_app(), terminal_size=WIDE, run_before=steps("plan optimized-baseline", keys=("j", "j", "j", "j")))


def test_deploy(snap_compare: SnapCompare) -> None:
    assert snap_compare(make_app(), terminal_size=WIDE, run_before=steps("plan optimized-baseline", deploy=True))


def test_stacks(snap_compare: SnapCompare) -> None:
    assert snap_compare(make_app(), terminal_size=WIDE, run_before=steps("stacks"))


def test_stack(snap_compare: SnapCompare) -> None:
    assert snap_compare(make_app(), terminal_size=WIDE, run_before=steps("stacks", keys=("enter",)))


def test_logs(snap_compare: SnapCompare) -> None:
    assert snap_compare(make_app(), terminal_size=WIDE, run_before=steps("stacks", keys=("enter", "l")))


def test_help(snap_compare: SnapCompare) -> None:
    assert snap_compare(make_app(), terminal_size=WIDE, run_before=steps(keys=("question_mark",)))


def test_no_snapshot_holds_a_token() -> None:
    files = [p for p in SNAPSHOTS.rglob("*") if p.is_file()]
    assert len(files) >= 10
    for path in files:
        text = path.read_text()
        assert not TOKEN_LIKE.search(text), path
        assert "HF_TOKEN=hf" not in text, path
