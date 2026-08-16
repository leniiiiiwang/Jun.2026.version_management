"""Validate the evidence and structure contract for Obsidian job-research briefs."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit, urlunsplit


REQUIRED_HEADINGS = (
    "一页结论",
    "岗位画像",
    "入职门槛",
    "面试流程与题库",
    "薪资待遇",
    "工作体验",
    "准备建议",
    "证据边界",
    "检索统计",
    "来源索引",
)

_FRONTMATTER_VALUE = re.compile(r"^\s*(?:[^\s:#][^:]*:\s*\S+|-\s+\S+)\s*$")
_FOOTNOTE_LABEL = re.compile(r"^ {0,3}\[\^([^\]\s]+)\]:")
_FOOTNOTE_DEFINITION = re.compile(r"^ {0,3}\[\^([^\]\s]+)\]:\s*(https://\S+)\s*$")
_FOOTNOTE_REF = re.compile(r"\[\^([^\]\s]+)\]")
_MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(\s*(?:<([^>]+)>|([^\s)]+))")
_UNRESOLVED_VARIABLE = re.compile(r"\{\{[^{}]*\}\}")
_IMPLEMENTATION_MARKER = re.compile(
    r"\b(?:TODO|FIXME|TBD|TBC|TO\s+DO)\b|待(?:确认|补充|定)|未(?:确定|决)", re.IGNORECASE
)
_CONFLICT_MARKER = re.compile(r"^(?:<{7}|={7}|>{7})")


def _normalized_lines(text: str) -> list[str]:
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _frontmatter_end(lines: list[str]) -> tuple[int | None, list[str]]:
    if not lines or lines[0] != "---":
        return None, ["frontmatter: missing leading YAML block"]
    for index in range(1, len(lines)):
        if lines[index] == "---":
            block = lines[1:index]
            if not any(_FRONTMATTER_VALUE.match(line) for line in block):
                return index, ["frontmatter: empty or invalid YAML block"]
            return index, []
    return None, ["frontmatter: unterminated YAML block"]


def _fence_opener(line: str) -> tuple[str, int] | None:
    stripped = line.lstrip()
    if not stripped or stripped[0] not in ("`", "~"):
        return None
    character = stripped[0]
    length = len(stripped) - len(stripped.lstrip(character))
    return (character, length) if length >= 3 else None


def _fence_closes(line: str, character: str, minimum_length: int) -> bool:
    stripped = line.lstrip()
    if not stripped.startswith(character * minimum_length):
        return False
    length = len(stripped) - len(stripped.lstrip(character))
    return length >= minimum_length and not stripped[length:].strip()


def _content_lines(lines: list[str], start: int) -> list[tuple[int, str]]:
    """Return document lines outside fenced code blocks and footnote definition bodies."""
    result: list[tuple[int, str]] = []
    fence: tuple[str, int] | None = None
    in_footnote = False
    for index, line in enumerate(lines[start:], start):
        opener = _fence_opener(line) if fence is None else None
        if opener is not None:
            fence = opener
            in_footnote = False
            continue
        if fence is not None and _fence_closes(line, *fence):
            fence = None
            in_footnote = False
            continue
        if fence is not None:
            continue
        if _FOOTNOTE_LABEL.match(line):
            in_footnote = True
            continue
        if in_footnote and (line.startswith(" ") or line.startswith("\t") or not line.strip()):
            continue
        in_footnote = False
        result.append((index, line))
    return result


def _definitions(lines: list[str], start: int) -> tuple[list[tuple[str, str]], list[str]]:
    definitions: list[tuple[str, str]] = []
    errors: list[str] = []
    fence: tuple[str, int] | None = None
    for line in lines[start:]:
        opener = _fence_opener(line) if fence is None else None
        if opener is not None:
            fence = opener
            continue
        if fence is not None and _fence_closes(line, *fence):
            fence = None
            continue
        if fence is not None:
            continue
        label = _FOOTNOTE_LABEL.match(line)
        if not label:
            continue
        definition = _FOOTNOTE_DEFINITION.match(line)
        if definition:
            definitions.append((definition.group(1), definition.group(2)))
        else:
            errors.append(f"sources: invalid HTTPS definition: {label.group(1)}")
    return definitions, errors


def _normalize_url(url: str) -> str | None:
    try:
        parts = urlsplit(url)
        if parts.scheme.lower() != "https" or not parts.hostname:
            return None
        parts.port  # Validate malformed ports while retaining the original authority text.
    except ValueError:
        return None
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ""))


def _matches_required_heading(line: str, heading: str) -> bool:
    leading_spaces = len(line) - len(line.lstrip(" "))
    return leading_spaces <= 3 and line[leading_spaces:].rstrip() == f"## {heading}"


def _is_level_two_heading(line: str) -> bool:
    leading_spaces = len(line) - len(line.lstrip(" "))
    return leading_spaces <= 3 and line[leading_spaces:].startswith("## ")


def _section_text(lines: list[tuple[int, str]], heading: str) -> str:
    for index, (_, line) in enumerate(lines):
        if _matches_required_heading(line, heading):
            following: list[str] = []
            for _, candidate in lines[index + 1:]:
                if _is_level_two_heading(candidate):
                    break
                following.append(candidate)
            return "\n".join(following)
    return ""


def _mask_inline_code_and_escaped_syntax(text: str) -> str:
    """Mask syntax that Markdown renders literally before scanning references and links."""
    masked = list(text)
    index = 0
    while index < len(text):
        if text[index] == "\\" and index + 1 < len(text) and text[index + 1] in "[]()`\\{}":
            masked[index] = "\0"
            masked[index + 1] = "\0"
            index += 2
            continue
        if text[index] != "`":
            index += 1
            continue
        length = 1
        while index + length < len(text) and text[index + length] == "`":
            length += 1
        delimiter = "`" * length
        closing = text.find(delimiter, index + length)
        if closing == -1:
            index += length
            continue
        for cursor in range(index, closing + length):
            masked[cursor] = "\0"
        index = closing + length
    return "".join(masked)


def _has_salary_limitation(text: str) -> bool:
    scope = re.search(r"样本|非官方|公开(?:分享|经验)|口径.{0,6}(?:不一致|差异)|个体(?:经验|差异)", text)
    no_inference = re.search(
        r"(?:不能|不可|不应|不宜|并非|不是|不代表|不可代表|不等同于).{0,18}(?:目标岗位|岗位|公司整体|全公司|公司范围|公司)",
        text,
    ) or re.search(
        r"(?:目标岗位|岗位|公司整体|全公司|公司范围|公司).{0,18}(?:不能|不可|不应|不宜|并非|不是|不代表|不可代表|不等同于)",
        text,
    )
    return bool(scope and no_inference)


def _has_entry_limitation(text: str) -> bool:
    observation = re.search(r"竞争信号|观察|样本|非官方|公开(?:分享|经验)", text)
    not_threshold = re.search(r"(?:不是|并非|不等于|非).{0,12}(?:官方)?.{0,8}(?:硬性|硬)门槛", text)
    return bool(observation and not_threshold)


def validate_text(text: str) -> list[str]:
    """Return deterministic, human-readable contract failures for an Obsidian brief."""
    try:
        lines = _normalized_lines(text)
    except (AttributeError, TypeError):
        return ["frontmatter: document text must be a string"]

    frontmatter_end, errors = _frontmatter_end(lines)
    content_start = 0 if frontmatter_end is None else frontmatter_end + 1
    frontmatter_lines = lines[1:] if frontmatter_end is None else lines[1:frontmatter_end]
    frontmatter_text = "\n".join(frontmatter_lines)

    document_errors: list[str] = []
    source_errors: list[str] = []
    salary_errors: list[str] = []
    entry_errors: list[str] = []
    content = _content_lines(lines, content_start)
    content_text = "\n".join(line for _, line in content)
    scannable_text = _mask_inline_code_and_escaped_syntax(content_text)

    positions: dict[str, list[int]] = {heading: [] for heading in REQUIRED_HEADINGS}
    for position, (_, line) in enumerate(content):
        for heading in REQUIRED_HEADINGS:
            if _matches_required_heading(line, heading):
                positions[heading].append(position)
    for heading in REQUIRED_HEADINGS:
        if not positions[heading]:
            document_errors.append(f"document: missing heading: {heading}")
        elif len(positions[heading]) > 1:
            document_errors.append(f"document: duplicate heading: {heading}")

    if _UNRESOLVED_VARIABLE.search(frontmatter_text) or _UNRESOLVED_VARIABLE.search(scannable_text):
        document_errors.append("document: unresolved variable")
    if _IMPLEMENTATION_MARKER.search(content_text):
        document_errors.append("document: implementation marker")
    if any(_CONFLICT_MARKER.match(line) for line in frontmatter_lines) or any(
        _CONFLICT_MARKER.match(line) for _, line in content
    ):
        document_errors.append("document: merge conflict marker")
    for match in _MARKDOWN_LINK.finditer(scannable_text):
        target = (match.group(1) or match.group(2)).strip()
        lowered = target.lower()
        if target.startswith(("/Users/", "/home/")) or lowered.startswith("file://") or re.match(r"^[a-zA-Z]:[\\/]", target):
            document_errors.append("document: absolute local Markdown link")

    definitions, definition_errors = _definitions(lines, content_start)
    source_errors.extend(definition_errors)
    definition_ids = [identifier for identifier, _ in definitions]
    for identifier, count in sorted(Counter(definition_ids).items()):
        if count <= 1:
            continue
        source_errors.append(f"sources: duplicate definition ID: {identifier}")
    normalized_urls: list[str] = []
    for identifier, url in definitions:
        normalized = _normalize_url(url)
        if normalized is None:
            source_errors.append(f"sources: invalid HTTPS URL: {identifier}")
        else:
            normalized_urls.append(normalized)
    for url, count in sorted(Counter(normalized_urls).items()):
        if count <= 1:
            continue
        source_errors.append(f"sources: duplicate normalized URL: {url}")
    if not definitions:
        source_errors.append("sources: no HTTPS source definitions")

    used_ids = [match.group(1) for match in _FOOTNOTE_REF.finditer(scannable_text)]
    defined_set = set(definition_ids)
    used_set = set(used_ids)
    for identifier in sorted(used_set - defined_set):
        source_errors.append(f"sources: used reference without definition: {identifier}")
    for identifier in sorted(defined_set - used_set):
        source_errors.append(f"sources: unused definition: {identifier}")

    salary_section = _section_text(content, "薪资待遇")
    if positions["薪资待遇"] and not _has_salary_limitation(salary_section):
        salary_errors.append("salary: missing sample/nonofficial/inconsistent limitation")
    entry_section = _section_text(content, "入职门槛")
    if positions["入职门槛"] and not _has_entry_limitation(entry_section):
        entry_errors.append("entry: missing competition-signal/nonofficial limitation")
    return errors + document_errors + source_errors + salary_errors + entry_errors


def validate_file(path: Path) -> list[str]:
    """Read a UTF-8 Markdown file and validate its contents."""
    return validate_text(Path(path).read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        print("ERROR: expected one Markdown path")
        return 2
    path = Path(arguments[0])
    try:
        errors = validate_file(path)
    except (OSError, UnicodeError):
        print("ERROR: cannot read supplied file")
        return 2
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"OK: {arguments[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
