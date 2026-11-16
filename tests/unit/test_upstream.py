# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
import io
import tarfile
from pathlib import Path

import pytest

from llmdup.core import upstream
from llmdup.core.models import UsageError


def _tarball(path: Path, *, evil: bool = False) -> None:
    with tarfile.open(path, "w:gz", format=tarfile.PAX_FORMAT, pax_headers={"comment": "abc123"}) as tar:
        name = "../evil.txt" if evil else "llm-d-9.9.9/scripts/guide.py"
        data = b"# guide\n"
        info = tarfile.TarInfo(name)
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))


def test_download_unpacks_and_records_commit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("LLMDUP_LLMD_PATH")
    _tarball(tmp_path / "v9.9.9.tar.gz")
    monkeypatch.setattr(upstream, "TARBALL_URL", f"file://{tmp_path}/{{ref}}.tar.gz")
    found = upstream.release("v9.9.9")
    assert found.commit == "abc123"
    assert (found.path / "scripts" / "guide.py").is_file()
    assert found.path == tmp_path / "cache" / "v9.9.9"


def test_download_refuses_path_traversal(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("LLMDUP_LLMD_PATH")
    _tarball(tmp_path / "v6.6.6.tar.gz", evil=True)
    monkeypatch.setattr(upstream, "TARBALL_URL", f"file://{tmp_path}/{{ref}}.tar.gz")
    with pytest.raises(tarfile.OutsideDestinationError):
        upstream.release("v6.6.6")
    assert not (tmp_path / "evil.txt").exists()


def test_bad_ref_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLMDUP_LLMD_PATH")
    with pytest.raises(UsageError):
        upstream.release("../etc")


def test_local_path_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLMDUP_LLMD_PATH", str(tmp_path))
    with pytest.raises(UsageError, match=r"no scripts/guide\.py"):
        upstream.release()
