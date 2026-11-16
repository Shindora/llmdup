# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
"""Modal dialogs: confirm the exact commands, read a text, edit a value."""

from collections.abc import Sequence
from typing import ClassVar

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.suggester import SuggestFromList
from textual.widgets import Input, Static

from llmdup.tui import keys, theme
from llmdup.tui.widgets.log import ScrollPane
from llmdup.tui.widgets.prompt import PromptInput


def key_line(*pairs: tuple[str, str]) -> Text:
    """Draw `<key> what` pairs on one line."""
    text = Text(no_wrap=True)
    for key, what in pairs:
        text.append(f"<{key}>", style=theme.KEY)
        text.append(f" {what}   ")
    return text


class ConfirmDialog(ModalScreen[bool]):
    """Ask before cluster-scoped steps or a delete, showing exactly what will run."""

    BINDINGS: ClassVar[list[BindingType]] = keys.bindings(keys.Scope.DIALOG)

    def __init__(self, title: str, intro: str, commands: Sequence[str]) -> None:
        super().__init__(classes="dialog")
        self.heading = title
        self.intro = intro
        self.commands = list(commands)

    def compose(self) -> ComposeResult:
        """Lay out the question, the commands and the keys."""
        with Vertical(classes="dialog-box") as box:
            box.border_title = Text(self.heading, style=f"bold {theme.ACCENT}")
            yield Static(Text(self.intro), classes="dialog-intro")
            with ScrollPane(classes="dialog-body"):
                yield Static(Text("\n\n".join(self.commands)), id="commands")
            yield Static(key_line(("y", "Run them"), ("n/esc", "Cancel"), ("c", "Copy the commands")), classes="dialog-keys")

    def action_confirm(self) -> None:
        """Run the commands."""
        self.dismiss(result=True)

    def action_close(self) -> None:
        """Do not run them."""
        self.dismiss(result=False)

    def action_copy(self) -> None:
        """Copy the commands."""
        self.app.copy_to_clipboard("\n\n".join(self.commands))
        self.notify("copied the commands")


class TextDialog(ModalScreen[None]):
    """Show a block of text, such as a fix, a step's command or a description."""

    BINDINGS: ClassVar[list[BindingType]] = keys.bindings(keys.Scope.DIALOG)

    def __init__(self, title: str, body: Text | str) -> None:
        super().__init__(classes="dialog")
        self.heading = title
        self.body = body if isinstance(body, Text) else Text(body)

    def compose(self) -> ComposeResult:
        """Lay out the text and the keys."""
        with Vertical(classes="dialog-box") as box:
            box.border_title = Text(self.heading, style=f"bold {theme.ACCENT}")
            with ScrollPane(classes="dialog-body", id="text-scroll"):
                yield Static(self.body, id="text")
            yield Static(key_line(("esc", "Close"), ("c", "Copy"), ("j/k", "Scroll")), classes="dialog-keys")

    def on_mount(self) -> None:
        """Focus the text so the movement keys scroll it."""
        self.query_one(ScrollPane).focus()

    def action_confirm(self) -> None:
        """Close the dialog."""
        self.dismiss()

    def action_close(self) -> None:
        """Close the dialog."""
        self.dismiss()

    def action_copy(self) -> None:
        """Copy the text."""
        self.app.copy_to_clipboard(self.body.plain)
        self.notify("copied")


class EditDialog(ModalScreen[str | None]):
    """Edit one plan variable; allowed values complete with tab."""

    def __init__(self, name: str, value: str, allowed: Sequence[str]) -> None:
        super().__init__(classes="dialog")
        self.variable = name
        self.initial = value
        self.allowed = list(allowed)

    def compose(self) -> ComposeResult:
        """Lay out the input and the allowed values."""
        with Vertical(classes="dialog-box edit-box") as box:
            box.border_title = Text(f"edit {self.variable}", style=f"bold {theme.ACCENT}")
            field = PromptInput(id="edit-input")
            field.suggester = SuggestFromList(self.allowed, case_sensitive=True) if self.allowed else None
            yield field
            allowed = f"allowed: {', '.join(self.allowed)}" if self.allowed else "any value"
            yield Static(Text(allowed, style=theme.DIM), classes="dialog-intro")
            yield Static(key_line(("enter", "Save"), ("tab", "Complete"), ("esc", "Cancel")), classes="dialog-keys")

    def on_mount(self) -> None:
        """Start with the current value."""
        field = self.query_one(PromptInput)
        field.value = self.initial
        field.cursor_position = len(self.initial)
        field.cursor_blink = not self.app.is_headless
        field.focus()

    @on(Input.Submitted)
    def _save(self, event: Input.Submitted) -> None:
        event.stop()
        self.dismiss(event.value.strip())

    @on(PromptInput.Cancelled)
    def _cancel(self, event: PromptInput.Cancelled) -> None:
        event.stop()
        self.dismiss(None)
