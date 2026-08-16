"""Risk-aware batch client for the stride28 Xiaohongshu MCP server."""

import argparse
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import re
from collections.abc import Mapping


RISK_CODES = {"captcha_detected", "search_blocked", "risk_cooldown_active"}
KEY_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
TOOL_NAMES = {"search": "search_xiaohongshu", "detail": "get_note_detail"}


def build_server_env(profile, headless, rate_limit, base_env=None):
    """Return the server process environment without exposing it in logs."""
    env = dict(os.environ if base_env is None else base_env)
    env.update(
        {
            "STRIDE28_SEARCH_MCP_PROFILE": str(profile),
            "STRIDE28_XHS_HEADLESS": "true" if headless else "false",
            "STRIDE28_RATE_LIMIT_SECONDS": str(rate_limit),
        }
    )
    return env


def _nonblank_string(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonblank string")
    return value.strip()


def _integer(value, name):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value


def item_arguments(mode, item):
    """Validate one manifest item and return the arguments expected by its tool."""
    if not isinstance(item, Mapping):
        raise ValueError("each manifest item must be an object")
    if mode == "search":
        query = _nonblank_string(item.get("query"), "query")
        limit = _integer(item.get("limit", 10), "limit")
        if not 1 <= limit <= 20:
            raise ValueError("limit must be between 1 and 20")
        note_type = item.get("note_type", "all")
        if not isinstance(note_type, str) or not note_type.strip():
            raise ValueError("note_type must be a nonblank string")
        return {"query": query, "limit": limit, "note_type": note_type.strip()}
    if mode == "detail":
        note_id = _nonblank_string(item.get("note_id"), "note_id")
        xsec_token = item.get("xsec_token", "")
        if not isinstance(xsec_token, str):
            raise ValueError("xsec_token must be a string")
        max_comments = _integer(item.get("max_comments", 10), "max_comments")
        if max_comments < 1:
            raise ValueError("max_comments must be at least 1")
        return {
            "note_id": note_id,
            "xsec_token": xsec_token,
            "max_comments": min(max_comments, 50),
        }
    raise ValueError("mode must be 'search' or 'detail'")


def _json_object(text):
    try:
        value = json.loads(text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("result does not contain valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("envelope must be a JSON object")
    return value


def _content_text(result):
    content = result.get("content") if isinstance(result, Mapping) else getattr(result, "content", None)
    if not isinstance(content, (list, tuple)) or not content:
        return None
    first = content[0]
    block_type = first.get("type") if isinstance(first, Mapping) else getattr(first, "type", None)
    text = first.get("text") if isinstance(first, Mapping) else getattr(first, "text", None)
    return text if block_type == "text" and isinstance(text, str) else None


def parse_envelope(result):
    """Normalize direct and MCP tool-call responses to an envelope object."""
    if isinstance(result, str):
        envelope = _json_object(result)
    elif isinstance(result, Mapping) and "ok" in result:
        envelope = dict(result)
    else:
        text = _content_text(result)
        if text is None:
            raise ValueError("result has no JSON text content block")
        envelope = _json_object(text)
    if type(envelope.get("ok")) is not bool:
        raise ValueError("envelope must contain Boolean ok")
    return envelope


def _error_code(envelope):
    error = envelope.get("error")
    if isinstance(error, Mapping) and isinstance(error.get("code"), str):
        return error["code"]
    return None


def _failure_envelope(code, message):
    return {"ok": False, "error": {"code": code, "message": message}}


def _write_json_atomically(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _validated_items(items, mode):
    if mode not in TOOL_NAMES:
        raise ValueError("mode must be 'search' or 'detail'")
    if not isinstance(items, list):
        raise ValueError("manifest items must be a list")
    validated = []
    for item in items:
        if not isinstance(item, Mapping):
            raise ValueError("each manifest item must be an object")
        key = item.get("key")
        if not isinstance(key, str) or not KEY_PATTERN.fullmatch(key):
            raise ValueError("item key must match ^[A-Za-z0-9._-]+$")
        validated.append((key, item_arguments(mode, item)))
    return validated


async def execute_batch(items, mode, call_tool, output_dir, delay_seconds, sleeper=asyncio.sleep):
    """Execute an already connected batch, persisting every attempted item once."""
    validated = _validated_items(items, mode)
    if delay_seconds < 0:
        raise ValueError("delay_seconds must not be negative")
    output_dir = Path(output_dir)
    summary = {
        "total": len(validated), "attempted": 0, "succeeded": 0,
        "timed_out": 0, "other_failures": 0, "stop_reason": None,
    }
    tool_name = TOOL_NAMES[mode]
    for index, (key, arguments) in enumerate(validated):
        try:
            envelope = parse_envelope(await call_tool(tool_name, arguments))
        except Exception as exc:  # Persist operational failures as a batch result.
            envelope = _failure_envelope("tool_call_failed", str(exc))
        record = {"key": key, "tool": tool_name, "arguments": arguments, "envelope": envelope}
        _write_json_atomically(output_dir / f"{key}.json", record)
        summary["attempted"] += 1
        if envelope["ok"]:
            summary["succeeded"] += 1
        else:
            code = _error_code(envelope)
            if code == "search_timeout":
                summary["timed_out"] += 1
            else:
                summary["other_failures"] += 1
            if code in RISK_CODES:
                summary["stop_reason"] = code
                break
        if index < len(validated) - 1:
            await sleeper(delay_seconds)
    _write_json_atomically(output_dir / "run_summary.json", summary)
    return summary


@asynccontextmanager
async def mcp_session(command, env):
    """Open one initialized stdio MCP session; imports stay lazy for testability."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    parameters = StdioServerParameters(command=command, args=[], env=env)
    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            yield session


def _load_manifest(path, expected_mode):
    try:
        manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("manifest must be readable UTF-8 JSON") from exc
    if not isinstance(manifest, Mapping) or manifest.get("mode") != expected_mode:
        raise ValueError(f"manifest mode must be {expected_mode!r}")
    _validated_items(manifest.get("items"), expected_mode)
    return manifest["items"]


async def _run_list_tools(args):
    env = build_server_env(args.profile, True, args.rate_limit)
    async with mcp_session(args.server_command, env) as session:
        result = await session.list_tools()
        tools = result.get("tools", []) if isinstance(result, Mapping) else getattr(result, "tools", [])
        names = [tool.get("name") if isinstance(tool, Mapping) else getattr(tool, "name", None) for tool in tools]
    print(json.dumps([name for name in names if isinstance(name, str)], ensure_ascii=False))


async def _run_login(args):
    env = build_server_env(args.profile, False, args.rate_limit)
    async with mcp_session(args.server_command, env) as session:
        result = await session.call_tool("login_xiaohongshu", {})
    print(json.dumps(parse_envelope(result), ensure_ascii=False))


async def _run_batch(args, mode):
    items = _load_manifest(args.manifest, mode)  # Validate before opening MCP.
    env = build_server_env(args.profile, True, args.rate_limit)
    async with mcp_session(args.server_command, env) as session:
        async def call_tool(name, arguments):
            return await session.call_tool(name, arguments)
        return await execute_batch(items, mode, call_tool, args.output_dir, args.delay)


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-command", default="stride28-search-mcp")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("list-tools", "login"):
        subparser = subparsers.add_parser(name)
        subparser.add_argument("--profile", required=True)
        subparser.add_argument("--rate-limit", default="12")
    for name, delay, rate_limit in (("search-batch", 12, 12), ("detail-batch", 20, 20)):
        subparser = subparsers.add_parser(name)
        subparser.add_argument("manifest")
        subparser.add_argument("--output-dir", required=True)
        subparser.add_argument("--profile", required=True)
        subparser.add_argument("--delay", type=float, default=delay)
        subparser.add_argument("--rate-limit", default=str(rate_limit))
    return parser


async def _main_async(args):
    if args.command == "list-tools":
        await _run_list_tools(args)
    elif args.command == "login":
        await _run_login(args)
    elif args.command == "search-batch":
        await _run_batch(args, "search")
    else:
        await _run_batch(args, "detail")


def main(argv=None):
    args = _parser().parse_args(argv)
    asyncio.run(_main_async(args))


if __name__ == "__main__":
    main()
