#!/usr/bin/env python3
"""
check_ocp.py - Open/Closed Principle (OCP) Violation Detector

Detects patterns in source code that violate the Open/Closed Principle,
which states that software entities should be open for extension but closed
for modification.

Detected patterns:
  1. Long if/elif/else or switch/case chains branching on type or category
  2. isinstance()/instanceof/is type checks in conditional logic
  3. Functions with conditional branches processing different "types" of input
  4. Type-code switching via string comparisons against type-like values

Supports: .py, .java, .ts, .js, .cs, .rb, .kt, .go, .swift, .cpp, .php

Usage:
  python check_ocp.py path/to/file_or_directory
  python check_ocp.py path/ --verbose --max-branches 4
  python check_ocp.py path/ --json
  python check_ocp.py path/ --rewrite

Exit codes:
  0 - No OCP concerns found
  1 - OCP concerns found
  2 - Input error (bad path, no files found, etc.)
"""

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
from typing import List, Optional, Dict, Tuple


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SUPPORTED_EXTENSIONS: Dict[str, str] = {
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
    ".h": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".php": "php",
}

TYPE_PARAM_NAMES = re.compile(
    r"\b(type|kind|category|variant|mode|action|command|event_type|"
    r"item_type|node_type|msg_type|message_type|op|operation)\b",
    re.IGNORECASE,
)

TYPE_LIKE_STRINGS = re.compile(
    r"""(?:==|!=|===|!==|\.equals\()\s*["']"""
    r"""[A-Za-z_]\w*["']"""
)

DEFAULT_MAX_BRANCHES = 3


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class Severity(str, Enum):
    WARNING = "WARNING"
    INFO = "INFO"


@dataclass
class Violation:
    severity: Severity
    message: str
    line: int
    end_line: Optional[int] = None
    branches: List[str] = field(default_factory=list)
    suggestion: str = ""
    rewrite_hint: str = ""


@dataclass
class ScopeResult:
    """Results for a class or top-level function."""
    name: str
    kind: str  # "Class" or "Function"
    start_line: int
    end_line: int
    violations: List[Violation] = field(default_factory=list)


@dataclass
class FileResult:
    path: str
    language: str
    scopes: List[ScopeResult] = field(default_factory=list)
    parse_error: Optional[str] = None

    @property
    def has_concerns(self) -> bool:
        return any(s.violations for s in self.scopes)


# ---------------------------------------------------------------------------
# Python AST-based analyser
# ---------------------------------------------------------------------------

