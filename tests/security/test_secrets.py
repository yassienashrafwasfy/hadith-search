"""Secrets: nothing sensitive is committed. Scans the files git tracks, not the working folder."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MAX_BYTES = 2_000_000
# Lock files are long hashes and URLs by design; images are binary.
SKIP_NAMES = {"package-lock.json"}
FORBIDDEN_NAMES = re.compile(
    r"(^|/)(\.env(\.(local|prod|production|development))?|id_rsa|id_ed25519|.*\.(pem|key|p12|pfx|jks|kdbx)"
    r"|\.netrc|\.pgpass|credentials\.json|service[-_]account.*\.json)$"
)
KEY_PATTERNS = {
    "private key block": r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY",
    "AWS access key id": r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
    "GitHub token": r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b|\bgithub_pat_[A-Za-z0-9_]{50,}\b",
    "Slack token": r"\bxox[baprs]-[A-Za-z0-9-]{10,}",
    "Google API key": r"\bAIza[0-9A-Za-z_\-]{35}\b",
    "Anthropic or OpenAI key": r"\bsk-(?:ant-)?[A-Za-z0-9_\-]{32,}",
    "Hugging Face token": r"\bhf_[A-Za-z0-9]{30,}\b",
    "signed JWT": r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}",
    "password in a connection URL": r"\b[a-z+]+://[^\s:/@'\"]+:(?!\$|\{|<|%|\*)[^\s:/@'\"$]{4,}@[^\s'\"/]+",
}
# The URL rule skips tests/ (fixtures use made-up passwords on purpose) and these placeholders.
PLACEHOLDER = re.compile(
    r"change-me|test-only-password|password|example|user:pass|u:p|\$\{|<", re.I
)


def _tracked() -> list[Path]:
    git = shutil.which("git")
    if not git or not (ROOT / ".git").exists():
        pytest.skip("not a git checkout, so there is no list of tracked files")
    out = subprocess.run(
        [git, "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
    ).stdout.decode()
    return [ROOT / name for name in out.split("\0") if name and (ROOT / name).is_file()]


def _text(path: Path) -> str | None:
    if path.name in SKIP_NAMES or path.stat().st_size > MAX_BYTES:
        return None
    data = path.read_bytes()
    return None if b"\0" in data else data.decode("utf-8", errors="replace")


def test_no_secret_files_are_tracked():
    names = [str(p.relative_to(ROOT)) for p in _tracked()]
    bad = [n for n in names if FORBIDDEN_NAMES.search(n) and n != ".env.example"]
    assert not bad, f"tracked files that look like secrets: {bad}"


def test_the_real_env_file_is_ignored_by_git():
    git = shutil.which("git")
    if not git or not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    for name in (".env", "backend/.env"):
        result = subprocess.run([git, "check-ignore", "-q", name], cwd=ROOT)
        assert result.returncode == 0, f"{name} is not ignored"


@pytest.mark.parametrize("kind", list(KEY_PATTERNS))
def test_no_tracked_file_contains(kind):
    pattern = re.compile(KEY_PATTERNS[kind])
    hits = []
    for path in _tracked():
        text = _text(path)
        if text is None:
            continue
        in_tests = path.relative_to(ROOT).parts[0] == "tests"
        for match in pattern.finditer(text):
            if kind == "password in a connection URL" and (
                in_tests or PLACEHOLDER.search(match.group())
            ):
                continue
            hits.append(f"{path.relative_to(ROOT)}: {match.group()[:12]}...")
    assert not hits, hits


def _find_gitleaks() -> str | None:
    found = shutil.which("gitleaks")
    if found:
        return found
    cached = sorted(Path.home().glob(".cache/pre-commit/*/golangenv-*/bin/gitleaks"))
    return str(cached[0]) if cached else None


def test_gitleaks_finds_nothing_in_the_tracked_files(tmp_path):
    """The same scanner the pre-commit hook runs, over a copy of the tracked files.

    The repo has no .gitleaks.toml, so this uses gitleaks' default rules (as the hook does).
    """
    binary = _find_gitleaks()
    if binary is None:
        pytest.skip("gitleaks is not installed (pre-commit installs it under ~/.cache/pre-commit)")
    for path in _tracked():
        target = tmp_path / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    result = subprocess.run(
        [binary, "dir", str(tmp_path), "--no-banner", "--redact", "--exit-code", "1"],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]


def test_the_patterns_catch_known_samples():
    """Guards the scan itself: samples are built in pieces so this file does not trip it."""
    samples = {
        "private key block": "-----BEGIN " + "RSA PRIVATE KEY-----",
        "AWS access key id": "AK" + "IA" + "ABCDEFGHIJKLMNOP",
        "GitHub token": "gh" + "p_" + "a" * 36,
        "Slack token": "xo" + "xb-" + "1234567890-abcdef",
        "Google API key": "AI" + "za" + "A" * 35,
        "Anthropic or OpenAI key": "sk-" + "ant-" + "a" * 40,
        "Hugging Face token": "hf" + "_" + "a" * 34,
        "signed JWT": "ey" + "J" + "a" * 12 + ".ey" + "J" + "b" * 12 + "." + "c" * 12,
        "password in a connection URL": "postgresql://admin:Sup3rSecret@db.internal/app",
    }
    assert set(samples) == set(KEY_PATTERNS)
    for kind, sample in samples.items():
        assert re.search(KEY_PATTERNS[kind], sample), kind
    assert FORBIDDEN_NAMES.search("config/server.pem") and FORBIDDEN_NAMES.search(".env")
    assert not FORBIDDEN_NAMES.search(".env.example")
