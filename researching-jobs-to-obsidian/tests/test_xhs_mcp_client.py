import asyncio
from contextlib import asynccontextmanager, redirect_stderr, redirect_stdout
import importlib.util
import io
import json
import os
import sys
import tempfile
from types import SimpleNamespace
from types import ModuleType
import unittest
from unittest.mock import patch
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "xhs_mcp_client.py"
SPEC = importlib.util.spec_from_file_location("xhs_mcp_client", MODULE_PATH)
xhs = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = xhs
SPEC.loader.exec_module(xhs)


class TextResult:
    def __init__(self, text):
        self.content = [type("Text", (), {"type": "text", "text": text})()]


class EnvTests(unittest.TestCase):
    def test_build_server_env_preserves_base_and_sets_only_contract_keys(self):
        base = {"PATH": "/bin", "UNRELATED": "keep"}
        env = xhs.build_server_env("codex", False, 12, base)

        self.assertEqual(env["PATH"], "/bin")
        self.assertEqual(env["UNRELATED"], "keep")
        self.assertEqual(
            {key: env[key] for key in env if key.startswith("STRIDE28_")},
            {
                "STRIDE28_SEARCH_MCP_PROFILE": "codex",
                "STRIDE28_XHS_HEADLESS": "false",
                "STRIDE28_RATE_LIMIT_SECONDS": "12",
            },
        )


class ArgumentTests(unittest.TestCase):
    def test_search_arguments_apply_defaults_and_validate_range(self):
        self.assertEqual(
            xhs.item_arguments("search", {"key": "q01", "query": "  蚂蚁  "}),
            {"query": "蚂蚁", "limit": 10, "note_type": "all"},
        )
        self.assertEqual(
            xhs.item_arguments("search", {"query": "x", "limit": 20, "note_type": "video"}),
            {"query": "x", "limit": 20, "note_type": "video"},
        )
        for item in ({"query": " "}, {"query": "x", "limit": 0}, {"query": "x", "limit": 21}):
            with self.subTest(item=item):
                with self.assertRaises(ValueError):
                    xhs.item_arguments("search", item)

    def test_detail_arguments_default_token_and_clamp_comments(self):
        self.assertEqual(
            xhs.item_arguments("detail", {"note_id": " 123 "}),
            {"note_id": "123", "xsec_token": "", "max_comments": 10},
        )
        self.assertEqual(
            xhs.item_arguments("detail", {"note_id": "123", "xsec_token": "t", "max_comments": 99}),
            {"note_id": "123", "xsec_token": "t", "max_comments": 50},
        )
        self.assertEqual(
            xhs.item_arguments("detail", {"note_id": "123", "max_comments": 0}),
            {"note_id": "123", "xsec_token": "", "max_comments": 0},
        )
        for item in ({"note_id": ""}, {"note_id": "123", "max_comments": -1}):
            with self.subTest(item=item):
                with self.assertRaises(ValueError):
                    xhs.item_arguments("detail", item)

    def test_item_arguments_rejects_unknown_mode(self):
        with self.assertRaises(ValueError):
            xhs.item_arguments("wrong", {})


class EnvelopeTests(unittest.TestCase):
    def test_parse_envelope_accepts_supported_result_shapes(self):
        envelope = {"ok": True, "data": {"id": "1"}}
        for result in (json.dumps(envelope), envelope, TextResult(json.dumps(envelope))):
            with self.subTest(result_type=type(result).__name__):
                self.assertEqual(xhs.parse_envelope(result), envelope)

    def test_parse_envelope_rejects_malformed_or_incomplete_data(self):
        for result in ("not json", "[]", {"ok": "true"}, {"data": 1}, TextResult("[]")):
            with self.subTest(result=repr(result)):
                with self.assertRaises(ValueError):
                    xhs.parse_envelope(result)


