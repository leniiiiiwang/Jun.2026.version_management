import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "validate_obsidian.py"
SPEC = importlib.util.spec_from_file_location("validate_obsidian", MODULE_PATH)
validator = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = validator
SPEC.loader.exec_module(validator)


def valid_brief():
    sections = {
        "一页结论": "这是一份基于公开样本的结论，不代表公司整体。[^a]",
        "岗位画像": "围绕内容策略与用户需求协作。[^b]",
        "入职门槛": "学历要求仅是竞争信号的观察，不是官方硬性门槛。",
        "面试流程与题库": "流程来自公开经验分享。",
        "薪资待遇": "薪资为非官方样本，口径可能不一致，不能推断目标岗位或公司整体。",
        "工作体验": "工作体验存在个体差异。",
        "准备建议": "准备案例并核对原始来源。",
        "证据边界": "公开内容有选择偏差。",
        "检索统计": "检索了两个来源。",
        "来源索引": "来源见脚注。",
    }
    body = "\n\n".join(f"## {heading}\n{text}" for heading, text in sections.items())
    return "---\ntitle: 岗位调研\ntags:\n  - 求职\n---\n\n" + body + (
        "\n\n[^a]: https://example.com/a#sample\n[^b]: https://example.org/b"
    )


class ValidateTextTests(unittest.TestCase):
    def test_valid_brief_passes(self):
        self.assertEqual(validator.validate_text(valid_brief()), [])

    def test_normalizes_crlf(self):
        self.assertEqual(validator.validate_text(valid_brief().replace("\n", "\r\n")), [])

    def test_allows_relative_links_and_horizontal_rules_outside_frontmatter(self):
        text = valid_brief() + "\n\n---\n\n[relative](notes/company.md)\n![[attachment.png]]"
        self.assertEqual(validator.validate_text(text), [])

    def test_allows_additional_level_two_headings(self):
        text = valid_brief() + "\n\n## 附录\n补充方法说明。"
        self.assertEqual(validator.validate_text(text), [])

    def test_allows_required_headings_with_up_to_three_leading_spaces(self):
        text = valid_brief().replace("\n## ", "\n   ## ")
        self.assertEqual(validator.validate_text(text), [])

    def test_does_not_treat_four_space_indented_heading_as_required_heading(self):
        text = valid_brief().replace("\n## 薪资待遇\n", "\n    ## 薪资待遇\n")
        self.assertIn("document: missing heading: 薪资待遇", validator.validate_text(text))

    def test_rejects_empty_or_unterminated_frontmatter(self):
        for text, expected in (
            (valid_brief().replace("title: 岗位调研\ntags:\n  - 求职", "", 1), "frontmatter"),
            (valid_brief().replace("\n---\n\n## 一页结论", "\n## 一页结论", 1), "frontmatter"),
        ):
            with self.subTest(expected=expected):
                self.assertTrue(any(expected in error for error in validator.validate_text(text)))

    def test_rejects_unresolved_variables_in_frontmatter(self):
        for assignment in ("company: {{company}}", "role: {{role}}"):
            with self.subTest(assignment=assignment):
                text = valid_brief().replace("title: 岗位调研", assignment)
                self.assertIn("document: unresolved variable", validator.validate_text(text))

    def test_rejects_each_merge_conflict_marker_in_frontmatter(self):
        for marker in ("<<<<<<< HEAD", "=======", ">>>>>>> branch"):
            with self.subTest(marker=marker):
                text = valid_brief().replace("title: 岗位调研", f"title: 岗位调研\n{marker}")
                self.assertIn("document: merge conflict marker", validator.validate_text(text))

    def test_resolved_frontmatter_remains_valid(self):
        text = valid_brief().replace("title: 岗位调研", "company: 示例公司\nrole: 内容策略运营")
        self.assertEqual(validator.validate_text(text), [])

    def test_deduplicates_frontmatter_and_body_marker_errors(self):
        text = valid_brief().replace("title: 岗位调研", "title: {{company}}\n<<<<<<< HEAD") + "\n{{role}}\n>>>>>>> branch"
        errors = validator.validate_text(text)
        self.assertEqual(errors.count("document: unresolved variable"), 1)
        self.assertEqual(errors.count("document: merge conflict marker"), 1)

    def test_reports_each_missing_heading(self):
        for heading in validator.REQUIRED_HEADINGS:
            with self.subTest(heading=heading):
                text = valid_brief().replace(f"## {heading}\n", f"### {heading}\n", 1)
                errors = validator.validate_text(text)
                self.assertIn(f"document: missing heading: {heading}", errors)

    def test_rejects_duplicate_heading(self):
        text = valid_brief().replace("## 工作体验", "## 工作体验\n重复\n\n## 工作体验", 1)
        self.assertIn("document: duplicate heading: 工作体验", validator.validate_text(text))

    def test_rejects_unresolved_variable_and_implementation_markers(self):
        todo = "T" + "ODO"
        uncertain = "待" + "确认"
        for marker in ("{{company}}", todo, uncertain):
            with self.subTest(marker=marker):
                errors = validator.validate_text(valid_brief() + "\n" + marker)
                self.assertTrue(any("implementation marker" in error or "unresolved variable" in error for error in errors))

    def test_rejects_each_merge_conflict_marker(self):
        for marker in ("<<<<<<< HEAD", "=======", ">>>>>>> branch"):
            with self.subTest(marker=marker):
                self.assertTrue(any("merge conflict marker" in error for error in validator.validate_text(valid_brief() + "\n" + marker)))

    def test_rejects_source_reference_without_definition(self):
        text = valid_brief().replace("[^b]", "[^missing]", 1)
        self.assertIn("sources: used reference without definition: missing", validator.validate_text(text))

    def test_rejects_unused_definition(self):
        text = valid_brief() + "\n[^unused]: https://example.net/unused"
        self.assertIn("sources: unused definition: unused", validator.validate_text(text))

    def test_rejects_malformed_https_definition_without_crashing(self):
        text = valid_brief().replace("https://example.com/a#sample", "https://[")
        errors = validator.validate_text(text)
        self.assertIn("sources: invalid HTTPS URL: a", errors)

    def test_allows_footnote_definitions_with_up_to_three_leading_spaces(self):
        text = valid_brief().replace("\n[^", "\n   [^")
        self.assertEqual(validator.validate_text(text), [])

    def test_handles_thousands_of_unique_used_footnotes(self):
        count = 2_000
        definitions = "\n".join(f"[^bulk-{index}]: https://example.net/{index}" for index in range(count))
        references = " ".join(f"[^bulk-{index}]" for index in range(count))
        self.assertEqual(validator.validate_text(valid_brief() + f"\n{definitions}\n{references}"), [])

    def test_rejects_duplicate_source_id_and_normalized_url(self):
        text = valid_brief() + "\n[^c]: https://EXAMPLE.com/a#other\n引用[^c]"
        errors = validator.validate_text(text)
        self.assertIn("sources: duplicate normalized URL: https://example.com/a", errors)
        duplicate_id = valid_brief() + "\n[^a]: https://example.net/another"
        self.assertIn("sources: duplicate definition ID: a", validator.validate_text(duplicate_id))

    def test_requires_at_least_one_https_source(self):
        text = valid_brief().replace("[^a]: https://example.com/a#sample\n[^b]: https://example.org/b", "")
        errors = validator.validate_text(text)
        self.assertIn("sources: no HTTPS source definitions", errors)

    def test_rejects_absolute_local_markdown_links(self):
        for target in ("/Users/name/note.md", "/home/name/note.md", "file:///Users/name/note.md", "C:\\Users\\name\\note.md"):
            with self.subTest(target=target):
                errors = validator.validate_text(valid_brief() + f"\n[local]({target})")
                self.assertTrue(any("absolute local Markdown link" in error for error in errors))

    def test_ignores_inline_code_and_escaped_footnotes_and_links(self):
        text = valid_brief() + (
            "\n`[^inline]` \\[^escaped] `[inline](/Users/name/note.md)` "
            "\\[escaped](/Users/name/note.md)"
        )
        self.assertEqual(validator.validate_text(text), [])

    def test_still_detects_real_footnotes_and_links_after_masking(self):
        errors = validator.validate_text(valid_brief() + "\n[^real] [real](/Users/name/note.md)")
        self.assertIn("sources: used reference without definition: real", errors)
        self.assertIn("document: absolute local Markdown link", errors)

    def test_requires_salary_limitation_language(self):
        text = valid_brief().replace(
            "薪资为非官方样本，口径可能不一致，不能推断目标岗位或公司整体。",
            "月薪表现良好。",
        )
        self.assertIn("salary: missing sample/nonofficial/inconsistent limitation", validator.validate_text(text))

    def test_requires_salary_limitation_for_an_empty_salary_section(self):
        text = valid_brief().replace(
            "## 薪资待遇\n薪资为非官方样本，口径可能不一致，不能推断目标岗位或公司整体。\n\n",
            "## 薪资待遇\n\n",
        )
        self.assertIn("salary: missing sample/nonofficial/inconsistent limitation", validator.validate_text(text))

    def test_requires_education_observation_language(self):
        text = valid_brief().replace(
            "学历要求仅是竞争信号的观察，不是官方硬性门槛。",
            "要求本科。",
        )
        self.assertIn("entry: missing competition-signal/nonofficial limitation", validator.validate_text(text))

    def test_requires_education_limitation_for_an_empty_entry_section(self):
        text = valid_brief().replace(
            "## 入职门槛\n学历要求仅是竞争信号的观察，不是官方硬性门槛。\n\n",
            "## 入职门槛\n\n",
        )
        self.assertIn("entry: missing competition-signal/nonofficial limitation", validator.validate_text(text))

    def test_ignores_fenced_code_and_footnote_bodies_for_document_markers(self):
        text = valid_brief().replace(
            "[^a]: https://example.com/a#sample",
            "[^a]: https://example.com/a#sample\n  {{ignored}} TODO <<<<<<< HEAD",
        ) + "\n```\n## 薪资待遇\n{{ignored}}\nTODO\n<<<<<<< HEAD\n[^fake]: https://example.net/fake\n```"
        self.assertEqual(validator.validate_text(text), [])

    def test_ignores_content_until_a_matching_long_fence_closes(self):
        for character in ("`", "~"):
            with self.subTest(character=character):
                text = valid_brief() + (
                    f"\n{character * 4}\n## 薪资待遇\n{{{{ignored}}}}\nTODO\n"
                    f"<<<<<<< HEAD\n{character * 3}\n## 入职门槛\n{{{{still_ignored}}}}\n{character * 4}"
                )
                self.assertEqual(validator.validate_text(text), [])

    def test_all_failures_have_stable_category_order(self):
        errors = validator.validate_text("no frontmatter\n{{x}}\nTODO\n<<<<<<< HEAD")
        categories = [error.split(":", 1)[0] for error in errors]
        self.assertEqual(categories, sorted(categories, key=("frontmatter", "document", "sources", "salary", "entry").index))


