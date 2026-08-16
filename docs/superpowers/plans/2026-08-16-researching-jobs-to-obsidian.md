# Researching Jobs to Obsidian Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build, validate, package, and install a reusable Codex skill that performs low-frequency Xiaohongshu or Zhihu job research and writes one evidence-graded, safely merged Obsidian Markdown document.

**Architecture:** Keep `stride28-search-mcp` as an external runtime and add a small, testable adaptation layer. Pure Python helpers own manifest parsing, batching, risk-stop behavior, image selection, and Markdown validation; one lazy MCP client owns each live browser session. The skill instructions enforce three user checkpoints, explicit evidence grades, local OCR, and non-overwriting Obsidian writes.

**Tech Stack:** Codex skill format, Python 3.11+ standard library, MCP Python client compatible with `mcp[cli]<2`, `unittest`, macOS Objective-C with Foundation/AppKit/Vision, Markdown/YAML, ZIP.

---

## Fixed contracts and defaults

These values are acceptance requirements, not implementation-time choices:

- Skill name: `researching-jobs-to-obsidian`.
- Source: `/Users/lynnwang/Documents/Skills/researching-jobs-to-obsidian/`.
- Package: `/Users/lynnwang/Documents/Skills/dist/researching-jobs-to-obsidian.zip`.
- Installed copy: `/Users/lynnwang/.codex/skills/researching-jobs-to-obsidian/`.
- Summary default: six keywords, ten results each, batches of two, 12-second in-batch delay, 180-second inter-batch delay, no automatic sparse-query retry.
- Detail default: at most 18 notes, batches of at most six, ten comments, 20-second in-batch delay, 300-second inter-batch delay, no automatic timeout retry.
- Images: selected successful details only, 2-second delay, local OCR.
- Hard-stop codes: `captcha_detected`, `search_blocked`, `risk_cooldown_active`.
- Ordinary `search_timeout`: write the result, continue the current batch, do not retry.
- Visible browser: login or explicitly approved manual repair only. Search and detail batches explicitly set Xiaohongshu headless mode.
- One MCP stdio process and one initialized client session per batch.
- Three checkpoints: scope/budget/destination; detail sample; evidence retention/file behavior.
- Evidence grades: A core, B directional, C context only.
- Existing Obsidian file: read its current version immediately before any approved merge; never silently overwrite or restore removed text.

## Task 1: Scaffold the skill and lock its public metadata

**Files:**

- Create: `researching-jobs-to-obsidian/SKILL.md`
- Create: `researching-jobs-to-obsidian/agents/openai.yaml`
- Create: `researching-jobs-to-obsidian/scripts/`
- Create: `researching-jobs-to-obsidian/references/`
- Create: `researching-jobs-to-obsidian/assets/`
- Create: `researching-jobs-to-obsidian/tests/`

- [ ] **Step 1: Confirm the target does not already exist**

Run:

```bash
test ! -e researching-jobs-to-obsidian
```

Expected: exit 0 and no output. If it exists, inspect it and stop instead of overwriting it.

- [ ] **Step 2: Generate the official scaffold**

Run:

```bash
python3 /Users/lynnwang/.codex/skills/.system/skill-creator/scripts/init_skill.py \
  researching-jobs-to-obsidian \
  --path /Users/lynnwang/Documents/Skills \
  --resources scripts,references,assets \
  --interface 'display_name=Job Research to Obsidian' \
  --interface 'short_description=低频采集岗位信息并生成可审计 Obsidian 岗位笔记' \
  --interface 'default_prompt=Use $researching-jobs-to-obsidian to research a company and role and write an evidence-graded Obsidian job brief.'
mkdir -p researching-jobs-to-obsidian/tests
```

Expected: the initializer reports the skill directory, `SKILL.md`, `agents/openai.yaml`, and all three resource directories as created.

- [ ] **Step 3: Assert the generated metadata**

Run:

```bash
sed -n '1,80p' researching-jobs-to-obsidian/agents/openai.yaml
find researching-jobs-to-obsidian -maxdepth 2 -type d -print | sort
```

Expected: `openai.yaml` has the exact display name, Chinese description, and default prompt above; the inventory includes `agents`, `assets`, `references`, `scripts`, and `tests`.

- [ ] **Step 4: Commit the scaffold**

```bash
git add researching-jobs-to-obsidian
git commit -m "feat: scaffold job research skill"
```

## Task 2: Implement the risk-aware MCP batch client with TDD

**Files:**

