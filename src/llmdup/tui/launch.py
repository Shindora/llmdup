# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
"""Open the terminal UI; Textual is imported only here."""

from llmdup.core.upstream import DEFAULT_LLMD_REF


def run(context: str | None = None, llmd_ref: str = DEFAULT_LLMD_REF) -> None:
    """Open the terminal UI on a kube context and an llm-d release."""
    from llmdup.tui.app import LlmdupApp  # imported here so `llmdup plan --json` never loads Textual
    from llmdup.tui.core import Core

    LlmdupApp(Core(context=context, ref=llmd_ref)).run()
