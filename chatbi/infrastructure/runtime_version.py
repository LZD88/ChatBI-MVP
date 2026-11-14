"""Source fingerprint for detecting a server still running older code."""
import hashlib
from pathlib import Path


def source_version() -> str:
    root = Path(__file__).resolve().parents[2]
    digest = hashlib.sha256()
    paths = sorted([*root.glob("chatbi/**/*.py"), *root.glob("chatbi/web/*.mjs"),
                    *root.glob("chatbi/web/*.html"), *root.glob("chatbi/web/*.css"),
                    *root.glob("*.py")])
    for path in paths:
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]
