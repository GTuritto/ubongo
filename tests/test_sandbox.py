from __future__ import annotations

import pytest

from ubongo.sandbox import (
    ALLOWED_COMMANDS,
    SandboxRefused,
    run_constrained,
)


def test_echo_runs_and_returns_stdout():
    result = run_constrained("echo hello")
    assert result.exit_code == 0
    assert result.stdout.strip() == "hello"
    assert result.argv == ("echo", "hello")


def test_pwd_runs_in_repo_root():
    result = run_constrained("pwd")
    assert result.exit_code == 0
    # cwd is the repo root which contains pyproject.toml
    from pathlib import Path
    expected_root = Path(__file__).resolve().parent.parent
    assert result.stdout.strip() == str(expected_root)


def test_disallowed_program_is_refused():
    with pytest.raises(SandboxRefused, match="allowlist"):
        run_constrained("rm -f /tmp/foo")


def test_shell_semicolon_is_refused():
    with pytest.raises(SandboxRefused, match="metacharacter"):
        run_constrained("ls; cat /etc/passwd")


def test_pipe_is_refused():
    with pytest.raises(SandboxRefused, match="metacharacter"):
        run_constrained("ls | grep x")


def test_redirect_is_refused():
    with pytest.raises(SandboxRefused, match="metacharacter"):
        run_constrained("echo hi > /tmp/out")


def test_backtick_is_refused():
    with pytest.raises(SandboxRefused, match="metacharacter"):
        run_constrained("echo `pwd`")


def test_command_substitution_is_refused():
    with pytest.raises(SandboxRefused, match="metacharacter"):
        run_constrained("echo $(pwd)")


def test_path_traversal_argument_is_refused():
    with pytest.raises(SandboxRefused, match="path fragment"):
        run_constrained("cat ../../etc/passwd")


def test_etc_path_is_refused():
    with pytest.raises(SandboxRefused, match="path fragment"):
        run_constrained("cat /etc/passwd")


def test_home_tilde_is_refused():
    with pytest.raises(SandboxRefused, match="home-dir"):
        run_constrained("ls ~")


def test_empty_command_is_refused():
    with pytest.raises(SandboxRefused, match="empty"):
        run_constrained("")
    with pytest.raises(SandboxRefused, match="empty"):
        run_constrained("   ")


def test_nonzero_exit_returned_not_raised():
    # `false` exits 1 cleanly; should be a SandboxResult, not an exception.
    result = run_constrained("false")
    assert result.exit_code == 1
    assert result.stdout == ""


def test_timeout_returns_result_with_exit_neg_one():
    # Single-expression sleep (no ';' since the metachar check is strict).
    result = run_constrained('python3 -c "__import__(\'time\').sleep(2)"', timeout=1)
    assert result.exit_code == -1
    assert "timed out" in result.stderr


def test_child_path_is_empty():
    # Phase 15c: the child runs with PATH="" — it cannot find programs by name.
    # No ';' in the python source: import inline via __import__.
    result = run_constrained('python3 -c "print(repr(__import__(\'os\').environ.get(\'PATH\')))"')
    assert result.exit_code == 0
    assert result.stdout.strip() == "''"


def test_allowed_commands_set_is_read_mostly():
    forbidden = {"rm", "mv", "cp", "chmod", "chown", "curl", "wget", "ssh", "docker", "make"}
    assert ALLOWED_COMMANDS.isdisjoint(forbidden)
