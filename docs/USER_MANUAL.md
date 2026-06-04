# Ubongo — User Manual (v0.1)

Ubongo is a personal, mood-aware AI mind that runs entirely on your own machine as a command-line app. *Ubongo* is Swahili for *brain*. It talks to you in one of three personas, remembers your conversations in a local database and an Obsidian-compatible Markdown vault, can run safe shell commands, gates risky actions behind your approval, and quietly tries to improve its own prompts over time — promoting changes only when you approve them.

This manual covers installing and running Ubongo on a Raspberry Pi 5 (or any Debian/Ubuntu machine), and everything you can do once it's running.

---

## 1. What you need

- A **Raspberry Pi 5** (or any Debian/Ubuntu box). 16 GB RAM is plenty; Ubongo is lightweight.
- **Ubuntu** (or Raspberry Pi OS based on Debian). Python **3.11 or newer** (Ubuntu 24.04 ships 3.12).
- An **OpenRouter API key** — free to create at <https://openrouter.ai/keys>. Ubongo uses OpenRouter for all model calls (chat + embeddings). You pay OpenRouter per use; Ubongo itself is free.
- An **internet connection** (for the model calls).

Ubongo is a single-user, single-machine, local app. No Docker, no database server, no cloud account beyond OpenRouter.

---

## 2. Install

1. Copy the bundle onto the Pi and unzip it:

   ```bash
   unzip ubongo-v0.1.zip
   cd ubongo-v0.1
   ```

2. If Python or the venv tools are missing, install them once:

   ```bash
   sudo apt update && sudo apt install -y python3 python3-venv python3-pip
   ```

3. Run the installer:

   ```bash
   ./install.sh
   ```

   It creates a private virtualenv (`.venv`), installs Ubongo and its dependencies, makes the `data/` and `vault/` folders, and asks for your OpenRouter API key (it saves it into `.env`). The first install on a Pi takes a few minutes.

That's it. The installer finishes by verifying a clean cold start.

> **Note on semantic recall:** Ubongo uses `sqlite-vec` for semantic memory. If it can't load on your platform, the installer says so and Ubongo runs in **recency-only** mode — everything else works normally. You lose only the "find older relevant turns" feature.

---

## 3. Your OpenRouter API key

Get a key at <https://openrouter.ai/keys>. The installer prompts for it; or edit `.env` yourself:

```
OPENROUTER_API_KEY=sk-or-v1-...your key...
```

Keep `.env` private — it holds your key and nothing else secret. Ubongo never puts secrets in config files.

---

## 4. Start Ubongo

Interactive (the normal way):

```bash
./start-ubongo.sh
```

You'll see `Ubongo REPL ready. /exit to quit.` and a `>` prompt. Type naturally and press Enter.

One-shot (run a single message and exit — good for scripts):

```bash
./start-ubongo.sh send "summarize what a write-ahead log is" --persona architect
```

To leave the REPL: type `/exit`, or press **Ctrl-D**.

---

## 5. How a turn works

When you type a message, Ubongo:

1. **Classifies** your intent and tone.
2. **Routes** to a persona + workflow.
3. **Runs** one or more worker agents (research, coding, evaluator, etc.).
4. **Governs** the result — auto-answers, asks you to clarify, asks approval, or refuses, based on risk.
5. **Composes** a reply in the chosen persona's voice.
6. **Remembers** everything (database + vault).

You just see a natural reply. Use `/trace` afterward if you want to see exactly what happened.

---

## 6. The three personas

Ubongo answers in one of three voices:

- **architect** — deep, structured, technical. For design and engineering questions.
- **operator** — terse and action-oriented. For tasks, ops, and logistics.
- **casual** — warm and brief. For chit-chat and quick exchanges.

Switch the voice yourself:

```
/architect      force the architect voice
/operator       force the operator voice
/casual         force the casual voice
/auto           let Ubongo pick the voice automatically (it classifies each turn)
```

A manual switch sticks until you change it or type `/auto`.

---

## 7. Command reference

All commands start with `/`. (One-shot mode uses CLI flags instead.)

