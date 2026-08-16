"""Static contract tests for the auditable job-research skill documents."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).parents[1]
SKILL = ROOT / "SKILL.md"
SETUP = ROOT / "references" / "setup.md"
RISK = ROOT / "references" / "evidence-and-risk.md"
TEMPLATE = ROOT / "assets" / "job-research-template.md"
LICENSE = ROOT / "license.txt"
AGENT = ROOT / "agents" / "openai.yaml"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class SkillContractTests(unittest.TestCase):
    def test_frontmatter_is_minimal_and_discoverable(self):
        text = read(SKILL)
        block = re.match(r"\A---\n(.*?)\n---\n", text, re.S)
        self.assertIsNotNone(block)
        fields = dict(line.split(": ", 1) for line in block.group(1).splitlines())
        self.assertEqual(set(fields), {"name", "description"})
        self.assertEqual(fields["name"], "researching-jobs-to-obsidian")
        description = fields["description"]
        self.assertTrue(description.startswith("Use when"))
        for phrase in ("Xiaohongshu", "Zhihu", "Obsidian", "job", "campus", "interview", "salary", "work-experience", "low-frequency", "login-sensitive"):
            self.assertIn(phrase, description)
        self.assertLessEqual(len(re.findall(r"[A-Za-z]+", text)), 700)
        self.assertNotRegex(text, r"\[TODO:|Structuring This Skill|generated scaffold|implementation marker")

    def test_skill_has_only_three_named_checkpoints_and_uses_references(self):
        text = read(SKILL)
        checkpoints = re.findall(r"^### Checkpoint\s+([123])\b", text, re.M)
        self.assertEqual(checkpoints, ["1", "2", "3"])
        for phrase in ("scope, budget, and destination", "detail sample", "A/B/C retention", "filename", "same-name"):
            self.assertIn(phrase, text)
        self.assertIn("references/setup.md", text)
        self.assertIn("references/evidence-and-risk.md", text)
        self.assertIn("before network/login", text)
        self.assertIn("before details", text)
        self.assertIn("before vault write", text)

    def test_core_skill_surfaces_the_operating_contract_when_explaining_or_simulating(self):
        """Plans must preserve safety controls, rather than hiding them in references."""
        text = read(SKILL)
        for phrase in (
            "When presenting the plan",
            "simulate or explain",
            "named persistent profile",
            "current-login check",
            "Checkpoint 1 approval",
            "visible login/manual repair only if needed",
            "After successful login, search and detail collection are headless",
            "no visible fallback during a batch",
            "one MCP/browser session per batch",
            "12 seconds/180 seconds",
            "20 seconds/300 seconds",
            "2 seconds",
            "search_timeout",
            "captcha_detected",
            "search_blocked",
            "risk_cooldown_active",
            "exactly three combined checkpoints",
            "re-read the current file",
            "manual deletions",
            "same-name append/new choice",
        ):
            self.assertIn(phrase, text)

    def test_collection_defaults_and_safety_boundaries_are_documented(self):
        combined = "\n".join(read(path) for path in (SKILL, SETUP, RISK))
        for phrase in (
            "6 keywords × 10", "3 batches × 2", "12 seconds", "180 seconds", "3 min",
            "≤18", "3 batches × ≤6", "max_comments 10", "20 seconds", "300 seconds", "5 min", "2 seconds",
            "within-batch", "interbatch", "search_timeout", "no retry", "continue", "sparse", "do not automatically retry",
            "captcha_detected", "search_blocked", "risk_cooldown_active", "named profile",
            "headed", "manual repair", "headless", "one MCP/browser session per batch", "no visible fallback",
        ):
            self.assertIn(phrase, combined)
        self.assertRegex(combined, r"search.*headless|headless.*search")
        self.assertRegex(combined, r"detail.*headless|headless.*detail")

    def test_evidence_rules_cover_claims_and_grade_boundaries(self):
        text = read(RISK)
        for phrase in (
            "A", "B", "C", "month", "bonus", "equity", "subsidy", "city", "cohort", "role", "business line", "source wording",
            "competition signal", "official JD", "work experience", "attributed", "empty comments", "no evidence",
            "OCR", "distinct", "uncertain", "visual check", "post text", "author statement", "agent inference",
            "exact role", "interview", "salary", "experience", "cohort", "aliases", "normalized-title", "marketing", "adjacent", "low-information", "unverifiable image", "not truth",
            "Checkpoint 1", "Checkpoint 2", "Checkpoint 3",
        ):
            self.assertIn(phrase, text)
        self.assertIn("high engagement", text.lower())
        self.assertIn("stopping criteria", text.lower())

    def test_template_has_required_shape_and_deliberate_unresolved_example(self):
        text = read(TEMPLATE)
        self.assertRegex(text, re.compile(r"\A---\n.+?\n---\n", re.S))
        headings = (
            "一页结论", "岗位画像", "入职门槛", "面试流程与题库", "薪资待遇", "工作体验",
            "准备建议", "证据边界", "检索统计", "来源索引",
        )
        for heading in headings:
            self.assertEqual(text.count(f"## {heading}"), 1)
        for variable in ("{{company}}", "{{role}}", "{{recruiting_type}}", "{{city_scope}}", "{{date}}", "{{source_url}}"):
            self.assertIn(variable, text)
        for phrase in ("非官方样本", "不能推断", "竞争信号", "不是官方硬性门槛", "[[", "作者", "发布日期", "URL", "查询词", "等级", "媒介", "招聘类型", "城市", "局限"):
            self.assertIn(phrase, text)
        self.assertIn("[^source]: {{source_url}}", text)
        self.assertIn("实际证据", text)
        rendered = (
            text.replace("{{company}}", "示例公司")
            .replace("{{role}}", "Data Analyst")
            .replace("{{recruiting_type}}", "校招")
            .replace("{{city_scope}}", "杭州")
            .replace("{{date}}", "2026-08-16")
            .replace("{{source_url}}", "https://example.org/evidence/data-analyst")
        )
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw) / "rendered.md"
            output.write_text(rendered, encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "validate_obsidian.py"), str(output)],
                text=True,
                capture_output=True,
            )
        self.assertEqual((result.returncode, result.stderr), (0, ""), result.stdout)

    def test_setup_commands_match_the_xiaohongshu_client_and_zhihu_has_a_safe_route(self):
        setup, risk = read(SETUP), read(RISK)
        self.assertIn(
            "uv tool install --python 3.11 --force --with 'mcp[cli]<2' 'stride28-search-mcp==0.2.1'",
            setup,
        )
        self.assertIn("stride28-search-mcp doctor", setup)
        self.assertIn("stride28-search-mcp install-browser", setup)
        self.assertRegex(setup, r"search-batch\s+\\?\s*\n\s*\./search-manifest\.json --output-dir")
        self.assertRegex(setup, r"detail-batch\s+\\?\s*\n\s*\./detail-manifest\.json --output-dir")
        self.assertNotIn("--manifest", setup)
        combined = f"{setup}\n{risk}"
        for phrase in (
            "Xiaohongshu-specific", "Zhihu", "login_zhihu", "search_zhihu", "get_zhihu_question",
            "one persistent session per batch", "same checkpoints", "same budgets", "no-retry", "evidence rules",
            "platform risk/verification/login restriction", "never bypass", "Normalize saved Zhihu sources",
            "image downloader only applies to successful Xiaohongshu detail envelopes",
        ):
            self.assertIn(phrase.lower(), combined.lower())

    def test_attribution_agent_manifest_and_no_packaged_sensitive_data(self):
        license_text = read(LICENSE)
        self.assertIn("BrunonXU/Stride28-search2docs", license_text)
        self.assertIn("https://github.com/BrunonXU/Stride28-search2docs", license_text)
        self.assertIn("MIT", license_text)
        self.assertIn("independent adaptation", license_text)
        self.assertIn("no vendoring", license_text)
        self.assertIn("MCP <2", read(SETUP))
        self.assertNotIn("MCP 2 client runtime", read(SETUP))
        self.assertEqual(
            read(AGENT),
            'interface:\n  display_name: "Job Research to Obsidian"\n  short_description: "低频采集岗位信息并生成可审计 Obsidian 岗位笔记"\n  default_prompt: "Use $researching-jobs-to-obsidian to research a company and role and write an evidence-graded Obsidian job brief."\n',
        )
        deliverables = [path for path in ROOT.rglob("*") if path.is_file() and "tests" not in path.parts and "__pycache__" not in path.parts]
        prohibited = ("cookies", "cookie", "raw post", "raw_post", "user vault", "Ant research")
        for path in deliverables:
            lowered = path.name.lower()
            self.assertFalse(any(token in lowered for token in ("cookie", "profile", "raw-post", "raw_post", "ocr-data", "vault", "ant-research")), path)
            if path.suffix in {".md", ".txt", ".yaml"}:
                text = read(path).lower()
                self.assertFalse(any(token in text for token in prohibited), path)
