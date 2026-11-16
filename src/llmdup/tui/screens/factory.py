# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
"""Build the screen of a view."""

from llmdup.tui.keys import View
from llmdup.tui.nav import ViewRef
from llmdup.tui.screens.base import Flash, ViewScreen
from llmdup.tui.screens.deploy import DeployScreen
from llmdup.tui.screens.doctor import DoctorScreen
from llmdup.tui.screens.guides import GuidesScreen
from llmdup.tui.screens.help import HelpScreen
from llmdup.tui.screens.logs import LogsScreen
from llmdup.tui.screens.plan import PlanScreen
from llmdup.tui.screens.stacks import StackScreen, StacksScreen

SCREENS: dict[View, type[ViewScreen]] = {
    View.GUIDES: GuidesScreen,
    View.DOCTOR: DoctorScreen,
    View.PLAN: PlanScreen,
    View.DEPLOY: DeployScreen,
    View.STACKS: StacksScreen,
    View.LOGS: LogsScreen,
    View.HELP: HelpScreen,
}


def make_screen(ref: ViewRef, message: Flash | None = None) -> ViewScreen:
    """Build the screen of a view; the stacks view with a namespace is that stack."""
    if ref.view is View.STACKS and ref.arg:
        return StackScreen(ref, message=message)
    return SCREENS[ref.view](ref, message=message)
