import json
import os
import platform
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts" / "vision_ocr.m"


@unittest.skipUnless(platform.system() == "Darwin", "Vision OCR is macOS-only")
class VisionOCRCompileContractTest(unittest.TestCase):
    def compile_binary(self, directory):
        binary = directory / "vision_ocr"
        result = subprocess.run(
            [
                "xcrun", "clang", "-fobjc-arc", "-framework", "Foundation",
                "-framework", "AppKit", "-framework", "Vision", str(SOURCE),
                "-o", str(binary),
            ],
            text=True,
            capture_output=True,
            env={**os.environ, "CLANG_MODULE_CACHE_PATH": str(directory / "module-cache")},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return binary

    def test_source_compiles_with_macos_vision_frameworks(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            self.compile_binary(directory)

    def test_cli_without_images_prints_usage_and_fails(self):
        with tempfile.TemporaryDirectory() as raw:
            binary = self.compile_binary(Path(raw))
            result = subprocess.run([str(binary)], text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("usage: vision_ocr", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_missing_image_is_a_sanitized_json_failure(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            binary = self.compile_binary(directory)
            missing = directory / "not-here-token-secret.png"
            result = subprocess.run([str(binary), str(missing)], text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        records = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(records, [{
            "path": str(missing), "ok": False, "text": "", "observations": [],
            "error": "image_load_failed",
        }])

    def test_each_requested_image_has_one_json_line(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            binary = self.compile_binary(directory)
            paths = [directory / "missing-one.png", directory / "missing-two.png"]
            result = subprocess.run([str(binary), *(str(path) for path in paths)], text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        records = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(records), 2)
        self.assertEqual([record["path"] for record in records], [str(path) for path in paths])

    @unittest.skipUnless(os.environ.get("RUN_VISION_OCR_SMOKE") == "1", "set RUN_VISION_OCR_SMOKE=1 to opt in")
    def test_self_test_recognizes_interview_and_removes_generated_artifacts(self):
        with tempfile.TemporaryDirectory() as raw:
            binary = self.compile_binary(Path(raw))
            result = subprocess.run([str(binary), "--self-test"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        records = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0]["ok"])
        self.assertIn("INTERVIEW", records[0]["text"].upper())
        self.assertFalse(Path(records[0]["path"]).exists())
        self.assertFalse(Path(records[0]["path"]).parent.exists())


if __name__ == "__main__":
    unittest.main()
