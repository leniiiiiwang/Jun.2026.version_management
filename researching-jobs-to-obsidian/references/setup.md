# Setup, runtime, and session boundaries

This reference is for installation, login, architecture, and runtime diagnosis only. Use `references/evidence-and-risk.md` for query and evidence decisions.

## Install in an approved environment

Request approval before installation, browser/network activity, or writes outside the workspace. On a clean environment, install the server with an MCP <2 / 1.x-compatible client runtime:

```bash
uv tool install --python 3.11 --force --with 'mcp[cli]<2' 'stride28-search-mcp==0.2.1'
stride28-search-mcp doctor
stride28-search-mcp install-browser
```

Use native arm64 Python on Apple Silicon. The upstream 0.2.1 server imports `mcp.server.fastmcp`; incompatible newer MCP installations can break that import. The client uses an ordinary-Python bootstrap to re-exec into the tool interpreter when its MCP runtime is missing; diagnose with `stride28-search-mcp doctor` rather than mixing interpreters.

## Local Vision OCR

Compile and run `scripts/vision_ocr.m` only into a task temp directory; never write its binary or module cache into the skill or vault. For selected, approved images:

```bash
ocr_tmp=$(mktemp -d)
export CLANG_MODULE_CACHE_PATH="$ocr_tmp/clang-module-cache"
xcrun clang -fobjc-arc -framework Foundation -framework AppKit -framework Vision \
  scripts/vision_ocr.m -o "$ocr_tmp/vision_ocr"
"$ocr_tmp/vision_ocr" --self-test
"$ocr_tmp/vision_ocr" IMAGE
```

The Vision service may fail in a restricted sandbox. Request approved system-context execution only when needed. If it remains unavailable, retain the image as pending manual/visual review and never invent OCR. Remove only the task-created temp directory after the task.

## Named profile and browser mode

Require a named profile (never a disposable anonymous path). `scripts/xhs_mcp_client.py` and `download_note_images.py` are Xiaohongshu-specific. Perform Xiaohongshu login visibly once; use headed mode only for login or manual repair. Then run approved Xiaohongshu search and detail batches headless. Headless reduces popups, but is not inherently safer and may change platform detection.

Do not turn a headless batch into a visible fallback. If login expires, pause for the user to repair it visibly, then resume only after approval. One MCP/browser session is allowed per batch.

## Zhihu route

For Zhihu, use the upstream MCP tools in one persistent session per batch: `login_zhihu` visibly only when needed, then `search_zhihu` and `get_zhihu_question`. Apply the same checkpoints, same budgets, no-retry rule, and evidence rules as the Xiaohongshu workflow. If Zhihu presents any platform risk/verification/login restriction, stop and report it; never bypass. Do not assume Xiaohongshu hard-stop codes are implemented identically for Zhihu.

## Safe CLI use

Use fresh, empty output directories. A manifest is a small JSON file with a `mode` (`search` or `detail`) and bounded `items`; the client writes one result per safe key plus `run_summary.json`. Never point output at an existing evidence directory you did not create for this run.

```bash
python3 scripts/xhs_mcp_client.py search-batch \
  ./search-manifest.json --output-dir ./search-output \
  --profile approved-job-research
python3 scripts/xhs_mcp_client.py detail-batch \
  ./detail-manifest.json --output-dir ./detail-output \
  --profile approved-job-research
```

Pass the script's explicit delay/rate options only at or above the documented within-batch floors. Batch spacing and checkpoint approvals are orchestration responsibilities, not script-enforced behavior.
