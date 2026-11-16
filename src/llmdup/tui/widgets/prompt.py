# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
"""The prompt row: `:` commands with a dim suggestion and history, `/` filters as you type."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Horizontal
from textual.message import Message
from textual.suggester import Suggester
from textual.widgets import Input, Static

from llmdup.tui import keys, theme


class Mode(StrEnum):
    """What the prompt is typing."""

    COMMAND = ":"
    FILTER = "/"


class CommandSuggester(Suggester):
    """Complete `:` commands, and the guide, context or namespace they take."""

    def __init__(self, arguments: Callable[[str], Sequence[str]]) -> None:
        super().__init__(use_cache=False, case_sensitive=True)
        self.arguments = arguments

    async def get_suggestion(self, value: str) -> str | None:
        """Return the first command, or command and argument, that starts with what was typed."""
        name, space, arg = value.partition(" ")
        if not space:
            return next((c.name for c in keys.COMMANDS if c.name.startswith(name) and c.name != name), None)
        return next((f"{name} {a}" for a in self.arguments(name) if a.startswith(arg) and a != arg), None)


class PromptInput(Input):
    """An input with the prompt keys of `keys.py`: accept the suggestion, history, clear, cancel."""

    BINDINGS: ClassVar[list[BindingType]] = keys.bindings(keys.Scope.PROMPT)

    @dataclass
    class Cancelled(Message):
        """Escape was pressed."""

        input: PromptInput

    def __init__(self, *, id: str | None = None, history: list[str] | None = None) -> None:
        super().__init__(select_on_focus=False, compact=True, id=id)
        self.history = history if history is not None else []
        self.position = len(self.history)

    @property
    def suggestion(self) -> str:
        """The dim completion shown after the cursor."""
        return self._suggestion

    def action_accept(self) -> None:
        """Take the suggestion."""
        if self._suggestion:
            self.value = self._suggestion
            self.cursor_position = len(self.value)

    def action_clear(self) -> None:
        """Empty the input."""
        self.value = ""

    def action_cancel(self) -> None:
        """Close the prompt."""
        self.post_message(self.Cancelled(self))

    def action_history_previous(self) -> None:
        """Recall the previous entry."""
        if self.position > 0:
            self.position -= 1
            self._recall()

    def action_history_next(self) -> None:
        """Recall the next entry, or empty the input after the last one."""
        if self.position < len(self.history):
            self.position += 1
            self._recall()

    def remember(self, value: str) -> None:
        """Add a submitted value to the history."""
        if value and (not self.history or self.history[-1] != value):
            self.history.append(value)
        self.position = len(self.history)

    def _recall(self) -> None:
        self.value = self.history[self.position] if self.position < len(self.history) else ""
        self.cursor_position = len(self.value)


class Prompt(Horizontal):
    """The prompt row, shown only while typing."""

    @dataclass
    class Changed(Message):
        """The text changed."""

        mode: Mode
        value: str

    @dataclass
    class Submitted(Message):
        """Enter was pressed."""

        mode: Mode
        value: str

    @dataclass
    class Closed(Message):
        """Escape was pressed."""

        mode: Mode

    def __init__(self, histories: dict[Mode, list[str]], arguments: Callable[[str], Sequence[str]], *, id: str | None = None) -> None:
        super().__init__(id=id)
        self.histories = histories
        self.suggester = CommandSuggester(arguments)
        self.mode = Mode.COMMAND

    def compose(self) -> ComposeResult:
        """Lay out the mode sign, the input and the hint."""
        yield Static(id="prompt-mode")
        yield PromptInput(id="prompt-input")
        yield Static(id="prompt-hint")

    @property
    def input(self) -> PromptInput:
        """The input widget."""
        return self.query_one(PromptInput)

    def open(self, mode: Mode, value: str = "") -> None:
        """Show the prompt in a mode and focus it."""
        self.mode = mode
        self.query_one("#prompt-mode", Static).update(Text(mode.value, style=f"bold {theme.KEY}"))
        hint = "enter to run · esc to cancel" if mode is Mode.COMMAND else "enter keeps the filter · esc clears it"
        self.set_hint(hint)
        field = self.input
        field.suggester = self.suggester if mode is Mode.COMMAND else None
        field.history = self.histories.setdefault(mode, [])
        field.position = len(field.history)
        field.cursor_blink = not self.app.is_headless
        field.value = value
        field.cursor_position = len(value)
        self.display = True
        field.focus()

    def close(self) -> None:
        """Hide the prompt."""
        self.display = False

    def set_hint(self, hint: str) -> None:
        """Draw the dim text on the right."""
        self.query_one("#prompt-hint", Static).update(Text(hint, style=theme.DIM, justify="right"))

    @on(Input.Changed, "#prompt-input")
    def _changed(self, event: Input.Changed) -> None:
        event.stop()
        self.post_message(self.Changed(self.mode, event.value))

    @on(Input.Submitted, "#prompt-input")
    def _submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.input.remember(event.value.strip())
        self.post_message(self.Submitted(self.mode, event.value.strip()))

    @on(PromptInput.Cancelled)
    def _cancelled(self, event: PromptInput.Cancelled) -> None:
        event.stop()
        self.post_message(self.Closed(self.mode))
