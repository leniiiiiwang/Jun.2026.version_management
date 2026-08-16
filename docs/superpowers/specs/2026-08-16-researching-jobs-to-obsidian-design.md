# Researching Jobs to Obsidian Skill Design

## Goal

Create an installable Codex skill that turns low-frequency Xiaohongshu job research into one auditable Obsidian Markdown document. The skill must support reusable company, role, recruiting type, cohort, city, and vault parameters without embedding data from the Ant Group research run.

The deliverables are:

1. A maintainable source directory in `/Users/lynnwang/Documents/Skills/researching-jobs-to-obsidian/`.
2. A portable ZIP package in `/Users/lynnwang/Documents/Skills/dist/`.
3. An installed copy in `/Users/lynnwang/.codex/skills/researching-jobs-to-obsidian/` so future Codex conversations can discover it.

## Non-goals

- Do not vendor or fork the `stride28-search-mcp` source code.
- Do not bypass login, CAPTCHA, platform restrictions, or risk cooldowns.
- Do not promise exhaustive coverage of Xiaohongshu; describe results as exhaustive only within the confirmed search budget.
- Do not bundle cookies, browser profiles, source-post data, raw research results, OCR output, or user vault content.
- Do not overwrite an existing Obsidian file or restore content the user previously removed.
- Do not infer a company-wide salary, education threshold, or work culture from weak or adjacent-role evidence.

## Baseline failures the skill must correct

Two independent agents were asked to plan the workflow without the new skill. Both understood generic low-frequency research, but the baseline exposed repeatable gaps:

- No concrete default search/detail batch sizes or wait intervals.
- No implementation-level switch from one visible login to headless search/detail calls.
- No guaranteed reuse of one MCP/browser session per batch.
- No knowledge of the `stride28-search-mcp 0.2.1` dependency conflict with `mcp 2.x`.
- One agent proposed retrying a failed detail page, which increases risk and contradicts the desired default.
- User approvals were ad hoc rather than organized around search budget, detail count, retention, document name, and same-name behavior.
- Image-based interview and salary evidence was either omitted or described without a reusable local OCR implementation.
- Evidence grading was useful but not consistently connected to what may appear as a final conclusion.

The skill will encode these gaps as explicit defaults, stop conditions, scripts, and validation rules.

## Selected architecture

Use an independent adaptation layer over the installed `stride28-search-mcp`. This avoids copying upstream browser automation while preserving a stable workflow for Codex.

```text
researching-jobs-to-obsidian/
├── SKILL.md
├── agents/
│   └── openai.yaml
├── scripts/
│   ├── xhs_mcp_client.py
│   ├── download_note_images.py
│   ├── vision_ocr.m
│   └── validate_obsidian.py
├── references/
│   ├── setup.md
│   └── evidence-and-risk.md
├── assets/
│   └── job-research-template.md
├── tests/
│   ├── test_xhs_mcp_client.py
│   └── test_validate_obsidian.py
└── license.txt
```

No runtime-generated file may be written inside the installed skill directory. Research manifests, details, images, OCR text, findings, and drafts must use a task-specific temporary or user-approved working directory.

## Core interaction model

Replace the upstream five-stop sequence with three explicit checkpoints while preserving user control.

### Checkpoint 1: scope and budget

Before network access, confirm or infer from the user's explicit request:

- platform;
- company and aliases;
- role and aliases;
- campus/intern/social recruitment type and cohort;
- city scope;
- search keywords;
- results per keyword;
- batch sizes and wait intervals;
- target Obsidian repository and directory.

If the user already supplied a field, do not ask for it again. Present one consolidated configuration and wait for confirmation before login or search.

### Checkpoint 2: detail sample

After all summary searches, report:

- raw and unique result counts;
- cross-query duplicate count;
- coverage by topic;
- high-engagement count as a relevance signal, not a truth signal;
- obvious marketing, adjacent-role, or low-information counts;
- recommended detail sample and its topic allocation.

Wait for confirmation of the detail count. Do not fetch detail pages before this checkpoint.

### Checkpoint 3: retention and destination

After detail fetch, image/OCR processing, deduplication, and evidence grading, report:

- successes, ordinary timeouts, and risk failures;
- A/B/C evidence counts;
- recommended core and context-only sources;
- unresolved data gaps;
- proposed document name;
- whether a same-name file exists.

