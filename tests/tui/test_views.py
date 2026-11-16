# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
"""Pilot tests: every view by key and by command, filter, sort, history and the prompt."""

import pytest
from textual.pilot import Pilot

from llmdup.tui.app import LlmdupApp
from llmdup.tui.keys import View
from llmdup.tui.nav import DEPLOY_LOG
from llmdup.tui.screens.base import ViewScreen
from llmdup.tui.screens.deploy import DeployScreen
from llmdup.tui.screens.dialogs import ConfirmDialog, TextDialog
from llmdup.tui.screens.doctor import DoctorScreen
from llmdup.tui.screens.guides import GuidesScreen
from llmdup.tui.screens.help import HelpScreen
from llmdup.tui.screens.logs import LogsScreen
from llmdup.tui.screens.plan import PlanScreen
from llmdup.tui.screens.stacks import StackScreen, StacksScreen
from llmdup.tui.widgets.crumbs import Level
from llmdup.tui.widgets.log import ViewLog
from llmdup.tui.widgets.prompt import Prompt, PromptInput
from llmdup.tui.widgets.table import ViewTable

from .fakecore import NAMESPACE, FakeCore
from .helpers import command, drive, run_done, settle, view


def app_of(pilot: Pilot) -> LlmdupApp:
    app = pilot.app
    assert isinstance(app, LlmdupApp)
    return app


def screen(pilot: Pilot) -> ViewScreen:
    return view(app_of(pilot))


def table(pilot: Pilot) -> ViewTable:
    return screen(pilot).query_one("#list", ViewTable)


def flash(pilot: Pilot) -> tuple[str, Level] | None:
    return screen(pilot).flash_message


def drawn_rows(grid: ViewTable) -> list[str]:
    """The rows the DataTable draws now, as plain text."""
    return [" ".join(c.plain for c in grid.get_row_at(i)) for i in range(grid.row_count)]


async def open_guide(pilot: Pilot, guide: str, key: str) -> None:
    table(pilot).select_key(guide)
    await pilot.press(key)
    await settle(pilot)


def test_guides_view_lists_the_fixture_guides_with_support_and_deployed() -> None:
    async def scenario(pilot: Pilot) -> None:
        await pilot.pause()
        assert isinstance(screen(pilot), GuidesScreen)
        rows = {r.key: r.plain for r in table(pilot).kept}
        assert {"optimized-baseline", "pd-disaggregation", "flow-control"} <= set(rows)
        assert rows["optimized-baseline"][3:] == ("validated", "yes")
        assert rows["pd-disaggregation"][-1] == "no"
        assert screen(pilot).title_text().plain == f"guides(all)[{len(rows)}]"

    drive(scenario)


def test_every_view_opens_by_its_key() -> None:
    async def scenario(pilot: Pilot) -> None:
        await open_guide(pilot, "optimized-baseline", "D")
        assert isinstance(screen(pilot), DoctorScreen)
        assert screen(pilot).ref.arg == "optimized-baseline"
        await pilot.press("escape")
        await settle(pilot)
        assert isinstance(screen(pilot), GuidesScreen)
        await open_guide(pilot, "optimized-baseline", "enter")
        assert isinstance(screen(pilot), PlanScreen)
        await pilot.press("u")
        await settle(pilot)
        assert isinstance(app_of(pilot).screen, ConfirmDialog)
        await pilot.press("y")
        await run_done(pilot)
        assert isinstance(screen(pilot), DeployScreen)
        await pilot.press("l")
        await settle(pilot)
        assert isinstance(screen(pilot), LogsScreen)
        assert screen(pilot).ref.arg == DEPLOY_LOG
        await pilot.press("question_mark")
        await settle(pilot)
        assert isinstance(screen(pilot), HelpScreen)
        await pilot.press("escape")
        await settle(pilot)
        assert isinstance(screen(pilot), LogsScreen)

    drive(scenario)


def test_guides_deploy_key_runs_the_guide() -> None:
    async def scenario(pilot: Pilot) -> None:
        await open_guide(pilot, "optimized-baseline", "u")
        assert isinstance(app_of(pilot).screen, ConfirmDialog)
        await pilot.press("y")
        await run_done(pilot)
        assert isinstance(screen(pilot), DeployScreen)
        run = app_of(pilot).deploy_run
        assert run is not None
        assert run.ok

    drive(scenario)


