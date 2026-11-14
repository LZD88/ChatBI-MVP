"""Keep API persistence tests away from the user's local query history."""
import pytest


@pytest.fixture(autouse=True)
def isolated_history_path(tmp_path, monkeypatch):
    monkeypatch.setenv("QUERY_HISTORY_PATH", str(tmp_path / "history.sqlite3"))
