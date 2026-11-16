# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
"""Every key of the terminal UI, declared once.

The Textual bindings, the header hints and the help view are all generated from `KEYS`.
"""

from dataclasses import dataclass
from enum import StrEnum

from textual.binding import Binding, BindingType


class View(StrEnum):
    """The views of the terminal UI."""

    GUIDES = "guides"
    DOCTOR = "doctor"
    PLAN = "plan"
    DEPLOY = "deploy"
    STACKS = "stacks"
    LOGS = "logs"
    HELP = "help"


class Group(StrEnum):
    """Sections of the help view."""

    MOVE = "move"
    HISTORY = "history"
    SEARCH = "search"
    SORT = "sort"
    PROMPT = "command prompt"
    COMMANDS = "commands"
    GENERAL = "general"
    DIALOG = "dialogs"
    VIEW = "actions by view"


class Scope(StrEnum):
    """The widget a key is bound on."""

    APP = "app"
    SCREEN = "screen"
    LIST = "list"
    PROMPT = "prompt"
    DIALOG = "dialog"


@dataclass(frozen=True)
class Key:
    """One key: the Textual keys it binds, how it is drawn, and what it does."""

    keys: str
    label: str
    action: str
    description: str
    group: Group
    scope: Scope
    view: View | None = None
    hint: str | None = None
    priority: bool = False

    def binding(self) -> Binding:
        """Return the Textual binding of this key."""
        return Binding(self.keys, self.action, self.description, show=False, priority=self.priority)

    def header_hint(self) -> str:
        """Return the hint drawn in the header, such as `<u> Deploy`."""
        return self.hint or f"<{self.label}> {self.description}"


@dataclass(frozen=True)
class Syntax:
    """Prompt syntax that is typed rather than bound, such as `/!text`."""

    label: str
    description: str
    group: Group


@dataclass(frozen=True)
class Command:
    """One `:` command of the prompt."""

    name: str
    arg: str
    description: str

    @property
    def label(self) -> str:
        """The command as the help view draws it."""
        return f":{self.name} {self.arg}".rstrip()


def _view(view: View, keys: str, label: str, action: str, description: str) -> Key:
    return Key(keys, label, action, description, Group.VIEW, Scope.SCREEN, view=view)


