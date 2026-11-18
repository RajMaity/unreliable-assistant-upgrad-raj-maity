import os
from pathlib import Path

import pytest

from config import load_env, parse_env


def test_parse_handles_comments_quotes_export_and_inline_comments() -> None:
    text = """
# a comment
export OPENROUTER_MODEL=anthropic/claude-haiku-4.5
OPENROUTER_API_KEY="sk-or-test"   # trailing comment
LLM_BACKEND='api'
EMPTY=
bad line without equals
"""
    assert parse_env(text) == {
        "OPENROUTER_MODEL": "anthropic/claude-haiku-4.5",
        "OPENROUTER_API_KEY": "sk-or-test",
        "LLM_BACKEND": "api",
        "EMPTY": "",
    }


def test_real_environment_wins_over_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / ".env"
    f.write_text("CFG_TEST_A=from_file\nCFG_TEST_B=from_file\n", encoding="utf-8")
    monkeypatch.setenv("CFG_TEST_A", "from_shell")
    monkeypatch.delenv("CFG_TEST_B", raising=False)
    applied = load_env(f)
    assert os.environ["CFG_TEST_A"] == "from_shell"
    assert os.environ["CFG_TEST_B"] == "from_file"
    assert applied == ["CFG_TEST_B"]
    monkeypatch.delenv("CFG_TEST_B", raising=False)


def test_missing_file_is_not_an_error(tmp_path: Path) -> None:
    assert load_env(tmp_path / "nope.env") == []
