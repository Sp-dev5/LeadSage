# How we work in this repo

**The one rule: nobody pushes straight to `main`.** Every change is proposed, reviewed by the repo owner, and only then merged. This keeps `main` always working and means no one can break things by accident.

## The flow, step by step

1. **Pull the latest `main`** so you start from what everyone else has.
   ```bash
   git checkout main
   git pull origin main
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
4. **Push your branch** (not `main`).
   ```bash
   git push -u origin udbhav/lead-scoring
   ```
5. **Open a Pull Request** on GitHub (it offers a "Compare & pull request" button after you push). Fill in the template that pops up.
6. **The repo owner reviews it.** They may approve it, or ask for changes in comments. If changes are asked for, push more commits to the same branch — the PR updates automatically.
7. **Once approved, it's merged into `main`.** Then delete the branch and start the next task from step 1.

## Rules we agreed on

- **No direct pushes to `main`.** The repo is set up to reject them. Always go through a branch and a PR.
- **One task per branch / PR.** Small PRs are reviewed fast; giant ones sit for ages.
- **Every PR needs the repo owner's approval before merging.** No self-merging around review.
- **Never commit secrets** — API keys, tokens, `.env` files. Use `.env.example` for the shape, keep real values out of git.
- **Keep `main` working.** If your branch is behind, pull `main` into it and fix conflicts before asking for review.

## If you're new to git

That's fine. The GitHub Desktop app does all of the above with buttons instead of commands, or ask in the team thread and someone will walk you through your first PR.