class ManifestTests(unittest.IsolatedAsyncioTestCase):
    async def test_manifest_rejects_malformed_json_and_missing_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            malformed = Path(directory) / "malformed.json"
            missing_mode = Path(directory) / "missing-mode.json"
            malformed.write_text("{not-json", encoding="utf-8")
            missing_mode.write_text(json.dumps({"items": []}), encoding="utf-8")
            with self.assertRaises(ValueError):
                xhs._load_manifest(malformed, "search")
            with self.assertRaises(ValueError):
                xhs._load_manifest(missing_mode, "search")

    async def test_batch_cli_rejects_missing_items_before_opening_mcp(self):
        opened = False

        def unexpected_session(*args, **kwargs):
            nonlocal opened
            opened = True
            raise AssertionError("MCP must not open for an invalid manifest")

        original_session = xhs.open_mcp_session
        xhs.open_mcp_session = unexpected_session
        try:
            with tempfile.TemporaryDirectory() as directory:
                manifest = Path(directory) / "manifest.json"
                manifest.write_text(json.dumps({"mode": "search"}), encoding="utf-8")
                args = SimpleNamespace(
                    manifest=str(manifest), output_dir=directory, profile="codex",
                    rate_limit="12", server_command="stride28-search-mcp", delay=12,
                )
                with self.assertRaises(ValueError):
                    await xhs._run_batch(args, "search")
        finally:
            xhs.open_mcp_session = original_session
        self.assertFalse(opened)

    async def test_batch_does_not_sleep_after_its_last_attempt(self):
        waits = []

        async def call_tool(name, arguments):
            return {"ok": True}

        async def sleeper(seconds):
            waits.append(seconds)

        with tempfile.TemporaryDirectory() as directory:
            await xhs.execute_batch(
                [{"key": "one", "query": "a"}], "search", call_tool, Path(directory), 8, sleeper,
            )
        self.assertEqual(waits, [])


class BatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_unsafe_keys_before_any_tool_call(self):
        calls = []

        async def call_tool(*args):
            calls.append(args)

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                await xhs.execute_batch(
                    [{"key": "safe", "query": "x"}, {"key": "../unsafe", "query": "y"}],
                    "search", call_tool, Path(directory), 0,
                )
        self.assertEqual(calls, [])

    async def test_executes_once_writes_records_summary_and_waits_between_items(self):
        calls, waits = [], []

        async def call_tool(name, arguments):
            calls.append((name, arguments))
            return {"ok": True, "data": {"query": arguments["query"]}}

        async def sleeper(seconds):
            waits.append(seconds)

        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            summary = await xhs.execute_batch(
                [{"key": "q01", "query": "a"}, {"key": "q02", "query": "b", "limit": 1}],
                "search", call_tool, output_dir, 12, sleeper,
            )
            self.assertEqual(calls, [
                ("search_xiaohongshu", {"query": "a", "limit": 10, "note_type": "all"}),
                ("search_xiaohongshu", {"query": "b", "limit": 1, "note_type": "all"}),
            ])
            self.assertEqual(waits, [12])
            self.assertEqual(summary, {"total": 2, "attempted": 2, "succeeded": 2, "timed_out": 0, "other_failures": 0, "stopped_on": None})
            record = json.loads((output_dir / "q01.json").read_text(encoding="utf-8"))
            self.assertEqual(record["key"], "q01")
            self.assertEqual(record["tool"], "search_xiaohongshu")
            self.assertEqual(record["arguments"]["query"], "a")
            self.assertTrue(record["envelope"]["ok"])
            self.assertEqual(json.loads((output_dir / "run_summary.json").read_text(encoding="utf-8")), summary)
            self.assertFalse(list(output_dir.glob("*.tmp")))

    async def test_search_timeout_is_written_without_retry_and_batch_continues(self):
        calls = []

        async def call_tool(name, arguments):
            calls.append(arguments["query"])
            return {"ok": False, "error": {"code": "search_timeout"}}

        with tempfile.TemporaryDirectory() as directory:
            summary = await xhs.execute_batch(
                [{"key": "first", "query": "a"}, {"key": "second", "query": "b"}],
                "search", call_tool, Path(directory), 0,
            )
            self.assertEqual(calls, ["a", "b"])
            self.assertEqual(summary["timed_out"], 2)
            self.assertEqual(summary["attempted"], 2)
            self.assertEqual(summary["stopped_on"], None)

    async def test_each_risk_code_hard_stops_after_writing_current_record(self):
        for code in xhs.RISK_CODES:
            with self.subTest(code=code):
                calls, waits = [], []

                async def call_tool(name, arguments):
                    calls.append(arguments["query"])
                    return {"ok": False, "error": {"code": code}}

                async def sleeper(seconds):
                    waits.append(seconds)

                with tempfile.TemporaryDirectory() as directory:
                    output_dir = Path(directory)
                    summary = await xhs.execute_batch(
                        [{"key": "first", "query": "a"}, {"key": "second", "query": "b"}],
                        "search", call_tool, output_dir, 5, sleeper,
                    )
                    self.assertEqual(calls, ["a"])
                    self.assertEqual(waits, [])
                    self.assertEqual(summary["attempted"], 1)
                    self.assertEqual(summary["other_failures"], 1)
                    self.assertEqual(summary["stopped_on"], code)
                    self.assertTrue((output_dir / "first.json").exists())
                    self.assertFalse((output_dir / "second.json").exists())

    async def test_tool_exceptions_are_recorded_as_other_failures(self):
        calls = []

        async def call_tool(name, arguments):
            calls.append(arguments["note_id"])
            raise RuntimeError("connection lost with token=secret")

        with tempfile.TemporaryDirectory() as directory:
            summary = await xhs.execute_batch(
                [{"key": "one", "note_id": "1"}], "detail", call_tool, Path(directory), 0,
            )
            record = json.loads((Path(directory) / "one.json").read_text(encoding="utf-8"))
            self.assertFalse(record["envelope"]["ok"])
            self.assertEqual(record["envelope"]["error"], {"code": "tool_call_failed", "message": "tool call failed"})
            self.assertEqual(summary["other_failures"], 1)
            self.assertEqual(summary["stopped_on"], "tool_call_failed")
        self.assertEqual(calls, ["1"])

    async def test_invalid_envelope_stops_without_calling_remaining_items(self):
        calls = []

        async def call_tool(name, arguments):
            calls.append(arguments["query"])
            return "not JSON"

        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            summary = await xhs.execute_batch(
                [{"key": "first", "query": "a"}, {"key": "second", "query": "b"}],
                "search", call_tool, output_dir, 0,
            )
            record = json.loads((output_dir / "first.json").read_text(encoding="utf-8"))
        self.assertEqual(calls, ["a"])
        self.assertEqual(summary["stopped_on"], "invalid_envelope")
        self.assertEqual(record["envelope"]["error"], {"code": "invalid_envelope", "message": "invalid tool envelope"})

    async def test_detail_output_uses_detail_tool_and_contract_shape(self):
        async def call_tool(name, arguments):
            self.assertEqual(name, "get_note_detail")
            self.assertEqual(arguments, {"note_id": "123", "xsec_token": "t", "max_comments": 0})
            return {"ok": True, "data": {"title": "note"}}

        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            await xhs.execute_batch(
                [{"key": "note-123", "note_id": "123", "xsec_token": "t", "max_comments": 0}],
                "detail", call_tool, output_dir, 0,
            )
            record = json.loads((output_dir / "note-123.json").read_text(encoding="utf-8"))
        self.assertEqual(
            record,
            {
                "key": "note-123", "tool": "get_note_detail",
                "arguments": {"note_id": "123", "xsec_token": "t", "max_comments": 0},
                "envelope": {"ok": True, "data": {"title": "note"}},
            },
        )


class McpBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_open_mcp_session_imports_lazily_and_initializes_once(self):
        events = []
        fake_mcp = ModuleType("mcp")
        fake_client = ModuleType("mcp.client")
        fake_client.__path__ = []
        fake_stdio = ModuleType("mcp.client.stdio")

        class Parameters:
            def __init__(self, **kwargs):
                events.append(("parameters", kwargs))

        class Session:
            def __init__(self, read_stream, write_stream):
                events.append(("session", read_stream, write_stream))

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                events.append(("session_close",))

            async def initialize(self):
                events.append(("initialize",))

        @asynccontextmanager
        async def stdio_client(parameters):
            events.append(("stdio", parameters))
            yield ("read", "write")

        fake_mcp.ClientSession = Session
        fake_mcp.StdioServerParameters = Parameters
        fake_stdio.stdio_client = stdio_client
        saved = {name: sys.modules.get(name) for name in ("mcp", "mcp.client", "mcp.client.stdio")}
        for name in saved:
            sys.modules.pop(name, None)
        self.assertNotIn("mcp", sys.modules)
        try:
            sys.modules.update({"mcp": fake_mcp, "mcp.client": fake_client, "mcp.client.stdio": fake_stdio})
            async with xhs.open_mcp_session("fake-server", {"PATH": "/bin"}) as session:
                self.assertIsInstance(session, Session)
        finally:
            for name, module in saved.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module
        self.assertEqual(events[0], ("parameters", {"command": "fake-server", "args": [], "env": {"PATH": "/bin"}}))
        self.assertEqual(events.count(("initialize",)), 1)