class ValidateFileAndCliTests(unittest.TestCase):
    def test_validate_file_reads_utf8(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "brief.md"
            path.write_text(valid_brief(), encoding="utf-8")
            self.assertEqual(validator.validate_file(path), [])

    def test_cli_exit_codes_and_output(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            valid = root / "valid.md"
            invalid = root / "invalid.md"
            unreadable = root / "invalid-utf8.md"
            missing = root / "missing.md"
            valid.write_text(valid_brief(), encoding="utf-8")
            invalid.write_text("bad", encoding="utf-8")
            unreadable.write_bytes(b"\xff")
            good = subprocess.run([sys.executable, str(MODULE_PATH), str(valid)], text=True, capture_output=True)
            bad = subprocess.run([sys.executable, str(MODULE_PATH), str(invalid)], text=True, capture_output=True)
            broken = subprocess.run([sys.executable, str(MODULE_PATH), str(unreadable)], text=True, capture_output=True)
            absent = subprocess.run([sys.executable, str(MODULE_PATH), str(missing)], text=True, capture_output=True)
            misuse = subprocess.run([sys.executable, str(MODULE_PATH)], text=True, capture_output=True)
            self.assertEqual((good.returncode, good.stdout, good.stderr), (0, f"OK: {valid}\n", ""))
            self.assertEqual(bad.returncode, 1)
            self.assertTrue(bad.stdout.startswith("ERROR: frontmatter:"))
            self.assertEqual(bad.stderr, "")
            self.assertEqual(broken.returncode, 2)
            self.assertIn("ERROR: cannot read supplied file", broken.stdout)
            self.assertNotIn("UnicodeDecodeError", broken.stdout + broken.stderr)
            self.assertEqual(absent.returncode, 2)
            self.assertEqual(absent.stdout, "ERROR: cannot read supplied file\n")
            self.assertEqual(misuse.returncode, 2)
            self.assertIn("ERROR: expected one Markdown path", misuse.stdout)

    def test_cli_reports_malformed_https_definition_as_validation_failure(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "malformed-url.md"
            path.write_text(valid_brief().replace("https://example.com/a#sample", "https://["), encoding="utf-8")
            result = subprocess.run([sys.executable, str(MODULE_PATH), str(path)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn("ERROR: sources: invalid HTTPS URL: a", result.stdout)
            self.assertNotIn("Traceback", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