class PythonAnalyser:
    """Analyses Python files using the ast module."""

    def __init__(self, source: str, max_branches: int):
        self.source = source
        self.lines = source.splitlines()
        self.max_branches = max_branches

    def analyse(self) -> List[ScopeResult]:
        try:
            tree = ast.parse(self.source)
        except SyntaxError as exc:
            return []

        results: List[ScopeResult] = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                results.append(self._analyse_class(node))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                results.append(self._analyse_function(node, top_level=True))
        return [r for r in results if r.violations]

    # -- class level --------------------------------------------------------

    def _analyse_class(self, cls_node: ast.ClassDef) -> ScopeResult:
        end_line = self._end_line(cls_node)
        scope = ScopeResult(
            name=cls_node.name,
            kind="Class",
            start_line=cls_node.lineno,
            end_line=end_line,
        )
        for node in ast.iter_child_nodes(cls_node):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fn_result = self._analyse_function(node, top_level=False)
                scope.violations.extend(fn_result.violations)
        return scope

    # -- function level -----------------------------------------------------

    def _analyse_function(
        self, fn_node: ast.FunctionDef, *, top_level: bool
    ) -> ScopeResult:
        end_line = self._end_line(fn_node)
        scope = ScopeResult(
            name=fn_node.name,
            kind="Function" if top_level else "Method",
            start_line=fn_node.lineno,
            end_line=end_line,
        )

        # Walk all statements inside the function
        for node in ast.walk(fn_node):
            if isinstance(node, ast.If):
                self._check_if_chain(node, fn_node, scope)
            if isinstance(node, ast.Match):
                self._check_match(node, fn_node, scope)

        # Check for isinstance patterns
        self._check_isinstance_calls(fn_node, scope)

        # Check for type-param + branching
        self._check_type_param_branching(fn_node, scope)

        return scope

    # -- detectors ----------------------------------------------------------

    def _check_if_chain(
        self, if_node: ast.If, fn_node: ast.FunctionDef, scope: ScopeResult
    ) -> None:
        """Detect long if/elif chains that compare against type-like values."""
        chain_nodes = self._collect_if_chain(if_node)
        if len(chain_nodes) < self.max_branches:
            return

        # Collect the branch discriminators
        branch_labels: List[str] = []
        has_type_comparison = False
        for cnode in chain_nodes:
            label = self._extract_comparison_label(cnode.test)
            if label:
                branch_labels.append(label)
            if self._is_type_like_test(cnode.test):
                has_type_comparison = True

        if not has_type_comparison and not branch_labels:
            return

        scope.violations.append(Violation(
            severity=Severity.WARNING,
            message=(
                f"{scope.kind} {fn_node.name} (line {if_node.lineno}): "
                f"{len(chain_nodes)}-branch if/elif chain"
                + (f" on type discriminator" if has_type_comparison else "")
            ),
            line=if_node.lineno,
            end_line=self._end_line(chain_nodes[-1]),
            branches=branch_labels or [f"branch at line {n.lineno}" for n in chain_nodes],
            suggestion=self._polymorphism_suggestion(fn_node.name, branch_labels),
            rewrite_hint=self._rewrite_if_chain(fn_node, branch_labels),
        ))

    def _check_match(
        self, match_node: ast.Match, fn_node: ast.FunctionDef, scope: ScopeResult
    ) -> None:
        """Detect match/case statements (Python 3.10+) with many cases."""
        cases = match_node.cases
        if len(cases) < self.max_branches:
            return
        branch_labels = []
        for case in cases:
            pattern = case.pattern
            label = self._pattern_label(pattern)
            if label:
                branch_labels.append(label)

        scope.violations.append(Violation(
            severity=Severity.WARNING,
            message=(
                f"{scope.kind} {fn_node.name} (line {match_node.lineno}): "
                f"{len(cases)}-case match statement"
            ),
            line=match_node.lineno,
            end_line=self._end_line(match_node),
            branches=branch_labels,
            suggestion=self._polymorphism_suggestion(fn_node.name, branch_labels),
            rewrite_hint=self._rewrite_if_chain(fn_node, branch_labels),
        ))

    def _check_isinstance_calls(
        self, fn_node: ast.FunctionDef, scope: ScopeResult
    ) -> None:
        """Detect isinstance() checks used in conditional logic."""
        isinstance_sites: List[Tuple[int, str]] = []

        for node in ast.walk(fn_node):
            if not isinstance(node, ast.If):
                continue
            for call_node in ast.walk(node.test):
                if (
                    isinstance(call_node, ast.Call)
                    and isinstance(call_node.func, ast.Name)
                    and call_node.func.id == "isinstance"
                    and len(call_node.args) >= 2
                ):
                    type_arg = call_node.args[1]
                    type_name = self._node_name(type_arg)
                    isinstance_sites.append((call_node.lineno, type_name))

        if len(isinstance_sites) >= self.max_branches:
            lines_str = ", ".join(str(ln) for ln, _ in isinstance_sites)
            type_names = [t for _, t in isinstance_sites if t]
            scope.violations.append(Violation(
                severity=Severity.WARNING,
                message=(
                    f"{scope.kind} {fn_node.name}: Uses isinstance() checks "
                    f"for {len(isinstance_sites)} types "
                    f"(lines {lines_str})"
                ),
                line=isinstance_sites[0][0],
                end_line=isinstance_sites[-1][0],
                branches=type_names,
                suggestion=(
                    "Use polymorphism or a strategy pattern -- define a common "
                    "interface and let each type implement its own behavior"
                ),
                rewrite_hint=self._rewrite_isinstance(fn_node, type_names),
            ))

    def _check_type_param_branching(
        self, fn_node: ast.FunctionDef, scope: ScopeResult
    ) -> None:
        """Detect functions that accept a 'type'/'kind'/'category' param and branch on it."""
        param_names = [a.arg for a in fn_node.args.args]
        type_params = [p for p in param_names if TYPE_PARAM_NAMES.search(p)]
        if not type_params:
            return

        # Count if-branches that reference the type param
        branch_count = 0
        for node in ast.walk(fn_node):
            if isinstance(node, ast.If):
                src_snippet = ast.dump(node.test)
                for tp in type_params:
                    if tp in src_snippet:
                        branch_count += 1
                        break

        if branch_count >= self.max_branches:
            scope.violations.append(Violation(
                severity=Severity.WARNING,
                message=(
                    f"{scope.kind} {fn_node.name} (line {fn_node.lineno}): "
                    f"Accepts type-discriminator parameter "
                    f"'{type_params[0]}' with {branch_count}+ conditional branches"
                ),
                line=fn_node.lineno,
                end_line=self._end_line(fn_node),
                suggestion=(
                    f"Instead of branching on '{type_params[0]}', use "
                    f"polymorphism or a dispatch table to map each type "
                    f"to its handler"
                ),
            ))

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _collect_if_chain(if_node: ast.If) -> List[ast.If]:
        """Walk an if/elif chain and return all branch nodes."""
        chain = [if_node]
        current = if_node
        while current.orelse:
            if len(current.orelse) == 1 and isinstance(current.orelse[0], ast.If):
                current = current.orelse[0]
                chain.append(current)
            else:
                break
        return chain

    @staticmethod
    def _extract_comparison_label(test_node: ast.expr) -> Optional[str]:
        """Try to pull a string/name constant from a comparison."""
        for node in ast.walk(test_node):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return node.value
            if isinstance(node, ast.Attribute):
                return node.attr
        return None

    @staticmethod
    def _is_type_like_test(test_node: ast.expr) -> bool:
        """Heuristic: does the test look like a type-dispatching comparison?"""
        dump = ast.dump(test_node).lower()
        keywords = ["type", "kind", "category", "isinstance", "class", "variant", "mode"]
        return any(kw in dump for kw in keywords)

    @staticmethod
    def _pattern_label(pattern: ast.pattern) -> Optional[str]:
        if isinstance(pattern, ast.MatchValue):
            if isinstance(pattern.value, ast.Constant):
                return str(pattern.value.value)
        if isinstance(pattern, ast.MatchClass):
            if isinstance(pattern.cls, ast.Name):
                return pattern.cls.id
        return None

    @staticmethod
    def _node_name(node: ast.expr) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        if isinstance(node, ast.Tuple):
            parts = []
            for elt in node.elts:
                if isinstance(elt, ast.Name):
                    parts.append(elt.id)
            return ", ".join(parts)
        return ""

    @staticmethod
    def _end_line(node: ast.AST) -> int:
        return getattr(node, "end_lineno", getattr(node, "lineno", 0))

    @staticmethod
    def _polymorphism_suggestion(func_name: str, branches: List[str]) -> str:
        if branches:
            items = ", ".join(branches[:5])
            extra = f" (and more)" if len(branches) > 5 else ""
            return (
                f"Replace type-checking conditionals with polymorphism -- "
                f"create a base class/interface with a {func_name}() method, "
                f"then subclass for each variant: {items}{extra}"
            )
        return (
            f"Replace type-checking conditionals with polymorphism -- "
            f"create a base class with a {func_name}() method"
        )

    @staticmethod
    def _rewrite_if_chain(fn_node: ast.FunctionDef, branches: List[str]) -> str:
        if not branches:
            return ""
        base = fn_node.name.replace("_", " ").title().replace(" ", "")
        iface = f"{base}Handler"
        lines = [
            f"from abc import ABC, abstractmethod",
            f"",
            f"class {iface}(ABC):",
            f"    @abstractmethod",
            f"    def {fn_node.name}(self):",
            f"        ...",
            f"",
        ]
        for b in branches[:6]:
            cls_name = b.replace(" ", "").replace("-", "").replace("_", " ").title().replace(" ", "")
            if not cls_name.isidentifier():
                cls_name = f"Variant_{b[:16]}"
            lines.append(f"class {cls_name}Handler({iface}):")
            lines.append(f"    def {fn_node.name}(self):")
            lines.append(f"        ...  # logic for {b}")
            lines.append("")
        if len(branches) > 6:
            lines.append(f"# ... and {len(branches) - 6} more subclasses")
            lines.append("")
        lines.append(f"# Dispatch:")
        lines.append(f"# handler = registry[item_type]")
        lines.append(f"# handler.{fn_node.name}()")
        return "\n".join(lines)

    @staticmethod
    def _rewrite_isinstance(fn_node: ast.FunctionDef, type_names: List[str]) -> str:
        if not type_names:
            return ""
        lines = [
            f"from abc import ABC, abstractmethod",
            f"",
            f"class Base(ABC):",
            f"    @abstractmethod",
            f"    def {fn_node.name}(self):",
            f"        ...",
            f"",
        ]
        for t in type_names[:6]:
            lines.append(f"class {t}(Base):")
            lines.append(f"    def {fn_node.name}(self):")
            lines.append(f"        ...  # logic for {t}")
            lines.append("")
        if len(type_names) > 6:
            lines.append(f"# ... and {len(type_names) - 6} more subclasses")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Regex-based analyser for non-Python languages
