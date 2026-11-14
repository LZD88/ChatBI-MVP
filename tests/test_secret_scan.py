import importlib.util
from pathlib import Path
import subprocess


def test_secret_gate_fails_on_tracked_and_untracked_secrets_without_printing_values(tmp_path):
    spec = importlib.util.spec_from_file_location("secret_scan", Path(__file__).resolve().parents[1] / "scripts/scan_secrets.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "config.txt").write_text("token = " + "sk-" + "a" * 32)
    assert module.scan(tmp_path) == [("config.txt", "model_key")]
    subprocess.run(["git", "-C", str(tmp_path), "add", "config.txt"], check=True)
    (tmp_path / "key.pem").write_text("-----BEGIN " + "PRIVATE KEY-----")
    assert module.scan(tmp_path) == [("config.txt", "model_key"), ("key.pem", "private_key")]
