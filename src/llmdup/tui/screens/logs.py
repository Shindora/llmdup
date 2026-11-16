# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
"""The logs view: a pod's log, or the full log of the current deploy."""

from pathlib import Path
from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Vertical

from llmdup.core.models import RunEvent
from llmdup.tui import keys, theme
from llmdup.tui.keys import View
from llmdup.tui.nav import DEPLOY_LOG, ViewRef
from llmdup.tui.screens.base import Flash, ViewScreen, view_title
from llmdup.tui.screens.deploy import EMPTY, log_text
from llmdup.tui.widgets.crumbs import Level
from llmdup.tui.widgets.log import ViewLog


class LogsScreen(ViewScreen):
    """Read a log with autoscroll, wrap, timestamps, filter and save."""

    VIEW = View.LOGS
    BINDINGS: ClassVar[list[BindingType]] = keys.bindings(keys.Scope.SCREEN, View.LOGS)

    def __init__(self, ref: ViewRef, *, message: Flash | None = None) -> None:
        super().__init__(ref, message=message)
        self.timestamps = False
        self.shown = 0
        self.raw: list[str] = []

    @property
    def deploy_log(self) -> bool:
        """Whether this is the deploy log rather than a pod's."""
        return self.ref.arg in {None, DEPLOY_LOG}

    @property
    def source(self) -> tuple[str, str]:
        """The namespace and pod of a pod log."""
        namespace, _, pod = (self.ref.arg or "").partition("/")
        return namespace, pod

    def body(self) -> ComposeResult:
        """The log pane."""
        with Vertical(id="main", classes="box"):
            yield ViewLog(id="list")

    @property
    def log_view(self) -> ViewLog:
        """The log pane."""
        return self.query_one("#list", ViewLog)

    def on_mount(self) -> None:
        """Follow the deploy log as it grows."""
        super().on_mount()
        if self.deploy_log:
            self.tui.run_listeners.append(self.follow)

    def on_unmount(self) -> None:
        """Stop following."""
        if self.follow in self.tui.run_listeners:
            self.tui.run_listeners.remove(self.follow)

    async def fetch(self) -> None:
        """Read the log again."""
        if self.deploy_log:
            self.shown, self.raw = 0, []
            self.log_view.set_lines([])
            self.follow(None)
            return
        namespace, pod = self.source
        found = await self.tui.core.pod_log(namespace, pod, timestamps=self.timestamps)
        if found.error:
            self.flash(found.error, Level.ERROR)
        self.raw = found.lines
        self.log_view.set_lines(Text(line) for line in found.lines)
        self.redraw_titles()

    def follow(self, event: RunEvent | None) -> None:
        """Append the deploy's new lines."""
        del event
        run = self.tui.deploy_run
        if run is None:
            self.log_view.set_lines([Text(EMPTY, style=theme.DIM)])
            return
        new = run.log[self.shown :]
        self.shown = len(run.log)
        self.raw.extend(e.text for e in new)
        self.log_view.add_lines(log_text(e, timestamps=self.timestamps) for e in new)
        self.redraw_titles()

    def title_text(self) -> Text:
        """`logs(<pod>)[lines]`."""
        name = DEPLOY_LOG if self.deploy_log else self.source[1]
        return view_title("logs", name, str(len(self.log_view.kept)), self.row_filter)

    def crumbs(self) -> list[str]:
        """Where the log comes from."""
        if self.deploy_log:
            return ["deploy", "logs"]
        return ["stacks", self.source[0], "logs"]

    def status(self) -> Flash:
        """The display settings."""
        flags = [f"autoscroll {'on' if self.log_view.auto_scroll else 'off'}", f"wrap {'on' if self.log_view.wrap else 'off'}"]
        flags.append(f"timestamps {'on' if self.timestamps else 'off'}")
        return " · ".join(flags), Level.INFO

    def action_autoscroll(self) -> None:
        """Follow the end of the log, or stop."""
        self.log_view.auto_scroll = not self.log_view.auto_scroll
        if self.log_view.auto_scroll:
            self.log_view.scroll_end(animate=False)
        self.clear_flash()

    def action_wrap(self) -> None:
        """Wrap long lines, or cut them."""
        self.log_view.toggle_wrap()
        self.clear_flash()

    def action_timestamps(self) -> None:
        """Show or hide timestamps, reading the log again."""
        self.timestamps = not self.timestamps
        self.clear_flash()
        self.load()

    def action_save(self) -> None:
        """Write the log to the current directory."""
        name = DEPLOY_LOG if self.deploy_log else self.source[1]
        path = self.tui.core.save(Path(f"llmdup-{name}.log"), "\n".join(self.raw) + "\n")
        self.flash(f"saved {path}", Level.OK)

    def copy_text(self) -> str | None:
        """Copy the lines the filter keeps."""
        return "\n".join(line.plain for line in self.log_view.kept) or None
