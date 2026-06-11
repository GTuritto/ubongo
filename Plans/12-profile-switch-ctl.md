# Phase 12 — Startup profiler switch + service control

Branch: `improve/12-profile-switch-ctl`. Approved in-session 2026-06-11
(design questions answered: one selectable knob `UBONGO_PROFILE=cpu|mem|all`
with a matching launch flag, flag wins; service commands as a control script
plus a systemd unit for the Pi deployment).

## Problem

The profiler (candidates 10–11) can only be armed interactively, per session.
There is no way to start Ubongo with profiling already on (for reproducing an
issue from launch), and no way to manage the long-running web service on the
LAN box: the launchers run foreground only — no start/stop/restart/status, no
pidfile, nothing that survives a reboot.

## Solution

### 12a — Startup profiler switch

One knob, resolved once at startup, flag over env:

- `profiling.resolve_startup_profile(flag, env) -> "cpu"|"mem"|"all"|None` —
  pure, testable. Flag values come pre-validated by argparse `choices`; env
  accepts `cpu|mem|all` (case-insensitive), treats empty/`off`/`0`/`false` as
  off, and warns-then-ignores anything else (an invalid env var must never
  block startup).
- `__main__.py`: top-level `--profile [cpu|mem|all|off]` (bare `--profile`
  means `cpu`) for the REPL path; `send --profile` upgraded from `store_true`
  to the same optional-value form (bare flag still means `cpu`, so existing
  usage is unchanged). Env read: `UBONGO_PROFILE` (loaded from `.env` by the
  existing dotenv path).
- REPL: `repl.run(startup_profile=...)`; a helper `_apply_startup_profile`
  arms the same toggles `/profile cpu on` / `/profile mem on` control
  (`state.cpu_profile = True`, `profiling.mem_start()`) and returns the
  startup notice line. `/profile ... off` disarms mid-session as usual.
- One-shot: `oneshot.run(profile=...)` accepts the value (True normalizes to
  `"cpu"` for backward compatibility): `cpu` wraps the turn as today; `mem`
  takes a baseline before the turn and prints the growth report after it;
  `all` does both.
- Web: `web/turn.bootstrap()` resolves the env knob once; when it includes
  `cpu`, `run_turn` routes `master.handle` through `profile_call` and logs the
  report's first line (`CPU profile written to …`) — artifacts in
  `data/profiles/` as usual, nothing rendered in the UI. `mem` is REPL-only
  (the web UI has no report surface); documented, not wired.
- `.env.example`: documented `UBONGO_PROFILE=` line. The "secrets only in
  .env" rule is about config files never holding secrets, not the reverse;
  `UBONGO_PROFILE` joins the existing `UBONGO_*` env-switch family.

### 12b — `ubongo-ctl.sh` (start|stop|restart|status)

A control script next to the existing launchers, shipped in the bundle:

- `start`: refuses if already running; otherwise `nohup ./start-ubongo-web.sh`
  in the background, stdout+stderr appended to `data/ubongo-web.log`, pid to
  `data/ubongo-web.pid` (the launcher `exec`s streamlit, so the pid is the
  server). Verifies liveness after a beat.
- `stop`: TERM, wait up to 10s, KILL fallback; removes the pidfile; tolerates
  a stale pidfile (reports and cleans).
- `status`: pid + uptime hint when running; "not running" otherwise; exit code
  0/1 so scripts can test it.
- `restart`: stop then start.
- The REPL stays foreground by design — stop/restart of an interactive
  session is `/exit`.

### 12c — systemd unit (Pi/Ubuntu)

`deploy/ubongo-web.service`: `Type=exec`, `ExecStart` pointing at
`start-ubongo-web.sh`, `Restart=on-failure`, optional
`EnvironmentFile=-…/.env`, `WantedBy=default.target`, with install steps in
comments (edit paths, `systemctl --user enable --now` or system-wide). The
unit and `ubongo-ctl.sh` are alternatives: systemd on the Pi for
reboot-survival, the script everywhere else.

### 12d — packaging

`scripts/package.sh`: add `ubongo-ctl.sh` to the top-level file list and copy
`deploy/` into the bundle.

## Behavior to preserve

- `ubongo send --profile "msg"` behaves exactly as today (bare flag = cpu).
- `repl.run()` with no knob behaves exactly as today (nothing armed).
- Web turns without the env knob: untouched path, no profiling import cost.
- A profiling failure still never breaks a turn (inherited from 10/11).

## Testing

- `tests/test_profiling.py`: `resolve_startup_profile` precedence (flag wins),
  off/empty/invalid env handling.
- `tests/test_repl.py`-style: `_apply_startup_profile` arms cpu/mem/all on a
  fresh `ReplState`, returns the notice, disarmed afterwards by the fixture.
- One-shot: monkeypatched `master.handle`; `profile="mem"` prints a growth
  report and leaves tracemalloc stopped; `profile=True` still means cpu.
- Web: `run_turn` with the knob resolved to `cpu` produces a `.prof` (tmp db
  dir) and the response unchanged.
- `ubongo-ctl.sh`: manual smoke (start → status → restart → stop on this
  machine, streamlit is installed); not pytest.
- Smoke playbook: new section rows (launch with `--profile mem`, `.env` knob,
  ctl start/status/restart/stop, systemd pointer).

## Done when

- `pytest -q` fully green (908 + new).
- `ubongo --profile mem` REPL boots armed; `UBONGO_PROFILE=cpu ubongo` too.
- `./ubongo-ctl.sh start && status && restart && stop` works live.
- PR ready; user merges.

## Estimated size

~60 LOC python + ~90 LOC shell/unit + ~70 LOC tests.
