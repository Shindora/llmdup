# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
"""Where the user is, and the view history behind `[`, `]` and `-`."""

from dataclasses import dataclass, field

from llmdup.tui.keys import View

DEPLOY_LOG = "deploy"


@dataclass(frozen=True)
class ViewRef:
    """A view and what it shows: a guide, a namespace, or a pod as `namespace/pod`."""

    view: View
    arg: str | None = None


@dataclass
class History:
    """Views visited, like a browser history, plus the last view for `-`."""

    entries: list[ViewRef] = field(default_factory=list)
    position: int = -1
    last: ViewRef | None = None

    @property
    def current(self) -> ViewRef | None:
        """The view shown now."""
        return self.entries[self.position] if 0 <= self.position < len(self.entries) else None

    def push(self, ref: ViewRef) -> None:
        """Visit a view; forward history is dropped."""
        if ref == self.current:
            return
        self.last = self.current
        del self.entries[self.position + 1 :]
        self.entries.append(ref)
        self.position = len(self.entries) - 1

    def back(self) -> ViewRef | None:
        """Step back, if there is a view behind."""
        if self.position <= 0:
            return None
        self.last = self.current
        self.position -= 1
        return self.current

    def forward(self) -> ViewRef | None:
        """Step forward, if there is a view ahead."""
        if self.position >= len(self.entries) - 1:
            return None
        self.last = self.current
        self.position += 1
        return self.current
