#!/usr/bin/env python3
"""
check_stale_todos.py — Stale TODO/FIXME Marker Detector

Scans source files for action markers (TODO, FIXME, HACK, XXX, NOTE, OPTIMIZE,
WORKAROUND) and flags them as stale when:
  1. No issue/ticket reference is present (e.g., no #123, JIRA-456, URL)
  2. An embedded date is present and older than --max-age-days (default 365)

Supported languages:
    .py, .java, .ts, .js, .cs, .rb, .kt, .go, .swift, .cpp, .hpp, .php

Exit codes:
    0 — No stale markers found
    1 — One or more stale markers found
    2 — Input error (file/directory not found, no eligible files, etc.)

Usage:
    python check_stale_todos.py path/to/file_or_directory
    python check_stale_todos.py src/ --verbose
    python check_stale_todos.py src/ --json
    python check_stale_todos.py src/ --rewrite
    python check_stale_todos.py src/ --max-age-days 180
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

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

# Markers to scan for (case-insensitive)
ACTION_MARKERS: list[str] = [
    "TODO", "FIXME", "HACK", "XXX", "NOTE", "OPTIMIZE", "WORKAROUND",
]

# Patterns that indicate a linked ticket — marker is NOT stale on this axis
TICKET_PATTERNS: list[re.Pattern] = [
    re.compile(r"#\d+"),                         # GitHub issue: #123
    re.compile(r"[A-Z]{2,10}-\d+"),              # Jira: PROJ-456
    re.compile(r"https?://"),                    # any URL
    re.compile(r"issue\s+\d+", re.IGNORECASE),   # "issue 789"
    re.compile(r"GH-\d+", re.IGNORECASE),        # GH-123
    re.compile(r"PR[- ]?\d+", re.IGNORECASE),    # PR-123
]

# Date patterns embedded in TODO comments
DATE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), "iso"),       # 2023-04-12
    (re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b"), "us4"),   # 4/12/2023
    (re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2})\b"), "us2"),   # 4/12/23
]

DEFAULT_MAX_AGE_DAYS = 365

# Combined pattern to find any action marker in a comment
_MARKER_RE = re.compile(
    r"(?:^|#|//|/\*|\*)\s*(" + "|".join(ACTION_MARKERS) + r")\b[:\s]*(.*)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class TodoFinding:
    """A single stale action marker found in a source file."""

    kind: str           # "stale_no_ticket" | "stale_old_date" | "unverifiable"
    marker: str         # "TODO", "FIXME", etc.
    line: int
    text: str           # comment text after the marker
    age_days: Optional[int] = None
    message: str = ""


@dataclass
class FileReport:
    """Aggregated stale-marker findings for one source file."""

    filepath: str
    findings: list[TodoFinding] = field(default_factory=list)
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------

def _has_ticket(text: str) -> bool:
    """Return True if the text contains a recognized ticket/issue reference."""
    return any(p.search(text) for p in TICKET_PATTERNS)


def _extract_date(text: str) -> Optional[date]:
    """Return the first parseable date found in text, or None."""
    for pattern, fmt in DATE_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        try:
            if fmt == "iso":
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            elif fmt in ("us4", "us2"):
                month, day = int(m.group(1)), int(m.group(2))
                year = int(m.group(3))
                if fmt == "us2":
                    year += 2000 if year < 70 else 1900
                return date(year, month, day)
        except ValueError:
            continue
    return None


def _classify_finding(
    marker: str,
    text: str,
    max_age_days: int,
    verbose: bool,
) -> Optional[TodoFinding]:
    """Return a TodoFinding if the marker is stale, else None."""
    today = date.today()
    has_ticket = _has_ticket(text)
    found_date = _extract_date(text)

    if found_date:
        age = (today - found_date).days
        if age > max_age_days:
            return TodoFinding(
                kind="stale_old_date",
                marker=marker.upper(),
                line=0,  # set by caller
                text=text.strip(),
                age_days=age,
                message=f"Date {found_date.isoformat()} is {age} days old (threshold: {max_age_days})",
            )
        # Date present and recent — not stale
        return None

    if not has_ticket:
        # No date, no ticket
        if verbose:
            return TodoFinding(
                kind="unverifiable",
                marker=marker.upper(),
                line=0,
                text=text.strip(),
                message="No ticket reference or date — cannot confirm staleness",
            )
        return TodoFinding(
            kind="stale_no_ticket",
            marker=marker.upper(),
            line=0,
            text=text.strip(),
            message="No linked issue or ticket reference",
        )

    # Has ticket — not stale
    return None


def analyze_file(
    filepath: str,
    max_age_days: int,
    verbose: bool,
) -> FileReport:
    """Scan a single file for stale TODO/FIXME markers."""
    report = FileReport(filepath=filepath)
    try:
        with open(filepath, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError as exc:
        report.error = str(exc)
        return report

    for lineno, raw_line in enumerate(lines, start=1):
        m = _MARKER_RE.search(raw_line)
        if not m:
            continue
        marker = m.group(1)
        text = m.group(2)
        finding = _classify_finding(marker, text, max_age_days, verbose)
        if finding:
            finding.line = lineno
            report.findings.append(finding)

    return report


# ---------------------------------------------------------------------------
# Rewrite
# ---------------------------------------------------------------------------

def rewrite_file(filepath: str, findings: list[TodoFinding]) -> int:
    """Prepend [STALE?] to lines that have stale markers. Returns lines changed."""
    stale_lines = {f.line for f in findings if f.kind != "unverifiable"}
    if not stale_lines:
        return 0

    with open(filepath, encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()

    changed = 0
    new_lines = []
    for lineno, line in enumerate(lines, start=1):
        if lineno in stale_lines and "[STALE?]" not in line:
            # Insert [STALE?] after the marker keyword
            new_lines.append(
                _MARKER_RE.sub(
                    lambda m2: m2.group(0).replace(
                        m2.group(1), m2.group(1) + " [STALE?]", 1
                    ),
                    line,
                )
            )
            changed += 1
        else:
            new_lines.append(line)

    with open(filepath, "w", encoding="utf-8") as fh:
        fh.writelines(new_lines)

    return changed


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
    """Print a human-readable summary of stale-marker findings to stdout."""
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
        print(f"\n=== Stale TODO Analysis: {report.filepath} ===\n")
        for f in report.findings:
            label = f"[{f.kind.upper()}]"
            preview = f.text[:100] + ("..." if len(f.text) > 100 else "")
            age_str = f" ({f.age_days} days old)" if f.age_days is not None else ""
            print(f"  Line {f.line}: {label} {f.marker}{age_str} — \"{preview}\"")
            print(f"    [SUGGESTION] {f.message}\n")

    if not any_findings:
        print("No stale TODO/FIXME markers found.")


def print_json(reports: list[FileReport], path: str) -> None:
    """Print a JSON summary of stale-marker findings to stdout."""
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
            "findings": [
                {
                    "kind": f.kind,
                    "marker": f.marker,
                    "line": f.line,
                    "text": f.text,
                    "age_days": f.age_days,
                    "message": f.message,
                }
                for f in report.findings
            ],
        })

    print(json.dumps({
        "script": "check_stale_todos",
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
        description="Detect stale TODO/FIXME/HACK markers in source files."
    )
    parser.add_argument("path", help="File or directory to analyze")
    parser.add_argument("--verbose", action="store_true",
                        help="Show all files; include unverifiable markers")
    parser.add_argument("--json", action="store_true", dest="json_output",
                        help="Output results as JSON")
    parser.add_argument("--rewrite", action="store_true",
                        help="Prepend [STALE?] tag to stale markers in-place")
    parser.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS,
                        metavar="DAYS",
                        help=f"Days before a dated marker is considered stale (default: {DEFAULT_MAX_AGE_DAYS})")
    args = parser.parse_args()

    files = collect_files(args.path)
    if not files:
        print(f"Error: no supported source files found at '{args.path}'", file=sys.stderr)
        return 2

    reports = [
        analyze_file(f, args.max_age_days, args.verbose)
        for f in files
    ]

    if args.rewrite:
        for report in reports:
            if report.findings and not report.error:
                changed = rewrite_file(report.filepath, report.findings)
                if changed:
                    print(f"Rewrote {changed} line(s) in {report.filepath}")

    if args.json_output:
        print_json(reports, args.path)
    else:
        print_report(reports, args.verbose)

    has_findings = any(r.findings for r in reports if not r.error)
    return 1 if has_findings else 0


if __name__ == "__main__":
    sys.exit(main())
