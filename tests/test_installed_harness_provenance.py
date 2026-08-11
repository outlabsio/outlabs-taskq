from __future__ import annotations

from subprocess import CompletedProcess

import taskq.bench


def test_git_sha_is_unknown_when_git_executable_is_absent(monkeypatch) -> None:
    def missing_git(*_args: object, **_kwargs: object) -> CompletedProcess[str]:
        raise FileNotFoundError("git")

    monkeypatch.setattr(taskq.bench.subprocess, "run", missing_git)
    assert taskq.bench._git_sha() == "unknown"


def test_git_sha_is_unknown_outside_a_checkout(monkeypatch) -> None:
    monkeypatch.setattr(
        taskq.bench.subprocess,
        "run",
        lambda *_args, **_kwargs: CompletedProcess(
            args=["git", "rev-parse", "HEAD"], returncode=128, stdout="", stderr="not a repo"
        ),
    )
    assert taskq.bench._git_sha() == "unknown"