# ---------------------------------------------------------------------------

# Patterns per language family
_SWITCH_PATTERNS: Dict[str, re.Pattern] = {
    "java":       re.compile(r"\bswitch\s*\("),
    "typescript":  re.compile(r"\bswitch\s*\("),
    "javascript": re.compile(r"\bswitch\s*\("),
    "csharp":     re.compile(r"\bswitch\s*\("),
    "kotlin":     re.compile(r"\bwhen\s*[\({]"),
    "go":         re.compile(r"\bswitch\s"),
    "swift":      re.compile(r"\bswitch\s"),
    "cpp":        re.compile(r"\bswitch\s*\("),
    "php":        re.compile(r"\bswitch\s*\("),
    "ruby":       re.compile(r"\bcase\s"),
}

_INSTANCEOF_PATTERNS: Dict[str, re.Pattern] = {
    "java":       re.compile(r"\binstanceof\b"),
    "typescript":  re.compile(r"\binstanceof\b"),
    "javascript": re.compile(r"\binstanceof\b"),
    "csharp":     re.compile(r"\bis\s+\w+"),
    "kotlin":     re.compile(r"\bis\s+\w+"),
    "go":         re.compile(r"\.\(\w+\)"),  # type assertion
    "swift":      re.compile(r"\bis\s+\w+|\bas\??\s+\w+"),
    "cpp":        re.compile(r"\bdynamic_cast\s*<"),
    "php":        re.compile(r"\binstanceof\b"),
    "ruby":       re.compile(r"\.is_a\?\(|\.kind_of\?\(|\.instance_of\?\("),
}