- Create: `researching-jobs-to-obsidian/tests/test_xhs_mcp_client.py`
- Create: `researching-jobs-to-obsidian/scripts/xhs_mcp_client.py`

The manifest contract is:

```json
{
  "mode": "search",
  "items": [
    {"key": "q01", "query": "蚂蚁集团 商业分析 校招", "limit": 10, "note_type": "all"}
  ]
}
```

or:

```json
{
  "mode": "detail",
  "items": [
    {"key": "note-123", "note_id": "123", "xsec_token": "token", "max_comments": 10}
  ]
}
```

Each item output must use this stable shape:

```json
{
  "key": "q01",
  "tool": "search_xiaohongshu",
  "arguments": {"query": "蚂蚁集团 商业分析 校招", "limit": 10, "note_type": "all"},
  "envelope": {"ok": true, "data": {"results": []}, "error": null}
}
```

- [ ] **Step 1: Write failing pure-helper and async batch tests**

Create `test_xhs_mcp_client.py` with standard-library tests covering:

```python
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from xhs_mcp_client import (  # noqa: E402
    build_server_env,
    execute_batch,
    item_arguments,
    parse_envelope,
)


def success(data):
    return json.dumps({"ok": True, "data": data, "error": None}, ensure_ascii=False)


def failure(code):
    return json.dumps({
        "ok": False,
        "data": None,
        "error": {"code": code, "message": code, "retryable": code == "search_timeout"},
    })


class TextBlock:
    def __init__(self, text):
        self.text = text


class ToolResult:
    def __init__(self, text):
        self.content = [TextBlock(text)]


class ClientHelpersTest(unittest.TestCase):
    def test_build_server_env_is_explicit_and_preserves_base(self):
        env = build_server_env("codex", True, 12, {"PATH": "/bin"})
        self.assertEqual(env["PATH"], "/bin")
        self.assertEqual(env["STRIDE28_SEARCH_MCP_PROFILE"], "codex")
        self.assertEqual(env["STRIDE28_XHS_HEADLESS"], "true")
        self.assertEqual(env["STRIDE28_RATE_LIMIT_SECONDS"], "12")

    def test_detail_arguments_default_to_ten_comments(self):
        args = item_arguments("detail", {"note_id": "n1", "xsec_token": "x1"})
        self.assertEqual(args, {"note_id": "n1", "xsec_token": "x1", "max_comments": 10})

    def test_detail_arguments_respect_upstream_safety_cap(self):
        args = item_arguments("detail", {
            "note_id": "n1", "xsec_token": "x1", "max_comments": 80,
        })
        self.assertEqual(args["max_comments"], 50)

    def test_parse_envelope_reads_mcp_text_content(self):
        parsed = parse_envelope(ToolResult(success({"results": [{"id": "n1"}]})))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["results"][0]["id"], "n1")


class BatchTest(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_is_recorded_once_and_batch_continues(self):
        calls = []
        sleeps = []

        async def call_tool(name, arguments):
            calls.append((name, arguments))
            return ToolResult(failure("search_timeout") if len(calls) == 1 else success({"id": "n2"}))

        async def sleeper(seconds):
            sleeps.append(seconds)

        items = [
            {"key": "n1", "note_id": "n1", "xsec_token": "x1", "max_comments": 10},
            {"key": "n2", "note_id": "n2", "xsec_token": "x2", "max_comments": 10},
        ]
        with tempfile.TemporaryDirectory() as directory:
            summary = await execute_batch(items, "detail", call_tool, Path(directory), 20, sleeper)
            self.assertEqual([call[1]["note_id"] for call in calls], ["n1", "n2"])
            self.assertEqual(summary["timed_out"], 1)
            self.assertEqual(summary["succeeded"], 1)
            self.assertEqual(sleeps, [20])
            saved = json.loads((Path(directory) / "n1.json").read_text())
            self.assertEqual(saved["envelope"]["error"]["code"], "search_timeout")

    async def test_risk_code_stops_remaining_items(self):
        calls = []

        async def call_tool(name, arguments):
            calls.append(arguments["query"])
            code = "captcha_detected" if len(calls) == 2 else None
            return ToolResult(failure(code) if code else success({"results": []}))

        async def sleeper(seconds):
            await asyncio.sleep(0)

        items = [
            {"key": "q1", "query": "one", "limit": 10},
            {"key": "q2", "query": "two", "limit": 10},
            {"key": "q3", "query": "three", "limit": 10},
        ]
        with tempfile.TemporaryDirectory() as directory:
            summary = await execute_batch(items, "search", call_tool, Path(directory), 12, sleeper)
            self.assertEqual(calls, ["one", "two"])
            self.assertEqual(summary["stopped_on"], "captcha_detected")
            self.assertFalse((Path(directory) / "q3.json").exists())
```

