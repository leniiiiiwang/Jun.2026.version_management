import asyncio
import importlib.util
import json
import sys
import tempfile
from types import SimpleNamespace
import unittest
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
        for item in ({"note_id": ""}, {"note_id": "123", "max_comments": 0}):
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
    async def test_batch_cli_rejects_missing_items_before_opening_mcp(self):
        opened = False

        def unexpected_session(*args, **kwargs):
            nonlocal opened
            opened = True
            raise AssertionError("MCP must not open for an invalid manifest")

        original_session = xhs.mcp_session
        xhs.mcp_session = unexpected_session
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
            xhs.mcp_session = original_session
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
            self.assertEqual(summary, {"total": 2, "attempted": 2, "succeeded": 2, "timed_out": 0, "other_failures": 0, "stop_reason": None})
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
            self.assertEqual(summary["stop_reason"], None)

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
                    self.assertEqual(summary["stop_reason"], code)
                    self.assertTrue((output_dir / "first.json").exists())
                    self.assertFalse((output_dir / "second.json").exists())

    async def test_tool_exceptions_are_recorded_as_other_failures(self):
        async def call_tool(name, arguments):
            raise RuntimeError("connection lost")

        with tempfile.TemporaryDirectory() as directory:
            summary = await xhs.execute_batch(
                [{"key": "one", "note_id": "1"}], "detail", call_tool, Path(directory), 0,
            )
            record = json.loads((Path(directory) / "one.json").read_text(encoding="utf-8"))
            self.assertFalse(record["envelope"]["ok"])
            self.assertEqual(summary["other_failures"], 1)


if __name__ == "__main__":
    unittest.main()
