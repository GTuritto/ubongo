---
name: summarize-conversation
description: Recap the recent conversation in 3-5 sentences. Use when the user wants to wrap up, get a recap, or ask "what did we just talk about".
risk: low
reversibility: reversible
default_persona: operator
prompts:
  summarize: prompts/summarize.md
---

The summarize-conversation skill condenses the last N turns of the active conversation into a short, operator-voice recap. It is read-only over conversation memory; it does not write a new turn into the conversation log or vault. Use it as a meta-command, not as a regular turn.
