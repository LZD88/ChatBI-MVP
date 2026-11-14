"""Basic tracked/unignored-file secret gate; prints paths and rule names only."""

from pathlib import Path
import re
import subprocess


RULES = {
    "model_key": re.compile(rb"\bsk-(?:[a-f0-9]{24,}|ws-[A-Za-z0-9_.-]{30,}|proj-[A-Za-z0-9_-]{30,})"),
    "github_token": re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})"),
    "private_key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "aws_access_key": re.compile(rb"\bAKIA[A-Z0-9]{16}\b"),
}


def scan(root: Path) -> list[tuple[str, str]]:
    files = subprocess.check_output(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=root).split(b"\0")
    findings = []
    for name in set(files):
        if not name:
            continue
        path = root / name.decode("utf-8")
        if not path.is_file() or "vendor" in path.parts:
            continue
        data = path.read_bytes()
        for rule, pattern in RULES.items():
            if pattern.search(data):
                findings.append((str(path.relative_to(root)), rule))
    return sorted(findings)


if __name__ == "__main__":
    findings = scan(Path(__file__).resolve().parents[1])
    for path, rule in findings:
        print(f"{path}: {rule}")
    print(f"Secret scan: {len(findings)} finding(s); values omitted.")
    raise SystemExit(bool(findings))
