"""Repo-rooted imports and store sandboxing for data/build tests."""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


@pytest.fixture
def decisions_sandbox(tmp_path, monkeypatch):
    """Redirect the decisions and review stores to a temp dir, so ledger and id-mint tests touch nothing real."""
    from data.build import config

    monkeypatch.setattr(config, "DECISIONS", tmp_path / "decisions")
    monkeypatch.setattr(config, "REVIEW_DIR", tmp_path / "review")
    (tmp_path / "decisions").mkdir()
    return tmp_path
