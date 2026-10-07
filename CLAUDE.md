# CLAUDE.md — Agent Entry Point

**Claude Code reads this automatically.**

This repo is **Odds in Your Favour** — our AI lead-generation, qualification and
sales-automation build for The Industry Games (District 02, Spazor Labs).
Read this fully before doing anything.

The backbone file is **`docs/Tracker.md`** — the single source of truth for who
owns what, what to do next, and where to wait on someone else.

## Do this every session, in order
1. **Open `docs/Tracker.md`** and read it top to bottom.
2. **Know who you're working as.** If the user hasn't said, ask which teammate.
3. **Work only in that person's lane** (their role in the Tracker). Pick their next
   unblocked task.
4. **If asked to touch someone else's area, STOP and warn the user** — don't
   silently cross lanes.
5. **Update the Tracker when done** — flip the task status and add a line to the log.

## The rules that don't bend
- **Stack & conventions:** follow `docs/Rules.md`. Don't hand-argue formatting —
  the tools (`ruff`) decide.
- **No secrets, ever.** No API keys, tokens, or `.env` files in git. Use
  `.env.example` for the shape.
- **Git flow:** branch off **`main`** as `<your-name>/<short-task>`; never commit
  straight to `main`. When done, **open a pull request and stop** — don't merge
  your own PR. The repo owner is the only approver.
- **Keep `main` clean.** It is always demo-ready.

## How to operate
1. **Think before coding.** State assumptions; if something's unclear, ask instead of
   guessing. If there's a simpler way, say so.
2. **Simplicity first.** The least code that solves the problem. No features nobody
   asked for, no abstractions for single-use code.
3. **Surgical changes.** Touch only what the task needs. Match the existing style.
   Don't refactor or reformat code that isn't yours.
4. **Goal-driven.** Turn a task into something checkable ("add scoring" → "write a
   test for the score, then make it pass"), then loop until it passes.
5. **Write down what you find.** Results (good *and* bad), decisions, and findings go
   in the docs, not just the chat. If it isn't written down, it didn't happen.

> **`CLAUDE.md` and `AGENTS.md` are the same file for different assistants.** Keep them
> identical except the title line and the "who reads this" line. Edit one, mirror the other
> in the same change.