class CliTests(unittest.IsolatedAsyncioTestCase):
    def test_cli_profiles_are_trimmed_and_reject_blank_or_unsafe_values_before_bootstrap(self):
        parser = xhs._parser()
        self.assertEqual(parser.parse_args(["list-tools", "--profile", " codex "]).profile, "codex")
        for profile in ("", "   ", "shared/default", "has space", ".."):
            with self.subTest(profile=profile), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    parser.parse_args(["list-tools", "--profile", profile])

        with patch.object(xhs, "_bootstrap_mcp_runtime") as bootstrap, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                xhs.main(["list-tools", "--profile", "unsafe/profile"])
        bootstrap.assert_not_called()

    async def test_all_cli_subcommands_apply_their_headless_rate_and_delay_contracts(self):
        opened, calls = [], []

        class Session:
            async def list_tools(self):
                calls.append(("list_tools",))
                return {"tools": [{"name": "search_xiaohongshu"}]}

            async def call_tool(self, name, arguments):
                calls.append((name, arguments))
                return {"ok": True, "data": {}}

        @asynccontextmanager
        async def fake_open(command, env):
            opened.append((command, env))
            yield Session()

        original_open = xhs.open_mcp_session
        xhs.open_mcp_session = fake_open
        try:
            with tempfile.TemporaryDirectory() as directory:
                directory_path = Path(directory)
                search_manifest = directory_path / "search.json"
                detail_manifest = directory_path / "detail.json"
                search_manifest.write_text(json.dumps({"mode": "search", "items": [{"key": "q1", "query": "a"}, {"key": "q2", "query": "b"}]}), encoding="utf-8")
                detail_manifest.write_text(json.dumps({"mode": "detail", "items": [{"key": "n1", "note_id": "1"}, {"key": "n2", "note_id": "2"}]}), encoding="utf-8")
                parser = xhs._parser()
                commands = [
                    ["list-tools", "--profile", "codex"],
                    ["login", "--profile", "codex"],
                    ["search-batch", str(search_manifest), "--output-dir", str(directory_path / "search-out"), "--profile", "codex"],
                    ["detail-batch", str(detail_manifest), "--output-dir", str(directory_path / "detail-out"), "--profile", "codex"],
                ]
                parsed = [parser.parse_args(command) for command in commands]
                self.assertEqual([args.delay for args in parsed[2:]], [12, 20])
                self.assertEqual([args.rate_limit for args in parsed], ["12", "12", "12", "20"])
                self.assertEqual([args.server_command for args in parsed], ["stride28-search-mcp"] * 4)
                for args in parsed[2:]:
                    args.delay = 0
                output = io.StringIO()
                with redirect_stdout(output):
                    for args in parsed:
                        await xhs._main_async(args)
        finally:
            xhs.open_mcp_session = original_open
        self.assertEqual([env["STRIDE28_XHS_HEADLESS"] for _, env in opened], ["true", "false", "true", "true"])
        self.assertEqual([env["STRIDE28_RATE_LIMIT_SECONDS"] for _, env in opened], ["12", "12", "12", "20"])
        self.assertEqual(len(opened), 4)
        self.assertEqual(calls.count(("list_tools",)), 1)
        self.assertEqual(calls.count(("login_xiaohongshu", {})), 1)
        self.assertEqual(sum(name == "search_xiaohongshu" for name, *_ in calls if name != "list_tools"), 2)
        self.assertEqual(sum(name == "get_note_detail" for name, *_ in calls if name != "list_tools"), 2)

    async def test_login_prints_only_allowlisted_result_fields(self):
        @asynccontextmanager
        async def fake_open(command, env):
            class Session:
                async def call_tool(self, name, arguments):
                    return {"ok": False, "error": {"code": "not_logged_in", "secret": "do-not-print"}, "data": {"secret": "no"}}
            yield Session()

        original_open = xhs.open_mcp_session
        xhs.open_mcp_session = fake_open
        try:
            output = io.StringIO()
            with redirect_stdout(output):
                await xhs._run_login(SimpleNamespace(profile="codex", rate_limit="12", server_command="fake"))
        finally:
            xhs.open_mcp_session = original_open
        self.assertEqual(json.loads(output.getvalue()), {"ok": False, "error_code": "not_logged_in"})

    def test_cli_rejects_nonfinite_or_below_minimum_timings_before_runtime(self):
        parser = xhs._parser()
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "manifest.json"
            manifest.write_text(json.dumps({"mode": "search", "items": []}), encoding="utf-8")
            base = ["search-batch", str(manifest), "--output-dir", str(Path(directory) / "out"), "--profile", "codex"]
            for option in ("--delay", "--rate-limit"):
                for value in ("0", "-1", "nan", "inf", "not-a-number"):
                    with self.subTest(option=option, value=value):
                        with redirect_stderr(io.StringIO()):
                            with self.assertRaises(SystemExit):
                                parser.parse_args(base + [option, value])
            detail = ["detail-batch", str(manifest), "--output-dir", str(Path(directory) / "detail-out"), "--profile", "codex"]
            for option in ("--delay", "--rate-limit"):
                with self.subTest(option=option, detail=True):
                    with redirect_stderr(io.StringIO()):
                        with self.assertRaises(SystemExit):
                            parser.parse_args(detail + [option, "12"])

    async def test_batch_prints_only_allowlisted_summary_fields(self):
        @asynccontextmanager
        async def fake_open(command, env):
            class Session:
                async def call_tool(self, name, arguments):
                    return {"ok": False, "error": {"code": "search_blocked", "secret": "no"}}
            yield Session()

        original_open = xhs.open_mcp_session
        xhs.open_mcp_session = fake_open
        try:
            with tempfile.TemporaryDirectory() as directory:
                manifest = Path(directory) / "manifest.json"
                output_dir = Path(directory) / "out"
                manifest.write_text(json.dumps({"mode": "search", "items": [{"key": "q", "query": "a"}]}), encoding="utf-8")
                args = SimpleNamespace(manifest=str(manifest), output_dir=str(output_dir), profile="codex", rate_limit="12", server_command="fake", delay=12)
                output = io.StringIO()
                with redirect_stdout(output):
                    await xhs._run_batch(args, "search")
        finally:
            xhs.open_mcp_session = original_open
        self.assertEqual(
            json.loads(output.getvalue()),
            {"total": 1, "attempted": 1, "succeeded": 0, "timed_out": 0, "other_failures": 1, "stopped_on": "search_blocked"},
        )


class OutputSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_writable_probe_has_no_residue_and_probe_failure_stops_before_session_or_tool(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "empty-output"
            prepared = xhs._prepare_output_dir(output_dir)
            self.assertEqual(prepared, output_dir.resolve())
            self.assertEqual(list(prepared.iterdir()), [])

        calls, opened = [], False

        async def call_tool(*args):
            calls.append(args)

        def unexpected_session(*args, **kwargs):
            nonlocal opened
            opened = True
            raise AssertionError("session must not open when write probe fails")

        original_open = xhs.open_mcp_session
        xhs.open_mcp_session = unexpected_session
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                manifest = root / "manifest.json"
                output_dir = root / "out"
                manifest.write_text(json.dumps({"mode": "search", "items": [{"key": "q", "query": "a"}]}), encoding="utf-8")
                args = SimpleNamespace(manifest=str(manifest), output_dir=str(output_dir), profile="codex", rate_limit="12", server_command="fake", delay=12)
                with patch.object(xhs.tempfile, "mkstemp", side_effect=PermissionError("denied")):
                    with self.assertRaises(ValueError):
                        await xhs.execute_batch([{"key": "q", "query": "a"}], "search", call_tool, output_dir, 0)
                    with self.assertRaises(ValueError):
                        await xhs._run_batch(args, "search")
                self.assertTrue(output_dir.exists())
                self.assertEqual(list(output_dir.iterdir()), [])
        finally:
            xhs.open_mcp_session = original_open
        self.assertEqual(calls, [])
        self.assertFalse(opened)

    async def test_rejects_duplicate_casefold_and_reserved_keys_before_calling_tool(self):
        cases = [
            [{"key": "same", "query": "a"}, {"key": "same", "query": "b"}],
            [{"key": "Case", "query": "a"}, {"key": "case", "query": "b"}],
            [{"key": "run_summary", "query": "a"}],
        ]
        for items in cases:
            with self.subTest(items=items):
                calls = []

                async def call_tool(*args):
                    calls.append(args)

                with tempfile.TemporaryDirectory() as directory:
                    with self.assertRaises(ValueError):
                        await xhs.execute_batch(items, "search", call_tool, Path(directory) / "out", 0)
                self.assertEqual(calls, [])

    async def test_rejects_file_or_nonempty_output_directory_before_calling_tool(self):
        for is_file in (True, False):
            with self.subTest(is_file=is_file):
                calls = []

                async def call_tool(*args):
                    calls.append(args)

                with tempfile.TemporaryDirectory() as directory:
                    output_dir = Path(directory) / "out"
                    if is_file:
                        output_dir.write_text("not a directory", encoding="utf-8")
                    else:
                        output_dir.mkdir()
                        (output_dir / "existing.json").write_text("{}", encoding="utf-8")
                    with self.assertRaises(ValueError):
                        await xhs.execute_batch([{"key": "q", "query": "a"}], "search", call_tool, output_dir, 0)
                self.assertEqual(calls, [])

    async def test_batch_cli_preflights_output_before_opening_session(self):
        opened = False

        def unexpected_session(*args, **kwargs):
            nonlocal opened
            opened = True
            raise AssertionError("session must not open for unsafe output")

        original_open = xhs.open_mcp_session
        xhs.open_mcp_session = unexpected_session
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                manifest = root / "manifest.json"
                output_dir = root / "out"
                manifest.write_text(json.dumps({"mode": "search", "items": [{"key": "q", "query": "a"}]}), encoding="utf-8")
                output_dir.mkdir()
                (output_dir / "existing.json").write_text("{}", encoding="utf-8")
                args = SimpleNamespace(manifest=str(manifest), output_dir=str(output_dir), profile="codex", rate_limit="12", server_command="fake", delay=12)
                with self.assertRaises(ValueError):
                    await xhs._run_batch(args, "search")
        finally:
            xhs.open_mcp_session = original_open
        self.assertFalse(opened)

    async def test_atomic_writer_overwrites_without_temp_residue_and_cleans_up_after_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "record.json"
            xhs._write_json_atomically(target, {"version": 1})
            xhs._write_json_atomically(target, {"version": 2})
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"version": 2})
            self.assertFalse(list(Path(directory).glob(".*.tmp")))
            with patch.object(xhs.os, "replace", side_effect=OSError("replace failed")):
                with self.assertRaises(OSError):
                    xhs._write_json_atomically(target, {"version": 3})
            self.assertEqual([path.name for path in Path(directory).iterdir()], ["record.json"])