Also add table-driven cases for `search_blocked` and `risk_cooldown_active`, invalid JSON envelopes, missing required manifest keys, unsafe output keys, and waits occurring only between attempted items.

- [ ] **Step 2: Run the test and confirm RED**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest researching-jobs-to-obsidian/tests/test_xhs_mcp_client.py -v
```

Expected: import failure because `scripts/xhs_mcp_client.py` does not exist.

- [ ] **Step 3: Implement pure helpers and batch execution**

Implement these public symbols:

```python
RISK_CODES = {"captcha_detected", "search_blocked", "risk_cooldown_active"}


def build_server_env(profile, headless, rate_limit, base_env=None):
    env = dict(base_env if base_env is not None else os.environ)
    env["STRIDE28_SEARCH_MCP_PROFILE"] = profile
    env["STRIDE28_XHS_HEADLESS"] = "true" if headless else "false"
    env["STRIDE28_RATE_LIMIT_SECONDS"] = str(rate_limit)
    return env


def item_arguments(mode, item):
    if mode == "search":
        return {
            "query": require_text(item, "query"),
            "limit": require_range(item, "limit", default=10, minimum=1, maximum=20),
            "note_type": item.get("note_type", "all"),
        }
    if mode == "detail":
        return {
            "note_id": require_text(item, "note_id"),
            "xsec_token": str(item.get("xsec_token", "")),
            "max_comments": require_range(item, "max_comments", default=10, minimum=0, maximum=50),
        }
    raise ValueError(f"unsupported mode: {mode}")
```

`parse_envelope` must accept a JSON string, a mapping, or an MCP result whose first text content block contains the JSON string. Reject non-dictionaries and envelopes missing Boolean `ok`.

`execute_batch` must:

1. create the output directory;
2. validate every `key` against `^[A-Za-z0-9._-]+$` before the first call;
3. call `search_xiaohongshu` or `get_note_detail` once per attempted item;
4. atomically write `<key>.json` through a sibling `.tmp` file and `Path.replace`;
5. increment success, timeout, or other-failure counts;
6. break immediately after writing a hard-stop result;
7. sleep only when another item remains and no hard stop occurred;
8. write `run_summary.json` with totals and the stop reason;
9. never retry an item.

- [ ] **Step 4: Add the real lazy MCP boundary and CLI**

Keep MCP imports inside `open_mcp_session` so importing the module needs only the standard library:

```python
@asynccontextmanager
async def open_mcp_session(command, env):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    server = StdioServerParameters(command=command, args=[], env=env)
    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            yield session
```

Provide subcommands and exact mode behavior:

- `list-tools --profile codex`: headless true; initialize once; print tool names as JSON.
- `login --profile codex`: headless false; call `login_xiaohongshu` once.
- `search-batch MANIFEST --output-dir RUN/search --profile codex --delay 12 --rate-limit 12`: require manifest mode `search`; one session for the entire manifest.
- `detail-batch MANIFEST --output-dir RUN/details --profile codex --delay 20 --rate-limit 20`: require manifest mode `detail`; one session for the entire manifest.
- `--server-command` defaults to `stride28-search-mcp`.

Do not print the full environment, cookies, token stores, or browser profile paths. Compact progress lines may include item number, key, `ok`, and error code.

- [ ] **Step 5: Run focused and full tests**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest researching-jobs-to-obsidian/tests/test_xhs_mcp_client.py -v
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s researching-jobs-to-obsidian/tests -v
```

Expected: all client tests pass without importing MCP or opening a browser.

- [ ] **Step 6: Commit**

```bash
git add researching-jobs-to-obsidian/scripts/xhs_mcp_client.py researching-jobs-to-obsidian/tests/test_xhs_mcp_client.py
git commit -m "feat: add risk-aware MCP batch client"
```

## Task 3: Implement selected-detail image downloads with TDD

**Files:**

- Create: `researching-jobs-to-obsidian/tests/test_download_note_images.py`
- Create: `researching-jobs-to-obsidian/scripts/download_note_images.py`

- [ ] **Step 1: Write failing tests for selection, extraction, delay, and manifest output**

The central fixture and assertions should follow this shape:

```python
class ImageDownloadTest(unittest.IsolatedAsyncioTestCase):
    async def test_only_selected_successful_details_are_downloaded(self):
        selected = {"n1"}
        envelopes = {
            "n1.json": {"key": "n1", "envelope": {"ok": True, "data": {
                "id": "n1", "image_urls": ["https://img.example/a.jpg", "https://img.example/b.png"]
            }, "error": None}},
            "n2.json": {"key": "n2", "envelope": {"ok": True, "data": {
                "id": "n2", "image_urls": ["https://img.example/c.jpg"]
            }, "error": None}},
            "n3.json": {"key": "n3", "envelope": {"ok": False, "data": None,
                "error": {"code": "search_timeout"}}},
        }
        calls = []
        sleeps = []

        async def fetcher(url):
            calls.append(url)
            return b"image-bytes", "image/jpeg"

        async def sleeper(seconds):
            sleeps.append(seconds)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            details = root / "details"
            details.mkdir()
            for name, value in envelopes.items():
                (details / name).write_text(json.dumps(value), encoding="utf-8")
            jobs = collect_jobs(details, selected)
            manifest = await download_jobs(jobs, root / "images", 2, fetcher, sleeper)
            self.assertEqual(calls, ["https://img.example/a.jpg", "https://img.example/b.png"])
            self.assertEqual(sleeps, [2])
            self.assertEqual(manifest["succeeded"], 2)
            self.assertEqual(sorted(path.name for path in (root / "images").iterdir()), [
                "download_manifest.json", "n1-01.jpg", "n1-02.png",
            ])
```

Add cases rejecting non-HTTP(S) URLs, path-like note IDs, duplicate URLs, malformed detail JSON, and `run_summary.json`.

- [ ] **Step 2: Confirm RED**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest researching-jobs-to-obsidian/tests/test_download_note_images.py -v
```

Expected: import failure because the downloader is absent.

- [ ] **Step 3: Implement the downloader**

Public interface:

```python
def collect_jobs(details_dir: Path, selected_ids: set[str]) -> list[dict]:
    """Return deduplicated image jobs from successful allowlisted detail envelopes."""


async def download_jobs(jobs, output_dir, delay_seconds, fetcher, sleeper=asyncio.sleep) -> dict:
    """Download each job once and atomically write download_manifest.json."""
```

CLI:

```bash
python3 scripts/download_note_images.py \
  --details-dir RUN/details \
  --selected-ids RUN/selected-note-ids.txt \
  --output-dir RUN/images \
  --delay 2
```

Use `urllib.request` through `asyncio.to_thread` for the default fetcher. Derive extensions only from a safe URL suffix or `Content-Type`, defaulting to `.jpg`. Write no files outside the resolved output directory. Continue individual download failures, record the error in the manifest, and never revisit a live note page.

- [ ] **Step 4: Run tests and commit**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest researching-jobs-to-obsidian/tests/test_download_note_images.py -v
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s researching-jobs-to-obsidian/tests -v
git add researching-jobs-to-obsidian/scripts/download_note_images.py researching-jobs-to-obsidian/tests/test_download_note_images.py
git commit -m "feat: add selected-note image downloader"
```

Expected: all tests pass without network access.

## Task 4: Implement Obsidian validation with TDD

**Files:**

- Create: `researching-jobs-to-obsidian/tests/test_validate_obsidian.py`
- Create: `researching-jobs-to-obsidian/scripts/validate_obsidian.py`

The exact required level-two headings are:

```python
REQUIRED_HEADINGS = (
    "一页结论",
    "岗位画像",
    "入职门槛",
    "面试流程与题库",
    "薪资待遇",
    "工作体验",
    "准备建议",
    "证据边界",
    "检索统计",
    "来源索引",
)
```

- [ ] **Step 1: Write a valid-document fixture and failing negative tests**

Build one complete in-memory Markdown fixture with nonempty YAML frontmatter, all headings, one used source footnote, and these explicit limits:

```markdown
## 入职门槛

样本中的学历分布只是竞争信号，不等于官方硬门槛。[^src-01]

## 薪资待遇

样本口径不一致，以下数据并非公司官方薪资范围，不能据此推断目标岗位统一待遇。[^src-01]

[^src-01]: https://example.com/note-01
```

Test that `validate_text(valid_text)` returns `[]`.

Add one mutation test for each of:

- missing or empty frontmatter;
- every missing required heading;
- an unresolved `{{company}}` token;
- the literal implementation markers detected by `r"\b(?:TO" + "DO|TB" + "D)\b"` so this plan itself contains no marker;
- the three standard Git merge-conflict markers, assembled in the test from repeated `<`, `=`, and `>` characters;
- a source reference without a definition;
- a definition never cited in the body;
- duplicated source URLs;
- no HTTPS source definitions;
- a Markdown link to `/Users/name/file.md`, `/home/name/file.md`, `file://`, or a Windows drive path;
- missing salary evidence-limit wording;
- missing education evidence-limit wording.

