# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
"""What every view shares: header, prompt, a titled main box, breadcrumbs, and the keys of every view."""

from typing import TYPE_CHECKING, ClassVar, cast

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.screen import Screen
from textual.worker import Worker, WorkerState

from llmdup.tui import keys, theme
from llmdup.tui.keys import View
from llmdup.tui.nav import ViewRef
from llmdup.tui.screens.dialogs import TextDialog
from llmdup.tui.widgets.crumbs import Crumbs, Level
from llmdup.tui.widgets.header import ClusterHeader
from llmdup.tui.widgets.log import ViewLog
from llmdup.tui.widgets.prompt import Mode, Prompt
from llmdup.tui.widgets.table import RowFilter, ViewTable

if TYPE_CHECKING:
    from llmdup.tui.app import LlmdupApp

type Flash = tuple[str, Level]


def view_title(name: str, scope: str | None, count: str | None, row_filter: RowFilter) -> Text:
    """Draw a box title such as `guides(all)[3] </cache>`."""
    text = Text.assemble((name, f"bold {theme.ACCENT}"))
    if scope:
        text.append(f"({scope})", style=theme.KEY)
    if count is not None:
        text.append(f"[{count}]", style=theme.TEXT)
    if row_filter.raw:
        text.append(f" </{row_filter.raw}>", style=theme.KEY)
    return text


def pane_title(name: str, scope: str | None = None) -> Text:
    """Draw the title of a secondary pane, such as `log(autoscroll)`."""
    text = Text(name, style=f"bold {theme.TEXT}")
    if scope:
        text.append(f"({scope})", style=theme.KEY)
    return text


