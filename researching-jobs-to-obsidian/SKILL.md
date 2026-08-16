---
name: researching-jobs-to-obsidian
description: Use when researching jobs from Xiaohongshu or Zhihu into Obsidian, especially campus recruiting, interview, salary, entry-threshold, or work-experience requests that need low-frequency, login-sensitive collection and auditable evidence.
---

# Researching Jobs to Obsidian

Produce a bounded, source-indexed job brief—not a platform-wide exhaustive answer. Use only public content the user authorizes; do not collect account secrets or retain platform data beyond the approved task artifacts.

## Applicability and prerequisites

Use for a company, role, recruiting type, and city scope that can be investigated through Xiaohongshu or Zhihu. Read [setup](references/setup.md) only for installation, login, architecture, or runtime diagnosis. Read [evidence and risk](references/evidence-and-risk.md) for the search matrix, batching, filters, evidence grades, and claim wording.

### Checkpoint 1 — scope, budget, and destination

Act before network/login: confirm target company/role, approved city and recruiting scope, source platforms, the query/detail budget, the named profile, and destination vault/note folder. Confirm approval for installation, browser/network use, and any write outside the workspace.

Run the six-query search matrix in up to 3 batches × 2 queries, each query returning 10 candidates. Use one MCP/browser session per batch. Scripts enforce only within-batch minimums; keep at least 12 seconds between search queries and at least 180 seconds/3 min between search batches. Search is explicitly headless; no visible fallback. On `search_timeout`, record it, do not retry, and continue; sparse searches do not automatically retry. Stop on the documented risk codes.

### Checkpoint 2 — detail sample

After search batches and before details, show the normalized, deduplicated candidate sample and request approval of the detail sample. Collect ≤18 details in 3 batches × ≤6, default `max_comments 10`; wait at least 20 seconds within a detail batch and 300 seconds/5 min between batches. Detail is explicitly headless; use one MCP/browser session per batch. Download selected images no faster than one per 2 seconds, OCR only selected material, then grade evidence.

### Checkpoint 3 — retention, filename, and merge

Act before vault write: get A/B/C retention approval plus the filename. Draft in a task temp location. Inspect target neighbors and the current note; preserve edits and deletions. Create a unique new file by default. For a same-name file, ask the user whether to append or create a new file; re-read it immediately before an approved merge.

Render [the template](assets/job-research-template.md), validate it, then copy only the approved result. After copy, verify checksum/line count, required sections, source count, and absence of conflict markers. Delete only temp artifacts created for this task. Report coverage within the approved budget, exclusions, risk stops, and remaining uncertainty.

## Completion checklist

- Three approvals recorded: scope/budget/destination, detail sample, retention/filename.
- Every material claim is grade-labeled and source-indexed; limits stay visible.
- Final Markdown passes `scripts/validate_obsidian.py`; no unresolved variables or merge conflicts remain.