@pytest.mark.parametrize(
    ("text", "kind", "arg"),
    [
        ("guides", GuidesScreen, None),
        ("doctor", DoctorScreen, None),
        ("doctor optimized-baseline", DoctorScreen, "optimized-baseline"),
        ("plan optimized-baseline", PlanScreen, "optimized-baseline"),
        ("deploy", DeployScreen, None),
        ("stacks", StacksScreen, None),
        ("logs", LogsScreen, DEPLOY_LOG),
        ("help", HelpScreen, None),
    ],
)
def test_every_view_opens_by_command(text: str, kind: type[ViewScreen], arg: str | None) -> None:
    async def scenario(pilot: Pilot) -> None:
        await command(pilot, "stacks" if kind is GuidesScreen else "guides")
        await command(pilot, text)
        assert isinstance(screen(pilot), kind)
        assert screen(pilot).ref.arg == arg

    drive(scenario)


def test_stack_view_opens_with_enter_and_pod_logs_with_l() -> None:
    async def scenario(pilot: Pilot) -> None:
        await command(pilot, "stacks")
        assert [r.key for r in table(pilot).kept] == [NAMESPACE]
        await pilot.press("enter")
        await settle(pilot)
        assert isinstance(screen(pilot), StackScreen)
        assert len(table(pilot).kept) == 3
        await pilot.press("j", "l")
        await settle(pilot)
        logs = screen(pilot)
        assert isinstance(logs, LogsScreen)
        assert logs.ref.arg == f"{NAMESPACE}/optimized-baseline-nvidia-gpu-vllm-decode-7d4b9c6f5-8hjzn"
        assert len(logs.query_one(ViewLog).kept) == 5

    drive(scenario)


def test_typing_cache_leaves_only_rows_that_contain_cache() -> None:
    async def scenario(pilot: Pilot) -> None:
        grid = table(pilot)
        before = len(grid.kept)
        await pilot.press("slash", *"cache")
        await settle(pilot)
        rows = drawn_rows(grid)
        assert rows
        assert len(rows) < before
        assert all("cache" in row.casefold() for row in rows)
        assert {r.key for r in grid.source if "cache" in " ".join(r.plain).casefold()} == {r.key for r in grid.kept}
        assert screen(pilot).title_text().plain == f"guides(all)[{len(rows)}] </cache>"
        await pilot.press("enter")
        await settle(pilot)
        assert not screen(pilot).query_one(Prompt).display
        assert len(drawn_rows(grid)) == len(rows)
        await pilot.press("escape")
        await settle(pilot)
        assert len(drawn_rows(grid)) == before
        assert isinstance(screen(pilot), GuidesScreen)

    drive(scenario)


def test_filter_invert_fuzzy_and_escape() -> None:
    async def scenario(pilot: Pilot) -> None:
        grid = table(pilot)
        total = len(grid.source)
        await pilot.press("slash", *"!cache")
        await settle(pilot)
        rows = drawn_rows(grid)
        assert rows
        assert not any("cache" in row.casefold() for row in rows)
        assert len(rows) + 3 == total
        await pilot.press("ctrl+u", *"-f pdis")
        await settle(pilot)
        assert "pd-disaggregation" in [r.key for r in grid.kept]
        assert len(grid.kept) < total
        await pilot.press("escape")
        await settle(pilot)
        assert len(grid.kept) == total
        assert screen(pilot).row_filter.raw == ""

    drive(scenario)


def test_sort_by_name_status_and_age() -> None:
    async def scenario(pilot: Pilot) -> None:
        grid = table(pilot)
        await pilot.press("N")
        names = [r.name for r in grid.kept]
        assert names == sorted(names)
        await pilot.press("N")
        assert [r.name for r in grid.kept] == sorted(names, reverse=True)
        await pilot.press("S")
        statuses = [r.status for r in grid.kept]
        assert statuses == sorted(statuses)
        await pilot.press("A")
        assert flash(pilot) == ("this view has no age column", Level.WARN)
        await command(pilot, "stacks")
        await pilot.press("A")
        assert flash(pilot) == ("sorted by age", Level.INFO)

    drive(scenario)


def test_history_back_forward_and_last_view() -> None:
    async def scenario(pilot: Pilot) -> None:
        await command(pilot, "stacks")
        await command(pilot, "help")
        await pilot.press("left_square_bracket")
        await settle(pilot)
        assert isinstance(screen(pilot), StacksScreen)
        await pilot.press("left_square_bracket")
        await settle(pilot)
        assert isinstance(screen(pilot), GuidesScreen)
        await pilot.press("right_square_bracket")
        await settle(pilot)
        assert isinstance(screen(pilot), StacksScreen)
        await pilot.press("minus")
        await settle(pilot)
        assert isinstance(screen(pilot), GuidesScreen)
        await pilot.press("minus")
        await settle(pilot)
        assert isinstance(screen(pilot), StacksScreen)

    drive(scenario)


