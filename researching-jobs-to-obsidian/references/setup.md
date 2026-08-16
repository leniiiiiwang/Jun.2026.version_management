# Setup, runtime, and session boundaries

This reference is for installation, login, architecture, and runtime diagnosis only. Use `references/evidence-and-risk.md` for query and evidence decisions.

## Install in an approved environment

Request approval before installation, browser/network activity, or writes outside the workspace. On a clean environment, install the server and MCP 2 client runtime together:

```bash
uv tool install --python 3.11 --force 'stride28-search-mcp==0.2.1' 'mcp[cli]<2'
stride28-search-mcp doctor
python3 -m playwright install chromium
```

Use native arm64 Python on Apple Silicon. The upstream 0.2.1 server imports `mcp.server.fastmcp`; an incompatible MCP 2 installation can break that import. The client uses an ordinary-Python bootstrap to re-exec into the tool interpreter when its MCP runtime is missing; diagnose with `stride28-search-mcp doctor` rather than mixing interpreters.

## Named profile and browser mode

Require a named profile (never a disposable anonymous path). Perform login visibly once; use headed mode only for login or manual repair. Then run approved search and detail batches headless: `scripts/xhs_mcp_client.py` sets the relevant headless environment for both modes. Headless reduces popups, but is not inherently safer and may change platform detection.

Do not turn a headless batch into a visible fallback. If login expires, pause for the user to repair it visibly, then resume only after approval. One MCP/browser session is allowed per batch.

## Safe CLI use

Use fresh, empty output directories. A manifest is a small JSON file with a `mode` (`search` or `detail`) and bounded `items`; the client writes one result per safe key plus `run_summary.json`. Never point output at an existing evidence directory you did not create for this run.

```bash
python3 scripts/xhs_mcp_client.py search-batch \
  --manifest ./search-manifest.json --output-dir ./search-output \
  --profile approved-job-research
python3 scripts/xhs_mcp_client.py detail-batch \
  --manifest ./detail-manifest.json --output-dir ./detail-output \
  --profile approved-job-research
```

Pass the script's explicit delay/rate options only at or above the documented within-batch floors. Batch spacing and checkpoint approvals are orchestration responsibilities, not script-enforced behavior.
