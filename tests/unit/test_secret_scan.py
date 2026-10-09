"""Regression tests for scripts/secret-scan.sh — the "no secret is ever pushed" gate.

The scanner's contract, and what each test pins down:

* Git OBJECTS, not the working tree, are what gets scanned in --staged/--tracked/
  --history. A secret edited out of the working copy is still in the index; a secret
  committed and later deleted is still in history. Only --files reads the worktree.
* Fail closed twice over: a scanner/tool error is exit 2, and truncated or
  unparseable scanner output becomes a finding (exit 1) with content withheld —
  never a silent pass.
* Findings print `path:line: label` only; the matched secret itself is never echoed.
* The allowlist can suppress the credential-URL pattern on named paths, never a
  token pattern anywhere.
* The hooks distinguish "findings" (exit 1) from "scanner could not run" (exit >= 2).

Tokens are built at runtime ("ghp_" + "A" * 36): a literal token in this file would
be flagged by the scanner the moment the file itself is committed.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GIT_BIN = shutil.which("git") or "git"
BASH_BIN = shutil.which("bash") or "bash"
GREP_BIN = shutil.which("grep") or "grep"
RG_BIN = shutil.which("rg")
SCAN = REPO_ROOT / "scripts" / "secret-scan.sh"
PRE_COMMIT = REPO_ROOT / "scripts" / "pre-commit.sh"
PRE_PUSH = REPO_ROOT / "scripts" / "pre-push.sh"

TOKEN = "ghp_" + "A" * 36
CRED_URL = "http://" + "analyst:hunter2" + "@" + "example.test/"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [GIT_BIN, *args], cwd=repo, check=True, capture_output=True, text=True
    )


def _repo(tmp_path: Path) -> Path:
    """A fresh repository with one clean commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "scanner@example.test")
    _git(repo, "config", "user.name", "Scanner Test")
    (repo / "README.md").write_text("clean\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-qm", "init")
    return repo


def _scan(
    *args: str, cwd: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [BASH_BIN, str(SCAN), *args], cwd=cwd, capture_output=True, text=True, env=env
    )


def _path_env(bin_dir: Path) -> dict[str, str]:
    return {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}


def _stub(bin_dir: Path, name: str, body: str) -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / name
    stub.write_text("#!/bin/sh\n" + body)
    stub.chmod(0o755)
    return stub


def _install_hooks(repo: Path) -> None:
    """Copy the scanner and hook into the fixture repo (hooks call ./scripts/…)."""
    scripts = repo / "scripts"
    scripts.mkdir(exist_ok=True)
    shutil.copy2(SCAN, scripts / "secret-scan.sh")
    shutil.copy2(PRE_COMMIT, scripts / "pre-commit.sh")
    shutil.copy2(PRE_PUSH, scripts / "pre-push.sh")


# ---------------------------------------------------------------------------
# Git-object scanning beats working-tree scanning
# ---------------------------------------------------------------------------


def test_staged_secret_survives_worktree_edit(tmp_path: Path) -> None:
    """Staged then edited clean: the index still holds the secret."""
    repo = _repo(tmp_path)
    (repo / "leak.txt").write_text(f"token {TOKEN}\n")
    _git(repo, "add", "leak.txt")
    (repo / "leak.txt").write_text("clean now\n")

    staged = _scan("--staged", cwd=repo)
    assert staged.returncode == 1
    assert "GitHub token (classic)" in staged.stdout
    assert TOKEN not in staged.stdout  # finding, not content

    # The worktree copy really is clean — this is what the old worktree scan missed.
    assert _scan("--files", "leak.txt", cwd=repo).returncode == 0


def test_tracked_scans_head_not_worktree(tmp_path: Path) -> None:
    """Committed secret removed from the worktree but not from HEAD: still flagged."""
    repo = _repo(tmp_path)
    (repo / "leak.txt").write_text(f"token {TOKEN}\n")
    _git(repo, "add", "leak.txt")
    _git(repo, "commit", "-qm", "leak")
    (repo / "leak.txt").write_text("clean now\n")

    assert _scan("--files", "leak.txt", cwd=repo).returncode == 0
    tracked = _scan("--tracked", cwd=repo)
    assert tracked.returncode == 1
    assert "leak.txt:1: GitHub token (classic)" in tracked.stdout