### Personas & workflows
| Command | What it does |
|---|---|
| `/architect`, `/operator`, `/casual` | Force a persona for the session |
| `/auto` | Return to automatic persona selection |
| `/mode <workflow> \| list` | Pin a workflow + execution mode for the next turn (e.g. a debate or a competitive coding pass) |

### Inspection
| Command | What it does |
|---|---|
| `/agents` | List the ten worker agents |
| `/skills` | List available skills |
| `/decisions [N]` | The last N governance decisions |
| `/trace [N]` | Full execution trace of the last N turns (agents, timings, tokens, repair, governance) |
| `/policy` | Print the live governance decision matrix |
| `/queue [N]` | The outbound message queue |
| `/exec <cmd>` | Run one command through the constrained sandbox (debug; e.g. `/exec echo hi`) |

### Memory
| Command | What it does |
|---|---|
| `/recall [query]` | Show the recency window, semantic hits (older relevant turns), and the vault-graph neighbors of today's note |
| `/audit [category] [N]` | Tail the audit log; category is `governance`, `evolution`, or `sync` |
| `/conflicts [resolve <id> <keep-mine\|keep-theirs\|merge>]` | Review/resolve external edits you made to vault notes |

### Self-improvement (the genetic-programming loop)
| Command | What it does |
|---|---|
| `/optimize <target>` | Generate candidate variants of an evolvable target |
| `/evaluate <target>` | Score the latest variants into a fitness leaderboard |
| `/evolution <status\|pause\|resume\|off>` | Control the autonomous background loop (starts **paused**) |
| `/improvements [approve <id> \| reject <id> \| rollback <target>]` | Review proposed promotions and apply/undo them |

Evolvable targets: `persona:architect`, `persona:operator`, `persona:casual`, `routing:default`, `toolchain:<workflow>`, `retry:repair`.

### Skills & system
| Command | What it does |
|---|---|
| `/skill <name>` | Pin a skill for the next turn |
| `/summary` | Summarize the current conversation |
| `/reload` | Hot-reload settings, `UBONGO.md`, personas, skills, and routing (no restart) |
| `/exit` | Quit |

---

## 8. Memory and recall

Ubongo remembers in two places:

- **SQLite** (`data/ubongo.db`) — the canonical record of every message, decision, and run.
- **The vault** (`vault/`) — Obsidian-compatible Markdown. Each day's conversation lands in `vault/daily/YYYY-MM-DD.md`.

Recall blends two signals when answering:

- **Recency** — the last several messages, always.
- **Semantic recall** — if embeddings are on, Ubongo also retrieves older messages similar to your current question, even if they scrolled out of view. Ask "remember our discussion about X" and the relevant old turns come back.

Type `/recall` any time to see exactly what would be recalled.

---

## 9. The vault (Obsidian)

Point Obsidian at the `vault/` folder and you get a browsable journal of everything Ubongo discussed, with proper headings and tags.

