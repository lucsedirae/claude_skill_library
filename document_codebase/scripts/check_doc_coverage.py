#!/usr/bin/env python3
"""
check_doc_coverage.py — Documentation Coverage Checker

Analyzes source files to report undocumented public symbols:
  1. Module-level docstring — first statement of the file
  2. Public function/method docstrings — functions not prefixed with _ or __
  3. Public class docstrings

For Python: uses the ast module for precise extraction.
For other languages: regex-based heuristics that look before and after
  a declaration for a doc-comment marker (/** ... */, ///, #).

Private symbols are excluded using language-specific conventions:
  Python      — names starting with _ or __
  Java/C#/Kotlin/Swift — explicit private/protected modifiers
  Go          — unexported (lowercase-first) names
  JavaScript/TypeScript — names starting with _
  PHP         — private/protected modifiers

Exit codes:
    0 — No missing documentation found (or all files meet --min-coverage)
    1 — One or more undocumented symbols found
    2 — Input error (file/directory not found, no eligible files, etc.)

Usage:
    python check_doc_coverage.py path/to/file_or_directory
    python check_doc_coverage.py src/ --verbose
    python check_doc_coverage.py src/ --json
    python check_doc_coverage.py src/ --rewrite
    python check_doc_coverage.py src/ --min-coverage 80
"""

from __future__ import annotations

import argparse
import ast
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

# Lines to scan before a declaration for a doc comment
DOC_COMMENT_LOOKBEHIND_LINES = 5
# Lines to scan after a declaration for a doc comment
DOC_COMMENT_LOOKAHEAD_LINES = 3

# Doc-comment opening markers (presence of any of these signals documentation)
DOC_COMMENT_MARKERS: list[str] = [
    '"""', "'''", "/**", "///", "##",
]