- [ ] **Step 2: Confirm RED**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest researching-jobs-to-obsidian/tests/test_validate_obsidian.py -v
```

Expected: import failure because the validator is absent.

- [ ] **Step 3: Implement pure validation and CLI**

Public interface:

```python
def validate_text(text: str) -> list[str]:
    """Return stable, human-readable validation failures in document order."""


def validate_file(path: Path) -> list[str]:
    return validate_text(path.read_text(encoding="utf-8"))
```

Implementation rules:

- accept CRLF by normalizing to LF;
- parse frontmatter boundaries without adding a YAML dependency;
- match exact `##` headings after trimming surrounding spaces;
- restrict salary and education phrase checks to their sections;
- recognize footnotes with `[^source-id]` syntax and definitions beginning `[^source-id]: https://`;
- report every independent error in one run;
- exit 0 with `OK: <path>` when valid;
- exit 1 and print one `ERROR:` line per failure when invalid;
- exit 2 for unreadable input or CLI misuse.

- [ ] **Step 4: Run tests and commit**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest researching-jobs-to-obsidian/tests/test_validate_obsidian.py -v
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s researching-jobs-to-obsidian/tests -v
git add researching-jobs-to-obsidian/scripts/validate_obsidian.py researching-jobs-to-obsidian/tests/test_validate_obsidian.py
git commit -m "feat: validate evidence-backed Obsidian briefs"
```

## Task 5: Add the macOS Vision OCR helper with compile-first TDD

**Files:**

- Create: `researching-jobs-to-obsidian/tests/test_vision_ocr.py`
- Create: `researching-jobs-to-obsidian/scripts/vision_ocr.m`

- [ ] **Step 1: Write the failing compile contract**

Create a macOS-only standard-library test that runs:

```python
command = [
    "xcrun", "clang", "-fobjc-arc",
    "-framework", "Foundation",
    "-framework", "AppKit",
    "-framework", "Vision",
    str(SOURCE), "-o", str(binary),
]
```

The test must assert exit 0 and print compiler stderr on failure. Add an opt-in runtime test guarded by both `sys.platform == "darwin"` and `RUN_VISION_OCR_SMOKE == "1"`; it compiles the binary in a temporary directory, runs `binary --self-test`, and requires recognized text containing `INTERVIEW`.

- [ ] **Step 2: Confirm RED**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest researching-jobs-to-obsidian/tests/test_vision_ocr.py -v
```

Expected: compile test fails because `vision_ocr.m` is absent.

- [ ] **Step 3: Implement the Objective-C command**

Requirements:

- usage: `vision_ocr [--languages zh-Hans,en-US] IMAGE...`;
- output: one JSON object per image with `path`, `ok`, `text`, `observations`, and `error`;
- use `VNRecognizeTextRequest` with accurate recognition and language correction;
- order observations top-to-bottom, then left-to-right;
- never change or delete the input image;
- return nonzero if any requested image cannot be loaded or processed;
- `--self-test` creates a temporary in-memory-rendered PNG containing `INTERVIEW 2026`, runs the same OCR path, prints its JSON result, and removes only that generated fixture;
- JSON-escape all output through `NSJSONSerialization` rather than string concatenation.

The executable is always compiled into a task-specific temporary directory, never into the skill directory:

```bash
OCR_RUN_DIR="$(mktemp -d /private/tmp/researching-jobs-ocr.XXXXXX)"
CLANG_MODULE_CACHE_PATH="$OCR_RUN_DIR/module-cache" \
  xcrun clang -fobjc-arc -framework Foundation -framework AppKit -framework Vision \
  researching-jobs-to-obsidian/scripts/vision_ocr.m \
  -o "$OCR_RUN_DIR/vision_ocr"
```

