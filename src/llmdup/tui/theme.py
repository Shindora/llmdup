# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
"""Colours of the terminal UI, from the mockups."""

from textual.theme import Theme

BACKGROUND = "#0E1116"
TEXT = "#D7DCE4"
DIM = "#8B95A5"
BORDER = "#3A4250"
ACCENT = "#F2B34C"
KEY = "#5CC8E8"
OK = "#6FD0B0"
FAIL = "#FF8A7A"

THEME = Theme(
    name="llmdup",
    primary=ACCENT,
    secondary=KEY,
    accent=KEY,
    foreground=TEXT,
    background=BACKGROUND,
    surface=BACKGROUND,
    panel=BACKGROUND,
    success=OK,
    warning=ACCENT,
    error=FAIL,
    dark=True,
    variables={
        "block-cursor-background": ACCENT,
        "block-cursor-foreground": BACKGROUND,
        "block-cursor-blurred-background": ACCENT,
        "block-cursor-blurred-foreground": BACKGROUND,
        "border": KEY,
        "border-blurred": BORDER,
        "input-cursor-background": TEXT,
        "input-cursor-foreground": BACKGROUND,
        "input-selection-background": f"{KEY} 40%",
        "scrollbar": BORDER,
        "scrollbar-hover": DIM,
        "scrollbar-active": ACCENT,
        "scrollbar-background": BACKGROUND,
        "scrollbar-background-hover": BACKGROUND,
        "scrollbar-background-active": BACKGROUND,
        "scrollbar-corner-color": BACKGROUND,
    },
)
