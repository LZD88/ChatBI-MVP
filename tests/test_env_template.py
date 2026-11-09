from pathlib import Path


def test_environment_template_has_no_usable_secret() -> None:
    content = Path(".env.example").read_text(encoding="utf-8")

    assert "sk-" not in content
    assert "123456" not in content
    assert "replace-with-your-api-key" in content
    assert "replace-with-a-local-read-only-password" in content


def test_local_environment_is_ignored() -> None:
    content = Path(".gitignore").read_text(encoding="utf-8")

    assert ".env\n" in content
    assert "!.env.example" in content
    assert ".venv/" in content
    assert "chroma_db/" in content
