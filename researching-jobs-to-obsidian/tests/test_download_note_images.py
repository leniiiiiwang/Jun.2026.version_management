import asyncio
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "download_note_images.py"
SPEC = importlib.util.spec_from_file_location("download_note_images", MODULE_PATH)
downloader = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = downloader
SPEC.loader.exec_module(downloader)


def detail(key, urls=None, *, ok=True, data_id="default", tool="get_note_detail", data=None):
    if data is None:
        data = None if not ok else {"id": key if data_id == "default" else data_id, "image_urls": urls}
    return {
        "key": key,
        "tool": tool,
        "arguments": {"note_id": key},
        "envelope": {"ok": ok, "data": data},
    }


def write_detail(directory, name, record):
    (directory / name).write_text(json.dumps(record), encoding="utf-8")


class CollectJobsTests(unittest.TestCase):
    def test_collects_only_selected_successful_details_and_deduplicates_urls(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            write_detail(directory, "one.json", detail("one", [
                "https://cdn.test/a.jpg", "https://cdn.test/a.jpg", "ftp://cdn.test/nope",
                "https://cdn.test/b.png",
            ]))
            write_detail(directory, "two.json", detail("two", ["https://cdn.test/two.jpg"]))
            write_detail(directory, "timeout.json", detail("timeout", ok=False))
            (directory / "run_summary.json").write_text("{not JSON", encoding="utf-8")

            self.assertEqual(
                downloader.collect_jobs(directory, {"one"}),
                [
                    {"note_id": "one", "source_url": "https://cdn.test/a.jpg"},
                    {"note_id": "one", "source_url": "https://cdn.test/b.png"},
                ],
            )

    def test_uses_record_key_when_data_id_is_absent_and_never_uses_data_key(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            write_detail(directory, "safe-key.json", detail(
                "safe-key", data={"key": "not-allowlisted", "image_urls": ["https://cdn.test/a"]},
            ))
            self.assertEqual(
                downloader.collect_jobs(directory, {"safe-key"}),
                [{"note_id": "safe-key", "source_url": "https://cdn.test/a"}],
            )

    def test_rejects_mismatched_unsafe_reserved_and_non_detail_records(self):
        cases = [
            ("wrong-stem.json", detail("right", ["https://cdn.test/a"])),
            ("unsafe stem.json", detail("unsafe stem", ok=False)),
            ("download_manifest.json", detail("download_manifest", ok=False)),
            ("tool.json", detail("tool", ["https://cdn.test/a"], tool="search_xiaohongshu")),
            ("conflict.json", detail("conflict", ["https://cdn.test/a"], data_id="other")),
        ]
        for filename, record in cases:
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as raw:
                directory = Path(raw)
                write_detail(directory, filename, record)
                with self.assertRaises(ValueError):
                    downloader.collect_jobs(directory, {"right", "tool", "conflict"})

    def test_skips_realistic_timeout_before_touching_images(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            write_detail(directory, "one.json", detail("one", ["https://cdn.test/a.jpg"]))
            timeout = detail("timeout", ok=False)
            timeout["envelope"] = {"ok": False, "data": None, "error": {"code": "search_timeout"}}
            write_detail(directory, "timeout.json", timeout)
            write_detail(directory, "unselected.json", detail(
                "unselected", data={"id": "unselected", "image_urls": {"not": "a list"}},
            ))
            self.assertEqual(
                downloader.collect_jobs(directory, {"one", "timeout"}),
                [{"note_id": "one", "source_url": "https://cdn.test/a.jpg"}],
            )

    def test_rejects_malformed_detail_json_with_filename_only(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            (directory / "broken.json").write_text("{bad", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, r"^malformed detail file: broken\.json$"):
                downloader.collect_jobs(directory, {"one"})


class DownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_downloads_in_order_waiting_only_between_attempted_jobs(self):
        calls, waits = [], []

        async def fetcher(url):
            calls.append(url)
            return (b"image", "image/png")

        async def sleeper(seconds):
            waits.append(seconds)

        jobs = [
            {"note_id": "one", "source_url": "https://cdn.test/one"},
            {"note_id": "two", "source_url": "https://cdn.test/two.jpeg"},
        ]
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw) / "images"
            summary = await downloader.download_jobs(jobs, output, 2, fetcher, sleeper)

            self.assertEqual(calls, [job["source_url"] for job in jobs])
            self.assertEqual(waits, [2])
            self.assertEqual(summary["attempted"], 2)
            self.assertEqual(sorted(item.name for item in output.iterdir()), [
                "download_manifest.json", "one-01.png", "two-01.jpeg",
            ])

    async def test_uses_url_suffix_then_content_type_then_jpg_and_writes_exact_manifest(self):
        async def fetcher(url):
            values = {
                "https://cdn.test/path/photo.webp?token=secret": (b"webp", "image/jpeg"),
                "https://cdn.test/path/no-extension": (b"gif", "image/gif; charset=binary"),
                "https://cdn.test/path/unknown": (b"unknown", "application/octet-stream"),
            }
            return values[url]

        jobs = [
            {"note_id": "note", "source_url": "https://cdn.test/path/photo.webp?token=secret"},
            {"note_id": "note", "source_url": "https://cdn.test/path/no-extension"},
            {"note_id": "other", "source_url": "https://cdn.test/path/unknown"},
        ]
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw) / "images"
            summary = await downloader.download_jobs(jobs, output, 0, fetcher)
            self.assertEqual(summary, {
                "total": 3, "attempted": 3, "succeeded": 3, "failed": 0,
                "records": [
                    {"note_id": "note", "source_url": jobs[0]["source_url"], "output_file": "note-01.webp", "status": "succeeded", "error": None},
                    {"note_id": "note", "source_url": jobs[1]["source_url"], "output_file": "note-02.gif", "status": "succeeded", "error": None},
                    {"note_id": "other", "source_url": jobs[2]["source_url"], "output_file": "other-01.jpg", "status": "succeeded", "error": None},
                ],
            })
            self.assertEqual(json.loads((output / "download_manifest.json").read_text(encoding="utf-8")), summary)

    async def test_fetch_failure_continues_without_leaking_query_tokens(self):
        calls = []

        async def fetcher(url):
            calls.append(url)
            if "bad" in url:
                raise RuntimeError("request failed token=very-secret")
            return b"ok", "image/jpeg"

        jobs = [
            {"note_id": "one", "source_url": "https://cdn.test/bad?token=very-secret"},
            {"note_id": "two", "source_url": "https://cdn.test/ok?token=also-secret"},
        ]
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw) / "images"
            captured = io.StringIO()
            with redirect_stdout(captured):
                summary = await downloader.download_jobs(jobs, output, 0, fetcher)
            self.assertEqual(calls, [job["source_url"] for job in jobs])
            self.assertEqual(summary["failed"], 1)
            self.assertEqual(summary["succeeded"], 1)
            failed = summary["records"][0]
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(failed["output_file"], None)
            self.assertNotIn("very-secret", failed["error"])
            self.assertNotIn("also-secret", captured.getvalue())

    async def test_preflight_rejects_bad_destinations_before_fetch(self):
        jobs = [{"note_id": "one", "source_url": "https://cdn.test/a.jpg"}]
        for kind in ("file", "nonempty", "symlink", "unwritable"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                output = root / "images"
                if kind == "file":
                    output.write_text("not a directory", encoding="utf-8")
                elif kind == "nonempty":
                    output.mkdir()
                    (output / "old.txt").write_text("old", encoding="utf-8")
                elif kind == "symlink":
                    target = root / "target"
                    target.mkdir()
                    output.symlink_to(target, target_is_directory=True)
                else:
                    output.mkdir()

                calls = []

                async def fetcher(url):
                    calls.append(url)
                    return b"bad", "image/jpeg"

                if kind == "unwritable":
                    with patch.object(downloader.tempfile, "mkstemp", side_effect=PermissionError("denied")):
                        with self.assertRaises(ValueError):
                            await downloader.download_jobs(jobs, output, 0, fetcher)
                else:
                    with self.assertRaises(ValueError):
                        await downloader.download_jobs(jobs, output, 0, fetcher)
                self.assertEqual(calls, [])

    async def test_atomic_write_removes_temporary_file_when_replace_fails(self):
        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / "image.jpg"
            with patch.object(downloader.os, "replace", side_effect=OSError("nope")):
                with self.assertRaises(OSError):
                    downloader._write_bytes_atomically(target, b"image")
            self.assertFalse(list(Path(raw).glob(".*.tmp")))

    async def test_rejects_casefold_colliding_note_ids_before_dynamic_extension_fetches(self):
        calls = []

        async def fetcher(url):
            calls.append(url)
            return b"image", "image/png"

        jobs = [
            {"note_id": "A", "source_url": "https://cdn.test/no-extension-one"},
            {"note_id": "a", "source_url": "https://cdn.test/no-extension-two"},
        ]
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaises(ValueError):
                await downloader.download_jobs(jobs, Path(raw) / "images", 0, fetcher)
        self.assertEqual(calls, [])

    async def test_does_not_overwrite_a_final_dynamic_extension_collision(self):
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw) / "images"

            async def fetcher(url):
                output.mkdir(exist_ok=True)
                (output / "one-01.png").write_bytes(b"foreign")
                return b"image", "image/png"

            summary = await downloader.download_jobs(
                [{"note_id": "one", "source_url": "https://cdn.test/no-extension"}], output, 0, fetcher,
            )
            self.assertEqual((output / "one-01.png").read_bytes(), b"foreign")
            self.assertEqual(summary["failed"], 1)
            self.assertEqual(summary["records"][0]["output_file"], None)

    async def test_preflight_reserves_download_manifest_note_id_before_fetch(self):
        calls = []

        async def fetcher(url):
            calls.append(url)
            return b"image", "image/jpeg"

        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaises(ValueError):
                await downloader.download_jobs(
                    [{"note_id": "download_manifest", "source_url": "https://cdn.test/image"}],
                    Path(raw) / "images", 0, fetcher,
                )
        self.assertEqual(calls, [])


class CliTests(unittest.TestCase):
    def test_selected_id_file_rejects_duplicates_case_collisions_and_unsafe_ids(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "ids.txt"
            for content in ("one\\none\\n", "one\\nONE\\n", "../one\\n", "download_manifest\\n"):
                path.write_text(content, encoding="utf-8")
                with self.subTest(content=content):
                    with self.assertRaises(ValueError):
                        downloader.load_selected_ids(path)

    def test_cli_defaults_and_accepts_two_second_delay(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            details = root / "details"
            details.mkdir()
            write_detail(details, "one.json", detail("one", ["https://cdn.test/a.jpg"]))
            ids = root / "ids.txt"
            ids.write_text("one\n", encoding="utf-8")
            calls = []

            async def fetcher(url):
                calls.append(url)
                return b"image", "image/jpeg"

            with patch.object(downloader, "default_fetcher", fetcher):
                default_summary = downloader.main([
                    "--details-dir", str(details), "--selected-ids", str(ids),
                    "--output-dir", str(root / "images-default"),
                ])
                explicit_summary = downloader.main([
                    "--details-dir", str(details), "--selected-ids", str(ids),
                    "--output-dir", str(root / "images-explicit"), "--delay", "2",
                ])
            self.assertEqual(calls, ["https://cdn.test/a.jpg", "https://cdn.test/a.jpg"])
            self.assertEqual(default_summary["succeeded"], 1)
            self.assertEqual(explicit_summary["succeeded"], 1)

    def test_cli_rejects_invalid_delay_before_fetch(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            details = root / "details"
            details.mkdir()
            write_detail(details, "one.json", detail("one", ["https://cdn.test/a.jpg"]))
            ids = root / "ids.txt"
            ids.write_text("one\\n", encoding="utf-8")
            for delay in ("1", "0", "-1", "nan", "inf", "not-a-number"):
                with self.subTest(delay=delay), patch.object(downloader, "default_fetcher", side_effect=AssertionError("no fetch")):
                    with self.assertRaises(ValueError):
                        downloader.main([
                            "--details-dir", str(details), "--selected-ids", str(ids),
                            "--output-dir", str(root / f"images-{delay}"), "--delay", delay,
                        ])
