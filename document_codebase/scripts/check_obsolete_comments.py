#!/usr/bin/env python3
"""
check_obsolete_comments.py — Obsolete Comment Detector

Analyzes source files to flag:
  1. Commented-out code blocks — consecutive comment lines (>= 2) whose
     stripped content contains code-like tokens (assignments, brackets,
     keywords, semicolons).
  2. Redundant inline comments — same-line comments whose words overlap
     heavily with nearby identifier names (Jaccard >= --min-echo-ratio,
     default 0.85). Intentionally conservative to avoid false positives.

Supported languages:
    .py, .java, .ts, .js, .cs, .rb, .kt, .go, .swift, .cpp, .hpp, .php

Exit codes:
    0 — No obsolete comments found
    1 — One or more obsolete comments found
    2 — Input error (file/directory not found, no eligible files, etc.)

Usage:
    python check_obsolete_comments.py path/to/file_or_directory
    python check_obsolete_comments.py src/ --verbose
    python check_obsolete_comments.py src/ --json
    python check_obsolete_comments.py src/ --rewrite
    python check_obsolete_comments.py src/ --min-echo-ratio 0.90
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SUPPORTED_EXTENSIONS: set[str] = {
    ".py", ".java", ".ts", ".js", ".cs", ".rb",
    ".kt", ".go", ".swift", ".cpp", ".hpp", ".php",
}

LANGUAGE_MAP: dict[str, str] = {
    ".py": "Python",
    ".java": "Java",
    ".ts": "TypeScript",
    ".js": "JavaScript",
    ".cs": "C#",
    ".rb": "Ruby",
    ".kt": "Kotlin",
    ".go": "Go",
    ".swift": "Swift",
    ".cpp": "C++",
    ".hpp": "C++",
    ".php": "PHP",
}

SKIP_DIRS: set[str] = {
    ".git", "node_modules", "__pycache__", ".venv",
    "dist", "build", ".mypy_cache", ".pytest_cache",
}

# Single-line comment markers per extension
SINGLE_LINE_MARKERS: dict[str, list[str]] = {
    ".py":    ["#"],
    ".java":  ["//"],
    ".ts":    ["//"],
    ".js":    ["//"],
    ".cs":    ["//"],
    ".rb":    ["#"],
    ".kt":    ["//"],
    ".go":    ["//"],
    ".swift": ["//"],
    ".cpp":   ["//"],
    ".hpp":   ["//"],
    ".php":   ["//", "#"],
}

# Tokens whose presence in a stripped comment line strongly suggest commented-out code
CODE_TOKENS: list[str] = [
    "=", "()", "->", "=>", ";",
    "{", "}",
    "return ", "if ", "else", "for ", "while ", "switch ",
    "import ", "from ", "require(",
    "const ", "var ", "let ", "def ", "fn ",
    "class ", "struct ", "interface ",
    "public ", "private ", "protected ", "static ",
    "int ", "string ", "void ", "bool ", "float ",
    "self.", "this.",
    "print(", "console.", "System.out",
]

# How many consecutive comment lines with code tokens form a "block"
CONSECUTIVE_CODE_COMMENT_THRESHOLD = 2

# Directives that should never be flagged as obsolete comments
SAFE_DIRECTIVES: list[re.Pattern] = [
    re.compile(r"type:\s*ignore"),          # mypy
    re.compile(r"noqa"),                    # flake8/ruff
    re.compile(r"@ts-ignore"),              # TypeScript
    re.compile(r"@ts-expect-error"),        # TypeScript
    re.compile(r"eslint-disable"),          # ESLint
    re.compile(r"istanbul ignore"),         # Istanbul
    re.compile(r"pragma"),                  # C/C++ pragma
    re.compile(r"noinspection"),            # JetBrains
    re.compile(r"copyright", re.IGNORECASE),
    re.compile(r"license", re.IGNORECASE),
    re.compile(r"^\s*[!#]\s*/"),            # shebang
    re.compile(r"-\*-"),                    # encoding declaration
    re.compile(r"<editor-fold"),            # IDE fold markers
]

DEFAULT_MIN_ECHO_RATIO = 0.85


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class CommentFinding:
    """A single obsolete comment found in a source file."""

    kind: str           # "commented_code" | "redundant_comment"
    line_start: int
    line_end: int
    content_preview: str
    message: str


@dataclass
class FileReport:
    """Aggregated obsolete-comment findings for one source file."""

    filepath: str
    language: str
    findings: list[CommentFinding] = field(default_factory=list)
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_safe_directive(text: str) -> bool:
    return any(p.search(text) for p in SAFE_DIRECTIVES)


def _strip_comment_marker(line: str, ext: str) -> Optional[str]:
    """Return the comment body if the line is a pure comment line, else None."""
    stripped = line.strip()
    for marker in SINGLE_LINE_MARKERS.get(ext, ["//", "#"]):
        if stripped.startswith(marker):
            return stripped[len(marker):].strip()
    return None


def _looks_like_code(text: str) -> bool:
    """Return True if stripped comment text contains code-like tokens."""
    for token in CODE_TOKENS:
        if token in text:
            return True
    return False


def _tokenize_identifier(name: str) -> set[str]:
    """Split a camelCase/snake_case identifier into lowercase words."""
    # Split on underscores and camelCase boundaries
    words = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    words = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", words)
    return {w.lower() for w in re.split(r"[\W_]+", words) if len(w) > 1}


def _jaccard(set_a: set[str], set_b: set[str]) -> float:
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union else 0.0


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------

def _detect_commented_code(
    lines: list[str],
    ext: str,
) -> list[CommentFinding]:
    """Detect blocks of consecutive comment lines that look like commented-out code."""
    findings: list[CommentFinding] = []
    run_start: Optional[int] = None
    run_lines: list[str] = []

    def flush_run(end_lineno: int) -> None:
        """Emit a finding for the current run of code-comment lines, then reset."""
        nonlocal run_start, run_lines
        if run_start is not None and len(run_lines) >= CONSECUTIVE_CODE_COMMENT_THRESHOLD:
            preview = run_lines[0][:80]
            findings.append(CommentFinding(
                kind="commented_code",
                line_start=run_start,
                line_end=end_lineno - 1,
                content_preview=preview,
                message=f"Commented-out code block ({len(run_lines)} lines)",
            ))
        run_start = None
        run_lines = []

    for lineno, raw_line in enumerate(lines, start=1):
        body = _strip_comment_marker(raw_line, ext)
        if body is None:
            flush_run(lineno)
            continue
        if _is_safe_directive(body):
            flush_run(lineno)
            continue
        if _looks_like_code(body):
            if run_start is None:
                run_start = lineno
            run_lines.append(body)
        else:
            flush_run(lineno)

    flush_run(len(lines) + 1)
    return findings


def _detect_redundant_comments(
    lines: list[str],
    ext: str,
    min_echo_ratio: float,
) -> list[CommentFinding]:
    """Detect inline comments that merely restate adjacent identifier names."""
    findings: list[CommentFinding] = []
    identifier_re = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]{2,})\b")

    for lineno, raw_line in enumerate(lines, start=1):
        # Only check lines that have both code and an inline comment
        for marker in SINGLE_LINE_MARKERS.get(ext, ["//", "#"]):
            idx = raw_line.find(marker)
            if idx <= 0:  # marker must not be at the start (that's a pure comment line)
                continue
            code_part = raw_line[:idx]
            comment_part = raw_line[idx + len(marker):].strip()

            if not comment_part or _is_safe_directive(comment_part):
                break

            # Extract identifiers from the code portion
            code_ids = identifier_re.findall(code_part)
            if not code_ids:
                break

            code_words: set[str] = set()
            for ident in code_ids:
                code_words |= _tokenize_identifier(ident)

            # Extract words from the comment
            comment_words = {
                w.lower() for w in re.split(r"\W+", comment_part) if len(w) > 1
            }

            if not comment_words:
                break

            ratio = _jaccard(comment_words, code_words)
            if ratio >= min_echo_ratio:
                findings.append(CommentFinding(
                    kind="redundant_comment",
                    line_start=lineno,
                    line_end=lineno,
                    content_preview=comment_part[:80],
                    message=f"Comment restates code identifiers (overlap ratio: {ratio:.2f})",
                ))
            break  # only check the first inline marker per line

    return findings


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def analyze_file(
    filepath: str,
    min_echo_ratio: float,
) -> FileReport:
    """Scan a single file for commented-out code and redundant inline comments."""
    ext = Path(filepath).suffix
    language = LANGUAGE_MAP.get(ext, "Unknown")
    report = FileReport(filepath=filepath, language=language)

    try:
        with open(filepath, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError as exc:
        report.error = str(exc)
        return report

    report.findings.extend(_detect_commented_code(lines, ext))
    report.findings.extend(_detect_redundant_comments(lines, ext, min_echo_ratio))
    report.findings.sort(key=lambda f: f.line_start)
    return report


# ---------------------------------------------------------------------------
# Rewrite
# ---------------------------------------------------------------------------

def rewrite_file(filepath: str, findings: list[CommentFinding]) -> int:
    """Remove lines flagged as commented_code. Returns number of lines removed."""
    lines_to_remove: set[int] = set()
    for f in findings:
        if f.kind == "commented_code":
            for ln in range(f.line_start, f.line_end + 1):
                lines_to_remove.add(ln)

    if not lines_to_remove:
        return 0

    with open(filepath, encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()

    new_lines = [
        line for lineno, line in enumerate(lines, start=1)
        if lineno not in lines_to_remove
    ]

    with open(filepath, "w", encoding="utf-8") as fh:
        fh.writelines(new_lines)

    return len(lines_to_remove)


# ---------------------------------------------------------------------------
# File collection
# ---------------------------------------------------------------------------

def collect_files(path: str) -> list[str]:
    """Return all eligible source files under path, skipping build/vendor dirs."""
    p = Path(path)
    if not p.exists():
        return []
    if p.is_file():
        return [str(p)] if p.suffix in SUPPORTED_EXTENSIONS else []

    result = []
    for root, dirs, files in os.walk(p):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            fp = Path(root) / f
            if fp.suffix in SUPPORTED_EXTENSIONS:
                result.append(str(fp))
    return sorted(result)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def print_report(reports: list[FileReport], verbose: bool) -> None:
    """Print a human-readable summary of obsolete-comment findings to stdout."""
    any_findings = False
    for report in reports:
        if report.error:
            print(f"ERROR: {report.filepath}: {report.error}", file=sys.stderr)
            continue
        if not report.findings:
            if verbose:
                print(f"  OK  {report.filepath}")
            continue

        any_findings = True
        print(f"\n=== Obsolete Comment Analysis: {report.filepath} ===\n")
        for f in report.findings:
            range_str = (
                f"Line {f.line_start}"
                if f.line_start == f.line_end
                else f"Lines {f.line_start}-{f.line_end}"
            )
            label = f"[{f.kind.upper()}]"
            print(f"  {range_str}: {label} {f.message}")
            print(f"    Preview: {f.content_preview}")
            if f.kind == "commented_code":
                print("    [SUGGESTION] Remove — code is preserved in version control history\n")
            else:
                print("    [SUGGESTION] Remove or rewrite — comment adds no information beyond the code\n")

    if not any_findings:
        print("No obsolete comments found.")


def print_json(reports: list[FileReport], path: str) -> None:
    """Print a JSON summary of obsolete-comment findings to stdout."""
    files = []
    total_findings = 0
    files_with_findings = 0
    total_analyzed = 0

    for report in reports:
        if report.error:
            files.append({"filepath": report.filepath, "error": report.error})
            continue
        total_analyzed += 1
        count = len(report.findings)
        total_findings += count
        if count:
            files_with_findings += 1
        files.append({
            "filepath": report.filepath,
            "language": report.language,
            "findings": [
                {
                    "kind": f.kind,
                    "line_start": f.line_start,
                    "line_end": f.line_end,
                    "content_preview": f.content_preview,
                    "message": f.message,
                }
                for f in report.findings
            ],
        })

    print(json.dumps({
        "script": "check_obsolete_comments",
        "path": path,
        "total_files_analyzed": total_analyzed,
        "files_with_findings": files_with_findings,
        "findings_count": total_findings,
        "files": files,
    }, indent=2))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    """Parse arguments, run analysis, and return an exit code."""
    parser = argparse.ArgumentParser(
        description="Detect obsolete comments (commented-out code, redundant inline comments)."
    )
    parser.add_argument("path", help="File or directory to analyze")
    parser.add_argument("--verbose", action="store_true",
                        help="Show all analyzed files, including clean ones")
    parser.add_argument("--json", action="store_true", dest="json_output",
                        help="Output results as JSON")
    parser.add_argument("--rewrite", action="store_true",
                        help="Remove commented-out code blocks in-place")
    parser.add_argument("--min-echo-ratio", type=float, default=DEFAULT_MIN_ECHO_RATIO,
                        metavar="RATIO",
                        help=f"Jaccard overlap threshold for redundant-comment detection (default: {DEFAULT_MIN_ECHO_RATIO})")
    args = parser.parse_args()

    files = collect_files(args.path)
    if not files:
        print(f"Error: no supported source files found at '{args.path}'", file=sys.stderr)
        return 2

    reports = [analyze_file(f, args.min_echo_ratio) for f in files]

    if args.rewrite:
        for report in reports:
            if report.findings and not report.error:
                removed = rewrite_file(report.filepath, report.findings)
                if removed:
                    print(f"Removed {removed} line(s) from {report.filepath}")

    if args.json_output:
        print_json(reports, args.path)
    else:
        print_report(reports, args.verbose)

    has_findings = any(r.findings for r in reports if not r.error)
    return 1 if has_findings else 0


if __name__ == "__main__":
    sys.exit(main())