class BootstrapTests(unittest.TestCase):
    def test_bootstrap_reexecs_with_server_shebang_interpreter_when_mcp_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            server = Path(directory) / "stride28-search-mcp"
            server.write_text(f"#!{sys.executable}\n", encoding="utf-8")
            executed = []
            with patch.object(xhs.importlib.util, "find_spec", return_value=None), patch.object(xhs.shutil, "which", return_value=str(server)), patch.object(xhs.os, "execv", side_effect=lambda *args: executed.append(args)):
                self.assertTrue(xhs._bootstrap_mcp_runtime("stride28-search-mcp", ["list-tools", "--profile", "codex"]))
        self.assertEqual(executed, [(sys.executable, [sys.executable, str(MODULE_PATH.resolve()), "list-tools", "--profile", "codex"])])

    def test_bootstrap_rejects_missing_or_unsafe_server_interpreter_without_path_leakage(self):
        with tempfile.TemporaryDirectory() as directory:
            unsafe = Path(directory) / "unsafe"
            unsafe.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            for command, located in (("missing", None), ("unsafe", str(unsafe))):
                with self.subTest(command=command), patch.object(xhs.importlib.util, "find_spec", return_value=None), patch.object(xhs.shutil, "which", return_value=located):
                    with self.assertRaises(RuntimeError) as caught:
                        xhs._bootstrap_mcp_runtime(command, ["list-tools", "--profile", "codex"])
                self.assertNotIn(directory, str(caught.exception))

    def test_bootstrap_does_not_reexec_when_mcp_is_already_available(self):
        with patch.object(xhs.importlib.util, "find_spec", return_value=object()), patch.object(xhs.os, "execv") as execv:
            self.assertFalse(xhs._bootstrap_mcp_runtime("stride28-search-mcp", ["list-tools", "--profile", "codex"]))
        execv.assert_not_called()

    def test_main_redacts_bootstrap_errors(self):
        with patch.object(xhs, "_bootstrap_mcp_runtime", side_effect=RuntimeError("/private/profile/path")):
            with self.assertRaises(SystemExit) as caught:
                xhs.main(["list-tools", "--profile", "codex"])
        self.assertEqual(str(caught.exception), "MCP runtime is unavailable")


@unittest.skipUnless(os.environ.get("RUN_STRIDE28_MCP_SMOKE") == "1", "set RUN_STRIDE28_MCP_SMOKE=1 to run real MCP smoke")
class Stride28SmokeTests(unittest.TestCase):
    def test_ordinary_python_list_tools_returns_json_names(self):
        import subprocess

        completed = subprocess.run(
            [sys.executable, str(MODULE_PATH), "list-tools", "--profile", "codex"],
            text=True, capture_output=True, timeout=60, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        names = json.loads(completed.stdout)
        self.assertTrue(names)
        self.assertTrue(all(isinstance(name, str) for name in names))


if __name__ == "__main__":
    unittest.main()