def test_prompt_suggests_completes_recalls_and_clears() -> None:
    async def scenario(pilot: Pilot) -> None:
        await pilot.press("colon", *"pl")
        await settle(pilot)
        field = screen(pilot).query_one(PromptInput)
        assert field.suggestion == "plan"
        await pilot.press("tab", "space", *"opt")
        await settle(pilot)
        assert field.suggestion == "plan optimized-baseline"
        await pilot.press("tab")
        assert field.value == "plan optimized-baseline"
        await pilot.press("enter")
        await settle(pilot)
        assert isinstance(screen(pilot), PlanScreen)
        await pilot.press("colon", "up")
        field = screen(pilot).query_one(PromptInput)
        assert field.value == "plan optimized-baseline"
        await pilot.press("down")
        assert field.value == ""
        await pilot.press(*"stack", "ctrl+u")
        assert field.value == ""
        await pilot.press("escape")
        await settle(pilot)
        assert not screen(pilot).query_one(Prompt).display
        assert isinstance(screen(pilot), PlanScreen)

    drive(scenario)


def test_unknown_command_and_namespace_switch() -> None:
    async def scenario(pilot: Pilot) -> None:
        await command(pilot, "nope")
        assert flash(pilot) == ("unknown command :nope; ctrl-a lists them", Level.ERROR)
        await command(pilot, f"ns {NAMESPACE}")
        assert app_of(pilot).namespace == NAMESPACE
        assert ("Namespace", NAMESPACE) in app_of(pilot).header_facts()
        await command(pilot, "ns")
        assert app_of(pilot).namespace is None
        await command(pilot, "ctx")
        assert isinstance(app_of(pilot).screen, TextDialog)

    drive(scenario)


def test_plan_edits_a_value_and_saves_the_script() -> None:
    core = FakeCore()

    async def scenario(pilot: Pilot) -> None:
        await command(pilot, "plan optimized-baseline")
        plan_view = screen(pilot)
        assert isinstance(plan_view, PlanScreen)
        await pilot.press("tab")
        plan_view.variables.select_key("MODEL_SERVER")
        await pilot.press("e")
        await settle(pilot)
        await pilot.press("ctrl+u", *"sg", "tab", "enter")
        await settle(pilot)
        assert plan_view.choices is not None
        assert plan_view.choices.engine == "sglang"
        assert flash(pilot) == ("MODEL_SERVER=sglang", Level.OK)
        await pilot.press("e", "ctrl+u", *"bogus", "enter")
        await settle(pilot)
        message = flash(pilot)
        assert message is not None
        assert message[1] is Level.ERROR
        assert "allowed values" in message[0]
        await pilot.press("ctrl+s")
        assert plan_view.plan is not None
        assert list(core.saved.values()) == [plan_view.plan.script]

    drive(scenario, app=LlmdupApp(core))


def test_doctor_shows_fix_and_copies_it() -> None:
    async def scenario(pilot: Pilot) -> None:
        await command(pilot, "doctor optimized-baseline")
        table(pilot).select_key("HF_TOKEN")
        await pilot.press("enter")
        await settle(pilot)
        assert isinstance(app_of(pilot).screen, TextDialog)
        await pilot.press("escape", "c")
        await settle(pilot)
        assert app_of(pilot).clipboard == "export HF_TOKEN=<your Hugging Face token>"

    drive(scenario)


def test_a_blocked_deploy_opens_the_doctor() -> None:
    async def scenario(pilot: Pilot) -> None:
        await open_guide(pilot, "optimized-baseline", "u")
        await settle(pilot)
        assert isinstance(screen(pilot), DoctorScreen)
        assert app_of(pilot).deploy_run is None

    drive(scenario, app=LlmdupApp(FakeCore(block_deploy=True)))


def test_header_hides_and_shows() -> None:
    async def scenario(pilot: Pilot) -> None:
        await pilot.press("ctrl+e")
        assert not app_of(pilot).show_header
        await command(pilot, "stacks")
        assert not screen(pilot).query_one("#header").display
        await pilot.press("ctrl+e")
        assert screen(pilot).query_one("#header").display

    drive(scenario)


def test_view_enum_covers_every_screen() -> None:
    assert {v.value for v in View} == {"guides", "doctor", "plan", "deploy", "stacks", "logs", "help"}
