# How we work in this repo

**The one rule: nobody pushes straight to `main`.** `main` is our stable,
demo-ready branch. All work happens on branches and comes together on `dev`, and
every change is reviewed by the repo owner (Shaivi) before it's merged.

## The two shared branches
- **`dev`** — where everyone's work integrates. You branch off it and open PRs into it.
- **`main`** — stable and demo-ready. Only reviewed code from `dev` reaches it, when Shaivi promotes it.

## The flow, step by step
1. **Start from the latest `dev`.**
   ```bash
   git checkout dev
   git pull origin dev
   ```
2. **Make a branch for your task.** Name it `your-name/what-it-does`.
   ```bash
   git checkout -b udbhav/lead-scoring
   ```
3. **Do your work and commit** in small, clear steps.
   ```bash
   git add .
   git commit -m "Add fit-score calculation"
   ```
4. **Push your branch** (never `main` or `dev` directly).
   ```bash
   git push -u origin udbhav/lead-scoring
   ```
5. **Open a Pull Request into `dev`** on GitHub (it offers a "Compare & pull request"
   button after you push — make sure the base is `dev`). Fill in the template.
6. **The checks run automatically.** Linting and tests run on your PR. Fix anything red.
7. **Shaivi reviews it.** She approves, or asks for changes — push more commits to the
   same branch and the PR updates.
8. **Once approved, it's merged into `dev`.** Delete the branch, start the next task from step 1.

## Rules we agreed on
- **No direct pushes to `main`** — it's protected and will reject them. `dev` is also for PRs, not direct pushes.
- **One task per branch / PR.** Small PRs get reviewed fast; giant ones sit for ages.
- **Every PR needs Shaivi's approval.** No self-merging around review.
- **Never commit secrets** — API keys, tokens, `.env`. Use `.env.example` for the shape.
- **Keep the checks green.** If the linter or tests fail on your PR, fix them before asking for review.
- **Match the house style** — see [docs/Rules.md](docs/Rules.md). Formatting is decided by `ruff`, not by hand.

## If you're new to git
That's fine. GitHub Desktop does all of the above with buttons, or ask in the team
thread and someone will walk you through your first PR.
