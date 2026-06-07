# Mutation testing (mutmut)

Mutation testing measures whether the test suite actually *catches behavior changes*,
not just whether it passes. The tool ([mutmut](https://mutmut.readthedocs.io/) 3.x)
makes small edits to the source ("mutants" — e.g. `>` becomes `>=`, `and` becomes `or`,
a return value flips) and re-runs the covering tests. A mutant that the tests still pass
on is a **survivor**: a real change to behavior that no test would have caught. Survivors
mark blind spots in the suite.

We introduced this as a safety net ahead of the architecture-review refactors (candidates
01 and 03) so test gaps surface *before* production code moves.

## Running it

```bash
./scripts/mutation.sh          # run the scoped mutants, then print results
./scripts/mutation.sh results  # re-print the last run's results
./scripts/mutation.sh stats    # write mutants/mutmut-cicd-stats.json
```

Under the hood: `python -m mutmut run`, `python -m mutmut results`,
`python -m mutmut export_cicd_stats`.

mutmut is **coverage-guided**: it runs the full pytest suite once to learn which tests
cover each line, then for each mutant runs only the covering tests. That is what keeps a
run over `runner.py` tractable despite a 37s full suite.

## Scope

`[tool.mutmut]` in `pyproject.toml` pins `source_paths` to the refactor targets rather
than all of `src/`. A naive whole-codebase run re-checks thousands of mutants and is a
nightly/CI job, not an interactive one. Widen `source_paths` as more modules are hardened.

## Reading the result

`mutmut results` lists each mutant by id and status. The summary line reports, with the
glyphs mutmut uses:

- 🎉 `killed` — a test failed on the mutant. Good.
- 🙁 `survived` — no test caught it. A gap: write a test that does, or confirm it is an
  equivalent mutant (see below).
- 🫥 `no_tests` — no test covers that line at all.
- ⏰ `timeout` / 🤔 `suspicious` — treated as killed-ish (the mutant broke something).

Inspect a specific mutant's diff with `python -m mutmut show <id>`.

## The bar we hold

For the scoped refactor targets: **100% killed, minus documented equivalent mutants.**
An *equivalent mutant* is a change that produces semantically identical behavior (e.g.
mutating a value that is never observed, or a `<=`/`<` boundary that no input can reach).
Those are genuinely unkillable; we list them here rather than chase them.

### Documented equivalent mutants

_(none yet — populated as the scoped runs surface them)_

## CI

Not a per-PR blocking gate (too slow for that). Intended as a manual/nightly check:
`./scripts/mutation.sh run` then `./scripts/mutation.sh stats` to emit
`mutants/mutmut-cicd-stats.json` for trend tracking.
