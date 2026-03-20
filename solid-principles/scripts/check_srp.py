#!/usr/bin/env python3
"""
check_srp.py — Single Responsibility Principle (SRP) Violation Detector

Analyzes source code files to detect potential SRP violations in classes.
Works language-agnostically: uses Python's ast module for .py files and
regex-based heuristics for all other supported languages.

Supported languages:
    .py, .java, .ts, .js, .cs, .rb, .kt, .go, .swift, .cpp, .hpp, .php

Detections:
    1. Classes with too many methods (default threshold: 10)
    2. Classes with too many lines (default threshold: 200)
    3. Mixed-concern method name clusters (verb/prefix frequency heuristic)
    4. High import fan-in for Python files (many unrelated imports in scope)

Exit codes:
    0 — No SRP concerns found
    1 — One or more SRP concerns found
    2 — Input error (file/directory not found, no eligible files, etc.)

Usage:
    python check_srp.py path/to/file_or_directory
    python check_srp.py path/ --verbose --max-methods 8
    python check_srp.py path/ --json
    python check_srp.py path/ --rewrite
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
import textwrap
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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

# Common verb prefixes that hint at a particular responsibility.
CONCERN_VERBS: dict[str, str] = {
    "save": "persistence",
    "load": "persistence",
    "store": "persistence",
    "persist": "persistence",
    "fetch": "persistence",
    "delete": "persistence",
    "remove": "persistence",
    "insert": "persistence",
    "update": "persistence",
    "query": "persistence",
    "find": "persistence",
    "read": "io",
    "write": "io",
    "open": "io",
    "close": "io",
    "flush": "io",
    "render": "presentation",
    "display": "presentation",
    "draw": "presentation",
    "show": "presentation",
    "hide": "presentation",
    "format": "presentation",
    "print": "presentation",
    "paint": "presentation",
    "send": "communication",
    "receive": "communication",
    "notify": "communication",
    "emit": "communication",
    "broadcast": "communication",
    "publish": "communication",
    "dispatch": "communication",
    "email": "communication",
    "mail": "communication",
    "calculate": "computation",
    "compute": "computation",
    "sum": "computation",
    "average": "computation",
    "evaluate": "computation",
    "process": "computation",
    "transform": "computation",
    "convert": "computation",
    "parse": "parsing",
    "decode": "parsing",
    "encode": "parsing",
    "serialize": "parsing",
    "deserialize": "parsing",
    "marshal": "parsing",
    "unmarshal": "parsing",
    "validate": "validation",
    "check": "validation",
    "verify": "validation",
    "assert": "validation",
    "ensure": "validation",
    "sanitize": "validation",
    "log": "logging",
    "trace": "logging",
    "debug": "logging",
    "warn": "logging",
    "audit": "logging",
    "connect": "networking",
    "disconnect": "networking",
    "listen": "networking",
    "bind": "networking",
    "accept": "networking",
    "authenticate": "auth",
    "authorize": "auth",
    "login": "auth",
    "logout": "auth",
    "encrypt": "security",
    "decrypt": "security",
    "hash": "security",
    "sign": "security",
    "cache": "caching",
    "invalidate": "caching",
    "evict": "caching",
}

# Python standard-library module groupings for import fan-in analysis.
IMPORT_GROUPS: dict[str, str] = {
    "os": "os/io",
    "sys": "os/io",
    "io": "os/io",
    "pathlib": "os/io",
    "shutil": "os/io",
    "glob": "os/io",
    "tempfile": "os/io",
    "subprocess": "os/io",
    "json": "serialization",
    "csv": "serialization",
    "xml": "serialization",
    "yaml": "serialization",
    "pickle": "serialization",
    "marshal": "serialization",
    "struct": "serialization",
    "configparser": "serialization",
    "toml": "serialization",
    "tomllib": "serialization",
    "http": "networking",
    "urllib": "networking",
    "socket": "networking",
    "ssl": "networking",
    "requests": "networking",
    "aiohttp": "networking",
    "httpx": "networking",
    "smtplib": "email",
    "email": "email",
    "imaplib": "email",
    "poplib": "email",
    "sqlite3": "database",
    "sqlalchemy": "database",
    "psycopg2": "database",
    "pymongo": "database",
    "redis": "database",
    "tkinter": "gui",
    "PyQt5": "gui",
    "PyQt6": "gui",
    "wx": "gui",
    "kivy": "gui",
    "logging": "logging",
    "unittest": "testing",
    "pytest": "testing",
    "mock": "testing",
    "re": "text",
    "string": "text",
    "textwrap": "text",
    "difflib": "text",
    "threading": "concurrency",
    "multiprocessing": "concurrency",
    "asyncio": "concurrency",
    "concurrent": "concurrency",
}

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class MethodInfo:
    """Information about a single method inside a class."""
    name: str
    line_start: int
    line_end: int
    concern: Optional[str] = None  # inferred concern label


@dataclass
class ClassInfo:
    """All information gathered about a single class."""
    name: str
    line_start: int
    line_end: int
    language: str
    methods: list[MethodInfo] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)  # Python-only


@dataclass
class Concern:
    """A group of methods that share a responsibility concern."""
    label: str
    methods: list[MethodInfo] = field(default_factory=list)

    @property
    def readable_label(self) -> str:
        return self.label.replace("_", " ")


@dataclass
class SRPWarning:
    """A single SRP-related warning for a class."""
    kind: str  # "too_many_methods" | "too_many_lines" | "mixed_concerns" | "high_import_fanin"
    message: str
    details: dict = field(default_factory=dict)


@dataclass
class ClassReport:
    """Full analysis report for one class."""
    cls: ClassInfo
    warnings: list[SRPWarning] = field(default_factory=list)
    concerns: dict[str, Concern] = field(default_factory=dict)

    @property
    def has_concerns(self) -> bool:
        return len(self.warnings) > 0


@dataclass
class FileReport:
    """Full analysis report for one file."""
    filepath: str
    language: str
    class_reports: list[ClassReport] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def has_concerns(self) -> bool:
        return any(cr.has_concerns for cr in self.class_reports)


# ---------------------------------------------------------------------------
# Python AST-based analysis
# ---------------------------------------------------------------------------

def _analyze_python_file(filepath: str, source: str) -> list[ClassInfo]:
    """Use the ast module to extract classes and their methods from Python."""
    try:
        tree = ast.parse(source, filename=filepath)
    except SyntaxError:
        return []

    source_lines = source.splitlines()
    classes: list[ClassInfo] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue

        # Determine class line range.
        line_start = node.lineno
        line_end = _ast_node_end_line(node, source_lines)

        cls = ClassInfo(
            name=node.name,
            line_start=line_start,
            line_end=line_end,
            language="Python",
        )

        # Gather methods.
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                m_end = _ast_node_end_line(item, source_lines)
                cls.methods.append(MethodInfo(
                    name=item.name,
                    line_start=item.lineno,
                    line_end=m_end,
                ))

        # Gather imports that appear *inside* the class body (rare but possible),
        # and also scan the module-level imports as they affect the class scope.
        cls.imports = _gather_python_imports(tree)

        classes.append(cls)

    return classes


def _ast_node_end_line(node: ast.AST, source_lines: list[str]) -> int:
    """Return the last line of an AST node."""
    if hasattr(node, "end_lineno") and node.end_lineno is not None:
        return node.end_lineno
    # Fallback: walk children.
    end = getattr(node, "lineno", 1)
    for child in ast.walk(node):
        child_end = getattr(child, "end_lineno", None) or getattr(child, "lineno", 0)
        if child_end > end:
            end = child_end
    return end


def _gather_python_imports(tree: ast.Module) -> list[str]:
    """Collect all top-level import names from a Python AST."""
    imports: list[str] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module.split(".")[0])
    return imports


# ---------------------------------------------------------------------------
# Regex-based analysis for other languages
# ---------------------------------------------------------------------------

# Patterns per language to detect class declarations and method declarations.

_CLASS_PATTERNS: dict[str, re.Pattern] = {
    "Java":       re.compile(r"^\s*(?:public\s+|private\s+|protected\s+)?(?:abstract\s+|final\s+)?class\s+(\w+)", re.MULTILINE),
    "TypeScript": re.compile(r"^\s*(?:export\s+)?(?:abstract\s+)?class\s+(\w+)", re.MULTILINE),
    "JavaScript": re.compile(r"^\s*(?:export\s+)?class\s+(\w+)", re.MULTILINE),
    "C#":         re.compile(r"^\s*(?:public\s+|private\s+|protected\s+|internal\s+)?(?:abstract\s+|sealed\s+|static\s+|partial\s+)*class\s+(\w+)", re.MULTILINE),
    "Ruby":       re.compile(r"^\s*class\s+(\w+)", re.MULTILINE),
    "Kotlin":     re.compile(r"^\s*(?:open\s+|abstract\s+|data\s+|sealed\s+)?class\s+(\w+)", re.MULTILINE),
    "Go":         re.compile(r"^\s*type\s+(\w+)\s+struct\s*\{", re.MULTILINE),
    "Swift":      re.compile(r"^\s*(?:open\s+|public\s+|internal\s+|fileprivate\s+|private\s+)?(?:final\s+)?class\s+(\w+)", re.MULTILINE),
    "C++":        re.compile(r"^\s*class\s+(\w+)", re.MULTILINE),
    "PHP":        re.compile(r"^\s*(?:abstract\s+|final\s+)?class\s+(\w+)", re.MULTILINE),
}

_METHOD_PATTERNS: dict[str, re.Pattern] = {
    "Java":       re.compile(r"^\s*(?:public|private|protected)?\s*(?:static\s+)?(?:final\s+)?(?:synchronized\s+)?(?:\w+(?:<[^>]*>)?(?:\[\])*)\s+(\w+)\s*\(", re.MULTILINE),
    "TypeScript": re.compile(r"^\s*(?:public|private|protected|static|async|readonly|\s)*(\w+)\s*\([^)]*\)\s*(?::\s*\w+)?\s*\{", re.MULTILINE),
    "JavaScript": re.compile(r"^\s*(?:static\s+|async\s+|get\s+|set\s+)*(\w+)\s*\([^)]*\)\s*\{", re.MULTILINE),
    "C#":         re.compile(r"^\s*(?:public|private|protected|internal|static|virtual|override|abstract|async|\s)*(?:\w+(?:<[^>]*>)?(?:\[\])*)\s+(\w+)\s*\(", re.MULTILINE),
    "Ruby":       re.compile(r"^\s*def\s+(?:self\.)?(\w+[?!=]?)", re.MULTILINE),
    "Kotlin":     re.compile(r"^\s*(?:override\s+|open\s+|private\s+|protected\s+|internal\s+|public\s+)*fun\s+(\w+)\s*\(", re.MULTILINE),
    "Go":         re.compile(r"^func\s+\(\w+\s+\*?\w+\)\s+(\w+)\s*\(", re.MULTILINE),
    "Swift":      re.compile(r"^\s*(?:open\s+|public\s+|internal\s+|fileprivate\s+|private\s+)?(?:override\s+)?(?:class\s+|static\s+)?func\s+(\w+)\s*\(", re.MULTILINE),
    "C++":        re.compile(r"^\s*(?:virtual\s+|static\s+|inline\s+)?(?:\w+(?:::\w+)?(?:<[^>]*>)?[*&\s]+)(\w+)\s*\([^)]*\)\s*(?:const\s*)?(?:override\s*)?(?:=\s*0\s*)?[{;]", re.MULTILINE),
    "PHP":        re.compile(r"^\s*(?:public|private|protected|static|\s)*function\s+(\w+)\s*\(", re.MULTILINE),
}

# Class-end detection: language-specific heuristics.
_CLASS_END_RUBY = re.compile(r"^\s*end\b", re.MULTILINE)


def _analyze_regex_file(filepath: str, source: str, language: str) -> list[ClassInfo]:
    """Heuristic regex-based class/method extraction for non-Python files."""
    class_pat = _CLASS_PATTERNS.get(language)
    method_pat = _METHOD_PATTERNS.get(language)
    if class_pat is None:
        return []

    lines = source.splitlines()
    classes: list[ClassInfo] = []

    # Find all class start positions.
    class_matches: list[Tuple[int, str, int]] = []  # (line_number, name, char_offset)
    for m in class_pat.finditer(source):
        line_no = source[:m.start()].count("\n") + 1
        class_matches.append((line_no, m.group(1), m.start()))

    for idx, (line_no, name, char_offset) in enumerate(class_matches):
        # Determine class end line.
        if language == "Ruby":
            line_end = _find_ruby_class_end(lines, line_no)
        elif language == "Go":
            line_end = _find_brace_end(lines, line_no)
        else:
            line_end = _find_brace_end(lines, line_no)

        # Clamp to next class start if needed.
        if idx + 1 < len(class_matches):
            next_start = class_matches[idx + 1][0]
            if line_end >= next_start:
                line_end = next_start - 1

        cls = ClassInfo(
            name=name,
            line_start=line_no,
            line_end=line_end,
            language=language,
        )

        # Extract methods within the class line range.
        if method_pat is not None:
            class_source = "\n".join(lines[line_no - 1:line_end])
            for mm in method_pat.finditer(class_source):
                m_line = class_source[:mm.start()].count("\n") + line_no
                mname = mm.group(1)
                # Skip common false positives.
                if mname in ("if", "for", "while", "switch", "catch", "class", "new", "return", "else"):
                    continue
                cls.methods.append(MethodInfo(
                    name=mname,
                    line_start=m_line,
                    line_end=m_line,  # single-line approx for regex mode
                ))

        classes.append(cls)

    return classes


def _find_brace_end(lines: list[str], start_line: int) -> int:
    """Find the closing brace that ends a block starting near start_line."""
    depth = 0
    found_open = False
    for i in range(start_line - 1, len(lines)):
        for ch in lines[i]:
            if ch == "{":
                depth += 1
                found_open = True
            elif ch == "}":
                depth -= 1
                if found_open and depth == 0:
                    return i + 1  # 1-indexed
    return len(lines)


def _find_ruby_class_end(lines: list[str], start_line: int) -> int:
    """Find the matching 'end' for a Ruby class definition."""
    depth = 0
    for i in range(start_line - 1, len(lines)):
        stripped = lines[i].strip()
        # Count openers.
        if re.match(r"(class|module|def|do|if|unless|case|while|until|for|begin)\b", stripped):
            depth += 1
        # One-line blocks don't count.
        if stripped == "end" or re.match(r"end\b", stripped):
            depth -= 1
            if depth <= 0:
                return i + 1
    return len(lines)


# ---------------------------------------------------------------------------
# Concern analysis
# ---------------------------------------------------------------------------

def _split_method_name(name: str) -> list[str]:
    """Split a method name into constituent words."""
    # Handle snake_case.
    if "_" in name:
        parts = [p.lower() for p in name.split("_") if p]
    else:
        # Handle camelCase / PascalCase.
        parts = [p.lower() for p in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\d|\b)", name)]
    return parts


def _infer_concern(method: MethodInfo) -> Optional[str]:
    """Infer the concern of a method from its name."""
    words = _split_method_name(method.name)
    if not words:
        return None
    # Check the first word (verb) against the concern map.
    verb = words[0]
    if verb in CONCERN_VERBS:
        return CONCERN_VERBS[verb]
    # Also check two-word prefixes for compound names.
    if len(words) >= 2:
        compound = words[0] + "_" + words[1]
        if compound in CONCERN_VERBS:
            return CONCERN_VERBS[compound]
    return None


def _group_methods_by_concern(methods: list[MethodInfo]) -> dict[str, Concern]:
    """Group methods by their inferred concern."""
    concerns: dict[str, Concern] = {}
    for method in methods:
        # Skip dunder / magic methods.
        if method.name.startswith("__") and method.name.endswith("__"):
            continue
        concern_label = _infer_concern(method)
        if concern_label is None:
            continue
        method.concern = concern_label
        if concern_label not in concerns:
            concerns[concern_label] = Concern(label=concern_label)
        concerns[concern_label].methods.append(method)
    return concerns


# ---------------------------------------------------------------------------
# Import fan-in (Python only)
# ---------------------------------------------------------------------------

def _analyze_import_fanin(imports: list[str]) -> Tuple[bool, dict[str, list[str]]]:
    """Determine if a Python class has a high import fan-in.

    Returns (is_high, groups) where groups maps group-label to module names.
    A fan-in is considered high when imports span 4+ distinct groups.
    """
    groups: dict[str, list[str]] = defaultdict(list)
    for mod in imports:
        grp = IMPORT_GROUPS.get(mod, None)
        if grp:
            groups[grp].append(mod)
    return len(groups) >= 4, dict(groups)


# ---------------------------------------------------------------------------
# Analysis orchestrator
# ---------------------------------------------------------------------------

def analyze_file(filepath: str, max_methods: int, max_lines: int) -> FileReport:
    """Analyze a single file and produce a report."""
    ext = Path(filepath).suffix.lower()
    language = LANGUAGE_MAP.get(ext, "Unknown")
    report = FileReport(filepath=filepath, language=language)

    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            source = f.read()
    except OSError as exc:
        report.error = str(exc)
        return report

    if language == "Python":
        classes = _analyze_python_file(filepath, source)
    else:
        classes = _analyze_regex_file(filepath, source, language)

    for cls in classes:
        cr = ClassReport(cls=cls)
        num_lines = cls.line_end - cls.line_start + 1
        num_methods = len(cls.methods)

        # Check 1: too many methods.
        if num_methods > max_methods:
            cr.warnings.append(SRPWarning(
                kind="too_many_methods",
                message=f"Class has {num_methods} methods (threshold: {max_methods}) — may have multiple responsibilities",
                details={"count": num_methods, "threshold": max_methods},
            ))

        # Check 2: too many lines.
        if num_lines > max_lines:
            cr.warnings.append(SRPWarning(
                kind="too_many_lines",
                message=f"Class has {num_lines} lines (threshold: {max_lines}) — may be too large",
                details={"count": num_lines, "threshold": max_lines},
            ))

        # Check 3: mixed concerns.
        concerns = _group_methods_by_concern(cls.methods)
        cr.concerns = concerns
        if len(concerns) >= 2:
            labels = sorted(concerns.keys())
            cr.warnings.append(SRPWarning(
                kind="mixed_concerns",
                message=f"Detected mixed concerns: methods suggest both {_join_labels(labels)} responsibilities",
                details={
                    "concerns": {
                        label: [m.name for m in c.methods]
                        for label, c in concerns.items()
                    },
                },
            ))

        # Check 4: import fan-in (Python only).
        if language == "Python" and cls.imports:
            is_high, groups = _analyze_import_fanin(cls.imports)
            if is_high:
                cr.warnings.append(SRPWarning(
                    kind="high_import_fanin",
                    message=f"High import fan-in: module imports span {len(groups)} distinct groups ({', '.join(sorted(groups.keys()))})",
                    details={"groups": groups},
                ))

        report.class_reports.append(cr)

    return report


def _join_labels(labels: list[str]) -> str:
    """Human-readable joining of concern labels."""
    if len(labels) == 2:
        return f'"{labels[0]}" and "{labels[1]}"'
    return ", ".join(f'"{l}"' for l in labels[:-1]) + f', and "{labels[-1]}"'


# ---------------------------------------------------------------------------
# Output formatting — plain text
# ---------------------------------------------------------------------------

def _format_plain(reports: list[FileReport], verbose: bool, rewrite: bool) -> str:
    """Format reports as human-readable plain text."""
    parts: list[str] = []
    for report in reports:
        parts.append(f"=== SRP Analysis: {report.filepath} ===")
        parts.append("")

        if report.error:
            parts.append(f"  [ERROR] {report.error}")
            parts.append("")
            continue

        if not report.class_reports:
            parts.append("  No classes found in this file.")
            parts.append("")
            continue

        for cr in report.class_reports:
            cls = cr.cls
            num_lines = cls.line_end - cls.line_start + 1
            num_methods = len(cls.methods)
            parts.append(
                f"Class: {cls.name} (lines {cls.line_start}-{cls.line_end}, "
                f"{num_lines} lines, {num_methods} methods)"
            )

            if not cr.has_concerns:
                parts.append("  [OK] No SRP concerns detected")
            else:
                for w in cr.warnings:
                    parts.append(f"  [WARNING] {w.message}")

                    if verbose and w.kind == "mixed_concerns":
                        for label, methods in w.details.get("concerns", {}).items():
                            parts.append(f"    - {label.capitalize()}-related: {', '.join(methods)}")

                    if verbose and w.kind == "high_import_fanin":
                        for grp, mods in w.details.get("groups", {}).items():
                            parts.append(f"    - {grp}: {', '.join(mods)}")

                # Suggestions.
                suggestions = _generate_suggestions(cr)
                for s in suggestions:
                    parts.append(f"  [SUGGESTION] {s}")

            parts.append("")

        # Rewrite section.
        if rewrite:
            rewrite_text = _generate_rewrite(report)
            if rewrite_text:
                parts.append(rewrite_text)
                parts.append("")

    return "\n".join(parts)


def _generate_suggestions(cr: ClassReport) -> list[str]:
    """Generate actionable suggestions for a class report."""
    suggestions: list[str] = []
    cls = cr.cls

    if cr.concerns and len(cr.concerns) >= 2:
        # Suggest extracting the minority concerns.
        concern_items = sorted(cr.concerns.items(), key=lambda x: len(x[1].methods), reverse=True)
        primary = concern_items[0]
        for label, concern in concern_items[1:]:
            class_name_suggestion = _suggest_class_name(label)
            method_names = ", ".join(m.name for m in concern.methods)
            suggestions.append(
                f"Consider extracting {label} functionality ({method_names}) "
                f"into a dedicated {class_name_suggestion} class"
            )

    return suggestions


def _suggest_class_name(concern_label: str) -> str:
    """Generate a suggested class name from a concern label."""
    parts = concern_label.split("_")
    return "".join(p.capitalize() for p in parts) + "Handler"


# ---------------------------------------------------------------------------
# Output formatting — JSON
# ---------------------------------------------------------------------------

def _format_json(reports: list[FileReport], rewrite: bool) -> str:
    """Format reports as structured JSON."""
    output: list[dict] = []
    for report in reports:
        file_obj: dict = {
            "filepath": report.filepath,
            "language": report.language,
            "has_concerns": report.has_concerns,
            "classes": [],
        }
        if report.error:
            file_obj["error"] = report.error
            output.append(file_obj)
            continue

        for cr in report.class_reports:
            cls = cr.cls
            cls_obj: dict = {
                "name": cls.name,
                "line_start": cls.line_start,
                "line_end": cls.line_end,
                "num_lines": cls.line_end - cls.line_start + 1,
                "num_methods": len(cls.methods),
                "has_concerns": cr.has_concerns,
                "methods": [
                    {
                        "name": m.name,
                        "line_start": m.line_start,
                        "line_end": m.line_end,
                        "concern": m.concern,
                    }
                    for m in cls.methods
                ],
                "warnings": [
                    {
                        "kind": w.kind,
                        "message": w.message,
                        "details": w.details,
                    }
                    for w in cr.warnings
                ],
                "suggestions": _generate_suggestions(cr),
            }

            if rewrite and cr.has_concerns:
                cls_obj["rewrite"] = _generate_rewrite_for_class(cr)

            file_obj["classes"].append(cls_obj)

        output.append(file_obj)

    return json.dumps(output, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Rewrite suggestion generation
# ---------------------------------------------------------------------------

def _generate_rewrite(report: FileReport) -> str:
    """Generate refactored code suggestions for all problematic classes in a file."""
    parts: list[str] = []
    for cr in report.class_reports:
        if not cr.has_concerns or len(cr.concerns) < 2:
            continue
        text = _generate_rewrite_for_class(cr)
        if text:
            parts.append(text)
    return "\n".join(parts)


def _generate_rewrite_for_class(cr: ClassReport) -> str:
    """Generate a refactored code suggestion for a single class."""
    if len(cr.concerns) < 2:
        return ""

    cls = cr.cls
    lang = cls.language
    lines: list[str] = []
    lines.append(f"--- Suggested refactoring for {cls.name} ({lang}) ---")
    lines.append("")

    # Sort concerns: largest group first (primary), rest are extracted.
    concern_items = sorted(cr.concerns.items(), key=lambda x: len(x[1].methods), reverse=True)
    primary_label, primary_concern = concern_items[0]
    extracted = concern_items[1:]

    # Collect methods that don't belong to any identified concern.
    all_concern_methods = set()
    for _, c in cr.concerns.items():
        for m in c.methods:
            all_concern_methods.add(m.name)
    uncategorized = [m for m in cls.methods if m.name not in all_concern_methods]

    if lang == "Python":
        lines.extend(_rewrite_python(cls, primary_label, primary_concern, extracted, uncategorized))
    else:
        lines.extend(_rewrite_generic(cls, primary_label, primary_concern, extracted, uncategorized))

    lines.append(f"--- End of suggested refactoring for {cls.name} ---")
    return "\n".join(lines)


def _rewrite_python(
    cls: ClassInfo,
    primary_label: str,
    primary_concern: Concern,
    extracted: list[Tuple[str, Concern]],
    uncategorized: list[MethodInfo],
) -> list[str]:
    """Generate Python-style refactored code."""
    lines: list[str] = []

    # Generate extracted classes first.
    for label, concern in extracted:
        cname = _suggest_class_name(label)
        lines.append(f"class {cname}:")
        lines.append(f'    """Handles {label} responsibilities extracted from {cls.name}."""')
        lines.append("")
        for m in concern.methods:
            lines.append(f"    def {m.name}(self, ...):")
            lines.append(f"        # Moved from {cls.name} (originally at line {m.line_start})")
            lines.append(f"        ...")
            lines.append("")
        lines.append("")

    # Generate slimmed-down original class.
    lines.append(f"class {cls.name}:")
    lines.append(f'    """Refactored to focus on {primary_label} responsibility."""')
    lines.append("")

    # Constructor with injected dependencies.
    init_params = []
    init_body = []
    for label, concern in extracted:
        attr_name = _to_snake_case(label) + "_handler"
        cname = _suggest_class_name(label)
        init_params.append(f"{attr_name}: {cname}")
        init_body.append(f"        self._{attr_name} = {attr_name}")

    if init_params:
        lines.append(f"    def __init__(self, {', '.join(init_params)}):")
        for b in init_body:
            lines.append(b)
        lines.append("")

    # Primary methods stay.
    for m in primary_concern.methods:
        lines.append(f"    def {m.name}(self, ...):")
        lines.append(f"        # Original implementation (line {m.line_start})")
        lines.append(f"        ...")
        lines.append("")

    # Uncategorized methods stay.
    for m in uncategorized:
        lines.append(f"    def {m.name}(self, ...):")
        lines.append(f"        # Original implementation (line {m.line_start})")
        lines.append(f"        ...")
        lines.append("")

    # Delegating wrappers for extracted methods.
    for label, concern in extracted:
        attr_name = _to_snake_case(label) + "_handler"
        for m in concern.methods:
            lines.append(f"    def {m.name}(self, *args, **kwargs):")
            lines.append(f"        # Delegate to {_suggest_class_name(label)}")
            lines.append(f"        return self._{attr_name}.{m.name}(*args, **kwargs)")
            lines.append("")

    return lines


def _rewrite_generic(
    cls: ClassInfo,
    primary_label: str,
    primary_concern: Concern,
    extracted: list[Tuple[str, Concern]],
    uncategorized: list[MethodInfo],
) -> list[str]:
    """Generate generic pseudocode-style refactored code."""
    lines: list[str] = []

    for label, concern in extracted:
        cname = _suggest_class_name(label)
        lines.append(f"// New class: {cname}")
        lines.append(f"class {cname} {{")
        for m in concern.methods:
            lines.append(f"    {m.name}(...) {{")
            lines.append(f"        // Moved from {cls.name} (originally at line {m.line_start})")
            lines.append(f"    }}")
        lines.append(f"}}")
        lines.append("")

    lines.append(f"// Refactored: {cls.name} (focused on {primary_label})")
    lines.append(f"class {cls.name} {{")

    # Constructor with dependencies.
    for label, concern in extracted:
        attr_name = _to_snake_case(label) + "Handler"
        cname = _suggest_class_name(label)
        lines.append(f"    private {cname} {attr_name};")
    lines.append("")

    for m in primary_concern.methods:
        lines.append(f"    {m.name}(...) {{")
        lines.append(f"        // Original implementation (line {m.line_start})")
        lines.append(f"    }}")

    for m in uncategorized:
        lines.append(f"    {m.name}(...) {{")
        lines.append(f"        // Original implementation (line {m.line_start})")
        lines.append(f"    }}")

    for label, concern in extracted:
        attr_name = _to_snake_case(label) + "Handler"
        for m in concern.methods:
            lines.append(f"    {m.name}(...) {{")
            lines.append(f"        // Delegate to {_suggest_class_name(label)}")
            lines.append(f"        return this.{attr_name}.{m.name}(...);")
            lines.append(f"    }}")

    lines.append(f"}}")
    lines.append("")

    return lines


def _to_snake_case(s: str) -> str:
    """Convert a concern label to snake_case."""
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

def discover_files(path: str) -> list[str]:
    """Recursively discover supported source files under a path."""
    target = Path(path)
    if target.is_file():
        if target.suffix.lower() in SUPPORTED_EXTENSIONS:
            return [str(target)]
        return []
    elif target.is_dir():
        files: list[str] = []
        for root, _dirs, filenames in os.walk(target):
            # Skip hidden directories and common non-source directories.
            _dirs[:] = [
                d for d in _dirs
                if not d.startswith(".")
                and d not in ("node_modules", "__pycache__", "venv", ".venv", "dist", "build", "vendor")
            ]
            for fname in sorted(filenames):
                if Path(fname).suffix.lower() in SUPPORTED_EXTENSIONS:
                    files.append(os.path.join(root, fname))
        return files
    return []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="check_srp",
        description="Detect Single Responsibility Principle violations in source code.",
    )
    parser.add_argument(
        "path",
        help="File or directory to analyze",
    )
    parser.add_argument(
        "--max-methods",
        type=int,
        default=10,
        metavar="N",
        help="Maximum number of methods before flagging (default: 10)",
    )
    parser.add_argument(
        "--max-lines",
        type=int,
        default=200,
        metavar="N",
        help="Maximum number of lines before flagging (default: 200)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show detailed output including per-concern method listings",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output results as structured JSON",
    )
    parser.add_argument(
        "--rewrite",
        action="store_true",
        help="Include suggested refactored code for problematic classes",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    target = args.path
    if not os.path.exists(target):
        print(f"Error: path does not exist: {target}", file=sys.stderr)
        return 2

    files = discover_files(target)
    if not files:
        print(f"Error: no supported source files found in: {target}", file=sys.stderr)
        return 2

    reports: list[FileReport] = []
    for filepath in files:
        report = analyze_file(filepath, args.max_methods, args.max_lines)
        reports.append(report)

    if args.json_output:
        print(_format_json(reports, args.rewrite))
    else:
        print(_format_plain(reports, args.verbose, args.rewrite))

    has_any_concern = any(r.has_concerns for r in reports)
    return 1 if has_any_concern else 0


if __name__ == "__main__":
    sys.exit(main())