def test_history_keeps_deleted_secret(tmp_path: Path) -> None:
    """Deleted in a later commit: --tracked (HEAD) is clean, --history is not."""
    repo = _repo(tmp_path)
    (repo / "leak.txt").write_text(f"token {TOKEN}\n")
    _git(repo, "add", "leak.txt")
    _git(repo, "commit", "-qm", "leak")
    _git(repo, "rm", "-q", "leak.txt")
    _git(repo, "commit", "-qm", "remove leak")

    assert _scan("--tracked", cwd=repo).returncode == 0
    history = _scan("--history", cwd=repo)
    assert history.returncode == 1
    assert "leak.txt:1: GitHub token (classic)" in history.stdout


# ---------------------------------------------------------------------------
# Filename guard
# ---------------------------------------------------------------------------


def test_forced_env_file_blocked_even_when_removed_from_disk(tmp_path: Path) -> None:
    """git add -f .env && rm .env: no content to match, but the *name* must block."""
    repo = _repo(tmp_path)
    (repo / ".env").write_text("X=1\n")
    _git(repo, "add", "-f", ".env")
    (repo / ".env").unlink()

    staged = _scan("--staged", cwd=repo)
    assert staged.returncode == 1
    assert ".env:0: forbidden file name" in staged.stdout

    _git(repo, "commit", "-qm", "add env")
    tracked = _scan("--tracked", cwd=repo)
    assert tracked.returncode == 1
    assert ".env:0: forbidden file name" in tracked.stdout

    # Removal from HEAD ends the tracked-tree finding but never history.
    _git(repo, "rm", "-q", "--cached", ".env")
    _git(repo, "commit", "-qm", "remove env")
    assert _scan("--tracked", cwd=repo).returncode == 0
    history = _scan("--history", cwd=repo)
    assert history.returncode == 1
    assert ".env:0: forbidden file name" in history.stdout


def test_env_variant_filenames_blocked(tmp_path: Path) -> None:
    """prod.env, .env.local and .envrc are env files too; .env.example is the template."""
    repo = _repo(tmp_path)
    for name in ("prod.env", ".env.local", ".envrc"):
        (repo / name).write_text("X=1\n")
        _git(repo, "add", name)
        result = _scan("--staged", cwd=repo)
        assert result.returncode == 1, name
        assert f"{name}:0: forbidden file name" in result.stdout
        _git(repo, "rm", "-q", "--cached", name)

    (repo / ".env.example").write_text("URL=" + CRED_URL + "\n")
    _git(repo, "add", ".env.example")
    assert _scan("--staged", cwd=repo).returncode == 0


def test_colon_filename_is_not_dropped(tmp_path: Path) -> None:
    """'a:b.txt' defeated the old colon-splitting parser; framing must keep it."""
    repo = _repo(tmp_path)
    path = "a:b.txt"
    (repo / path).write_text(f"token {TOKEN}\n")
    _git(repo, "add", path)

    staged = _scan("--staged", cwd=repo)
    assert staged.returncode == 1
    assert f"{path}:1: GitHub token (classic)" in staged.stdout
    assert _scan("--files", path, cwd=repo).returncode == 1

    _git(repo, "commit", "-qm", "colon")
    tracked = _scan("--tracked", cwd=repo)
    assert tracked.returncode == 1
    assert f"{path}:1: GitHub token (classic)" in tracked.stdout


def test_binary_file_with_nul_bytes(tmp_path: Path) -> None:
    """A NUL byte must not turn into a dropped 'binary file matches' notice."""
    repo = _repo(tmp_path)
    (repo / "bin.dat").write_bytes(b"bin\x00tok " + TOKEN.encode() + b"\n")
    _git(repo, "add", "bin.dat")

    assert _scan("--files", "bin.dat", cwd=repo).returncode == 1
    staged = _scan("--staged", cwd=repo)
    assert staged.returncode == 1
    assert "bin.dat:1: GitHub token (classic)" in staged.stdout

    _git(repo, "commit", "-qm", "bin")
    assert _scan("--tracked", cwd=repo).returncode == 1