- [ ] **Step 4: Run compile tests, then the system-context smoke test**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest researching-jobs-to-obsidian/tests/test_vision_ocr.py -v
RUN_VISION_OCR_SMOKE=1 PYTHONDONTWRITEBYTECODE=1 python3 -m unittest researching-jobs-to-obsidian/tests/test_vision_ocr.py -v
```

Expected: compile test passes. The runtime smoke test recognizes `INTERVIEW`; if the restricted sandbox denies Vision service access, rerun only the smoke command with explicit user approval in system context. A runtime denial is documented as an environment limitation, never converted into invented OCR text.

- [ ] **Step 5: Commit**

```bash
git add researching-jobs-to-obsidian/scripts/vision_ocr.m researching-jobs-to-obsidian/tests/test_vision_ocr.py
git commit -m "feat: add local Vision OCR helper"
```

## Task 6: Write the skill contract, references, template, and attribution

**Files:**

- Create: `researching-jobs-to-obsidian/tests/test_skill_contract.py`
- Replace: `researching-jobs-to-obsidian/SKILL.md`
- Create: `researching-jobs-to-obsidian/references/setup.md`
- Create: `researching-jobs-to-obsidian/references/evidence-and-risk.md`
- Create: `researching-jobs-to-obsidian/assets/job-research-template.md`
- Create: `researching-jobs-to-obsidian/license.txt`
- Verify: `researching-jobs-to-obsidian/agents/openai.yaml`

- [ ] **Step 1: Encode baseline gaps as failing contract tests**

`test_skill_contract.py` must read files as text and assert all of the following:

- frontmatter has only `name` and `description`;
- description begins with `Use when` and includes Xiaohongshu, Zhihu, Obsidian, job research, campus/interview/salary/work-experience triggers, and low-frequency/login-sensitive language;
- `SKILL.md` names exactly three checkpoints and routes detailed setup and evidence rules to the two references;
- the numeric defaults `6`, `10`, `2`, `12`, `180`, `18`, `6`, `20`, `300`, and `2` occur with their units and meaning in the relevant files;
- all three risk codes and `search_timeout` behavior appear;
- named profile, headed login, explicit headless batch mode, and one-session-per-batch appear;
- A/B/C evidence grades and the salary, education, work-experience, comments, and OCR claim limits appear;
- the template includes every validator heading and unresolved `{{company}}`, `{{role}}`, `{{recruiting_type}}`, `{{city_scope}}`, and `{{date}}` tokens;
- the source index asks for author, publish date, URL, query, evidence grade, evidence medium, recruiting type, city, and limitations;
- `license.txt` attributes `BrunonXU/Stride28-search2docs` and its MIT license;
- neither the Ant Group source data nor the completed Ant research document is bundled;
- `agents/openai.yaml` contains `$researching-jobs-to-obsidian` in `default_prompt`.

- [ ] **Step 2: Confirm RED**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest researching-jobs-to-obsidian/tests/test_skill_contract.py -v
```

Expected: multiple failures against the generated scaffold and absent reference files.

- [ ] **Step 3: Write concise `SKILL.md`**

Keep it under 700 words and use this workflow order:

1. applicability and prerequisites;
2. read `references/setup.md` only when installation, login, architecture, or runtime diagnosis is needed;
3. Checkpoint 1 configuration and approval;
4. summary batches and Checkpoint 2;
5. detail batches, selected images, OCR, evidence grading, and Checkpoint 3;
6. draft, validate, safe write, and file-level verification;
7. completion checklist.

Use this exact frontmatter shape:

```yaml
---
name: researching-jobs-to-obsidian
description: Use when researching jobs from Xiaohongshu or Zhihu into Obsidian, especially campus recruiting, interview, salary, entry-threshold, or work-experience requests that need low-frequency, login-sensitive collection and auditable evidence.
---
```

Hard rules in the body must say:

- network/login begins only after Checkpoint 1;
- no detail calls before Checkpoint 2;
- no final vault write before Checkpoint 3;
- no automatic retry for timeout or sparse results;
- hard stop on risk codes;
- no visible-browser fallback from headless mode;
- claims must retain source grade and scope;
- existing Markdown is re-read before an approved merge.

- [ ] **Step 4: Write the two references**

`setup.md` must include exact current compatibility guidance:

```bash
uv tool install --python 3.11 --force 'stride28-search-mcp==0.2.1' 'mcp[cli]<2'
stride28-search-mcp doctor
python3 -m playwright install chromium
```

Explain that commands and paths are examples to adapt to the current system, Apple Silicon should use a native arm64 Python, upstream 0.2.1 imports `mcp.server.fastmcp`, and package installation, browser launch, network access, or outside-workspace writes may require user approval. Do not claim that headless mode is safer; state that it reduces popups but may change platform detection behavior.

`evidence-and-risk.md` must define:

- six-query search matrix construction across exact role, interview, salary, experience, recruiting cohort, and aliases;
- ID deduplication first and normalized-title similarity second;
- marketing, adjacent-role, low-information, empty-comment, and unverifiable-image filters;
- topic allocation for entry threshold, interviews, compensation, and work experience;
- A/B/C grade definitions and permitted claim language;
- separate fields for post text, OCR text, author statement, and agent inference;
- salary dimensions: monthly pay, months, bonus, equity, subsidy, city, cohort, role, business line, and source wording;
- education as a competition signal unless an official JD proves a hard rule;
- work experience attributed to the author/team/time;
- stopping criteria and how to report coverage within the approved budget rather than claiming platform-wide completeness.

- [ ] **Step 5: Write the Obsidian template and license**

The template must have valid YAML frontmatter and exactly the required headings. It should include a compact source-index row schema and footnote examples using obvious template variables that cannot accidentally validate before substitution.

`license.txt` must state that the skill is an independent workflow adaptation, does not vendor upstream source, and includes the upstream repository URL and MIT attribution. Do not include cookies, profile paths, post content, screenshots, OCR results, or the Ant Group research data.

- [ ] **Step 6: Make contract tests GREEN and validate metadata**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest researching-jobs-to-obsidian/tests/test_skill_contract.py -v
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s researching-jobs-to-obsidian/tests -v
UV_CACHE_DIR=/private/tmp/researching-jobs-uv-cache \
  /Users/lynnwang/.local/bin/uv run --with pyyaml \
  python /Users/lynnwang/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  researching-jobs-to-obsidian