_IF_ELIF_PATTERN = re.compile(
    r"^\s*(?:if|elif|else\s+if|elseif|elsif|}\s*else\s+if)\b", re.MULTILINE
)

_CASE_BRANCH_PATTERNS: Dict[str, re.Pattern] = {
    "java":       re.compile(r"^\s*case\b", re.MULTILINE),
    "typescript":  re.compile(r"^\s*case\b", re.MULTILINE),
    "javascript": re.compile(r"^\s*case\b", re.MULTILINE),
    "csharp":     re.compile(r"^\s*case\b", re.MULTILINE),
    "kotlin":     re.compile(r"^\s*(?:is\b|\")", re.MULTILINE),
    "go":         re.compile(r"^\s*case\b", re.MULTILINE),
    "swift":      re.compile(r"^\s*case\b", re.MULTILINE),
    "cpp":        re.compile(r"^\s*case\b", re.MULTILINE),
    "php":        re.compile(r"^\s*case\b", re.MULTILINE),
    "ruby":       re.compile(r"^\s*when\b", re.MULTILINE),
}

_CLASS_PATTERN: Dict[str, re.Pattern] = {
    "java":       re.compile(r"^\s*(?:public\s+|private\s+|protected\s+)?(?:abstract\s+)?class\s+(\w+)", re.MULTILINE),
    "typescript":  re.compile(r"^\s*(?:export\s+)?(?:abstract\s+)?class\s+(\w+)", re.MULTILINE),
    "javascript": re.compile(r"^\s*(?:export\s+)?class\s+(\w+)", re.MULTILINE),
    "csharp":     re.compile(r"^\s*(?:public\s+|private\s+|internal\s+|protected\s+)?(?:abstract\s+|static\s+)?class\s+(\w+)", re.MULTILINE),
    "kotlin":     re.compile(r"^\s*(?:open\s+|abstract\s+|data\s+)?class\s+(\w+)", re.MULTILINE),
    "go":         re.compile(r"^\s*type\s+(\w+)\s+struct\b", re.MULTILINE),
    "swift":      re.compile(r"^\s*(?:open\s+|public\s+|internal\s+|fileprivate\s+|private\s+)?class\s+(\w+)", re.MULTILINE),
    "cpp":        re.compile(r"^\s*class\s+(\w+)", re.MULTILINE),
    "php":        re.compile(r"^\s*(?:abstract\s+)?class\s+(\w+)", re.MULTILINE),
    "ruby":       re.compile(r"^\s*class\s+(\w+)", re.MULTILINE),
}