- **Daily notes** are written automatically.
- **`[[wikilinks]]`** in a turn become a queryable link graph (see `/recall` neighbors).
- **Bidirectional sync (optional):** if you turn on `vault.sync.enabled` in `config/settings.yaml`, a background watcher notices when *you* edit a vault note in Obsidian and ingests your change (so semantic recall sees it). If your edit collides with something Ubongo manages, it queues a **conflict** instead of overwriting — review it with `/conflicts`. (Daily notes are append-only, so in practice your edits and Ubongo's just coexist.)

---

## 10. Self-improvement, with you in control

Ubongo can evolve its own prompts and routing/retry configuration:

1. It **generates** variants (`/optimize`), **evaluates** them against held-out sample conversations (`/evaluate`), and a background loop quietly **evolves** better generations.
2. When a candidate beats the current version by a margin, the loop **proposes a promotion**.
3. **Nothing changes until you approve it.** Run `/improvements` to see proposals with a diff and a fitness delta, then `approve`, `reject`, or `rollback`.

The background loop is **off (paused) by default** and never spends money on its own — type `/evolution resume` to let it run, `/evolution pause` or `/evolution off` to stop it, `/evolution status` to check. To enable it at all, set `evolution.enabled: true` in `config/settings.yaml`.

---

## 11. Safety and governance

Every turn is scored for risk, confidence, and reversibility. Risky turns are gated:

- A clearly destructive request (e.g. "delete the entire vault") triggers an **approval prompt**: `Approve? (y/n/why)`. Type `why` to see the reasoning, `y` to proceed, `n` to abort.
- Shell execution (`/exec` and the Execution agent) runs in a **sandbox**: an allowlist of read-mostly commands, no shell metacharacters, no path traversal, no network, a repo-root working directory, and a 10-second timeout. See `docs/SECURITY.md` for the full contract.

`/policy` prints the live rules; `/audit governance` shows what was gated.

---

## 12. Configuration

All config is plain files under `config/` — edit them, then `/reload` (no restart):

- **`config/settings.yaml`** — models, memory/recall, embeddings, the evolution loop, vault sync.
- **`config/UBONGO.md`** — the global identity loaded for every persona. Edit this to change Ubongo's behavior across all voices.
- **`config/personas/*.md`** — the three persona prompts.
- **`config/governance.yaml`** — the decision-matrix thresholds and the destructive-keyword list.
- **`config/routing.yaml`**, **`config/workflows.yaml`** — how intents map to workflows and which agents run.

To change the model a persona uses, edit `models.*` in `settings.yaml` and `/reload`.

---

## 13. Where your data lives

| Path | What |
|---|---|
| `data/ubongo.db` | The SQLite database (all memory, runs, decisions). Back this up. |
| `vault/daily/` | Daily conversation notes (Markdown). |
| `vault/system/audit.md` | The unified audit log. |
| `.env` | Your OpenRouter API key. |

To start fresh, stop Ubongo and delete `data/ubongo.db` (and optionally the `vault/` contents).

---

## 14. Keeping it running on the Pi

Ubongo v0.1 is an interactive CLI, not a background service (a persistent always-on channel is v0.2, Telegram). To keep a session alive across SSH disconnects, run it inside **tmux**:

```bash
sudo apt install -y tmux
tmux new -s ubongo
./start-ubongo.sh
#  detach with: Ctrl-b then d   |   reattach with: tmux attach -t ubongo
```

The background evolution loop and vault watcher only run **while the REPL is open** (and only if you've enabled and resumed them).

---

## 15. Updating

Replace the folder with a newer bundle, keeping your `data/`, `vault/`, and `.env`:

```bash
# from inside the old folder, save your state
cp -r data vault .env /tmp/ubongo-state

# unzip the new bundle, then restore
unzip ubongo-v0.1-NEW.zip && cd ubongo-v0.1
cp -r /tmp/ubongo-state/data /tmp/ubongo-state/vault /tmp/ubongo-state/.env .
./install.sh
```

---

## 16. Troubleshooting

- **`OPENROUTER_API_KEY not set`** — edit `.env` and add your key, or re-run `./install.sh`.
- **`Sorry, I couldn't reach the model`** — check your internet and that the key is valid / has credit on OpenRouter.
- **`sqlite-vec` won't load / "recency-only"** — harmless; semantic recall is disabled but everything else works. You can ignore it.
- **Python too old** — `sudo apt install -y python3.12 python3.12-venv`, then re-run `./install.sh`.
- **It's slow on the first message** — the model call dominates; the Pi's CPU barely matters. Subsequent turns are similar.
- **I want to see what it did** — `/trace` (last turn) or `/decisions`.
- **Costs** — every message is one or more OpenRouter calls. The autonomous evolution loop is off by default; leave it off (or paused) if you want to control spend.

To run the test suite (optional sanity check):

```bash
source .venv/bin/activate
python -m pip install pytest
python -m pytest -q
```

---

## 17. Uninstall

Ubongo is fully contained in its folder. To remove it, just delete the folder. Nothing is installed system-wide (the virtualenv lives in `.venv` inside the folder).

---

Enjoy your local mind. For the deeper design, see `README.md`, `docs/system-architecture.md`, and `docs/adr/`.