Ask for one combined decision covering retention, file name, and append-versus-new behavior when relevant. A unique confirmed name may be written without a fourth stop. Existing files must be read immediately before any merge.

## Default risk budget

Defaults come from the successful Ant Group run and may be lowered by the user.

### Summary search

- Six complementary keywords.
- Ten results per keyword; never exceed the MCP's 20-result limit.
- Three batches of two queries.
- At least 12 seconds between queries in a batch.
- At least 3 minutes between batches.
- Search each keyword once; do not automatically re-run sparse queries.

### Detail retrieval

- Recommend no more than 18 initial details unless the user expands the budget.
- Three batches of at most six notes.
- At most 10 comments per note by default.
- At least 20 seconds between note calls.
- At least 5 minutes between batches.
- One MCP process and one browser session per batch.

### Images

- Download only URLs returned by successful, selected detail results.
- Restrict downloads to posts whose core evidence is image-based.
- Wait at least 2 seconds between image downloads.
- OCR locally; do not revisit a post merely to extract image text.

### Hard stops

Immediately stop the active run on:

- `captcha_detected`;
- `search_blocked`;
- `risk_cooldown_active`.

Record `search_timeout` and continue the confirmed batch without retrying that item. Any later retry requires a separate user decision after the run or cooldown; it is not automatic.

## Browser and login behavior

Use a named profile so the login persists between processes.

1. Run doctor/preflight before network access.
2. Open a visible browser only for `login_xiaohongshu` or a user-approved manual CAPTCHA/login repair.
3. After login, set `STRIDE28_XHS_HEADLESS=true` for summary and detail tools.
4. Keep a single MCP stdio session for every confirmed batch.
5. If headless mode returns a risk error, stop. Do not silently fall back to a visible browser or repeat the query.

The setup reference will document that upstream currently defaults Xiaohongshu to headed mode to reduce detection risk. The skill's headless default is a user-experience choice with a stated tradeoff, not a claim that headless is safer.

## Components

### `scripts/xhs_mcp_client.py`

Provide a deterministic stdio client with subcommands:

- `list-tools`;
- `login`;
- `search-batch`;
- `detail-batch`.

Inputs use JSON manifests. Outputs use one JSON file per query or note plus a run summary. The client must:

- set profile and headless environment variables explicitly;
- lazily import MCP so pure helper functions can be unit-tested without the dependency;
- keep one session per batch;
- enforce configured waits;
- parse MCP envelopes;
- stop on risk codes;
- avoid logging cookies or browser-profile data;
- print compact progress messages suitable for polling.

### `scripts/download_note_images.py`

Read successful detail JSON files and an allowlist of selected note IDs. Download returned image URLs into a separate run directory, enforce a delay, and write a manifest with success or failure per image. Never scrape additional URLs from a live page.

### `scripts/vision_ocr.m`

Use macOS Vision for Chinese and English OCR. Compile into the task's temporary directory with a temporary Clang module cache. Document that Vision service calls may fail inside a restricted sandbox and can require an approved system-context execution. If macOS Vision is unavailable, retain the image as pending manual/visual review rather than fabricating text.

### `scripts/validate_obsidian.py`

Validate a generated Markdown file for:

- nonempty YAML frontmatter;
- required research sections;
- source references and unique source URLs;
- unresolved template placeholders;
- merge-conflict markers;
- absolute local file paths accidentally embedded as source links;
- missing evidence-limit language in salary and education sections.

Return a nonzero exit code with specific failures.

### `references/setup.md`

Cover installation and diagnosis only when needed:

- install and run `stride28-search-mcp`;
- use a native Python architecture on Apple Silicon;
- constrain the upstream dependency to `mcp[cli]<2` when version 0.2.1 resolves MCP 2.x and fails on `mcp.server.fastmcp`;
- install Playwright Chromium and run doctor;
- use a named browser profile;
- request user approval for package installation, browser login, network access, or writes outside the workspace.

### `references/evidence-and-risk.md`

Define:

- search-matrix construction;
- topical coverage requirements;
- advertising and low-information filters;
- ID and title-similarity deduplication;
- A/B/C evidence grades;
- post text versus image OCR versus agent inference;
- rules for salary, education, work hours, work culture, and adjacent roles;
- source index fields;
- document completeness and stopping criteria.

