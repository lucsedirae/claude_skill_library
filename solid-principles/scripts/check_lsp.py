#!/usr/bin/env python3
"""
check_lsp.py — Liskov Substitution Principle (LSP) Violation Detector

Detects common LSP violations in object-oriented source code by analyzing class
hierarchies and method overrides. Works language-agnostically: uses Python's ast
module for .py files and regex-based heuristics for other OOP languages.

Detected violations:
  1. Subclass methods raising NotImplementedError / NotSupportedError /
     UnsupportedOperationException (subclass refuses base class contract).
  2. Overridden methods with changed return types (where detectable).
  3. Overridden methods that add preconditions (e.g., isinstance checks the
     base class does not perform).
  4. Empty method overrides (pass in Python, empty {} bodies elsewhere).
  5. Methods that throw exceptions not thrown by the parent.

Supported file types:
  .py .java .ts .js .cs .rb .kt .go .swift .cpp .hpp .php

Usage:
  python check_lsp.py path/to/file_or_directory
  python check_lsp.py path/ --verbose
  python check_lsp.py path/ --json
  python check_lsp.py path/ --rewrite

Exit codes:
  0 — no concerns found
  1 — one or more concerns found
  2 — input error (bad path, no files, etc.)
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
import textwrap
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OOP_EXTENSIONS: Set[str] = {
    ".py", ".java", ".ts", ".js", ".cs", ".rb", ".kt", ".go",
    ".swift", ".cpp", ".hpp", ".php",
}

LANGUAGE_MAP: Dict[str, str] = {
    ".py": "python",
    ".java": "java",
    ".ts": "typescript",
    ".js": "javascript",
    ".cs": "csharp",
    ".rb": "ruby",
    ".kt": "kotlin",
    ".go": "go",
    ".swift": "swift",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".php": "php",
}

CONTRACT_BREAKING_EXCEPTIONS = {
    "NotImplementedError",
    "NotSupportedError",
    "NotSupportedException",
    "UnsupportedOperationException",
    "UnsupportedError",
}

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


class Severity(str, Enum):
    WARNING = "WARNING"
    INFO = "INFO"


@dataclass
class Concern:
    """A single LSP concern within a method."""
    severity: Severity
    method_name: str
    line: int
    message: str
    category: str  # e.g. "raises-not-implemented", "empty-override", etc.


@dataclass
class ClassReport:
    """All concerns for one class."""
    class_name: str
    base_classes: List[str]
    start_line: int
    end_line: int
    concerns: List[Concern] = field(default_factory=list)
    suggestion: Optional[str] = None


@dataclass
class FileReport:
    """All concerns for one file."""
    file_path: str
    language: str
    classes: List[ClassReport] = field(default_factory=list)
    parse_error: Optional[str] = None

    @property
    def total_concerns(self) -> int:
        return sum(len(c.concerns) for c in self.classes)


@dataclass
class RewriteSuggestion:
    """Suggested refactored code for a class with LSP violations."""
    class_name: str
    original_lines: Tuple[int, int]
    suggestion_text: str


# ---------------------------------------------------------------------------
# Python AST-based analysis
# ---------------------------------------------------------------------------


class _MethodInfo:
    """Extracted metadata about a single method definition."""

    def __init__(self, node: ast.FunctionDef):
        self.name: str = node.name
        self.node: ast.FunctionDef = node
        self.lineno: int = node.lineno
        self.end_lineno: int = node.end_lineno or node.lineno
        self.args: ast.arguments = node.args
        self.return_annotation: Optional[ast.expr] = node.returns
        self.raises: Set[str] = set()
        self.isinstance_checks: Set[str] = set()
        self.is_empty: bool = False
        self._analyse_body(node)

    # --- internal helpers ---------------------------------------------------

    def _analyse_body(self, node: ast.FunctionDef) -> None:
        """Walk the method body to gather raises / isinstance / emptiness."""
        body = node.body

        # Check for empty body: only `pass`, `...`, or a lone docstring
        effective = [
            s for s in body
            if not (
                isinstance(s, ast.Pass)
                or (isinstance(s, ast.Expr) and isinstance(s.value, (ast.Constant, ast.Ellipsis)))
            )
        ]
        if not effective:
            self.is_empty = True

        for child in ast.walk(node):
            # Collect raised exception names
            if isinstance(child, ast.Raise) and child.exc is not None:
                exc = child.exc
                if isinstance(exc, ast.Call):
                    exc = exc.func
                if isinstance(exc, ast.Name):
                    self.raises.add(exc.id)
                elif isinstance(exc, ast.Attribute):
                    self.raises.add(exc.attr)

            # Collect isinstance checks (precondition detection)
            if isinstance(child, ast.Call):
                func = child.func
                if isinstance(func, ast.Name) and func.id == "isinstance":
                    if len(child.args) >= 2:
                        type_arg = child.args[1]
                        self.isinstance_checks.add(ast.dump(type_arg))

    def return_annotation_str(self) -> Optional[str]:
        if self.return_annotation is None:
            return None
        try:
            return ast.unparse(self.return_annotation)
        except Exception:
            return ast.dump(self.return_annotation)


class PythonAnalyser:
    """Analyses a single Python file using the ast module."""

    def __init__(self, source: str, file_path: str, verbose: bool = False):
        self.source = source
        self.file_path = file_path
        self.verbose = verbose
        self.tree: Optional[ast.Module] = None
        self.class_nodes: Dict[str, ast.ClassDef] = {}
        self.class_methods: Dict[str, Dict[str, _MethodInfo]] = {}

    def analyse(self) -> FileReport:
        report = FileReport(file_path=self.file_path, language="python")
        try:
            self.tree = ast.parse(self.source, filename=self.file_path)
        except SyntaxError as exc:
            report.parse_error = f"SyntaxError: {exc}"
            return report

        # Collect all top-level and nested class definitions
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ClassDef):
                self.class_nodes[node.name] = node
                methods: Dict[str, _MethodInfo] = {}
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        methods[item.name] = _MethodInfo(item)
                self.class_methods[node.name] = methods

        # Analyse each class that has base classes resolvable in-file
        for cls_name, cls_node in self.class_nodes.items():
            base_names = self._resolve_bases(cls_node)
            if not base_names:
                continue  # no in-file parents to compare against

            cls_report = ClassReport(
                class_name=f"{cls_name}({', '.join(base_names)})",
                base_classes=base_names,
                start_line=cls_node.lineno,
                end_line=cls_node.end_lineno or cls_node.lineno,
            )

            child_methods = self.class_methods.get(cls_name, {})
            for base_name in base_names:
                parent_methods = self.class_methods.get(base_name, {})
                for mname, child_m in child_methods.items():
                    if mname.startswith("_") and mname != "__init__":
                        continue  # skip private/dunder helpers (except __init__)
                    parent_m = parent_methods.get(mname)
                    self._check_method(child_m, parent_m, cls_report)

            if cls_report.concerns:
                cls_report.suggestion = self._generate_suggestion(
                    cls_name, base_names, cls_report.concerns
                )
            report.classes.append(cls_report)

        return report

    # --- helpers ------------------------------------------------------------

    def _resolve_bases(self, cls_node: ast.ClassDef) -> List[str]:
        """Return base class names that are defined in the same file."""
        names: List[str] = []
        for base in cls_node.bases:
            if isinstance(base, ast.Name) and base.id in self.class_nodes:
                names.append(base.id)
            elif isinstance(base, ast.Attribute):
                names.append(base.attr)
        return names

    def _check_method(
        self,
        child: _MethodInfo,
        parent: Optional[_MethodInfo],
        report: ClassReport,
    ) -> None:
        # 1. Contract-breaking raises
        contract_raises = child.raises & CONTRACT_BREAKING_EXCEPTIONS
        for exc_name in sorted(contract_raises):
            report.concerns.append(Concern(
                severity=Severity.WARNING,
                method_name=child.name,
                line=child.lineno,
                message=(
                    f"raises {exc_name} — subclass cannot fulfill base class contract"
                ),
                category="raises-not-implemented",
            ))

        # 4. Empty override
        if child.is_empty and parent is not None and not parent.is_empty:
            report.concerns.append(Concern(
                severity=Severity.WARNING,
                method_name=child.name,
                line=child.lineno,
                message="empty override (pass) — silently breaks base class behavior",
                category="empty-override",
            ))

        if parent is None:
            return  # remaining checks need a parent method

        # 2. Return type change
        child_ret = child.return_annotation_str()
        parent_ret = parent.return_annotation_str()
        if (
            child_ret is not None
            and parent_ret is not None
            and child_ret != parent_ret
        ):
            report.concerns.append(Concern(
                severity=Severity.WARNING,
                method_name=child.name,
                line=child.lineno,
                message=(
                    f"return type changed from '{parent_ret}' to '{child_ret}' "
                    f"— may violate substitutability"
                ),
                category="return-type-changed",
            ))

        # 3. Added preconditions (isinstance checks the parent does not have)
        added_checks = child.isinstance_checks - parent.isinstance_checks
        if added_checks:
            report.concerns.append(Concern(
                severity=Severity.WARNING,
                method_name=child.name,
                line=child.lineno,
                message=(
                    "adds isinstance precondition(s) not present in parent "
                    "— strengthens preconditions"
                ),
                category="added-precondition",
            ))

        # 5. New exceptions not in parent
        new_raises = child.raises - parent.raises - CONTRACT_BREAKING_EXCEPTIONS
        if new_raises:
            report.concerns.append(Concern(
                severity=Severity.WARNING,
                method_name=child.name,
                line=child.lineno,
                message=(
                    f"raises {', '.join(sorted(new_raises))} not raised by parent "
                    f"— may surprise callers expecting base class behavior"
                ),
                category="new-exception",
            ))

    def _generate_suggestion(
        self,
        cls_name: str,
        bases: List[str],
        concerns: List[Concern],
    ) -> str:
        categories = {c.category for c in concerns}
        bases_str = ", ".join(bases)
        parts: List[str] = []

        if "raises-not-implemented" in categories:
            refusing = [
                c.method_name for c in concerns
                if c.category == "raises-not-implemented"
            ]
            parts.append(
                f"{cls_name} refuses operations ({', '.join(refusing)}) that "
                f"{bases_str} supports."
            )

        if "empty-override" in categories:
            empty = [
                c.method_name for c in concerns
                if c.category == "empty-override"
            ]
            parts.append(
                f"{cls_name} silently nullifies {', '.join(empty)}."
            )

        if "return-type-changed" in categories:
            parts.append(
                f"{cls_name} changes return types, breaking substitutability."
            )

        if "added-precondition" in categories:
            parts.append(
                f"{cls_name} adds preconditions not required by {bases_str}."
            )

        if parts:
            parts.append(
                f"Consider whether {cls_name} truly IS-A {bases_str}. "
                f"If not, use composition or define a narrower interface."
            )

        return " ".join(parts)


# ---------------------------------------------------------------------------
# Regex-based analysis for non-Python languages
# ---------------------------------------------------------------------------

# Patterns per language family for class declarations with inheritance
_CLASS_PATTERNS: Dict[str, re.Pattern] = {
    "java": re.compile(
        r"^\s*(?:public\s+|private\s+|protected\s+|abstract\s+|final\s+)*"
        r"class\s+(\w+)\s+extends\s+(\w+)",
        re.MULTILINE,
    ),
    "typescript": re.compile(
        r"^\s*(?:export\s+)?(?:abstract\s+)?class\s+(\w+)\s+extends\s+(\w+)",
        re.MULTILINE,
    ),
    "javascript": re.compile(
        r"^\s*(?:export\s+)?class\s+(\w+)\s+extends\s+(\w+)",
        re.MULTILINE,
    ),
    "csharp": re.compile(
        r"^\s*(?:public\s+|private\s+|protected\s+|internal\s+|abstract\s+|sealed\s+)*"
        r"class\s+(\w+)\s*:\s*(\w+)",
        re.MULTILINE,
    ),
    "kotlin": re.compile(
        r"^\s*(?:open\s+|abstract\s+|data\s+)?class\s+(\w+)(?:\s*\([^)]*\))?\s*:\s*(\w+)",
        re.MULTILINE,
    ),
    "swift": re.compile(
        r"^\s*(?:final\s+|open\s+)?class\s+(\w+)\s*:\s*(\w+)",
        re.MULTILINE,
    ),
    "cpp": re.compile(
        r"^\s*class\s+(\w+)\s*:\s*(?:public|protected|private)\s+(\w+)",
        re.MULTILINE,
    ),
    "php": re.compile(
        r"^\s*(?:abstract\s+|final\s+)?class\s+(\w+)\s+extends\s+(\w+)",
        re.MULTILINE,
    ),
    "ruby": re.compile(
        r"^\s*class\s+(\w+)\s*<\s*(\w+)",
        re.MULTILINE,
    ),
    "go": re.compile(
        # Go uses struct embedding; detect embedded struct fields
        r"^\s*type\s+(\w+)\s+struct\s*\{",
        re.MULTILINE,
    ),
}

# Method/function patterns per language family
_METHOD_PATTERNS: Dict[str, re.Pattern] = {
    "java": re.compile(
        r"(?:@Override\s+)?(?:public|protected|private)\s+"
        r"(?:static\s+)?(\w+)\s+(\w+)\s*\(([^)]*)\)\s*(?:throws\s+[\w,\s]+)?\s*\{",
        re.MULTILINE,
    ),
    "typescript": re.compile(
        r"^\s*(?:public\s+|protected\s+|private\s+|async\s+)*"
        r"(\w+)\s*\(([^)]*)\)\s*(?::\s*([\w<>\[\]|&\s]+))?\s*\{",
        re.MULTILINE,
    ),
    "javascript": re.compile(
        r"^\s*(?:async\s+)?(\w+)\s*\(([^)]*)\)\s*\{",
        re.MULTILINE,
    ),
    "csharp": re.compile(
        r"(?:public|protected|private|internal)\s+(?:override\s+|virtual\s+|new\s+)?"
        r"(\w+)\s+(\w+)\s*\(([^)]*)\)\s*\{",
        re.MULTILINE,
    ),
    "kotlin": re.compile(
        r"^\s*(?:override\s+)?(?:fun)\s+(\w+)\s*\(([^)]*)\)\s*(?::\s*(\w+))?\s*\{",
        re.MULTILINE,
    ),
    "swift": re.compile(
        r"^\s*(?:override\s+)?func\s+(\w+)\s*\(([^)]*)\)\s*(?:->\s*([\w?]+))?\s*\{",
        re.MULTILINE,
    ),
    "cpp": re.compile(
        r"^\s*(?:virtual\s+)?(\w[\w:*&<>\s]*?)\s+(\w+)\s*\(([^)]*)\)\s*"
        r"(?:const\s*)?(?:override\s*)?\{",
        re.MULTILINE,
    ),
    "php": re.compile(
        r"^\s*(?:public|protected|private)\s+(?:static\s+)?function\s+(\w+)\s*\(([^)]*)\)"
        r"(?:\s*:\s*(\??\w+))?\s*\{",
        re.MULTILINE,
    ),
    "ruby": re.compile(
        r"^\s*def\s+(\w+)(?:\(([^)]*)\))?\s*$",
        re.MULTILINE,
    ),
    "go": re.compile(
        r"^\s*func\s+\(\w+\s+\*?(\w+)\)\s+(\w+)\s*\(([^)]*)\)\s*"
        r"(?:\(?([\w*,\s]+)\)?)?\s*\{",
        re.MULTILINE,
    ),
}

# Patterns for throw/raise statements
_THROW_PATTERNS: Dict[str, re.Pattern] = {
    "java": re.compile(r"\bthrow\s+new\s+(\w+)"),
    "typescript": re.compile(r"\bthrow\s+new\s+(\w+)"),
    "javascript": re.compile(r"\bthrow\s+new\s+(\w+)"),
    "csharp": re.compile(r"\bthrow\s+new\s+(\w+)"),
    "kotlin": re.compile(r"\bthrow\s+(\w+)"),
    "swift": re.compile(r"\bthrow\s+(\w+)"),
    "cpp": re.compile(r"\bthrow\s+(\w+)"),
    "php": re.compile(r"\bthrow\s+new\s+(\w+)"),
    "ruby": re.compile(r"\braise\s+(\w+)"),
    "go": re.compile(r"\breturn\s+.*(?:errors\.New|fmt\.Errorf)\s*\("),
}

# Empty body detection
_EMPTY_BODY_RE = re.compile(r"\{\s*\}")


@dataclass
class _RegexMethodInfo:
    name: str
    line: int
    return_type: Optional[str]
    body: str
    raises: Set[str]
    is_empty: bool
    has_isinstance: bool


class RegexAnalyser:
    """Regex-based heuristic analyser for non-Python OOP files."""

    def __init__(self, source: str, file_path: str, language: str, verbose: bool = False):
        self.source = source
        self.file_path = file_path
        self.language = language
        self.verbose = verbose
        self.lines = source.split("\n")

    def analyse(self) -> FileReport:
        report = FileReport(file_path=self.file_path, language=self.language)

        classes = self._find_classes()
        if not classes:
            return report

        for cls_name, base_name, start_line, class_body, end_line in classes:
            cls_report = ClassReport(
                class_name=f"{cls_name}({base_name})",
                base_classes=[base_name],
                start_line=start_line,
                end_line=end_line,
            )

            methods = self._find_methods(class_body, start_line)

            # Try to find parent class methods in the same file
            parent_methods: Dict[str, _RegexMethodInfo] = {}
            for pc_name, _, pc_start, pc_body, _ in classes:
                if pc_name == base_name:
                    for m in self._find_methods(pc_body, pc_start):
                        parent_methods[m.name] = m

            for method in methods:
                self._check_method(method, parent_methods.get(method.name), cls_report)

            if cls_report.concerns:
                cls_report.suggestion = self._generate_suggestion(
                    cls_name, base_name, cls_report.concerns
                )
            report.classes.append(cls_report)

        return report

    def _find_classes(self) -> List[Tuple[str, str, int, str, int]]:
        """Return list of (child_name, base_name, start_line, body, end_line)."""
        pattern = _CLASS_PATTERNS.get(self.language)
        if pattern is None:
            return []

        results: List[Tuple[str, str, int, str, int]] = []
        for m in pattern.finditer(self.source):
            child = m.group(1)
            base = m.group(2) if m.lastindex and m.lastindex >= 2 else ""
            start_pos = m.start()
            start_line = self.source[:start_pos].count("\n") + 1

            # Extract class body by brace-matching (or indent for Ruby)
            body, end_line = self._extract_body(m.end(), start_line)
            if base:
                results.append((child, base, start_line, body, end_line))
        return results

    def _extract_body(self, start_offset: int, start_line: int) -> Tuple[str, int]:
        """Extract the body of a class/method starting after the opening brace."""
        if self.language == "ruby":
            # Ruby uses end keyword
            depth = 1
            lines_after = self.source[start_offset:].split("\n")
            body_lines: List[str] = []
            line_num = start_line
            for ln in lines_after:
                line_num += 1
                stripped = ln.strip()
                if re.match(r"\b(class|module|def|do|if|unless|while|until|for|case|begin)\b", stripped):
                    depth += 1
                if stripped == "end":
                    depth -= 1
                    if depth <= 0:
                        return "\n".join(body_lines), line_num
                body_lines.append(ln)
            return "\n".join(body_lines), line_num

        # Brace-matched languages
        rest = self.source[start_offset:]
        brace_idx = rest.find("{")
        if brace_idx == -1:
            # No opening brace found; use a generous chunk
            chunk = rest[:2000]
            end_line = start_line + chunk.count("\n")
            return chunk, end_line

        depth = 1
        i = brace_idx + 1
        while i < len(rest) and depth > 0:
            ch = rest[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            i += 1

        body = rest[brace_idx + 1 : i - 1] if depth == 0 else rest[brace_idx + 1 : brace_idx + 2000]
        end_line = start_line + self.source[start_offset : start_offset + brace_idx + i].count("\n")
        return body, end_line

    def _find_methods(self, body: str, body_start_line: int) -> List[_RegexMethodInfo]:
        """Find methods inside a class body."""
        pattern = _METHOD_PATTERNS.get(self.language)
        if pattern is None:
            return []

        results: List[_RegexMethodInfo] = []
        for m in pattern.finditer(body):
            # Determine method name and return type based on language
            if self.language in ("java", "csharp", "cpp"):
                ret_type = m.group(1)
                name = m.group(2)
            elif self.language == "go":
                name = m.group(2)
                ret_type = m.group(4).strip() if m.lastindex and m.lastindex >= 4 and m.group(4) else None
            elif self.language in ("typescript", "kotlin", "swift", "php"):
                name = m.group(1)
                ret_type = m.group(3) if m.lastindex and m.lastindex >= 3 else None
            else:
                name = m.group(1)
                ret_type = None

            line = body_start_line + body[:m.start()].count("\n")

            # Extract method body
            mbody, _ = self._extract_body(m.end() - 1, line)

            # Find throws
            throw_pat = _THROW_PATTERNS.get(self.language)
            raises: Set[str] = set()
            if throw_pat:
                for tm in throw_pat.finditer(mbody):
                    if tm.lastindex and tm.lastindex >= 1:
                        raises.add(tm.group(1))

            is_empty = bool(_EMPTY_BODY_RE.search(body[m.start():m.end() + 5])) or mbody.strip() == ""

            has_isinstance = bool(re.search(r"\binstanceof\b|\bis_a\?\b|\btype\s+assertion\b", mbody))

            results.append(_RegexMethodInfo(
                name=name,
                line=line,
                return_type=ret_type.strip() if ret_type else None,
                body=mbody,
                raises=raises,
                is_empty=is_empty,
                has_isinstance=has_isinstance,
            ))

        return results

    def _check_method(
        self,
        child: _RegexMethodInfo,
        parent: Optional[_RegexMethodInfo],
        report: ClassReport,
    ) -> None:
        # 1. Contract-breaking raises
        contract_raises = child.raises & CONTRACT_BREAKING_EXCEPTIONS
        for exc_name in sorted(contract_raises):
            report.concerns.append(Concern(
                severity=Severity.WARNING,
                method_name=child.name,
                line=child.line,
                message=f"raises {exc_name} — subclass cannot fulfill base class contract",
                category="raises-not-implemented",
            ))

        # 4. Empty override
        if child.is_empty and parent is not None and not parent.is_empty:
            report.concerns.append(Concern(
                severity=Severity.WARNING,
                method_name=child.name,
                line=child.line,
                message="empty override — silently breaks base class behavior",
                category="empty-override",
            ))

        if parent is None:
            return

        # 2. Return type change
        if (
            child.return_type is not None
            and parent.return_type is not None
            and child.return_type != parent.return_type
        ):
            report.concerns.append(Concern(
                severity=Severity.WARNING,
                method_name=child.name,
                line=child.line,
                message=(
                    f"return type changed from '{parent.return_type}' to "
                    f"'{child.return_type}' — may violate substitutability"
                ),
                category="return-type-changed",
            ))

        # 3. Added preconditions
        if child.has_isinstance and not parent.has_isinstance:
            report.concerns.append(Concern(
                severity=Severity.WARNING,
                method_name=child.name,
                line=child.line,
                message="adds type-checking precondition not present in parent — strengthens preconditions",
                category="added-precondition",
            ))

        # 5. New exceptions
        new_raises = child.raises - parent.raises - CONTRACT_BREAKING_EXCEPTIONS
        if new_raises:
            report.concerns.append(Concern(
                severity=Severity.WARNING,
                method_name=child.name,
                line=child.line,
                message=(
                    f"raises {', '.join(sorted(new_raises))} not raised by parent "
                    f"— may surprise callers expecting base class behavior"
                ),
                category="new-exception",
            ))

    def _generate_suggestion(
        self,
        cls_name: str,
        base_name: str,
        concerns: List[Concern],
    ) -> str:
        categories = {c.category for c in concerns}
        parts: List[str] = []
        if "raises-not-implemented" in categories:
            refusing = [c.method_name for c in concerns if c.category == "raises-not-implemented"]
            parts.append(
                f"{cls_name} refuses operations ({', '.join(refusing)}) that {base_name} supports."
            )
        if "empty-override" in categories:
            empty = [c.method_name for c in concerns if c.category == "empty-override"]
            parts.append(f"{cls_name} silently nullifies {', '.join(empty)}.")
        if "return-type-changed" in categories:
            parts.append(f"{cls_name} changes return types, breaking substitutability.")
        if "added-precondition" in categories:
            parts.append(f"{cls_name} adds preconditions not required by {base_name}.")
        if parts:
            parts.append(
                f"Consider whether {cls_name} truly IS-A {base_name}. "
                f"If not, use composition or define a narrower interface."
            )
        return " ".join(parts)


# ---------------------------------------------------------------------------
# Rewrite suggestion generator
# ---------------------------------------------------------------------------


def generate_rewrite_suggestions(report: FileReport) -> List[RewriteSuggestion]:
    """Generate refactored code suggestions for classes with LSP violations."""
    suggestions: List[RewriteSuggestion] = []

    for cls in report.classes:
        if not cls.concerns:
            continue

        categories = {c.category for c in cls.concerns}
        base = cls.base_classes[0] if cls.base_classes else "Base"
        raw_name = cls.class_name.split("(")[0]

        lines: List[str] = []
        lines.append(f"# --- Suggested refactoring for {cls.class_name} ---")
        lines.append(f"#")
        lines.append(f"# Problem: {raw_name} inherits from {base} but violates LSP.")
        lines.append(f"#")

        if "raises-not-implemented" in categories or "empty-override" in categories:
            refusing = sorted({
                c.method_name for c in cls.concerns
                if c.category in ("raises-not-implemented", "empty-override")
            })

            if report.language == "python":
                lines.append(f"# Option 1: Extract a common interface")
                lines.append(f"from abc import ABC, abstractmethod")
                lines.append(f"")
                lines.append(f"class {base}Interface(ABC):")
                lines.append(f'    """Define only the operations common to both {base} and {raw_name}."""')

                # Figure out safe methods (those NOT in the refusing list)
                all_methods = {c.method_name for c in cls.concerns}
                safe_hint = f"common_operation"
                lines.append(f"    @abstractmethod")
                lines.append(f"    def {safe_hint}(self):")
                lines.append(f"        ...")
                lines.append(f"")
                lines.append(f"class {base}({base}Interface):")
                lines.append(f"    # Keeps all original methods including {', '.join(refusing)}")
                lines.append(f"    ...")
                lines.append(f"")
                lines.append(f"class {raw_name}({base}Interface):")
                lines.append(f"    # Only implements the operations it truly supports")
                lines.append(f"    # Does NOT inherit {', '.join(refusing)} from {base}")
                lines.append(f"    ...")
                lines.append(f"")
                lines.append(f"# Option 2: Use composition")
                lines.append(f"class {raw_name}:")
                lines.append(f'    """Uses a {base} internally but does not claim to BE a {base}."""')
                lines.append(f"    def __init__(self):")
                lines.append(f"        self._inner = {base}()")
                lines.append(f"")
                lines.append(f"    # Delegate only the operations {raw_name} supports")
                lines.append(f"    # to self._inner, without exposing {', '.join(refusing)}")
            else:
                lines.append(f"// Option 1: Extract a common interface")
                lines.append(f"interface I{base} {{")
                lines.append(f"    // Only declare methods common to {base} and {raw_name}")
                lines.append(f"}}")
                lines.append(f"")
                lines.append(f"// Option 2: Use composition")
                lines.append(f"class {raw_name} {{")
                lines.append(f"    private {base.lower()}: {base};")
                lines.append(f"    // Delegate supported operations to inner {base}")
                lines.append(f"}}")

        elif "return-type-changed" in categories:
            lines.append(f"# Ensure {raw_name} returns a type compatible with {base}.")
            lines.append(f"# Use covariant return types (subtype of the parent return type).")

        elif "added-precondition" in categories:
            lines.append(f"# Remove isinstance/type checks from {raw_name} methods.")
            lines.append(f"# A subclass must accept all inputs the parent accepts.")

        suggestions.append(RewriteSuggestion(
            class_name=cls.class_name,
            original_lines=(cls.start_line, cls.end_line),
            suggestion_text="\n".join(lines),
        ))

    return suggestions


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------


def discover_files(path: str) -> List[Path]:
    """Recursively discover OOP source files under a path."""
    target = Path(path)
    if target.is_file():
        if target.suffix in OOP_EXTENSIONS:
            return [target]
        return []

    files: List[Path] = []
    for root, _dirs, filenames in os.walk(target):
        # Skip hidden directories and common non-source directories
        root_path = Path(root)
        parts = root_path.parts
        if any(
            p.startswith(".") or p in ("node_modules", "__pycache__", "venv", ".venv", "dist", "build")
            for p in parts
        ):
            continue
        for fname in sorted(filenames):
            fpath = root_path / fname
            if fpath.suffix in OOP_EXTENSIONS:
                files.append(fpath)
    return files


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------


def format_text(reports: List[FileReport], verbose: bool = False) -> str:
    """Format reports as human-readable text."""
    sections: List[str] = []

    for report in reports:
        if not report.classes and not report.parse_error and not verbose:
            continue

        header = f"=== LSP Analysis: {report.file_path} ==="
        parts: List[str] = [header]

        if report.parse_error:
            parts.append(f"  [ERROR] {report.parse_error}")
            parts.append("")
            sections.append("\n".join(parts))
            continue

        if verbose and not report.classes:
            parts.append("  No class hierarchies detected.")
            parts.append("")
            sections.append("\n".join(parts))
            continue

        for cls in report.classes:
            if not cls.concerns and not verbose:
                continue
            parts.append("")
            parts.append(
                f"  Class: {cls.class_name} (lines {cls.start_line}-{cls.end_line})"
            )
            if not cls.concerns:
                parts.append("    No concerns.")
                continue

            for c in cls.concerns:
                parts.append(
                    f"    [{c.severity.value}] Method {c.method_name} "
                    f"(line {c.line}): {c.message}"
                )

            if cls.suggestion:
                parts.append(f"    [SUGGESTION] {cls.suggestion}")

        parts.append("")
        sections.append("\n".join(parts))

    if not sections:
        return "No LSP concerns found.\n"

    return "\n".join(sections)


def format_json(
    reports: List[FileReport],
    rewrite_suggestions: Optional[Dict[str, List[RewriteSuggestion]]] = None,
) -> str:
    """Format reports as JSON."""

    def _concern_dict(c: Concern) -> dict:
        return {
            "severity": c.severity.value,
            "method": c.method_name,
            "line": c.line,
            "message": c.message,
            "category": c.category,
        }

    def _class_dict(cr: ClassReport) -> dict:
        return {
            "class": cr.class_name,
            "bases": cr.base_classes,
            "lines": [cr.start_line, cr.end_line],
            "concerns": [_concern_dict(c) for c in cr.concerns],
            "suggestion": cr.suggestion,
        }

    def _report_dict(r: FileReport) -> dict:
        d: dict = {
            "file": r.file_path,
            "language": r.language,
            "parse_error": r.parse_error,
            "classes": [_class_dict(c) for c in r.classes if c.concerns],
            "total_concerns": r.total_concerns,
        }
        if rewrite_suggestions and r.file_path in rewrite_suggestions:
            d["rewrite_suggestions"] = [
                {
                    "class": s.class_name,
                    "original_lines": list(s.original_lines),
                    "suggestion": s.suggestion_text,
                }
                for s in rewrite_suggestions[r.file_path]
            ]
        return d

    output = {
        "summary": {
            "files_analysed": len(reports),
            "files_with_concerns": sum(1 for r in reports if r.total_concerns > 0),
            "total_concerns": sum(r.total_concerns for r in reports),
        },
        "files": [_report_dict(r) for r in reports],
    }
    return json.dumps(output, indent=2)


def format_rewrite(suggestions_by_file: Dict[str, List[RewriteSuggestion]]) -> str:
    """Format rewrite suggestions as text."""
    parts: List[str] = []
    for fpath, suggestions in suggestions_by_file.items():
        if not suggestions:
            continue
        parts.append(f"=== Rewrite Suggestions: {fpath} ===")
        for s in suggestions:
            parts.append(f"\n  {s.class_name} (lines {s.original_lines[0]}-{s.original_lines[1]}):")
            for line in s.suggestion_text.split("\n"):
                parts.append(f"    {line}")
        parts.append("")
    if not parts:
        return ""
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


def analyse_file(file_path: Path, verbose: bool = False) -> FileReport:
    """Analyse a single file and return a FileReport."""
    ext = file_path.suffix
    language = LANGUAGE_MAP.get(ext, "unknown")

    try:
        source = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        report = FileReport(file_path=str(file_path), language=language)
        report.parse_error = str(exc)
        return report

    if language == "python":
        analyser = PythonAnalyser(source, str(file_path), verbose=verbose)
    else:
        analyser = RegexAnalyser(source, str(file_path), language, verbose=verbose)

    return analyser.analyse()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Detect Liskov Substitution Principle (LSP) violations in source code.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              %(prog)s src/
              %(prog)s myfile.py --verbose
              %(prog)s project/ --json
              %(prog)s project/ --rewrite

            Exit codes:
              0  No concerns found
              1  One or more concerns found
              2  Input error
        """),
    )
    parser.add_argument(
        "path",
        help="File or directory to analyse",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show detailed output including files with no concerns",
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
        help="Additionally output suggested refactored code",
    )

    args = parser.parse_args(argv)

    # Validate input path
    target = Path(args.path)
    if not target.exists():
        print(f"Error: path does not exist: {args.path}", file=sys.stderr)
        return 2

    files = discover_files(args.path)
    if not files:
        print(f"Error: no supported source files found in: {args.path}", file=sys.stderr)
        return 2

    if args.verbose:
        print(f"Scanning {len(files)} file(s)...\n", file=sys.stderr)

    # Analyse all files
    reports: List[FileReport] = []
    for fpath in files:
        report = analyse_file(fpath, verbose=args.verbose)
        reports.append(report)

    # Generate rewrite suggestions if requested
    rewrite_map: Dict[str, List[RewriteSuggestion]] = {}
    if args.rewrite:
        for report in reports:
            suggestions = generate_rewrite_suggestions(report)
            if suggestions:
                rewrite_map[report.file_path] = suggestions

    # Output
    if args.json_output:
        print(format_json(reports, rewrite_map if args.rewrite else None))
    else:
        text = format_text(reports, verbose=args.verbose)
        print(text)
        if args.rewrite and rewrite_map:
            print(format_rewrite(rewrite_map))

    # Determine exit code
    total = sum(r.total_concerns for r in reports)
    return 1 if total > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
