# Ubongo

A personal, mood-aware AI assistant that lives in Telegram. One user. Adapts persona and model to the kind of conversation you're having. Capabilities are organized as composable skills with progressive disclosure. Outbound messages flow through a notification queue with a policy engine that respects quiet hours and ad-hoc holds.

The name is Swahili for *brain* or *mind*.

## Status

v0.1 in development. See `UBONGO_BUILD.md` for the full build specification and phased plan.

## What Ubongo Is

A single-user Telegram bot. You talk to it; it talks back. Internally it picks one of three personas (Architect, Operator, Casual) based on the tone and intent of your message, runs the appropriate model via OpenRouter, and stores the conversation in SQLite with an Obsidian-compatible Markdown projection.

## What Ubongo Is Not

Not a multi-agent platform. Not a control plane for AI employees. Not a SaaS product. Not multi-channel. Not autonomous. Not self-improving.

If you need orchestration of many agents toward business goals, look at Paperclip. If you need a coding agent toolkit, look at Pi or OpenClaw. Ubongo is narrower than all of them on purpose.

## Tech Stack

- Python 3.11+
- python-telegram-bot v21+
- LiteLLM (model routing)
- OpenRouter (model provider)
- SQLite (storage)
- YAML + Markdown (configuration)
- uv (package management)

No FastAPI, Redis, Docker, or distributed anything. Single process, single user, single channel.

## Prerequisites

- Python 3.11 or newer
- [uv](https://docs.astral.sh/uv/) installed
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- An [OpenRouter](https://openrouter.ai/) API key
- Your Telegram numeric user ID (message [@userinfobot](https://t.me/userinfobot) on Telegram to get it)

## Setup

```bash
git clone <repo-url> ubongo
cd ubongo
uv sync
```

Copy the environment template and fill in your secrets:

```bash
cp .env.example .env
```

Edit `.env`:

```
OPENROUTER_API_KEY=sk-or-v1-...
TELEGRAM_BOT_TOKEN=123456:ABC-...
```

Edit `config/settings.yaml` and set `telegram.allowed_user_ids` to your Telegram numeric ID:

```yaml
telegram:
  allowed_user_ids: [YOUR_NUMERIC_ID]
```

Optionally edit `config/UBONGO.md` to customize the global identity context. This file is loaded for every conversation regardless of persona, so it's where you put facts about yourself and communication preferences.

Optionally edit `config/personas/*.md` to tune voices.

## Run

```bash
uv run python -m ubongo
```

The bot starts polling Telegram. Send it a message from your allowed account; it should respond.

If you get no response, check:

1. The bot is actually running (`Ubongo starting` should appear in the logs).
2. Your numeric Telegram ID matches `allowed_user_ids` exactly. Wrong ID means silent drop.
3. `OPENROUTER_API_KEY` is set in `.env` and the file is loaded.

## Usage

Send any text message. Ubongo will classify the intent and tone, pick a persona, and respond.

Slash commands:

- `/architect`, `/operator`, `/casual` — force a persona for the current session
- `/auto` — return to automatic persona selection
- `/summary` — summarize the current conversation
- `/skills` — list available skills
- `/reload` — reload personas, skills, and global context without restarting
- `/hold 3h` — hold non-urgent notifications for 3 hours
- `/hold until 18:00` — hold until 6pm today
- `/hold` — hold until you say `/resume`
- `/resume` — release any active hold and deliver queued items
- `/queue` — show what's currently held
- `/quiet` — show or change quiet hours

Quiet hours and holds only affect *proactive* messages (which arrive once the scheduler ships in v0.3). Synchronous responses to your messages always come through, even at 3am.

## Configuration

| File | Purpose |
|---|---|
| `.env` | Secrets only. Never committed. |
| `config/settings.yaml` | Models, memory tuning, delivery policy, logging. |
| `config/UBONGO.md` | Global identity and communication preferences. Loaded for every persona. |
| `config/personas/*.md` | Voice-specific overlays for each persona. |
| `config/skills/<name>/SKILL.md` | Skill definitions. Frontmatter + body. |
| `config/routing.yaml` | Tone/intent to persona/skill mapping rules. |
| `config/urgency.yaml` | Urgency assignment rules (used in v0.3). |

Edit any of these and run `/reload` in Telegram to apply without restart (except `settings.yaml`, which requires a restart).

## Vault

Daily conversation logs are written to `vault/daily/YYYY-MM-DD.md` in Obsidian-compatible Markdown. Open the `vault/` directory as an Obsidian vault to browse them. The vault is gitignored by default; if you want versioned history of your conversation logs, run `git init` inside `vault/` separately.

In v0.1 the vault is write-only. The system reads from SQLite, never from the Markdown files. Bidirectional sync is on the v0.2 list.

## Project Structure

```
ubongo/
  config/             # all user-editable configuration
    UBONGO.md         # global identity context
    settings.yaml
    routing.yaml
    personas/
    skills/
  src/ubongo/
    bot.py            # Telegram handlers
    classifier.py     # tone/intent classification
    router.py         # persona and skill selection
    context.py        # hierarchical prompt assembly
    events.py         # named event dispatcher
    llm.py            # LiteLLM wrapper
    skills.py         # skill registry with progressive disclosure
    personas.py
    delivery/         # notification queue and policy engine
    memory/           # SQLite store, compaction, vault projection
  vault/              # generated daily notes (gitignored)
  tests/
```

See `UBONGO_BUILD.md` for the full architecture and the phased build plan.

## Roadmap

**v0.1 (current):** Telegram, three personas, tone routing, SQLite memory, Markdown vault, skills infrastructure with one demo skill, notification queue with quiet hours and holds.

**v0.2:** Pick at most two from: CLI front-end, Google Calendar integration, embedding-based recall, topic-aware compaction, structured fact extraction, bidirectional vault sync, a fourth persona.

**v0.3:** Scheduler for proactive jobs (cron-style). Additional integrations as skills (email, news, Reddit). Each integration is a CLI script the agent invokes via a single `bash` tool, not a first-class tool definition.

The roadmap is loose on purpose. Build v0.1, use it for two weeks, prioritize v0.2 from observed friction rather than from architectural ambition.

## License

TBD. Personal project; not currently published.
