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


# --- Phase 15c: empty PATH + filesystem allowlist ---


def test_absolute_path_outside_repo_is_refused():
    # /etc is caught by the fragment blacklist; an arbitrary out-of-repo
    # absolute path is caught by the new positive containment rule.
    with pytest.raises(SandboxRefused, match="outside the repo sandbox"):
        run_constrained("cat /tmp/secret.txt")


def test_absolute_path_to_user_home_is_refused():
    with pytest.raises(SandboxRefused, match="outside the repo sandbox"):
        run_constrained("cat /Users/somebody/.ssh/id_rsa")


def test_in_repo_absolute_path_is_allowed():
    # The repo's own README, addressed absolutely, resolves inside the sandbox.
    from ubongo.sandbox import _REPO_ROOT
    result = run_constrained(f"cat {_REPO_ROOT / 'README.md'}")
    assert result.exit_code == 0
    assert "Ubongo" in result.stdout or len(result.stdout) > 0


def test_relative_in_repo_path_still_works():
    result = run_constrained("cat README.md")
    assert result.exit_code == 0


def test_program_paths_resolved_to_absolute():
    # Common allowlisted commands resolve to absolute paths at import.
    from ubongo.sandbox import _PROGRAM_PATHS
    assert "echo" in _PROGRAM_PATHS
    assert _PROGRAM_PATHS["echo"].startswith("/")
    assert "git" in _PROGRAM_PATHS


def test_child_cannot_spawn_program_by_bare_name():
    # With PATH="" the child can't resolve `ls` itself — defense in depth
    # against an allowlisted interpreter shelling out. Single expression so
    # the command carries no ';' metacharacter.
    code = "__import__('subprocess').run(['ls'])"
    result = run_constrained(f'python3 -c "{code}"')
    # The child's subprocess.run raises FileNotFoundError -> non-zero exit.
    assert result.exit_code != 0


def test_curl_refused_network_via_allowlist():
    # Phase 15 scenario 6: no network tool is allowlisted.
    with pytest.raises(SandboxRefused, match="not in allowlist"):
        run_constrained("curl https://example.com")
