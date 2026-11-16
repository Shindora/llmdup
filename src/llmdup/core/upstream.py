# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0

import os
import re
import shutil
import tarfile
import tempfile
import urllib.error
import urllib.request
from datetime import UTC, datetime
from functools import cache
from pathlib import Path

from pydantic import BaseModel

from llmdup.core.models import LlmdupError, UsageError

DEFAULT_LLMD_REF = "v1.0.0"
TARBALL_URL = "https://codeload.github.com/llm-d/llm-d/tar.gz/refs/tags/{ref}"
SOURCE_FILE = ".llmdup-source.json"
_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
KNOWN_COMMITS = {"v1.0.0": "378b42d18dabe0b9c4ced4e6f3670d93b52db08e"}


class Release(BaseModel, frozen=True):
    """An unpacked llm-d release that llmdup reads guides from."""

    ref: str
    path: Path
    commit: str | None = None
    source: str
    fetched_at: str | None = None


def cache_root() -> Path:
    """Return the directory that holds downloaded releases."""
    if override := os.environ.get("LLMDUP_CACHE_DIR"):
        return Path(override)
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "llmdup" / "llm-d"


def release(ref: str = DEFAULT_LLMD_REF) -> Release:
    """Return the llm-d release for `ref`, downloading it on first use.

    `LLMDUP_LLMD_PATH` points at a local llm-d checkout and skips the download.

    Args:
        ref: The llm-d tag to use.

    Returns:
        The unpacked release.

    Raises:
        UsageError: `ref` is not a plain tag name, or the local checkout has no `scripts/guide.py`.
    """
    if local := os.environ.get("LLMDUP_LLMD_PATH"):
        path = Path(local).expanduser().resolve()
        if not (path / "scripts" / "guide.py").is_file():
            msg = f"LLMDUP_LLMD_PATH={path} has no scripts/guide.py"
            raise UsageError(msg, hint="point it at an llm-d checkout of v1.0.0 or newer")
        return Release(ref=ref, path=path, source="LLMDUP_LLMD_PATH")
    if not _REF.fullmatch(ref):
        msg = f"invalid llm-d ref {ref!r}"
        raise UsageError(msg, hint="use a tag such as v1.0.0")
    return _cached(ref, cache_root())


@cache
def _cached(ref: str, root: Path) -> Release:
    marker = root / ref / SOURCE_FILE
    if marker.is_file():
        return Release.model_validate_json(marker.read_text())
    return _download(ref, root)


def _download(ref: str, root: Path) -> Release:
    root.mkdir(parents=True, exist_ok=True)
    url = TARBALL_URL.format(ref=ref)
    with tempfile.TemporaryDirectory(dir=root, prefix=f".{ref}-") as tmp:
        archive = Path(tmp) / "release.tar.gz"
        _fetch(url, ref, archive)
        with tarfile.open(archive, "r:gz") as tar:
            commit = tar.pax_headers.get("comment")
            if ref in KNOWN_COMMITS and commit != KNOWN_COMMITS[ref]:
                msg = f"llm-d {ref} tarball is commit {commit}, expected {KNOWN_COMMITS[ref]}"
                raise LlmdupError(msg, hint="the tag moved upstream; check before trusting it")
            tar.extractall(Path(tmp) / "x", filter="data")
        (top,) = (Path(tmp) / "x").iterdir()
        found = Release(
            ref=ref,
            path=root / ref,
            commit=commit,
            source=url,
            fetched_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        (top / SOURCE_FILE).write_text(found.model_dump_json(indent=2))
        try:
            top.rename(root / ref)
        except OSError:
            if not (root / ref / SOURCE_FILE).is_file():
                raise
    return Release.model_validate_json((root / ref / SOURCE_FILE).read_text())


def _fetch(url: str, ref: str, archive: Path) -> None:
    try:
        with urllib.request.urlopen(url, timeout=120) as response, archive.open("wb") as out:  # ruff: ignore[suspicious-url-open-usage]
            shutil.copyfileobj(response, out)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            msg = f"llm-d has no tag {ref!r}"
            raise UsageError(msg, hint="see https://github.com/llm-d/llm-d/tags") from exc
        msg = f"downloading llm-d {ref} failed: HTTP {exc.code}"
        raise LlmdupError(msg) from exc
    except urllib.error.URLError as exc:
        msg = f"cannot download llm-d {ref}: {exc.reason}"
        raise LlmdupError(msg, hint="check the network, or point LLMDUP_LLMD_PATH at a local llm-d checkout") from exc
