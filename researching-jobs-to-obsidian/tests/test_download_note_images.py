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


def detail(note_id, urls, *, ok=True, data_key="id"):
    data = {data_key: note_id, "image_urls": urls}
    return {
        "key": f"detail-{note_id}",
        "tool": "get_note_detail",
        "arguments": {"note_id": note_id},
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
            write_detail(directory, "bad.json", detail("one", ["https://cdn.test/bad.jpg"], ok=False))
            (directory / "run_summary.json").write_text("{not JSON", encoding="utf-8")

            self.assertEqual(
                downloader.collect_jobs(directory, {"one"}),
                [
                    {"note_id": "one", "source_url": "https://cdn.test/a.jpg"},
                    {"note_id": "one", "source_url": "https://cdn.test/b.png"},
                ],
            )

    def test_accepts_allowlisted_data_key_and_rejects_unsafe_note_id(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            write_detail(directory, "key.json", detail("safe-key", ["https://cdn.test/a"], data_key="key"))
            self.assertEqual(
                downloader.collect_jobs(directory, {"safe-key"}),
                [{"note_id": "safe-key", "source_url": "https://cdn.test/a"}],
            )
            write_detail(directory, "unsafe.json", detail("../unsafe", ["https://cdn.test/a"]))
            with self.assertRaises(ValueError):
                downloader.collect_jobs(directory, {"safe-key", "../unsafe"})

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


class CliTests(unittest.TestCase):
    def test_selected_id_file_rejects_duplicates_case_collisions_and_unsafe_ids(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "ids.txt"
            for content in ("one\\none\\n", "one\\nONE\\n", "../one\\n"):
                path.write_text(content, encoding="utf-8")
                with self.subTest(content=content):
                    with self.assertRaises(ValueError):
                        downloader.load_selected_ids(path)

    def test_cli_rejects_non_positive_and_nonfinite_delay_before_fetch(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            details = root / "details"
            details.mkdir()
            write_detail(details, "one.json", detail("one", ["https://cdn.test/a.jpg"]))
            ids = root / "ids.txt"
            ids.write_text("one\\n", encoding="utf-8")
            for delay in ("0", "-1", "nan", "inf"):
                with self.subTest(delay=delay), patch.object(downloader, "default_fetcher", side_effect=AssertionError("no fetch")):
                    with self.assertRaises(ValueError):
                        downloader.main([
                            "--details-dir", str(details), "--selected-ids", str(ids),
                            "--output-dir", str(root / f"images-{delay}"), "--delay", delay,
                        ])
