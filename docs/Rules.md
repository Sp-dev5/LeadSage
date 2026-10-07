# Rules.md — how we write code here

> Goal: four people's code reads like one person wrote it, and `main` always works.
> When in doubt, favour **simple and readable over clever**.

## Coding standards
- **Python 3.12.** Follow PEP 8.
- **Auto-format and lint with `ruff`** (config in `ruff.toml` at the repo root). It also
  sorts imports. Don't argue formatting by hand — run the tool. CI runs `ruff check .`
  on every PR, so fix lint before asking for review.
- **Readability first.** Clear and slightly longer beats dense and clever.
- **Small functions, one job each.** If a function does three things, split it.
- **Type hints + a one-line docstring** on functions others will call.
- **No magic numbers or hardcoded paths.** Put tunables (score weights, thresholds,
  file paths, model names) in one config place, not scattered through the code.
- **No secrets in git.** API keys, tokens, `.env` stay out. Commit `.env.example` for shape.

## Naming
| Thing | Convention | Example |
|---|---|---|
| Files / modules | `snake_case` | `lead_scoring.py` |
| Functions / variables | `snake_case` | `score_lead()` |
| Classes | `PascalCase` | `LeadScore` |
| Constants | `UPPER_SNAKE` | `MIN_FIT_SCORE` |
| Git branches | `name/short-task` | `shaivi/agent-loop`, `udbhav/hubspot-sync` |
| Commits | imperative, short | `Add fit-score calculation` |

## Git flow (the short version — full steps in CONTRIBUTING.md)
- Branch off **`dev`**, never `main`. Name it `your-name/what-it-does`.
- One task per branch and PR. Small PRs get reviewed fast.
- Open the PR into **`dev`** and stop — don't self-merge. **Shaivi approves everything.**
- `main` is the stable, demo-ready branch. Only reviewed code from `dev` reaches it.

## Docs
- **Write decisions down** in `docs/` as you make them, not after.
- **Keep docs in sync with code.** A doc that lies is worse than no doc.
- **Update `docs/Tracker.md`** as tasks move.

## Testing (pragmatic, not exhaustive)
- Unit-test the **load-bearing logic** — the scoring maths, qualification rules —
  the parts with clear inputs and outputs.
- One **smoke test** that the end-to-end agent loop runs without crashing.
- Don't chase 100% coverage in a hackathon. Protect what would hurt most if it broke.