```

Expected: every unit test passes and the official validator prints `Skill is valid!`. If PyYAML is not already cached, request approval for that dependency download rather than replacing the official validator.

- [ ] **Step 7: Validate the template's intentional RED state and a rendered GREEN fixture**

Run the validator directly on the untouched template and expect unresolved-variable failures. Then copy it to a temporary directory, substitute every variable with deterministic sample values, add a complete HTTPS source definition and the required limit statements, and expect exit 0. Do not write this fixture into the skill directory.

- [ ] **Step 8: Commit**

```bash
git add researching-jobs-to-obsidian
git commit -m "feat: document auditable job research workflow"
```

## Task 7: Forward-test the skill against the baseline failures

**Files:**

- Modify only if a gap is found: `researching-jobs-to-obsidian/SKILL.md`
- Modify only if a gap is found: `researching-jobs-to-obsidian/references/*.md`
- Modify only if a gap is found: `researching-jobs-to-obsidian/assets/job-research-template.md`
- Modify only if a gap is found: `researching-jobs-to-obsidian/tests/test_skill_contract.py`

- [ ] **Step 1: Run two independent no-network forward tests**

Give both agents the implemented skill and tell them explicitly to plan but not access the network or write a vault file.

Prompt A:

```text
使用 researching-jobs-to-obsidian：从小红书调研字节跳动/战略分析/城市不限/2027校招。尽量不弹浏览器，控制检索频率。先告诉我你会怎样确认范围、分批搜索、处理超时和风控，再说明何时需要我确认。
```

Prompt B:

```text
使用 researching-jobs-to-obsidian：调研某互联网公司的商业分析岗位，薪资和面经主要在图片里。目标 Obsidian 目录已有同名文件且我手动删过内容。请只模拟完整流程，不联网、不写文件。
```

- [ ] **Step 2: Grade both outputs with one checklist**

Each output must include:

- exactly three approval checkpoints;
- quantitative summary/detail/image defaults and batch gaps;
- one named persistent profile;
- headed login followed by explicit headless batches;
- one MCP/browser session per batch;
- no automatic timeout or sparse-query retry;
- immediate stop on all three risk codes;
- successful-selected-detail-only image downloads;
- local Vision OCR plus uncertainty marking and visual review for material numbers;
- A/B/C evidence separation and claim limits;
- current-file re-read, no overwrite, and explicit append-versus-new decision for a same-name file;
- validation before copy and checksum/line/section/source/conflict checks after copy.

No output may claim exhaustive platform coverage, invent exact compensation, treat observed education as official, or restore deleted content.

- [ ] **Step 3: Convert every miss into a contract test before editing**

For each missing behavior, first add a failing assertion to `test_skill_contract.py`, run it to see the expected failure, then make the smallest skill/reference/template edit and rerun the test.

- [ ] **Step 4: Re-run all offline tests and commit any forward-test hardening**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s researching-jobs-to-obsidian/tests -v
git diff --check
```

If files changed:

```bash
git add researching-jobs-to-obsidian
git commit -m "test: harden job research skill from forward tests"
```

## Task 8: Final verification, package, install, and compare

**Files:**

- Create: `dist/researching-jobs-to-obsidian.zip`
- Create outside repository after approval: `/Users/lynnwang/.codex/skills/researching-jobs-to-obsidian/`

- [ ] **Step 1: Run the complete verification suite from a clean source tree**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s researching-jobs-to-obsidian/tests -v
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest researching-jobs-to-obsidian/tests/test_vision_ocr.py -v
UV_CACHE_DIR=/private/tmp/researching-jobs-uv-cache \
  /Users/lynnwang/.local/bin/uv run --with pyyaml \
  python /Users/lynnwang/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  researching-jobs-to-obsidian
git diff --check
```

Expected: all tests pass, OCR source compiles, the skill is valid, and the diff check is empty.

- [ ] **Step 2: Audit the source inventory for private or generated data**

```bash
find researching-jobs-to-obsidian -type f -print | sort
find researching-jobs-to-obsidian -type f \( \
  -name '.DS_Store' -o -name '*.pyc' -o -name '*.log' -o -name '*.png' -o \
  -name '*.jpg' -o -name '*.jpeg' -o -name 'Cookies' -o -perm -111 \
\) -print
rg -n -P '/Users/lynnwang|蚂蚁集团商业分析校招|"(?:xsec_token|cookie)"\s*:\s*"[A-Za-z0-9_%=-]{16,}"' researching-jobs-to-obsidian
```

Expected: the first command shows only designed source, test, reference, asset, metadata, and license files. The second and third print nothing. The skill must contain no user vault path, Ant research content, live note token, cookie, raw post, image, OCR output, browser profile, or compiled binary.

- [ ] **Step 3: Build and inspect the ZIP**

```bash
mkdir -p dist
zip -r -FS dist/researching-jobs-to-obsidian.zip researching-jobs-to-obsidian \
  -x '*/__pycache__/*' '*.pyc' '*.log' '*/.DS_Store' '*/browser_data/*' '*/runtime_state/*'
unzip -t dist/researching-jobs-to-obsidian.zip
unzip -l dist/researching-jobs-to-obsidian.zip
```

Expected: ZIP integrity is OK, it has one top-level `researching-jobs-to-obsidian/` directory, and its inventory matches the audited source files.

- [ ] **Step 4: Commit the distributable**

```bash
git add dist/researching-jobs-to-obsidian.zip
git commit -m "build: package job research skill"
```

- [ ] **Step 5: Install without overwriting an existing skill**

First run the read-only preflight:

```bash
test ! -e /Users/lynnwang/.codex/skills/researching-jobs-to-obsidian
```

Expected: exit 0. If the target exists, inspect and compare it, then stop for an explicit replace/update decision.

With approval for the outside-workspace write:

```bash
mkdir -p /Users/lynnwang/.codex/skills
cp -R /Users/lynnwang/Documents/Skills/researching-jobs-to-obsidian \
  /Users/lynnwang/.codex/skills/researching-jobs-to-obsidian
```

- [ ] **Step 6: Validate and compare the installed copy**

```bash
UV_CACHE_DIR=/private/tmp/researching-jobs-uv-cache \
  /Users/lynnwang/.local/bin/uv run --with pyyaml \
  python /Users/lynnwang/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  /Users/lynnwang/.codex/skills/researching-jobs-to-obsidian
diff -qr \
  /Users/lynnwang/Documents/Skills/researching-jobs-to-obsidian \
  /Users/lynnwang/.codex/skills/researching-jobs-to-obsidian
shasum -a 256 dist/researching-jobs-to-obsidian.zip
git status --short
```

Expected: installed validation prints `Skill is valid!`, `diff` prints nothing, the ZIP checksum is recorded for handoff, and Git has no uncommitted skill changes.

## Task 9: Final handoff

- [ ] **Step 1: Report only verified outcomes**

Provide clickable paths to:

- `researching-jobs-to-obsidian/SKILL.md`;
- `researching-jobs-to-obsidian/`;
- `dist/researching-jobs-to-obsidian.zip`;
- `/Users/lynnwang/.codex/skills/researching-jobs-to-obsidian/SKILL.md`;
- this implementation plan;
- the approved design specification.

Include test count, official validator result, Vision compile/runtime result, ZIP checksum, installed-copy comparison result, and any environment-specific OCR caveat. State that no network job research was run during skill tests and no user research data or browser state was packaged.

- [ ] **Step 2: Preserve scope boundaries**

Do not push to GitHub, delete source artifacts, modify the earlier Ant Group Obsidian document, or replace any existing installed skill unless the user separately authorizes that action.