# ---------------------------------------------------------------------------
# Messages: commit and annotated tag
# ---------------------------------------------------------------------------


def test_commit_message_scanned(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _git(repo, "commit", "-q", "--allow-empty", "-m", f"note: {TOKEN} in message")

    history = _scan("--history", cwd=repo)
    assert history.returncode == 1
    assert "(commit message)" in history.stdout
    assert re.search(r"[0-9a-f]{40}:0: GitHub token \(classic\) \(commit message\)", history.stdout)
    assert TOKEN not in history.stdout


def test_annotated_tag_message_scanned(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _git(repo, "tag", "-a", "rel-1", "-m", f"release {TOKEN}")
    _git(repo, "tag", "light-1")  # lightweight: no message of its own

    history = _scan("--history", cwd=repo)
    assert history.returncode == 1
    assert "refs/tags/rel-1:0: GitHub token (classic) (annotated tag message)" in history.stdout
    assert TOKEN not in history.stdout


# ---------------------------------------------------------------------------
# Allowlist scope: URL suppressed on named paths, tokens never suppressed
# ---------------------------------------------------------------------------


def test_allowlist_scope(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / ".env.example").write_text("URL=" + CRED_URL + "\n")
    _git(repo, "add", ".env.example")
    assert _scan("--staged", cwd=repo).returncode == 0, "url placeholder in allowlisted path"

    (repo / ".env.example").write_text("URL=" + CRED_URL + f"\nTOK={TOKEN}\n")
    _git(repo, "add", ".env.example")
    blocked = _scan("--staged", cwd=repo)
    assert blocked.returncode == 1, "a token on an allowlisted path must still block"
    assert "GitHub token (classic)" in blocked.stdout

    # Same URL outside the allowlist: reported.
    (repo / "normal.txt").write_text("DB=" + CRED_URL + "\n")
    _git(repo, "add", "normal.txt")
    result = _scan("--staged", cwd=repo)
    assert result.returncode == 1
    assert "Credential-bearing URL" in result.stdout


def test_pattern_engines_accept_every_pattern(tmp_path: Path) -> None:
    """All patterns must compile in ripgrep, grep -E and `git log --grep` (POSIX ERE).

    An engine-rejected pattern would make that engine's scan fail — with exit 2
    (fail closed), but a green test keeps the pattern table honest anyway.
    """
    listing = subprocess.run(  # noqa: S603
        [
            BASH_BIN,
            "-c",
            'eval "$(sed -n "/^emit_patterns()/,/^}/p" "$1")"; emit_patterns',
            "extract",
            str(SCAN),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    patterns = [line.split("\t") for line in listing.splitlines() if line]
    assert len(patterns) == 10

    for scope, label, pattern in patterns:
        assert scope in {"token", "url"}, label
        if RG_BIN:
            rg = subprocess.run(  # noqa: S603
                [RG_BIN, "-e", pattern], input="", capture_output=True, text=True
            )
            assert rg.returncode in {0, 1}, f"rg rejected {label}: {rg.stderr}"
        grep = subprocess.run(  # noqa: S603
            [GREP_BIN, "-E", "-e", pattern], input="", capture_output=True, text=True
        )
        assert grep.returncode in {0, 1}, f"grep -E rejected {label}: {grep.stderr}"
        git_log = subprocess.run(  # noqa: S603
            [GIT_BIN, "log", "--all", "--extended-regexp", f"--grep={pattern}", "--format=%H"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert git_log.returncode == 0, f"git log --grep rejected {label}: {git_log.stderr}"


# ---------------------------------------------------------------------------
# Fail closed: tool errors exit 2, unparseable output becomes a finding
# ---------------------------------------------------------------------------


def test_scanner_error_exits_2(tmp_path: Path) -> None:
    """A search tool that cannot run must not be mistaken for 'no findings'."""
    repo = _repo(tmp_path)
    (repo / "file.txt").write_text("clean\n")
    bin_dir = tmp_path / "bin"
    _stub(bin_dir, "rg", "exit 2\n")

    result = _scan("--files", "file.txt", cwd=repo, env=_path_env(bin_dir))
    assert result.returncode == 2
    assert "search tool failed" in result.stderr


def test_unparseable_scanner_output_is_a_finding(tmp_path: Path) -> None:
    """Tool output that is not a record blocks the run — content never echoed."""
    repo = _repo(tmp_path)
    (repo / "file.txt").write_text("clean\n")
    bin_dir = tmp_path / "bin"
    _stub(bin_dir, "rg", "printf 'garbage without framing\\n'\nexit 0\n")

    result = _scan("--files", "file.txt", cwd=repo, env=_path_env(bin_dir))
    assert result.returncode == 1
    assert "content withheld" in result.stdout
    assert "garbage" not in result.stdout


# ---------------------------------------------------------------------------
# Hooks: findings (1) vs scanner-broken (>=2) are distinct branches
# ---------------------------------------------------------------------------


def test_pre_commit_blocks_findings(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _install_hooks(repo)
    (repo / "notes.txt").write_text(f"token {TOKEN}\n")
    _git(repo, "add", "notes.txt")

    result = subprocess.run(  # noqa: S603
        [BASH_BIN, str(repo / "scripts" / "pre-commit.sh")],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "SECRET DETECTED" in result.stdout
    assert "check-fast" not in result.stdout  # scan blocks before the test gate


def test_pre_commit_fails_closed_on_scanner_error(tmp_path: Path) -> None:
    """git grep broken => scanner exit 2 => hook says SCAN ERROR, not DETECTED."""
    repo = _repo(tmp_path)
    _install_hooks(repo)
    (repo / "notes.txt").write_text("nothing to see\n")
    _git(repo, "add", "notes.txt")

    real_git = shutil.which("git")
    assert real_git is not None
    bin_dir = tmp_path / "bin"
    _stub(bin_dir, "git", f'if [ "$1" = "grep" ]; then exit 128; fi\nexec "{real_git}" "$@"\n')

    result = subprocess.run(  # noqa: S603
        [BASH_BIN, str(repo / "scripts" / "pre-commit.sh")],
        cwd=repo,
        capture_output=True,
        text=True,
        env=_path_env(bin_dir),
    )
    assert result.returncode == 1
    assert "SECRET SCAN ERROR" in result.stdout
    assert "SECRET DETECTED" not in result.stdout


def test_pre_push_blocks_findings(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _install_hooks(repo)
    (repo / "leak.txt").write_text(f"token {TOKEN}\n")
    _git(repo, "add", "leak.txt")
    _git(repo, "commit", "-qm", "leak")

    result = subprocess.run(  # noqa: S603
        [BASH_BIN, str(repo / "scripts" / "pre-push.sh")],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "PUSH BLOCKED — secret detected" in result.stdout
    assert "running full check" not in result.stdout  # blocked before make check


def test_pre_push_fails_closed_on_scanner_error(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _install_hooks(repo)

    real_git = shutil.which("git")
    assert real_git is not None
    bin_dir = tmp_path / "bin"
    _stub(bin_dir, "git", f'if [ "$1" = "grep" ]; then exit 128; fi\nexec "{real_git}" "$@"\n')

    result = subprocess.run(  # noqa: S603
        [BASH_BIN, str(repo / "scripts" / "pre-push.sh")],
        cwd=repo,
        capture_output=True,
        text=True,
        env=_path_env(bin_dir),
    )
    assert result.returncode == 1
    assert "SECRET SCAN ERROR" in result.stdout
    assert "running full check" not in result.stdout


# ---------------------------------------------------------------------------
# Clean paths and usage
# ---------------------------------------------------------------------------


def test_clean_repo_all_modes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    for mode in ("--staged", "--tracked", "--history"):
        result = _scan(mode, cwd=repo)
        assert result.returncode == 0, result.stdout
        assert "secret-scan: clean" in result.stdout


def test_missing_files_path_reports_scope(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    result = _scan("--files", "does-not-exist.txt", cwd=repo)
    assert result.returncode == 0
    assert "0 of 1 file(s)" in result.stdout


def test_usage_errors_exit_2(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert _scan("--nope", cwd=repo).returncode == 2
    assert _scan("--files", cwd=repo).returncode == 2
    assert _scan("--staged", "extra.txt", cwd=repo).returncode == 2