### `assets/job-research-template.md`

Provide a generic Obsidian skeleton with frontmatter, one-page conclusion, role portrait, entry threshold, interview flow and question bank, compensation evidence, work experience, preparation advice, evidence limits, search statistics, and source index.

The template must use placeholders that the validator detects if left unresolved.

## Evidence model

### A: core evidence

First-person, direct target-role or close-role experience with substantive text, image evidence, timeline, or result proof. Use for concrete statements about what occurred, while preserving sample limitations.

### B: directional evidence

Third-party hiring observations, generic industry experience, adjacent recruiting types, or incomplete but substantive reports. Use for trends and hypotheses, never as official requirements.

### C: context only

Anonymous salary compilations, marketing accounts, other roles, unclear identities, title-only results, or highly subjective fragments. Use only to show possible ranges, disagreements, or data gaps.

### Claim rules

- A single post proves only that the described event or opinion occurred.
- Multi-post agreement may be called a pattern only when sources are independent and comparable in cohort, role, city, and business line.
- Salary must preserve monthly pay, pay months, bonus, equity, subsidies, city, cohort, role, and source wording. Do not fabricate a target-role range from adjacent roles.
- Education observations are competition signals, not official hard thresholds unless an official JD says so.
- Work experiences remain attributed to the author and team context.
- Empty comment bodies are not evidence even when comment counts are available.
- OCR uncertainty must be marked and visually checked when it affects numbers or interview wording.

## Obsidian write behavior

1. Inspect the target vault and neighboring documents before drafting.
2. Create the draft in a writable task directory.
3. Preserve manual edits and user deletions.
4. For an existing name, read the latest file and follow the user's append/new decision.
5. Use a unique new file by default when the research is distinct from interview preparation.
6. Link related local notes with Obsidian wikilinks when the target exists.
7. Validate the draft before copying.
8. Copy only after exact destination resolution and approval when the sandbox requires it.
9. Verify the target's checksum, line count, required sections, source count, and conflict-marker absence.
10. Remove only temporary artifacts created by the run.

## Testing strategy

Follow skill TDD and script TDD.

### RED evidence already collected

The two no-skill baseline scenarios showed the gaps listed above. Preserve their conclusions in the implementation plan; do not include the session narrative in the final skill.

### Script tests

Write failing tests before each script behavior:

- manifest parsing and batch partitioning;
- one-session batch iteration;
- risk-code hard stop;
- timeout record-without-retry;
- headless/profile environment construction;
- image allowlist and URL extraction;
- Obsidian good-document pass;
- unresolved placeholder, missing section, missing source, and conflict-marker failures.

Network, login, and CAPTCHA behavior must not be exercised in unit tests. Use fake MCP result objects or pure envelope fixtures only where external boundaries make real access inappropriate.

### Skill forward tests

After implementation, run independent agents with the new skill on at least two simulated prompts:

1. A company/role search with no-popups and low-frequency constraints.
2. An image-heavy salary/interview request with an existing same-name Obsidian document.

The agents must propose the three checkpoints, quantitative defaults, one-session headless batches, no automatic timeout retry, OCR/evidence separation, and safe file handling.

## Packaging and installation

- Generate `agents/openai.yaml` using the system skill-creator script.
- Validate with `quick_validate.py`.
- Run all unit tests and a syntax/compile smoke test for reusable scripts.
- Create a ZIP that contains exactly the skill directory and excludes caches, compiled binaries, run data, cookies, profiles, raw posts, and `.DS_Store`.
- Copy the validated directory to `/Users/lynnwang/.codex/skills/researching-jobs-to-obsidian/`.
- Re-run validation on the installed copy and compare source and installed file inventories or checksums.
- Do not push the repository unless the user separately asks for GitHub synchronization.

## Acceptance criteria

The work is complete only when:

- the source directory follows Codex skill structure;
- frontmatter and `agents/openai.yaml` pass validation;
- script tests pass with no network access;
- the macOS OCR source compiles and a fixture smoke test succeeds where system Vision access is available;
- forward tests demonstrate the missing baseline behaviors are corrected;
- the ZIP is readable and contains no runtime/private data;
- the installed copy matches the validated source;
- the user receives clickable paths to the source skill, ZIP, installed skill, and design/plan documents.