_FUNC_PATTERN: Dict[str, re.Pattern] = {
    "java":       re.compile(r"(?:public|private|protected|static|\w+)\s+\w+\s+(\w+)\s*\(", re.MULTILINE),
    "typescript":  re.compile(r"(?:function\s+(\w+)|(\w+)\s*\(.*\)\s*(?::\s*\w+)?\s*\{)", re.MULTILINE),
    "javascript": re.compile(r"(?:function\s+(\w+)|(\w+)\s*\(.*\)\s*\{)", re.MULTILINE),
    "csharp":     re.compile(r"(?:public|private|protected|internal|static|\w+)\s+\w+\s+(\w+)\s*\(", re.MULTILINE),
    "kotlin":     re.compile(r"\bfun\s+(\w+)\s*\(", re.MULTILINE),
    "go":         re.compile(r"\bfunc\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)\s*\(", re.MULTILINE),
    "swift":      re.compile(r"\bfunc\s+(\w+)\s*\(", re.MULTILINE),
    "cpp":        re.compile(r"\w[\w:]*\s+(\w+)\s*\([^)]*\)\s*(?:const\s*)?\{", re.MULTILINE),
    "php":        re.compile(r"\bfunction\s+(\w+)\s*\(", re.MULTILINE),
    "ruby":       re.compile(r"\bdef\s+(\w+)", re.MULTILINE),
}


