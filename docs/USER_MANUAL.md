<p align="center">
  <img src="Ubongo.png" alt="Ubongo logo: an armored brain with a terminal-prompt face and a governance shield" width="360">
</p>

# Ubongo User Manual (v0.1.2)

Ubongo is a personal, mood-aware AI mind that runs entirely on your own machine as a command-line app. *Ubongo* is Swahili for *brain*. It talks to you in one of three personas, remembers your conversations in a local database and an Obsidian-compatible Markdown vault, can run safe shell commands, gates risky actions behind your approval, and quietly tries to improve its own prompts over time — promoting changes only when you approve them.

This manual covers installing and running Ubongo on a Raspberry Pi 5 (or any Debian/Ubuntu machine), and everything you can do once it's running.

---

## 1. What you need

- **macOS or Linux** (a Raspberry Pi 5 or any Debian/Ubuntu box; macOS on Apple Silicon or Intel). 16 GB RAM is plenty; Ubongo is lightweight.
- **Python 3.11 or newer** (Ubuntu 24.04 ships 3.12; on macOS: `brew install python` or python.org).
- An **OpenRouter API key** — free to create at <https://openrouter.ai/keys>. Ubongo uses OpenRouter for all model calls (chat + embeddings). You pay OpenRouter per use; Ubongo itself is free.
- An **internet connection** (for the model calls).

Ubongo is a single-user, single-machine, local app. No Docker, no database server, no cloud account beyond OpenRouter.

---

## 2. Install

You are given two files: **`install-ubongo.sh`** and **`ubongo-v0.1.2.zip`**.

### Easiest: the one-step installer (macOS + Linux)

Put both files in the same folder, then run:

```bash
./install-ubongo.sh
```

It opens the zip, places the files where you choose (it asks; default `~/ubongo`), creates a private virtualenv, installs all dependencies, makes the `data/` and `vault/` folders, and asks for your OpenRouter API key. Add `--web` to also install the optional web UI, or `--dest DIR` to skip the location prompt. The first install takes a few minutes (it downloads the Python dependencies).

If Python is missing first: on Debian/Ubuntu `sudo apt update && sudo apt install -y python3 python3-venv python3-pip`; on macOS `brew install python` (or python.org).

### Manual: unzip yourself

If you prefer, unzip the bundle and run the in-place installer:

```bash
unzip ubongo-v0.1.2.zip
cd ubongo-v0.1.2
./install.sh        # add --web for the web UI
```

Either way, the installer creates a private virtualenv (`.venv`), installs Ubongo and its dependencies, makes the `data/` and `vault/` folders, and saves your OpenRouter API key into `.env`. It finishes by verifying a clean cold start.

> **Want the web UI too?** Run `./install.sh --web` instead. It additionally
> installs the optional Streamlit chat page so you can talk to Ubongo from a
> tablet on your home network. See §4 below. (You can re-run `./install.sh --web`
> later to add it.)

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

Start with the profiler armed (for debugging a slow or leaky session):

```bash
./start-ubongo.sh --profile mem        # cpu | mem | all | off
UBONGO_PROFILE=cpu ./start-ubongo.sh   # same via .env / environment; the flag wins
./start-ubongo.sh send "hello" --profile   # one-shot turn under cProfile
```

Profiling is opt-in and free when off; disarm any time with `/profile cpu off` /
`/profile mem off`. See the `/profile` rows in the command reference (section 7).

### Web UI (optional — talk to Ubongo from your tablet)

If you installed with `./install.sh --web`, you can run a local chat page that
other devices on your home Wi-Fi (a tablet, a laptop) can open in a browser:

```bash
./start-ubongo-web.sh
```

It prints the address to use, e.g. `http://192.168.1.20:8501`. Open that on your
tablet. The page has a chat box, a persona selector, and an auto-route toggle;
when a turn needs approval you get **Approve / Deny** buttons instead of the
`y/n` prompt. Change the port with `UBONGO_WEB_PORT=9000 ./start-ubongo-web.sh`.

To keep the web page running in the background (instead of holding a terminal),
use the service controller: `./ubongo-ctl.sh start|stop|restart|status` (logs to
`data/ubongo-web.log`). On a Pi/Ubuntu box that should survive reboots, use the
systemd unit in `deploy/ubongo-web.service` instead (install steps in its
comments) — use one or the other, not both.

### MCP server (optional — let other agents call Ubongo)

