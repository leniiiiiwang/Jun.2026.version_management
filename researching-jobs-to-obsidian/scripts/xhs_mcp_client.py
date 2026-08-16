"""Risk-aware batch client for the stride28 Xiaohongshu MCP server."""

import argparse
import asyncio
from contextlib import asynccontextmanager
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from collections.abc import Mapping


RISK_CODES = {"captcha_detected", "search_blocked", "risk_cooldown_active"}
KEY_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
TOOL_NAMES = {"search": "search_xiaohongshu", "detail": "get_note_detail"}
SUMMARY_FIELDS = ("total", "attempted", "succeeded", "timed_out", "other_failures", "stopped_on")


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
        if max_comments < 0:
            raise ValueError("max_comments must not be negative")
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
    descriptor = None
    temporary = None
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, text=True,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = None
            handle.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


def _validated_items(items, mode):
    if mode not in TOOL_NAMES:
        raise ValueError("mode must be 'search' or 'detail'")
    if not isinstance(items, list):
        raise ValueError("manifest items must be a list")
    validated = []
    seen_keys = set()
    for item in items:
        if not isinstance(item, Mapping):
            raise ValueError("each manifest item must be an object")
        key = item.get("key")
        if not isinstance(key, str) or not KEY_PATTERN.fullmatch(key):
            raise ValueError("item key must match ^[A-Za-z0-9._-]+$")
        folded_key = key.casefold()
        if folded_key == "run_summary":
            raise ValueError("item key is reserved")
        if folded_key in seen_keys:
            raise ValueError("item keys must be unique without case collisions")
        seen_keys.add(folded_key)
        validated.append((key, item_arguments(mode, item)))
    return validated


def _prepare_output_dir(output_dir):
    path = Path(output_dir)
    try:
        if path.exists() or path.is_symlink():
            if path.is_symlink() or not path.is_dir():
                raise ValueError("output directory must be a directory")
            if any(path.iterdir()):
                raise ValueError("output directory must be new or empty")
        else:
            path.mkdir(parents=True)
    except OSError as exc:
        raise ValueError("output directory is unavailable") from exc
    return path


async def execute_batch(items, mode, call_tool, output_dir, delay_seconds, sleeper=asyncio.sleep):
    """Execute an already connected batch, persisting every attempted item once."""
    validated = _validated_items(items, mode)
    if delay_seconds < 0:
        raise ValueError("delay_seconds must not be negative")
    output_dir = _prepare_output_dir(output_dir)
    summary = {
        "total": len(validated), "attempted": 0, "succeeded": 0,
        "timed_out": 0, "other_failures": 0, "stopped_on": None,
    }
    tool_name = TOOL_NAMES[mode]
    for index, (key, arguments) in enumerate(validated):
        try:
            result = await call_tool(tool_name, arguments)
        except Exception:  # Persist operational failures without leaking process details.
            envelope = _failure_envelope("tool_call_failed", "tool call failed")
            terminal_failure = True
        else:
            try:
                envelope = parse_envelope(result)
            except ValueError:
                envelope = _failure_envelope("invalid_envelope", "invalid tool envelope")
                terminal_failure = True
            else:
                terminal_failure = False
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
            if terminal_failure or code in RISK_CODES:
                summary["stopped_on"] = code
                break
        if index < len(validated) - 1:
            await sleeper(delay_seconds)
    _write_json_atomically(output_dir / "run_summary.json", summary)
    return summary


@asynccontextmanager
async def open_mcp_session(command, env):
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
    async with open_mcp_session(args.server_command, env) as session:
        result = await session.list_tools()
        tools = result.get("tools", []) if isinstance(result, Mapping) else getattr(result, "tools", [])
        names = [tool.get("name") if isinstance(tool, Mapping) else getattr(tool, "name", None) for tool in tools]
    print(json.dumps([name for name in names if isinstance(name, str)], ensure_ascii=False))


async def _run_login(args):
    env = build_server_env(args.profile, False, args.rate_limit)
    async with open_mcp_session(args.server_command, env) as session:
        result = await session.call_tool("login_xiaohongshu", {})
    envelope = parse_envelope(result)
    print(json.dumps({"ok": envelope["ok"], "error_code": _error_code(envelope)}, ensure_ascii=False))


async def _run_batch(args, mode):
    items = _load_manifest(args.manifest, mode)  # Validate before opening MCP.
    output_dir = _prepare_output_dir(args.output_dir)
    env = build_server_env(args.profile, True, args.rate_limit)
    async with open_mcp_session(args.server_command, env) as session:
        async def call_tool(name, arguments):
            return await session.call_tool(name, arguments)
        summary = await execute_batch(items, mode, call_tool, output_dir, args.delay)
    print(json.dumps({field: summary[field] for field in SUMMARY_FIELDS}, ensure_ascii=False))
    return summary


def _finite_float(minimum):
    def parse(value):
        try:
            number = float(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("must be a finite number") from exc
        if not math.isfinite(number) or number < minimum:
            raise argparse.ArgumentTypeError(f"must be a finite number >= {minimum}")
        return number
    return parse


def _finite_rate_limit(minimum):
    validate = _finite_float(minimum)

    def parse(value):
        validate(value)
        return value
    return parse


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-command", default="stride28-search-mcp")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("list-tools", "login"):
        subparser = subparsers.add_parser(name)
        subparser.add_argument("--profile", required=True)
        subparser.add_argument("--rate-limit", type=_finite_rate_limit(12), default="12")
    for name, delay, rate_limit in (("search-batch", 12, 12), ("detail-batch", 20, 20)):
        subparser = subparsers.add_parser(name)
        subparser.add_argument("manifest")
        subparser.add_argument("--output-dir", required=True)
        subparser.add_argument("--profile", required=True)
        subparser.add_argument("--delay", type=_finite_float(delay), default=delay)
        subparser.add_argument("--rate-limit", type=_finite_rate_limit(rate_limit), default=str(rate_limit))
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


def _server_python_interpreter(server_command):
    executable = shutil.which(server_command)
    if executable is None:
        raise RuntimeError("MCP runtime is unavailable")
    try:
        first_line = Path(executable).read_bytes().splitlines()[0]
    except (OSError, IndexError):
        raise RuntimeError("MCP runtime is unavailable") from None
    if not first_line.startswith(b"#!"):
        raise RuntimeError("MCP runtime is unavailable")
    try:
        parts = first_line[2:].strip().decode("utf-8").split()
    except UnicodeDecodeError:
        raise RuntimeError("MCP runtime is unavailable") from None
    if len(parts) != 1:
        raise RuntimeError("MCP runtime is unavailable")
    interpreter = Path(parts[0])
    if (
        not interpreter.is_absolute()
        or not interpreter.is_file()
        or not os.access(interpreter, os.X_OK)
        or "python" not in interpreter.name.lower()
    ):
        raise RuntimeError("MCP runtime is unavailable")
    return str(interpreter)


def _bootstrap_mcp_runtime(server_command, argv):
    """Re-exec the CLI using the server's isolated Python when MCP is absent."""
    if importlib.util.find_spec("mcp") is not None:
        return False
    interpreter = _server_python_interpreter(server_command)
    os.execv(interpreter, [interpreter, str(Path(__file__).resolve()), *argv])
    return True


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    args = _parser().parse_args(argv)
    try:
        if _bootstrap_mcp_runtime(args.server_command, argv):
            return
    except RuntimeError:
        raise SystemExit("MCP runtime is unavailable") from None
    asyncio.run(_main_async(args))


if __name__ == "__main__":
    main()
