---
name: git-github-sync
description: Use when the user asks to push, pull, sync, publish, back up, version, or connect a local Git repository with GitHub, especially from /Users/lynnwang/Documents/Skills.
---

# Git GitHub Sync

## Overview

Use this skill to manage routine Git version control and GitHub synchronization for the user. Keep the workflow practical: inspect first, preserve remote history, avoid destructive commands, and never ask the user to reveal tokens.

## Default Workflow

1. Inspect local state:
   - `git status --short --branch`
   - `git remote -v`
   - `git log --oneline --decorate --graph --all --max-count=8`
2. If the repo has no remote, ask for the GitHub URL or use the known URL only when already configured locally.
3. If GitHub network access fails with DNS, timeout, or port 443 errors, configure repository-local proxy:
   - `git config http.proxy http://127.0.0.1:7897`
   - `git config https.proxy http://127.0.0.1:7897`
   Do not set global proxy unless the user explicitly asks.
4. If there are local changes, stage and commit them only after understanding the change scope.
   - For simple user-requested syncs, `git add .` is acceptable when the user clearly wants all current files included.
   - Generate a concise commit message if the user did not provide one.
5. Before pushing, fetch remote state:
   - `git fetch origin`
6. If remote has commits not present locally, integrate first:
   - Prefer `git pull --rebase` for ordinary linear history.
   - If histories are unrelated because both sides were initialized separately, use `git merge origin/main --allow-unrelated-histories`, resolve conflicts, commit, then push.
7. Push:
   - First push: `git push -u origin main`
   - Later pushes: `git push`
8. Verify:
   - `git status --short --branch`
   - Confirm the branch tracks `origin/main` and the worktree is clean.

## Authentication

- Never ask the user to paste a GitHub token into chat.
- If Git prompts for credentials, tell the user:
  - Username: GitHub username
  - Password: paste the GitHub personal access token
- A `403` after network succeeds usually means cached credentials or token permissions are wrong.
- For macOS cached credential issues, guide the user to erase only the GitHub credential:
  - `git credential-osxkeychain erase`
  - input:
    ```text
    protocol=https
    host=github.com

    ```

## Safety Rules

- Do not run `git reset --hard`, `git push --force`, or destructive cleanup unless the user explicitly asks and the risk is explained.
- Do not overwrite remote history to solve `fetch first`; fetch and integrate remote history instead.
- Do not commit secrets, tokens, `.env`, or generated dependency directories.
- Keep proxy settings repository-local by default.

## Current Known Repository

For `/Users/lynnwang/Documents/Skills`:

- Remote: `https://github.com/leniiiiiwang/Jun.2026.version_management.git`
- Branch: `main`
- Proxy port used successfully: `7897`