If you installed with `./install.sh --mcp`, other AI tools can talk to Ubongo
over MCP. A local tool that launches its own server (Claude Code on this
machine) should run `python -m ubongo mcp` (stdio). For tools on other
machines, start the network form — `./start-ubongo-mcp.sh`, or as a background
service with `./ubongo-ctl.sh start mcp` — and point them at
`http://<this-machine-ip>:8765/mcp`. Callers get two tools (`ubongo_send`: a
full normal turn, exactly as if you had typed it; `ubongo_recall`: read-only
memory search) and two read-only resources (today's note, the audit log).
Anything the governance gate would stop still gets stopped — and a gated turn
**cannot be approved over MCP**; approving stays here (REPL `y/n/why`) or on
the web page (Approve/Deny). Same home-network-only rule as the web page.

### Calling other services (the Connector)

Ubongo can also use *other* MCP servers (e.g. your Compendium project).
Declare them under `mcp.servers` in `config/settings.yaml` (examples are in
the file), then ask explicitly: `/mode connector_session` followed by your
question. Ubongo's Connector agent picks the right tool, calls it, and weaves
the result into the answer; if the server is down you still get a normal
answer. Each server declares a `risk` level — a high-risk server makes the
turn ask for your approval, exactly like a destructive request would.

It is the same Ubongo — the web page runs every turn through the exact same
pipeline as the REPL (classify → plan → execute → govern → compose → remember),
sharing the same database and vault.

> **Security:** the web page has **no password and no HTTPS**, on purpose — it is
> meant for your trusted home network only. Anyone who can reach the address can
> talk to Ubongo (and it can run sandboxed shell commands). Do not forward the
> port through your router or expose it to the internet. See `docs/SECURITY.md`.

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
| `/agents` | List the eleven worker agents |
| `/skills` | List available skills |
| `/decisions [N]` | The last N governance decisions |
| `/trace [N]` | Full execution trace of the last N turns (agents, timings, tokens, repair, governance) |
| `/policy` | Print the live governance decision matrix |
| `/queue [N]` | The outbound message queue |
| `/exec <cmd>` | Run one command through the constrained sandbox (debug; e.g. `/exec echo hi`) |
| `/profile [agents\|models\|modes] [N]` | Performance summary or breakdowns (latency, tokens, failure rates) over the recorded runs |
| `/profile cpu on\|off\|status` | Arm/disarm CPU profiling: each turn writes a `.prof` under `data/profiles/` plus a top-25 summary |
| `/profile mem [on\|off\|status]` | Arm memory profiling (baseline); bare `/profile mem` shows allocation growth since the baseline |

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

### Self-authored skills (authoring)
| Command | What it does |
|---|---|
| `/author <description>` | Draft a brand-new skill from a description; it is validated, quality-scored, and **quarantined** (not usable yet) |
| `/skill-candidates [approve <id> \| reject <id> \| rollback <name>]` | Review drafts; **approve** makes a skill live, **rollback** restores the prior version (or removes it) |
| `/authoring <status\|pause\|resume\|off>` | Control the autonomous authoring daemon (starts **paused**) |

Where self-improvement *tunes* Ubongo's existing prompts, authoring lets it *write new skills*. Nothing a draft proposes is usable until you approve it — see section 10.5.

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

## 10.5. Self-authored skills, behind your approval

Beyond tuning what it has, Ubongo can **write brand-new skills** — and you decide which ones become real.

1. **Draft.** `/author "summarize a git diff into release notes"` asks Ubongo to design a skill. It is validated, given a rough quality score, and put in **quarantine** — written to disk but invisible to the running assistant. A skill that runs a shell command is automatically marked medium-risk (it can't downgrade itself), and an unsafe command is rejected on the spot.
2. **Review.** `/skill-candidates` lists drafts. Each is just a proposal until you act.
3. **Approve → live.** `/skill-candidates approve <id>` registers the skill so it shows up in `/skills` and you can pin it with `/skill <name>`. If it replaces an older version, the old one is backed up first.
4. **Undo anytime.** `/skill-candidates rollback <name>` restores the previous version, or removes the skill entirely if it was new. `/skill-candidates reject <id>` discards a draft without registering it.

There is also an **autonomous authoring daemon**: it watches for kinds of requests you keep making that no skill handles, and quietly drafts a candidate for them. It is **off (paused) by default**, never spends on its own, and — importantly — **never approves anything**. It only ever puts drafts in the review queue. Control it with `/authoring resume | pause | off | status`, and see what it has been doing with `/audit authoring`.

The rule is the same as self-improvement: Ubongo can propose, but nothing it authors becomes a usable capability without your explicit approval. The full safety design is in `docs/SECURITY.md` ("Self-authored skills").

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