class RegexAnalyser:
    """Heuristic analyser for non-Python OOP languages."""

    def __init__(self, source: str, language: str, max_branches: int):
        self.source = source
        self.lines = source.splitlines()
        self.language = language
        self.max_branches = max_branches

    def analyse(self) -> List[ScopeResult]:
        results: List[ScopeResult] = []
        results.extend(self._find_switch_violations())
        results.extend(self._find_instanceof_violations())
        results.extend(self._find_long_if_chains())
        results.extend(self._find_type_string_comparisons())
        return self._merge_scopes(results)

    # -- detectors ----------------------------------------------------------

    def _find_switch_violations(self) -> List[ScopeResult]:
        pattern = _SWITCH_PATTERNS.get(self.language)
        case_pattern = _CASE_BRANCH_PATTERNS.get(self.language)
        if not pattern or not case_pattern:
            return []

        results: List[ScopeResult] = []
        for match in pattern.finditer(self.source):
            line_no = self.source[:match.start()].count("\n") + 1
            # Find the block after the switch
            block_start = self.source.find("{", match.start())
            if block_start == -1:
                # Ruby case doesn't use braces
                block_text = self.source[match.start():match.start() + 2000]
            else:
                block_text = self._extract_brace_block(block_start)

            cases = case_pattern.findall(block_text)
            if len(cases) >= self.max_branches:
                # Check if it looks type-related
                if self._block_has_type_indicators(block_text):
                    scope_name = self._enclosing_scope_name(match.start())
                    branch_labels = self._extract_case_labels(block_text)
                    results.append(ScopeResult(
                        name=scope_name,
                        kind=self._scope_kind(match.start()),
                        start_line=line_no,
                        end_line=line_no + block_text.count("\n"),
                        violations=[Violation(
                            severity=Severity.WARNING,
                            message=(
                                f"{scope_name} (line {line_no}): "
                                f"{len(cases)}-branch switch/case statement"
                            ),
                            line=line_no,
                            branches=branch_labels,
                            suggestion=(
                                "Replace switch/case with polymorphism or a "
                                "strategy/visitor pattern -- define an interface "
                                "and implement each case as a separate class"
                            ),
                        )],
                    ))
        return results

    def _find_instanceof_violations(self) -> List[ScopeResult]:
        pattern = _INSTANCEOF_PATTERNS.get(self.language)
        if not pattern:
            return []

        matches = list(pattern.finditer(self.source))
        if len(matches) < self.max_branches:
            return []

        # Group by enclosing function
        groups: Dict[str, List[Tuple[int, str]]] = {}
        for m in matches:
            line_no = self.source[:m.start()].count("\n") + 1
            scope = self._enclosing_scope_name(m.start())
            groups.setdefault(scope, []).append((line_no, m.group()))

        results: List[ScopeResult] = []
        for scope_name, sites in groups.items():
            if len(sites) < self.max_branches:
                continue
            lines_str = ", ".join(str(ln) for ln, _ in sites)
            results.append(ScopeResult(
                name=scope_name,
                kind="Function",
                start_line=sites[0][0],
                end_line=sites[-1][0],
                violations=[Violation(
                    severity=Severity.WARNING,
                    message=(
                        f"{scope_name}: Uses type-checking operator "
                        f"{len(sites)} times (lines {lines_str})"
                    ),
                    line=sites[0][0],
                    end_line=sites[-1][0],
                    suggestion=(
                        "Use polymorphism or a strategy pattern -- define "
                        "a common interface and let each type implement "
                        "its own behavior"
                    ),
                )],
            ))
        return results

    def _find_long_if_chains(self) -> List[ScopeResult]:
        results: List[ScopeResult] = []
        # Find sequences of if/elif
        chain_starts: List[Tuple[int, int]] = []  # (line_no, pos)
        current_chain: List[Tuple[int, int]] = []

        for m in _IF_ELIF_PATTERN.finditer(self.source):
            line_no = self.source[:m.start()].count("\n") + 1
            if current_chain:
                last_line = current_chain[-1][0]
                # Allow some gap for body lines between branches
                if line_no - last_line <= 20:
                    current_chain.append((line_no, m.start()))
                else:
                    if len(current_chain) >= self.max_branches:
                        chain_starts.append((current_chain[0][0], current_chain[0][1]))
                    current_chain = [(line_no, m.start())]
            else:
                current_chain = [(line_no, m.start())]

        if len(current_chain) >= self.max_branches:
            chain_starts.append((current_chain[0][0], current_chain[0][1]))

        for start_line, pos in chain_starts:
            context = self.source[max(0, pos - 200):pos + 1000]
            if self._block_has_type_indicators(context):
                scope_name = self._enclosing_scope_name(pos)
                results.append(ScopeResult(
                    name=scope_name,
                    kind=self._scope_kind(pos),
                    start_line=start_line,
                    end_line=start_line,
                    violations=[Violation(
                        severity=Severity.WARNING,
                        message=(
                            f"{scope_name} (line {start_line}): "
                            f"Long if/else-if chain with type-checking logic"
                        ),
                        line=start_line,
                        suggestion=(
                            "Replace type-checking conditionals with "
                            "polymorphism or a lookup table"
                        ),
                    )],
                ))
        return results

    def _find_type_string_comparisons(self) -> List[ScopeResult]:
        results: List[ScopeResult] = []
        matches = list(TYPE_LIKE_STRINGS.finditer(self.source))
        if len(matches) < self.max_branches:
            return results

        # Group nearby matches
        groups: Dict[str, List[int]] = {}
        for m in matches:
            line_no = self.source[:m.start()].count("\n") + 1
            scope = self._enclosing_scope_name(m.start())
            groups.setdefault(scope, []).append(line_no)

        for scope_name, line_nos in groups.items():
            if len(line_nos) < self.max_branches:
                continue
            results.append(ScopeResult(
                name=scope_name,
                kind="Function",
                start_line=line_nos[0],
                end_line=line_nos[-1],
                violations=[Violation(
                    severity=Severity.INFO,
                    message=(
                        f"{scope_name}: {len(line_nos)} string comparisons "
                        f"against type-like values (possible type-code pattern)"
                    ),
                    line=line_nos[0],
                    end_line=line_nos[-1],
                    suggestion=(
                        "Consider replacing string-based type dispatch with "
                        "an enum + polymorphism or a registry/map pattern"
                    ),
                )],
            ))
        return results

    # -- helpers ------------------------------------------------------------

    def _extract_brace_block(self, brace_pos: int) -> str:
        depth = 0
        i = brace_pos
        while i < len(self.source):
            ch = self.source[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return self.source[brace_pos:i + 1]
            i += 1
        return self.source[brace_pos:brace_pos + 3000]

    def _block_has_type_indicators(self, text: str) -> bool:
        lower = text.lower()
        indicators = [
            "type", "kind", "category", "instanceof", "is ", "as ",
            "dynamic_cast", ".is_a?", "typeof", "getclass", "variant",
            "mode", "action",
        ]
        return any(ind in lower for ind in indicators)

    def _enclosing_scope_name(self, pos: int) -> str:
        """Find the name of the enclosing function or class."""
        text_before = self.source[:pos]
        func_pattern = _FUNC_PATTERN.get(self.language)
        if func_pattern:
            for m in func_pattern.finditer(text_before):
                last_func = m
            else:
                last_func = None
            # Find all matches and pick last
            all_matches = list(func_pattern.finditer(text_before))
            if all_matches:
                groups = all_matches[-1].groups()
                name = next((g for g in groups if g), None)
                if name:
                    return name
        return "<unknown>"

    def _scope_kind(self, pos: int) -> str:
        text_before = self.source[:pos]
        class_pattern = _CLASS_PATTERN.get(self.language)
        if class_pattern and class_pattern.search(text_before):
            return "Method"
        return "Function"

    def _extract_case_labels(self, block_text: str) -> List[str]:
        labels = []
        # Match case "string": or case CONSTANT:
        for m in re.finditer(r'case\s+["\']?(\w+)["\']?\s*:', block_text):
            labels.append(m.group(1))
        # Ruby: when "string"
        for m in re.finditer(r'when\s+["\']?(\w+)["\']?', block_text):
            labels.append(m.group(1))
        return labels

    @staticmethod
    def _merge_scopes(scopes: List[ScopeResult]) -> List[ScopeResult]:
        """Merge violations that belong to the same scope."""
        merged: Dict[str, ScopeResult] = {}
        for s in scopes:
            key = f"{s.name}:{s.start_line}"
            if key in merged:
                merged[key].violations.extend(s.violations)
            else:
                merged[key] = ScopeResult(
                    name=s.name,
                    kind=s.kind,
                    start_line=s.start_line,
                    end_line=s.end_line,
                    violations=list(s.violations),
                )
        return list(merged.values())


# ---------------------------------------------------------------------------
# File discovery and orchestration
# ---------------------------------------------------------------------------

def discover_files(path: str) -> List[Path]:
    """Recursively find supported source files."""
    p = Path(path)
    if p.is_file():
        if p.suffix in SUPPORTED_EXTENSIONS:
            return [p]
        return []
    if p.is_dir():
        files = []
        for ext in SUPPORTED_EXTENSIONS:
            files.extend(p.rglob(f"*{ext}"))
        return sorted(set(files))
    return []


def analyse_file(filepath: Path, max_branches: int) -> FileResult:
    language = SUPPORTED_EXTENSIONS.get(filepath.suffix, "unknown")
    result = FileResult(path=str(filepath), language=language)

    try:
        source = filepath.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        result.parse_error = str(exc)
        return result

    if not source.strip():
        return result

    if language == "python":
        analyser = PythonAnalyser(source, max_branches)
    else:
        analyser = RegexAnalyser(source, language, max_branches)

    try:
        result.scopes = analyser.analyse()
    except Exception as exc:
        result.parse_error = f"Analysis error: {exc}"

    return result


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------

def format_text(results: List[FileResult], verbose: bool, show_rewrite: bool) -> str:
    lines: List[str] = []
    for fr in results:
        if not fr.has_concerns and not fr.parse_error:
            if verbose:
                lines.append(f"=== OCP Analysis: {fr.path} ===")
                lines.append("  No concerns found.")
                lines.append("")
            continue

        lines.append(f"=== OCP Analysis: {fr.path} ===")
        if fr.parse_error:
            lines.append(f"  [ERROR] {fr.parse_error}")
            lines.append("")
            continue

        lines.append("")
        for scope in fr.scopes:
            if not scope.violations:
                continue
            scope_header = f"{scope.kind}: {scope.name}"
            if scope.start_line and scope.end_line and scope.start_line != scope.end_line:
                scope_header += f" (lines {scope.start_line}-{scope.end_line})"
            elif scope.start_line:
                scope_header += f" (line {scope.start_line})"
            lines.append(scope_header)

            for v in scope.violations:
                lines.append(f"  [{v.severity.value}] {v.message}")
                if v.branches and verbose:
                    lines.append(f"    - Branches: {', '.join(repr(b) for b in v.branches)}")
                if v.suggestion:
                    lines.append(f"  [SUGGESTION] {v.suggestion}")

                if show_rewrite and v.rewrite_hint:
                    lines.append("")
                    lines.append("  [REWRITE] Suggested refactored structure:")
                    for rline in v.rewrite_hint.splitlines():
                        lines.append(f"    {rline}")

            lines.append("")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n" if lines else ""


def format_json(results: List[FileResult]) -> str:
    output = []
    for fr in results:
        file_data = {
            "path": fr.path,
            "language": fr.language,
            "parse_error": fr.parse_error,
            "scopes": [],
        }
        for scope in fr.scopes:
            scope_data = {
                "name": scope.name,
                "kind": scope.kind,
                "start_line": scope.start_line,
                "end_line": scope.end_line,
                "violations": [],
            }
            for v in scope.violations:
                scope_data["violations"].append({
                    "severity": v.severity.value,
                    "message": v.message,
                    "line": v.line,
                    "end_line": v.end_line,
                    "branches": v.branches,
                    "suggestion": v.suggestion,
                    "rewrite_hint": v.rewrite_hint if v.rewrite_hint else None,
                })
            file_data["scopes"].append(scope_data)
        output.append(file_data)
    return json.dumps(output, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="check_ocp",
        description=(
            "Detect Open/Closed Principle violations in source code. "
            "Finds patterns like long type-switching conditionals, "
            "instanceof checks, and type-code dispatch that should be "
            "replaced with polymorphism."
        ),
    )
    parser.add_argument(
        "path",
        help="File or directory to analyse (recursively for directories)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show detailed output including branch labels and clean files",
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
        help="Include suggested refactored code using polymorphism/strategy pattern",
    )
    parser.add_argument(
        "--max-branches",
        type=int,
        default=DEFAULT_MAX_BRANCHES,
        metavar="N",
        help=f"Minimum branch count to flag (default: {DEFAULT_MAX_BRANCHES})",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    target = Path(args.path)
    if not target.exists():
        print(f"Error: path does not exist: {args.path}", file=sys.stderr)
        return 2

    files = discover_files(args.path)
    if not files:
        print(
            f"Error: no supported source files found in: {args.path}",
            file=sys.stderr,
        )
        return 2

    results: List[FileResult] = []
    for f in files:
        results.append(analyse_file(f, args.max_branches))

    if args.json_output:
        print(format_json(results))
    else:
        output = format_text(results, args.verbose, args.rewrite)
        if output:
            print(output, end="")
        elif not args.verbose:
            print("No OCP concerns found.")

    has_concerns = any(r.has_concerns for r in results)
    return 1 if has_concerns else 0


if __name__ == "__main__":
    sys.exit(main())