KEYS: tuple[Key, ...] = (
    Key("j,down", "j ↓", "cursor_down", "Down", Group.MOVE, Scope.LIST, hint="<j/k> Down, up"),
    Key("k,up", "k ↑", "cursor_up", "Up", Group.MOVE, Scope.LIST),
    Key("g", "g", "scroll_top", "Top", Group.MOVE, Scope.LIST, hint="<g/G> Top, bottom"),
    Key("G,shift+g", "shift-g", "scroll_bottom", "Bottom", Group.MOVE, Scope.LIST),
    Key("ctrl+f,pagedown", "ctrl-f", "page_down", "Page down", Group.MOVE, Scope.LIST),
    Key("ctrl+b,pageup", "ctrl-b", "page_up", "Page up", Group.MOVE, Scope.LIST),
    Key("enter", "enter", "open", "Open", Group.MOVE, Scope.SCREEN),
    Key("escape", "esc", "back", "Back", Group.MOVE, Scope.SCREEN, hint="<esc> Back"),
    Key("tab", "tab", "next_pane", "Next pane", Group.MOVE, Scope.SCREEN),
    Key("[", "[", "history_back", "History back", Group.HISTORY, Scope.SCREEN, hint="<[ ]> History"),
    Key("]", "]", "history_forward", "History forward", Group.HISTORY, Scope.SCREEN),
    Key("-", "-", "last_view", "Last view", Group.HISTORY, Scope.SCREEN),
    Key("/", "/", "filter", "Filter rows", Group.SEARCH, Scope.SCREEN, hint="</> Filter"),
    Key("N,shift+n", "shift-n", "sort('name')", "Sort by name", Group.SORT, Scope.SCREEN),
    Key("S,shift+s", "shift-s", "sort('status')", "Sort by status", Group.SORT, Scope.SCREEN),
    Key("A,shift+a", "shift-a", "sort('age')", "Sort by age", Group.SORT, Scope.SCREEN),
    Key(":", ":", "command", "Open the prompt", Group.PROMPT, Scope.SCREEN, hint="<:> Command"),
    Key("tab", "tab", "accept", "Accept the suggestion", Group.PROMPT, Scope.PROMPT),
    Key("up", "↑", "history_previous", "Previous command", Group.PROMPT, Scope.PROMPT),
    Key("down", "↓", "history_next", "Next command", Group.PROMPT, Scope.PROMPT),
    Key("ctrl+u", "ctrl-u", "clear", "Clear input", Group.PROMPT, Scope.PROMPT),
    Key("enter", "enter", "submit", "Run", Group.PROMPT, Scope.PROMPT),
    Key("escape", "esc", "cancel", "Close the prompt", Group.PROMPT, Scope.PROMPT),
    Key("?", "?", "help", "Help", Group.GENERAL, Scope.SCREEN, hint="<?> Help"),
    Key("ctrl+r", "ctrl-r", "refresh", "Refresh", Group.GENERAL, Scope.SCREEN),
    Key("ctrl+a", "ctrl-a", "all_commands", "All commands", Group.GENERAL, Scope.SCREEN),
    Key("ctrl+e", "ctrl-e", "toggle_header", "Hide the header", Group.GENERAL, Scope.SCREEN),
    Key("c", "c", "copy", "Copy", Group.GENERAL, Scope.SCREEN),
    Key("ctrl+c", "ctrl-c", "quit", "Quit", Group.GENERAL, Scope.APP, priority=True),
    Key("y", "y", "confirm", "Confirm", Group.DIALOG, Scope.DIALOG),
    Key("n,escape", "n esc", "close", "Cancel, close", Group.DIALOG, Scope.DIALOG),
    Key("c", "c", "copy", "Copy the text", Group.DIALOG, Scope.DIALOG),
    _view(View.GUIDES, "enter", "enter", "open", "Plan"),
    _view(View.GUIDES, "d", "d", "describe", "Describe"),
    _view(View.GUIDES, "D,shift+d", "shift-d", "doctor", "Doctor"),
    _view(View.GUIDES, "u", "u", "deploy", "Deploy"),
    _view(View.GUIDES, "tab", "tab", "next_pane", "Next section"),
    _view(View.DOCTOR, "ctrl+r", "ctrl-r", "refresh", "Run again"),
    _view(View.DOCTOR, "enter", "enter", "open", "Show fix"),
    _view(View.DOCTOR, "c", "c", "copy", "Copy fix"),
    _view(View.PLAN, "e", "e", "edit", "Edit value"),
    _view(View.PLAN, "enter", "enter", "open", "Show command"),
    _view(View.PLAN, "ctrl+s", "ctrl-s", "save", "Save script"),
    _view(View.PLAN, "u", "u", "deploy", "Deploy"),
    _view(View.PLAN, "tab", "tab", "next_pane", "Switch pane"),
    _view(View.DEPLOY, "ctrl+k", "ctrl-k", "cancel_run", "Cancel"),
    _view(View.DEPLOY, "l", "l", "logs", "Full log"),
    _view(View.DEPLOY, "s", "s", "autoscroll", "Autoscroll"),
    _view(View.DEPLOY, "enter", "enter", "open", "Step output"),
    _view(View.DEPLOY, "tab", "tab", "next_pane", "Switch pane"),
    _view(View.STACKS, "enter", "enter", "open", "Open"),
    _view(View.STACKS, "l", "l", "logs", "Logs"),
    _view(View.STACKS, "d", "d", "describe", "Describe"),
    _view(View.STACKS, "t", "t", "test", "Test request"),
    _view(View.STACKS, "m", "m", "metrics", "Metrics"),
    _view(View.STACKS, "F,shift+f", "shift-f", "port_forward", "Port-forward"),
    _view(View.STACKS, "ctrl+d", "ctrl-d", "teardown", "Tear down"),
    _view(View.LOGS, "s", "s", "autoscroll", "Autoscroll"),
    _view(View.LOGS, "w", "w", "wrap", "Wrap"),
    _view(View.LOGS, "t", "t", "timestamps", "Timestamps"),
    _view(View.LOGS, "ctrl+s", "ctrl-s", "save", "Save"),
    _view(View.HELP, "escape", "esc", "back", "Close help"),
)

SYNTAX: tuple[Syntax, ...] = (
    Syntax("/text", "Rows that contain text", Group.SEARCH),
    Syntax("/!text", "Rows without text", Group.SEARCH),
    Syntax("/-f text", "Fuzzy match", Group.SEARCH),
    Syntax("esc", "Clear the filter", Group.SEARCH),
)

COMMANDS: tuple[Command, ...] = (
    Command("guides", "", "Guide list"),
    Command("doctor", "[guide]", "Preflight checks"),
    Command("plan", "<guide>", "Plan a deploy"),
    Command("deploy", "[guide]", "Deploy view or a guide"),
    Command("stacks", "", "Deployed stacks"),
    Command("logs", "", "Deploy log"),
    Command("help", "", "Every key"),
    Command("ctx", "[name]", "Switch context"),
    Command("ns", "[name]", "Switch namespace"),
    Command("q", "", "Quit"),
)

GENERIC_HINTS = ("cursor_down", "scroll_top", "history_back", "filter", "command", "help", "back")


def bindings(scope: Scope, view: View | None = None) -> list[BindingType]:
    """Return the Textual bindings of one scope, for every view or for one view only."""
    return [k.binding() for k in KEYS if k.scope is scope and k.view is view]


def hints(view: View) -> list[str]:
    """Return the header hints of a view: its own keys first, then the keys of every view."""
    own = [k.header_hint() for k in KEYS if k.view is view]
    generic = {k.action: k for k in KEYS if k.view is None and k.hint}
    shadowed = {k.keys for k in KEYS if k.view is view}
    return own + [generic[a].header_hint() for a in GENERIC_HINTS if a in generic and generic[a].keys not in shadowed]


def view_keys(view: View) -> list[Key]:
    """Return the keys that only one view binds."""
    return [k for k in KEYS if k.view is view]


def group_keys(group: Group) -> list[Key]:
    """Return the keys of one help section."""
    return [k for k in KEYS if k.group is group]
