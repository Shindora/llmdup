# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
from enum import IntEnum
from typing import Any

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1


class ExitCode(IntEnum):
    """Process exit codes shared by every front end."""

    OK = 0
    STEP_FAILED = 1
    USAGE = 2
    DOCTOR_FAILED = 3
    CONFIRMATION_NEEDED = 4


class ErrorInfo(BaseModel):
    """One error in the JSON envelope."""

    code: str
    message: str
    hint: str | None = None


class Envelope(BaseModel):
    """The single JSON document printed by every `--json` command."""

    schema_version: int = SCHEMA_VERSION
    ok: bool
    command: str
    data: Any = None
    errors: list[ErrorInfo] = Field(default_factory=list)


class LlmdupError(Exception):
    """An error a front end reports to the user with an exit code."""

    exit_code: ExitCode = ExitCode.STEP_FAILED
    code: str = "error"

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.hint = hint

    def info(self) -> ErrorInfo:
        """Return the envelope form of this error."""
        return ErrorInfo(code=self.code, message=str(self), hint=self.hint)


class UsageError(LlmdupError):
    """Bad usage, or a value the guide does not allow."""

    exit_code = ExitCode.USAGE
    code = "usage"


class VersionInfo(BaseModel):
    """Output of `llmdup version`."""

    llmdup: str
    llmd_ref: str
