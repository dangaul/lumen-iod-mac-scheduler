#!/usr/bin/env python3
"""security_check.py — block commits/pushes that would leak account data.

This repo is public. Fails (exit 1) if outgoing content contains:
  * a tracked secret/state file (.env, config.json, state, lock, log files)
  * any real value from the local .env or config.json (credentials, service
    and billing IDs, webhook URLs, passphrase) — read at runtime, so the
    sensitive values themselves never live in the repo
  * a generic secret pattern (Teams webhook, bearer/basic token, private key)

Findings name the file and config key, never the value.

Usage:
  python3 security_check.py --staged        # pre-commit: staged changes
  python3 security_check.py --range A..B    # pre-push: commits incl. messages
  python3 security_check.py                 # full scan: HEAD tree + all history
  python3 security_check.py --install-hooks # install pre-commit + pre-push hooks
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FORBIDDEN_FILES = re.compile(r"(^|/)(\.env|config\.json|\.lumen-bandwidth-state\.json|\.lumen-bandwidth-run\.lock|[^/]*\.log)$")
PATTERNS = {
    "Teams/Power Automate webhook URL": re.compile(r"(webhook\.office\.com/webhookb2/|logic\.azure\.com[:/])[0-9a-fA-F-]{20,}"),
    "Bearer token": re.compile(r"Bearer [A-Za-z0-9._~+/-]{24,}"),
    "Basic auth value": re.compile(r"Basic [A-Za-z0-9+/]{24,}={0,2}"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}
ENV_DEFAULT = re.compile(r"\$\{[A-Z0-9_]+:-([^}]*)\}")
MIN_LEN = 6


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, errors="ignore", check=True).stdout


def read(path: Path) -> str:
    return path.read_text(errors="ignore") if path.exists() else ""


def local_secret_values() -> dict[str, str]:
    """Real values from .env and config.json, minus anything the public templates already contain."""
    values: dict[str, str] = {}
    for line in read(ROOT / ".env").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, val = line.split("=", 1)
            values[f".env:{key.strip()}"] = val.strip().strip("'\"")

    def walk(node, path: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}" if path else k)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
        elif isinstance(node, str):
            defaults = ENV_DEFAULT.findall(node)
            for d in defaults:
                values[f"config.json:{path}"] = d
            if not defaults and not node.startswith("${"):
                values[f"config.json:{path}"] = node

    try:
        walk(json.loads(read(ROOT / "config.json") or "{}"), "")
    except json.JSONDecodeError:
        print("[security] WARNING: config.json is not valid JSON; its values were not checked", file=sys.stderr)

    public = read(ROOT / "config.example.json") + read(ROOT / ".env.example")
    return {k: v for k, v in values.items() if len(v) >= MIN_LEN and v not in public}


def scan_text(label: str, text: str, secrets: dict[str, str]) -> list[str]:
    findings = [f"{label}: contains value of {key}" for key, val in secrets.items() if val in text]
    findings += [f"{label}: looks like a {name}" for name, rx in PATTERNS.items() if rx.search(text)]
    return findings


def added_lines(diff: str) -> dict[str, str]:
    """Map file -> added lines from a unified diff."""
    out: dict[str, list[str]] = {}
    current = "?"
    for line in diff.splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else line[4:]
        elif line.startswith("+") and not line.startswith("+++"):
            out.setdefault(current, []).append(line[1:])
    return {f: "\n".join(lines) for f, lines in out.items()}


def check_staged(secrets: dict[str, str]) -> list[str]:
    findings = [f"{f}: secret/state file is staged" for f in git("diff", "--cached", "--name-only", "--diff-filter=ACMR").split() if FORBIDDEN_FILES.search(f)]
    for f, text in added_lines(git("diff", "--cached", "-U0", "--no-color")).items():
        findings += scan_text(f, text, secrets)
    return findings


def check_range(rev_range: str, secrets: dict[str, str]) -> list[str]:
    findings: list[str] = []
    for sha in git("rev-list", rev_range).split():
        short = sha[:8]
        findings += [f"{short} {f}: secret/state file committed" for f in git("show", "--name-only", "--format=", "--diff-filter=ACMR", sha).split() if FORBIDDEN_FILES.search(f)]
        findings += scan_text(f"{short} commit message", git("log", "-1", "--format=%B", sha), secrets)
        for f, text in added_lines(git("show", "-U0", "--no-color", "--format=", sha)).items():
            findings += scan_text(f"{short} {f}", text, secrets)
    return findings


def check_full(secrets: dict[str, str]) -> list[str]:
    findings = [f"{f}: secret/state file is tracked" for f in git("ls-files").split() if FORBIDDEN_FILES.search(f)]
    for f in git("ls-files").splitlines():
        findings += scan_text(f"HEAD {f}", read(ROOT / f), secrets)
    return findings + check_range("--all", secrets)


def install_hooks() -> int:
    hooks = Path(git("rev-parse", "--git-path", "hooks").strip())
    hooks = hooks if hooks.is_absolute() else ROOT / hooks
    hooks.mkdir(parents=True, exist_ok=True)
    pre_commit = '#!/bin/sh\nexec python3 "$(git rev-parse --show-toplevel)/security_check.py" --staged\n'
    pre_push = (
        "#!/bin/sh\n"
        "zero=0000000000000000000000000000000000000000\n"
        "while read local_ref local_sha remote_ref remote_sha; do\n"
        '  [ "$local_sha" = "$zero" ] && continue\n'
        '  if [ "$remote_sha" = "$zero" ]; then range="$local_sha"; else range="$remote_sha..$local_sha"; fi\n'
        '  python3 "$(git rev-parse --show-toplevel)/security_check.py" --range "$range" || exit 1\n'
        "done\n"
    )
    for name, body in (("pre-commit", pre_commit), ("pre-push", pre_push)):
        path = hooks / name
        path.write_text(body)
        path.chmod(0o755)
        print(f"[security] installed {path}")
    return 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["--install-hooks"]:
        return install_hooks()
    secrets = local_secret_values()
    if not (ROOT / ".env").exists() and not (ROOT / "config.json").exists():
        print("[security] WARNING: no local .env or config.json; only file-name and pattern checks ran", file=sys.stderr)
    if argv[:1] == ["--staged"]:
        findings = check_staged(secrets)
    elif argv[:1] == ["--range"] and len(argv) == 2:
        findings = check_range(argv[1], secrets)
    elif not argv:
        findings = check_full(secrets)
    else:
        print(__doc__)
        return 2
    if findings:
        print("[security] BLOCKED — this repo is public. Remove these before committing/pushing:", file=sys.stderr)
        for f in sorted(set(findings)):
            print(f"  - {f}", file=sys.stderr)
        return 1
    print(f"[security] OK ({len(secrets)} local values + {len(PATTERNS)} patterns checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