# Regex patterns for function/method declarations per extension
_FUNC_PATTERNS: dict[str, re.Pattern] = {
    ".java": re.compile(
        r"^\s*(?:(?:public|protected|static|final|abstract|synchronized|native|strictfp)\s+)*"
        r"(?:[\w<>\[\]]+\s+)(\w+)\s*\([^)]*\)\s*(?:throws\s+\w+(?:\s*,\s*\w+)*)?\s*\{"
    ),
    ".cs": re.compile(
        r"^\s*(?:(?:public|protected|internal|static|virtual|override|abstract|async|sealed|extern|new)\s+)*"
        r"(?:[\w<>\[\]?]+\s+)(\w+)\s*\([^)]*\)\s*(?:where\s+\w+[^{]*)?\{"
    ),
    ".ts": re.compile(
        r"^\s*(?:(?:export|public|protected|private|static|async|abstract|override)\s+)*"
        r"(?:function\s+(\w+)|(\w+)\s*[=:]\s*(?:async\s+)?(?:function|\([^)]*\)\s*=>|\w+\s*=>))"
    ),
    ".js": re.compile(
        r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\("
        r"|^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?(?:function|\()"
    ),
    ".kt": re.compile(
        r"^\s*(?:(?:public|protected|internal|private|override|open|abstract|inline|suspend|external|operator)\s+)*"
        r"fun\s+(\w+)\s*\("
    ),
    ".go": re.compile(
        r"^func\s+(?:\([^)]+\)\s+)?(\w+)\s*\("
    ),
    ".swift": re.compile(
        r"^\s*(?:(?:public|internal|fileprivate|private|open|static|class|override|final|mutating|nonmutating)\s+)*"
        r"func\s+(\w+)\s*[(<]"
    ),
    ".rb": re.compile(
        r"^\s*def\s+(self\.)?(\w+)"
    ),
    ".php": re.compile(
        r"^\s*(?:(?:public|protected|private|static|abstract|final)\s+)*"
        r"function\s+(\w+)\s*\("
    ),
    ".cpp": re.compile(
        r"^\s*(?:[\w:*&<>\[\]]+\s+)+(\w+)\s*\([^;]*\)\s*(?:const\s*)?(?:override\s*)?(?:noexcept\s*)?\{"
    ),
    ".hpp": re.compile(
        r"^\s*(?:[\w:*&<>\[\]]+\s+)+(\w+)\s*\([^;]*\)\s*(?:const\s*)?(?:override\s*)?(?:noexcept\s*)?\{"
    ),
}

_CLASS_PATTERNS: dict[str, re.Pattern] = {
    ".java": re.compile(r"^\s*(?:(?:public|protected|private|static|abstract|final)\s+)*(?:class|interface|enum|record)\s+(\w+)"),
    ".cs": re.compile(r"^\s*(?:(?:public|protected|internal|private|static|abstract|sealed|partial)\s+)*(?:class|interface|struct|enum|record)\s+(\w+)"),
    ".ts": re.compile(r"^\s*(?:(?:export|abstract)\s+)*class\s+(\w+)"),
    ".js": re.compile(r"^\s*(?:export\s+)?class\s+(\w+)"),
    ".kt": re.compile(r"^\s*(?:(?:public|protected|internal|private|open|abstract|sealed|data|inner)\s+)*(?:class|interface|object|enum class)\s+(\w+)"),
    ".go": re.compile(r"^type\s+(\w+)\s+struct\b"),
    ".swift": re.compile(r"^\s*(?:(?:public|internal|fileprivate|private|open|final)\s+)*(?:class|struct|protocol|enum|actor)\s+(\w+)"),
    ".rb": re.compile(r"^\s*(?:module|class)\s+(\w+)"),
    ".php": re.compile(r"^\s*(?:(?:abstract|final)\s+)*(?:class|interface|trait|enum)\s+(\w+)"),
    ".cpp": re.compile(r"^\s*(?:class|struct)\s+(\w+)\b"),
    ".hpp": re.compile(r"^\s*(?:class|struct)\s+(\w+)\b"),
}

# Private modifiers to detect in non-Python languages
_PRIVATE_MODIFIERS: dict[str, re.Pattern] = {
    ".java": re.compile(r"\b(private|protected)\b"),
    ".cs": re.compile(r"\b(private|protected)\b"),
    ".ts": re.compile(r"\b(private|protected)\b"),
    ".kt": re.compile(r"\b(private|protected)\b"),
    ".swift": re.compile(r"\b(private|fileprivate)\b"),
    ".php": re.compile(r"\b(private|protected)\b"),
    ".cpp": re.compile(r"\b(private|protected)\b"),
    ".hpp": re.compile(r"\b(private|protected)\b"),
}

# Stub docstring templates per extension
_STUB_TEMPLATES: dict[str, dict[str, str]] = {
    "python_func": '    """TODO: document this function."""',
    "python_class": '    """TODO: document this class."""',
    "python_module": '"""TODO: document this module."""\n',
    "jsdoc": "/** TODO: document this. */",
    "xmldoc": "/// <summary>TODO: document this.</summary>",
    "godoc_prefix": "// {name} TODO: document this.",
    "hash": "# TODO: document this.",
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class DocFinding:
    """A single undocumented public symbol found in a source file."""

    kind: str           # "missing_module_doc" | "missing_function_doc" | "missing_class_doc"
    symbol_name: str
    line: int
    message: str


@dataclass
class FileReport:
    """Aggregated documentation-coverage findings for one source file."""

    filepath: str
    language: str
    findings: list[DocFinding] = field(default_factory=list)
    total_public_symbols: int = 0
    documented_symbols: int = 0
    error: Optional[str] = None

    @property
    def coverage_pct(self) -> float:
        """Return percentage of public symbols that have documentation."""
        if self.total_public_symbols == 0:
            return 100.0
        return 100.0 * self.documented_symbols / self.total_public_symbols


# ---------------------------------------------------------------------------
# Python analysis (AST-based)
# ---------------------------------------------------------------------------

def _analyze_python(filepath: str) -> FileReport:
    report = FileReport(filepath=filepath, language="Python")
    try:
        source = Path(filepath).read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=filepath)
    except SyntaxError as exc:
        report.error = f"SyntaxError: {exc}"
        return report
    except OSError as exc:
        report.error = str(exc)
        return report

    # Module-level docstring
    module_doc = ast.get_docstring(tree)
    if module_doc:
        report.documented_symbols += 1
    else:
        report.findings.append(DocFinding(
            kind="missing_module_doc",
            symbol_name="<module>",
            line=1,
            message="Module has no docstring",
        ))
    report.total_public_symbols += 1

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        name = node.name
        # Skip private names
        if name.startswith("_"):
            continue

        kind = "missing_class_doc" if isinstance(node, ast.ClassDef) else "missing_function_doc"
        report.total_public_symbols += 1

        doc = ast.get_docstring(node)
        if doc:
            report.documented_symbols += 1
        else:
            report.findings.append(DocFinding(
                kind=kind,
                symbol_name=name,
                line=node.lineno,
                message=f"{'Class' if isinstance(node, ast.ClassDef) else 'Function'} '{name}' has no docstring",
            ))

    return report


# ---------------------------------------------------------------------------
# Non-Python analysis (regex-based)
# ---------------------------------------------------------------------------

def _has_doc_comment_nearby(
    lines: list[str],
    decl_lineno: int,  # 1-based
) -> bool:
    """Return True if a doc-comment marker appears near the declaration."""
    # Look in the lines immediately BEFORE the declaration (Java/C#/TS style)
    start_before = max(0, decl_lineno - 1 - DOC_COMMENT_LOOKBEHIND_LINES)
    end_before = decl_lineno - 1  # exclusive, 0-based
    for i in range(end_before - 1, start_before - 1, -1):
        for marker in DOC_COMMENT_MARKERS:
            if marker in lines[i]:
                return True
        # Stop looking back if we hit a blank line or another declaration
        if lines[i].strip() == "" and i < end_before - 1:
            break

    # Look AFTER the declaration (Python / Ruby style)
    start_after = decl_lineno  # 0-based
    end_after = min(len(lines), decl_lineno + DOC_COMMENT_LOOKAHEAD_LINES)
    for i in range(start_after, end_after):
        for marker in DOC_COMMENT_MARKERS:
            if marker in lines[i]:
                return True

    return False


def _is_private_symbol(line: str, name: str, ext: str) -> bool:
    """Return True if the symbol should be excluded from coverage checks."""
    # Underscore prefix convention (Python-style, also JS/TS)
    if name.startswith("_"):
        return True
    # Go: unexported names start with lowercase
    if ext == ".go" and name and name[0].islower():
        return True
    # Explicit private/protected modifier
    priv_re = _PRIVATE_MODIFIERS.get(ext)
    if priv_re and priv_re.search(line):
        return True
    return False


def _analyze_generic(filepath: str) -> FileReport:
    ext = Path(filepath).suffix
    report = FileReport(filepath=filepath, language=LANGUAGE_MAP.get(ext, "Unknown"))

    try:
        with open(filepath, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError as exc:
        report.error = str(exc)
        return report

    func_re = _FUNC_PATTERNS.get(ext)
    class_re = _CLASS_PATTERNS.get(ext)

    # Check module-level doc comment (first non-blank, non-import line)
    for i, raw in enumerate(lines[:10]):
        stripped = raw.strip()
        if not stripped:
            continue
        has_doc = any(m in stripped for m in DOC_COMMENT_MARKERS)
        report.total_public_symbols += 1
        if has_doc:
            report.documented_symbols += 1
        else:
            report.findings.append(DocFinding(
                kind="missing_module_doc",
                symbol_name="<module>",
                line=i + 1,
                message="File has no module-level doc comment",
            ))
        break

    for lineno, raw_line in enumerate(lines, start=1):
        # Check functions
        if func_re:
            m = func_re.match(raw_line)
            if m:
                # Extract name from the first non-None group
                name = next((g for g in m.groups() if g), "<unknown>")
                if not _is_private_symbol(raw_line, name, ext):
                    report.total_public_symbols += 1
                    if _has_doc_comment_nearby(lines, lineno):
                        report.documented_symbols += 1
                    else:
                        report.findings.append(DocFinding(
                            kind="missing_function_doc",
                            symbol_name=name,
                            line=lineno,
                            message=f"Function '{name}' has no doc comment",
                        ))

        # Check classes/structs
        if class_re:
            m = class_re.match(raw_line)
            if m:
                name = m.group(1)
                if not _is_private_symbol(raw_line, name, ext):
                    report.total_public_symbols += 1
                    if _has_doc_comment_nearby(lines, lineno):
                        report.documented_symbols += 1
                    else:
                        report.findings.append(DocFinding(
                            kind="missing_class_doc",
                            symbol_name=name,
                            line=lineno,
                            message=f"Class '{name}' has no doc comment",
                        ))

    return report


# ---------------------------------------------------------------------------
# Rewrite (stub insertion)
# ---------------------------------------------------------------------------

def _get_indentation(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def rewrite_python(filepath: str, findings: list[DocFinding]) -> int:
    """Insert stub docstrings into undocumented Python symbols."""
    if not findings:
        return 0

    target_lines = {f.line for f in findings}

    try:
        source = Path(filepath).read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
    except (SyntaxError, OSError):
        return 0

    lines = source.splitlines(keepends=True)
    insertions: list[tuple[int, str]] = []  # (0-based insert-before index, text)

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            continue
        if isinstance(node, ast.Module):
            if 1 in target_lines and not ast.get_docstring(node):
                insertions.append((0, '"""TODO: document this module."""\n\n'))
        else:
            if node.lineno in target_lines and not ast.get_docstring(node):
                # Insert after the def/class line
                indent = _get_indentation(lines[node.lineno - 1]) + "    "
                stub = f'{indent}"""TODO: document this.\"\"\"\n'
                # node.body[0] is where the docstring should go
                insert_pos = node.body[0].lineno - 1  # 0-based
                insertions.append((insert_pos, stub))

    if not insertions:
        return 0

    # Apply insertions in reverse order to preserve line numbers
    for pos, text in sorted(insertions, reverse=True):
        lines.insert(pos, text)

    Path(filepath).write_text("".join(lines), encoding="utf-8")
    return len(insertions)


def rewrite_generic(filepath: str, findings: list[DocFinding], ext: str) -> int:
    """Insert stub doc comments before undocumented symbols in non-Python files."""
    if not findings:
        return 0

    target_lines = {f.line: f for f in findings}

    try:
        with open(filepath, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return 0

    new_lines = []
    changed = 0
    for lineno, raw_line in enumerate(lines, start=1):
        if lineno in target_lines:
            finding = target_lines[lineno]
            indent = _get_indentation(raw_line)
            if ext == ".go":
                stub = f"{indent}// {finding.symbol_name} TODO: document this.\n"
            elif ext in (".cs",):
                stub = f"{indent}/// <summary>TODO: document this.</summary>\n"
            else:
                stub = f"{indent}/** TODO: document this. */\n"
            new_lines.append(stub)
            changed += 1
        new_lines.append(raw_line)

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
# Analysis dispatch
# ---------------------------------------------------------------------------

def analyze_file(filepath: str) -> FileReport:
    """Dispatch to the Python AST analyzer or the regex-based generic analyzer."""
    ext = Path(filepath).suffix
    if ext == ".py":
        return _analyze_python(filepath)
    return _analyze_generic(filepath)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def print_report(reports: list[FileReport], verbose: bool, min_coverage: int) -> None:
    """Print a human-readable summary of documentation-coverage findings to stdout."""
    any_findings = False
    for report in reports:
        if report.error:
            print(f"ERROR: {report.filepath}: {report.error}", file=sys.stderr)
            continue

        cov = report.coverage_pct
        below_threshold = min_coverage > 0 and cov < min_coverage

        if not report.findings and not below_threshold:
            if verbose:
                print(f"  OK  {report.filepath}  ({cov:.0f}% documented)")
            continue

        any_findings = True
        total = report.total_public_symbols
        documented = report.documented_symbols
        print(f"\n=== Doc Coverage Analysis: {report.filepath} ===")
        print(f"Coverage: {documented}/{total} public symbols documented ({cov:.0f}%)\n")
        for f in report.findings:
            label = f"[{f.kind.upper()}]"
            print(f"  Line {f.line}: {label} {f.symbol_name} — {f.message}")
        print()

    if not any_findings:
        print("No documentation gaps found.")


def print_json(reports: list[FileReport], path: str) -> None:
    """Print a JSON summary of documentation-coverage findings to stdout."""
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
            "coverage_pct": round(report.coverage_pct, 1),
            "total_public_symbols": report.total_public_symbols,
            "documented_symbols": report.documented_symbols,
            "findings": [
                {
                    "kind": f.kind,
                    "symbol_name": f.symbol_name,
                    "line": f.line,
                    "message": f.message,
                }
                for f in report.findings
            ],
        })

    print(json.dumps({
        "script": "check_doc_coverage",
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
        description="Check documentation coverage for public functions, classes, and modules."
    )
    parser.add_argument("path", help="File or directory to analyze")
    parser.add_argument("--verbose", action="store_true",
                        help="Show all files including fully documented ones")
    parser.add_argument("--json", action="store_true", dest="json_output",
                        help="Output results as JSON")
    parser.add_argument("--rewrite", action="store_true",
                        help="Insert stub docstrings for undocumented symbols in-place")
    parser.add_argument("--min-coverage", type=int, default=0, metavar="PCT",
                        help="Fail if any file is below this documentation percentage (0 = off)")
    args = parser.parse_args()

    files = collect_files(args.path)
    if not files:
        print(f"Error: no supported source files found at '{args.path}'", file=sys.stderr)
        return 2

    reports = [analyze_file(f) for f in files]

    if args.rewrite:
        for report in reports:
            if report.findings and not report.error:
                ext = Path(report.filepath).suffix
                if ext == ".py":
                    changed = rewrite_python(report.filepath, report.findings)
                else:
                    changed = rewrite_generic(report.filepath, report.findings, ext)
                if changed:
                    print(f"Inserted {changed} stub docstring(s) in {report.filepath}")

    if args.json_output:
        print_json(reports, args.path)
    else:
        print_report(reports, args.verbose, args.min_coverage)

    has_findings = any(r.findings for r in reports if not r.error)
    if args.min_coverage > 0:
        below = any(
            r.coverage_pct < args.min_coverage
            for r in reports
            if not r.error
        )
        has_findings = has_findings or below

    return 1 if has_findings else 0


if __name__ == "__main__":
    sys.exit(main())