class ViewScreen(Screen[None]):
    """A view: subclasses fill the body, load data and handle their own keys."""

    VIEW: ClassVar[View] = View.GUIDES
    BINDINGS: ClassVar[list[BindingType]] = keys.bindings(keys.Scope.SCREEN)

    def __init__(self, ref: ViewRef, *, message: Flash | None = None) -> None:
        super().__init__()
        self.ref = ref
        self.row_filter = RowFilter()
        self.flash_message = message

    @property
    def tui(self) -> LlmdupApp:
        """The llmdup app."""
        return cast("LlmdupApp", self.app)

    def compose(self) -> ComposeResult:
        """Lay out header, prompt, body and breadcrumbs."""
        yield ClusterHeader(id="header")
        yield Prompt(self.tui.histories, self.tui.prompt_arguments, id="prompt")
        yield from self.body()
        yield Crumbs(id="crumbs")

    def body(self) -> ComposeResult:
        """The widgets of the view."""
        yield from ()

    def on_mount(self) -> None:
        """Draw the chrome and start loading."""
        self.query_one(Prompt).close()
        self.redraw()
        self.load()
        self.focus_main()

    def on_screen_resume(self) -> None:
        """Draw the chrome again, the header may have changed."""
        self.redraw()

    def load(self) -> None:
        """Fetch the view's data in a worker; a newer load cancels an older one."""
        self.run_worker(self.fetch, name="load", group="load", exclusive=True, exit_on_error=False)

    async def fetch(self) -> None:
        """Read what the view shows."""

    @on(Worker.StateChanged)
    def _worker_changed(self, event: Worker.StateChanged) -> None:
        error = event.worker.error
        if event.state is WorkerState.ERROR and error is not None:
            self.flash(f"{type(error).__name__}: {error}", Level.ERROR)

    def main(self) -> ViewTable | ViewLog | None:
        """The table or log the filter and sort keys act on."""
        found = self.query("#list")
        widget = found.first() if found else None
        return widget if isinstance(widget, ViewTable | ViewLog) else None

    def focus_main(self) -> None:
        """Put the focus on the main table or log."""
        if (target := self.main()) is not None:
            target.focus()

    def title_text(self) -> Text:
        """The title of the main box."""
        target = self.main()
        count = str(len(target.kept)) if target is not None else None
        return view_title(self.VIEW.value, self.ref.arg, count, self.row_filter)

    def crumbs(self) -> list[str]:
        """The breadcrumb blocks."""
        return [self.VIEW.value]

    def status(self) -> Flash:
        """What the right of the breadcrumb line says when there is no message."""
        return "", Level.INFO

    def copy_text(self) -> str | None:
        """What `c` copies."""
        target = self.main()
        if isinstance(target, ViewTable) and target.current is not None:
            return "  ".join(target.current.plain)
        return None

    @property
    def composed(self) -> bool:
        """Whether the view's widgets exist yet; a message can arrive before they do."""
        return bool(self.query(Crumbs))

    def redraw(self) -> None:
        """Draw header, titles and breadcrumbs."""
        if not self.composed:
            return
        header = self.query_one(ClusterHeader)
        header.display = self.tui.show_header
        header.show(self.tui.header_facts(), keys.hints(self.VIEW))
        self.redraw_titles()
        self.redraw_crumbs()

    def redraw_titles(self) -> None:
        """Draw the title of the main box."""
        found = self.query("#main")
        if found:
            found.first().border_title = self.title_text()

    def redraw_crumbs(self) -> None:
        """Draw the breadcrumb line with the message, or the view's status."""
        if not self.composed:
            return
        text, level = self.flash_message or self.status()
        self.query_one(Crumbs).show(self.crumbs(), text, level)

    def flash(self, message: str, level: Level = Level.INFO) -> None:
        """Show a message on the right of the breadcrumb line."""
        self.flash_message = (message, level)
        self.redraw_crumbs()

    def clear_flash(self) -> None:
        """Go back to the view's status text."""
        self.flash_message = None
        self.redraw_crumbs()

    def apply_filter(self, raw: str) -> None:
        """Filter the main table or log as typed after `/`."""
        self.row_filter = RowFilter(raw)
        target = self.main()
        if target is not None:
            target.set_filter(self.row_filter)
            prompt = self.query_one(Prompt)
            if prompt.display and prompt.mode is Mode.FILTER:
                total = len(target.source) if isinstance(target, ViewTable) else len(target.entries)
                prompt.set_hint(f"{len(target.kept)} of {total} match")
        self.redraw_titles()

    def action_filter(self) -> None:
        """Open the prompt in filter mode."""
        self.query_one(Prompt).open(Mode.FILTER, self.row_filter.raw)

    def action_command(self) -> None:
        """Open the prompt in command mode."""
        self.query_one(Prompt).open(Mode.COMMAND)

    @on(Prompt.Changed)
    def _prompt_changed(self, event: Prompt.Changed) -> None:
        if event.mode is Mode.FILTER:
            self.apply_filter(event.value)

    @on(Prompt.Submitted)
    def _prompt_submitted(self, event: Prompt.Submitted) -> None:
        self.query_one(Prompt).close()
        self.focus_main()
        if event.mode is Mode.FILTER:
            self.apply_filter(event.value)
        elif event.value:
            self.tui.run_command(event.value)

    @on(Prompt.Closed)
    def _prompt_closed(self, event: Prompt.Closed) -> None:
        self.query_one(Prompt).close()
        self.focus_main()
        if event.mode is Mode.FILTER:
            self.apply_filter("")

    def action_back(self) -> None:
        """Clear the filter, else go back one view."""
        if self.row_filter.raw:
            self.apply_filter("")
            return
        self.tui.history_back()

    def action_history_back(self) -> None:
        """Go back one view."""
        self.tui.history_back()

    def action_history_forward(self) -> None:
        """Go forward one view."""
        self.tui.history_forward()

    def action_last_view(self) -> None:
        """Go to the view shown before this one."""
        self.tui.last_view()

    def action_sort(self, field: str) -> None:
        """Sort the main table by name, status or age."""
        target = self.main()
        if not isinstance(target, ViewTable) or not target.sort_by(field):
            self.flash(f"this view has no {field} column", Level.WARN)
            return
        reverse = target.order is not None and target.order[1]
        self.flash(f"sorted by {field}{', reversed' if reverse else ''}")

    def action_help(self) -> None:
        """Open the help view."""
        self.tui.open(ViewRef(View.HELP))

    def action_refresh(self) -> None:
        """Load the view's data again."""
        self.load()

    def action_all_commands(self) -> None:
        """List every `:` command."""
        body = Text()
        for command in keys.COMMANDS:
            body.append(f"{command.label:<16}", style=theme.KEY)
            body.append(f"{command.description}\n")
        self.app.push_screen(TextDialog("commands", body))

    def action_toggle_header(self) -> None:
        """Hide or show the header."""
        self.tui.show_header = not self.tui.show_header
        self.query_one(ClusterHeader).display = self.tui.show_header

    def action_copy(self) -> None:
        """Copy what the view offers, usually the selected row."""
        text = self.copy_text()
        if not text:
            self.flash("nothing to copy here", Level.WARN)
            return
        self.app.copy_to_clipboard(text)
        first = text.splitlines()[0]
        self.flash(f"copied: {first[:60]}{'…' if len(first) > 60 or '\n' in text else ''}", Level.OK)

    def action_open(self) -> None:
        """Open the selected row; each view decides what that means."""

    def action_next_pane(self) -> None:
        """Move the focus to the next pane."""
        self.focus_next("ViewTable, ViewLog")
