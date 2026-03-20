#!/usr/bin/env python3
"""
check_dip.py — Dependency Inversion Principle (DIP) Violation Detector

Analyzes source code files to detect potential DIP violations in classes.
Works language-agnostically: uses Python's ast module for .py files and
regex-based heuristics for all other supported languages.

Supported languages:
    .py, .java, .ts, .js, .cs, .rb, .kt, .go, .swift, .cpp, .hpp, .php

Detections:
    1. Direct instantiation of concrete classes inside other classes
       (e.g., self.db = MySQLDatabase() instead of receiving an abstraction)
    2. Constructor bodies that create dependencies rather than receiving them
       (new/instantiation inside __init__, constructors)
    3. Absence of dependency injection patterns: constructors that take no
       parameters but use concrete collaborators
    4. For Python: ast-based detection of __init__ methods, ast.Call nodes
       for direct instantiation, and concrete imports used directly
    5. For other languages: regex detection of `new ConcreteClass()` inside
       constructors and methods

Exit codes:
    0 — No DIP concerns found
    1 — One or more DIP concerns found
    2 — Input error (file/directory not found, no eligible files, etc.)

Usage:
    python check_dip.py path/to/file_or_directory
    python check_dip.py path/ --verbose
    python check_dip.py path/ --json
    python check_dip.py path/ --rewrite
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
import textwrap
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

# Classes whose instantiation is typically acceptable (value objects,
# standard-library types, data containers, etc.).  Instantiating these
# inside a constructor does NOT indicate a DIP violation.
SAFE_INSTANTIATION_NAMES: set[str] = {
    # Python builtins / stdlib
    "dict", "list", "set", "tuple", "frozenset", "bytearray", "bytes",
    "str", "int", "float", "bool", "complex", "object", "type",
    "defaultdict", "OrderedDict", "Counter", "deque",
    "Path", "PurePath", "PosixPath", "WindowsPath",
    "Lock", "RLock", "Event", "Condition", "Semaphore",
    "Queue", "PriorityQueue", "LifoQueue",
    "Exception", "ValueError", "TypeError", "RuntimeError", "KeyError",
    "AttributeError", "IOError", "OSError", "IndexError", "StopIteration",
    "Logger",
    # Java / C# / general
    "ArrayList", "HashMap", "HashSet", "LinkedList", "TreeMap", "TreeSet",
    "StringBuilder", "StringBuffer", "StringJoiner",
    "List", "Map", "Set", "Dictionary", "Array",
    "Object", "String", "Integer", "Double", "Float", "Boolean",
    "BigDecimal", "BigInteger", "Date", "LocalDate", "LocalDateTime",
    "Instant", "Duration", "Period", "UUID",
    "Optional", "OptionalInt", "OptionalLong", "OptionalDouble",
    "Mutex", "Lock", "Semaphore", "AtomicInteger", "AtomicBoolean",
    "File", "Path", "Paths", "URI", "URL",
    "IOException", "Exception", "RuntimeException",
    "IllegalArgumentException", "IllegalStateException",
    "NullPointerException", "UnsupportedOperationException",
    # TypeScript / JavaScript
    "Map", "Set", "WeakMap", "WeakSet", "Promise", "Error",
    "TypeError", "RangeError", "Date", "RegExp", "URL", "URLSearchParams",
    "FormData", "Headers", "AbortController", "EventEmitter",
    "Buffer",
}

# Python modules commonly providing abstractions (ABC, Protocol, etc.)
ABSTRACT_INDICATOR_BASES: set[str] = {
    "ABC", "ABCMeta", "Protocol", "Interface",
    "AbstractBase", "BaseClass", "Abstract",
}

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Instantiation:
    """A single concrete instantiation found in code."""
    class_name: str
    line: int
    location: str  # "constructor" or "method:<name>"
    attribute: Optional[str] = None  # e.g. self.db, this.service


@dataclass
class ClassInfo:
    """Collected information about one class for DIP analysis."""
    name: str
    file_path: str
    language: str
    start_line: int
    end_line: int
    constructor_params: List[str] = field(default_factory=list)
    constructor_param_types: Dict[str, str] = field(default_factory=dict)
    instantiations: List[Instantiation] = field(default_factory=list)
    bases: List[str] = field(default_factory=list)
    is_abstract: bool = False
    is_config_or_value: bool = False


@dataclass
class Concern:
    """A single DIP concern flagged for a class."""
    kind: str  # "constructor_creates_dependency", "method_creates_dependency",
    # "no_injection_parameters"
    message: str
    line: int
    class_name: str
    instantiated_class: Optional[str] = None
    location: Optional[str] = None  # constructor / method name


@dataclass
class FileResult:
    """All analysis results for one file."""
    file_path: str
    language: str
    classes: List[ClassInfo] = field(default_factory=list)
    concerns: List[Concern] = field(default_factory=list)
    suggestions: Dict[str, str] = field(default_factory=dict)
    rewrite_snippets: Dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def collect_files(path: Path) -> List[Path]:
    """Recursively collect source files with supported extensions."""
    if path.is_file():
        if path.suffix in SUPPORTED_EXTENSIONS:
            return [path]
        return []
    results: List[Path] = []
    for root, _dirs, files in os.walk(path):
        for f in files:
            fp = Path(root) / f
            if fp.suffix in SUPPORTED_EXTENSIONS:
                results.append(fp)
    return sorted(results)


def is_safe_instantiation(name: str) -> bool:
    """Return True if *name* is a benign/value type we should not flag."""
    if name in SAFE_INSTANTIATION_NAMES:
        return True
    # Exception subclasses
    if name.endswith("Error") or name.endswith("Exception"):
        return True
    # Clearly a data/value type name
    lower = name.lower()
    if lower.endswith("dto") or lower.endswith("vo") or lower.endswith("enum"):
        return True
    return False


def looks_like_config_class(name: str) -> bool:
    """Heuristic: classes named *Config*, *Settings*, *Options*, *Constants*
    are typically value/configuration holders and not DIP violators."""
    lower = name.lower()
    return any(tok in lower for tok in (
        "config", "setting", "option", "constant", "properties",
        "defaults", "params", "args", "context", "env",
    ))


def _extract_name_from_node(node: ast.expr) -> Optional[str]:
    """Get a simple string name from an AST node (Name or Attribute)."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


# ---------------------------------------------------------------------------
# Python analysis (ast-based)
# ---------------------------------------------------------------------------

class _PythonClassVisitor(ast.NodeVisitor):
    """Walk an AST and collect ClassInfo for each class definition."""

    def __init__(self, source_lines: List[str], file_path: str):
        self.source_lines = source_lines
        self.file_path = file_path
        self.classes: List[ClassInfo] = []
        # Track top-level imports so we can resolve concrete vs abstract.
        self.imported_names: Dict[str, str] = {}  # name -> module

    # -- imports -----------------------------------------------------------

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            local = alias.asname or alias.name.split(".")[-1]
            self.imported_names[local] = alias.name
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        mod = node.module or ""
        for alias in (node.names or []):
            local = alias.asname or alias.name
            self.imported_names[local] = f"{mod}.{alias.name}"
        self.generic_visit(node)

    # -- classes -----------------------------------------------------------

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        bases = []
        for b in node.bases:
            bn = _extract_name_from_node(b)
            if bn:
                bases.append(bn)

        is_abstract = any(b in ABSTRACT_INDICATOR_BASES for b in bases)

        ci = ClassInfo(
            name=node.name,
            file_path=self.file_path,
            language="Python",
            start_line=node.lineno,
            end_line=node.end_lineno or node.lineno,
            bases=bases,
            is_abstract=is_abstract,
            is_config_or_value=looks_like_config_class(node.name),
        )

        for item in ast.walk(node):
            if isinstance(item, ast.FunctionDef) or isinstance(item, ast.AsyncFunctionDef):
                if item.name == "__init__":
                    self._analyze_init(item, ci)
                else:
                    self._analyze_method(item, ci)

        self.classes.append(ci)
        # Do NOT call generic_visit — we handle nested walking ourselves
        # to avoid double-counting inner classes as top-level.

    # -- __init__ ----------------------------------------------------------

    def _analyze_init(self, node: ast.FunctionDef, ci: ClassInfo) -> None:
        # Collect constructor parameters (skip 'self'/'cls').
        for arg in node.args.args:
            name = arg.arg
            if name in ("self", "cls"):
                continue
            ci.constructor_params.append(name)
            if arg.annotation:
                ann = _extract_name_from_node(arg.annotation)
                if ann:
                    ci.constructor_param_types[name] = ann

        # Walk the body looking for Call nodes (instantiations).
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                callee = _extract_name_from_node(child.func)
                if callee and self._is_concrete_instantiation(callee):
                    attr = self._find_assignment_target(child, node)
                    ci.instantiations.append(Instantiation(
                        class_name=callee,
                        line=child.lineno,
                        location="constructor",
                        attribute=attr,
                    ))

    # -- regular methods ---------------------------------------------------

    def _analyze_method(self, node: ast.FunctionDef, ci: ClassInfo) -> None:
        if node.name.startswith("_") and node.name != "__init__":
            # Still analyse private methods — they can violate DIP too.
            pass
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                callee = _extract_name_from_node(child.func)
                if callee and self._is_concrete_instantiation(callee):
                    ci.instantiations.append(Instantiation(
                        class_name=callee,
                        line=child.lineno,
                        location=f"method:{node.name}",
                        attribute=None,
                    ))

    # -- helpers -----------------------------------------------------------

    def _is_concrete_instantiation(self, name: str) -> bool:
        """Decide whether *name* looks like a concrete-class instantiation."""
        if is_safe_instantiation(name):
            return False
        # Must start with an uppercase letter (convention for classes).
        if not name or not name[0].isupper():
            return False
        # If it is known to come from an abc/protocol module, skip.
        fqn = self.imported_names.get(name, "")
        if "abc" in fqn.lower() or "abstract" in fqn.lower() or "protocol" in fqn.lower():
            return False
        return True

    @staticmethod
    def _find_assignment_target(call_node: ast.Call, func_node: ast.FunctionDef) -> Optional[str]:
        """If the call is the value side of `self.x = Call(...)`, return 'self.x'."""
        for stmt in ast.walk(func_node):
            if isinstance(stmt, ast.Assign):
                if stmt.value is call_node:
                    for tgt in stmt.targets:
                        if isinstance(tgt, ast.Attribute) and isinstance(tgt.value, ast.Name):
                            return f"{tgt.value.id}.{tgt.attr}"
            elif isinstance(stmt, ast.AnnAssign):
                if stmt.value is call_node and isinstance(stmt.target, ast.Attribute):
                    t = stmt.target
                    if isinstance(t.value, ast.Name):
                        return f"{t.value.id}.{t.attr}"
        return None


def analyze_python(file_path: Path, source: str) -> FileResult:
    """Full DIP analysis for a Python file using the ast module."""
    result = FileResult(file_path=str(file_path), language="Python")
    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError:
        return result

    lines = source.splitlines()
    visitor = _PythonClassVisitor(lines, str(file_path))
    visitor.visit(tree)
    result.classes = visitor.classes

    for ci in result.classes:
        _evaluate_class(ci, result)

    return result


# ---------------------------------------------------------------------------
# Regex-based analysis (non-Python languages)
# ---------------------------------------------------------------------------

# Regex patterns keyed by language for extracting classes and constructors.

# Generic class header: captures class name and optional body span.
_RE_CLASS_HEADER = {
    "Java":       re.compile(r'^\s*(?:public\s+|private\s+|protected\s+)?(?:abstract\s+|final\s+)?class\s+(\w+)', re.MULTILINE),
    "C#":         re.compile(r'^\s*(?:public\s+|private\s+|protected\s+|internal\s+)?(?:abstract\s+|sealed\s+|static\s+)?class\s+(\w+)', re.MULTILINE),
    "TypeScript": re.compile(r'^\s*(?:export\s+)?(?:abstract\s+)?class\s+(\w+)', re.MULTILINE),
    "JavaScript": re.compile(r'^\s*(?:export\s+)?class\s+(\w+)', re.MULTILINE),
    "Kotlin":     re.compile(r'^\s*(?:open\s+|abstract\s+|data\s+|sealed\s+)?class\s+(\w+)', re.MULTILINE),
    "Swift":      re.compile(r'^\s*(?:open\s+|public\s+|internal\s+|fileprivate\s+|private\s+)?(?:final\s+)?class\s+(\w+)', re.MULTILINE),
    "C++":        re.compile(r'^\s*class\s+(\w+)', re.MULTILINE),
    "Ruby":       re.compile(r'^\s*class\s+(\w+)', re.MULTILINE),
    "Go":         re.compile(r'^\s*type\s+(\w+)\s+struct\b', re.MULTILINE),
    "PHP":        re.compile(r'^\s*(?:abstract\s+|final\s+)?class\s+(\w+)', re.MULTILINE),
}

# Patterns that detect `new SomeClass(...)` inside a block.
_RE_NEW_INSTANTIATION = re.compile(
    r'\bnew\s+([A-Z]\w+)\s*\(', re.MULTILINE
)

# Constructor patterns per language (to identify constructor bodies).
_RE_CONSTRUCTOR = {
    "Java":       lambda cls: re.compile(rf'(?:public|protected|private)?\s*{re.escape(cls)}\s*\([^)]*\)\s*\{{', re.MULTILINE),
    "C#":         lambda cls: re.compile(rf'(?:public|protected|private|internal)?\s*{re.escape(cls)}\s*\([^)]*\)\s*(?::\s*\w+\([^)]*\)\s*)?\{{', re.MULTILINE),
    "TypeScript": lambda _: re.compile(r'constructor\s*\([^)]*\)\s*\{', re.MULTILINE),
    "JavaScript": lambda _: re.compile(r'constructor\s*\([^)]*\)\s*\{', re.MULTILINE),
    "Kotlin":     lambda cls: re.compile(rf'class\s+{re.escape(cls)}\s*(?:\([^)]*\))?\s*(?::\s*[^{{]+)?\s*\{{', re.MULTILINE),
    "Swift":      lambda _: re.compile(r'init\s*\([^)]*\)\s*\{', re.MULTILINE),
    "C++":        lambda cls: re.compile(rf'{re.escape(cls)}\s*::\s*{re.escape(cls)}\s*\([^)]*\)\s*(?::[^{{]*)?\{{', re.MULTILINE),
    "Ruby":       lambda _: re.compile(r'def\s+initialize\s*(?:\([^)]*\))?\s*$', re.MULTILINE),
    "Go":         lambda cls: re.compile(rf'func\s+New{re.escape(cls)}\s*\([^)]*\)\s*\*?{re.escape(cls)}\s*\{{', re.MULTILINE),
    "PHP":        lambda _: re.compile(r'(?:public|protected|private)?\s*function\s+__construct\s*\([^)]*\)\s*\{', re.MULTILINE),
}

# Constructor parameter extraction — captures the parameter list text.
_RE_CTOR_PARAMS = {
    "Java":       lambda cls: re.compile(rf'(?:public|protected|private)?\s*{re.escape(cls)}\s*\(([^)]*)\)', re.MULTILINE),
    "C#":         lambda cls: re.compile(rf'(?:public|protected|private|internal)?\s*{re.escape(cls)}\s*\(([^)]*)\)', re.MULTILINE),
    "TypeScript": lambda _: re.compile(r'constructor\s*\(([^)]*)\)', re.MULTILINE),
    "JavaScript": lambda _: re.compile(r'constructor\s*\(([^)]*)\)', re.MULTILINE),
    "Kotlin":     lambda cls: re.compile(rf'class\s+{re.escape(cls)}\s*\(([^)]*)\)', re.MULTILINE),
    "Swift":      lambda _: re.compile(r'init\s*\(([^)]*)\)', re.MULTILINE),
    "C++":        lambda cls: re.compile(rf'{re.escape(cls)}\s*::\s*{re.escape(cls)}\s*\(([^)]*)\)', re.MULTILINE),
    "Ruby":       lambda _: re.compile(r'def\s+initialize\s*\(([^)]*)\)', re.MULTILINE),
    "Go":         lambda cls: re.compile(rf'func\s+New{re.escape(cls)}\s*\(([^)]*)\)', re.MULTILINE),
    "PHP":        lambda _: re.compile(r'function\s+__construct\s*\(([^)]*)\)', re.MULTILINE),
}


def _find_matching_brace(source: str, open_pos: int) -> int:
    """Find the position of the closing brace matching the one at *open_pos*."""
    depth = 0
    i = open_pos
    while i < len(source):
        ch = source[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return len(source) - 1


def _find_ruby_end(source: str, start_pos: int) -> int:
    """Find matching 'end' for a Ruby def/class starting at *start_pos*."""
    depth = 0
    lines = source[start_pos:].splitlines(True)
    offset = start_pos
    for line in lines:
        stripped = line.strip()
        # Count openers
        if re.match(r'^(class|module|def|do|if|unless|while|until|for|case|begin)\b', stripped):
            depth += 1
        elif stripped == "end" or stripped.startswith("end ") or stripped.startswith("end;"):
            depth -= 1
            if depth <= 0:
                return offset + len(line)
        offset += len(line)
    return len(source) - 1


def _line_number(source: str, pos: int) -> int:
    """1-based line number for character position *pos*."""
    return source[:pos].count("\n") + 1


def analyze_regex(file_path: Path, source: str, language: str) -> FileResult:
    """Regex-based DIP analysis for non-Python languages."""
    result = FileResult(file_path=str(file_path), language=language)

    class_re = _RE_CLASS_HEADER.get(language)
    if class_re is None:
        return result

    is_ruby = language == "Ruby"
    is_go = language == "Go"

    for m_class in class_re.finditer(source):
        class_name = m_class.group(1)

        # Determine class body boundaries.
        class_start = m_class.start()
        class_start_line = _line_number(source, class_start)

        if is_ruby:
            class_end_pos = _find_ruby_end(source, class_start)
        elif is_go:
            brace = source.find("{", m_class.end())
            class_end_pos = _find_matching_brace(source, brace) if brace != -1 else len(source) - 1
        else:
            brace = source.find("{", m_class.end())
            class_end_pos = _find_matching_brace(source, brace) if brace != -1 else len(source) - 1

        class_end_line = _line_number(source, class_end_pos)
        class_body = source[class_start:class_end_pos + 1]

        ci = ClassInfo(
            name=class_name,
            file_path=str(file_path),
            language=language,
            start_line=class_start_line,
            end_line=class_end_line,
            is_config_or_value=looks_like_config_class(class_name),
        )

        # --- Extract constructor params ---
        ctor_param_factory = _RE_CTOR_PARAMS.get(language)
        if ctor_param_factory:
            ctor_param_re = ctor_param_factory(class_name)
            m_params = ctor_param_re.search(class_body)
            if m_params:
                raw_params = m_params.group(1).strip()
                if raw_params:
                    for p in raw_params.split(","):
                        p = p.strip()
                        if p:
                            # Try to extract a simple param name (last word token)
                            tokens = re.split(r'[\s:=]+', p)
                            param_name = tokens[-1].strip("?").strip("!") if tokens else p
                            ci.constructor_params.append(param_name)

        # --- Find constructor body and detect instantiations in it ---
        ctor_factory = _RE_CONSTRUCTOR.get(language)
        if ctor_factory:
            ctor_re = ctor_factory(class_name)
            m_ctor = ctor_re.search(class_body)
            if m_ctor:
                if is_ruby:
                    ctor_end = _find_ruby_end(class_body, m_ctor.start())
                else:
                    brace_pos = class_body.find("{", m_ctor.start())
                    ctor_end = _find_matching_brace(class_body, brace_pos) if brace_pos != -1 else len(class_body) - 1
                ctor_body = class_body[m_ctor.start():ctor_end + 1]

                if is_ruby:
                    # Ruby: detect ClassName.new
                    for m_new in re.finditer(r'([A-Z]\w+)\.new\b', ctor_body):
                        inst_name = m_new.group(1)
                        if not is_safe_instantiation(inst_name):
                            abs_pos = class_start + m_ctor.start() + m_new.start()
                            ci.instantiations.append(Instantiation(
                                class_name=inst_name,
                                line=_line_number(source, abs_pos),
                                location="constructor",
                            ))
                elif is_go:
                    # Go: detect &ConcreteType{} or ConcreteType{}
                    for m_new in re.finditer(r'&?([A-Z]\w+)\{', ctor_body):
                        inst_name = m_new.group(1)
                        if not is_safe_instantiation(inst_name):
                            abs_pos = class_start + m_ctor.start() + m_new.start()
                            ci.instantiations.append(Instantiation(
                                class_name=inst_name,
                                line=_line_number(source, abs_pos),
                                location="constructor",
                            ))
                else:
                    for m_new in _RE_NEW_INSTANTIATION.finditer(ctor_body):
                        inst_name = m_new.group(1)
                        if not is_safe_instantiation(inst_name):
                            abs_pos = class_start + m_ctor.start() + m_new.start()
                            ci.instantiations.append(Instantiation(
                                class_name=inst_name,
                                line=_line_number(source, abs_pos),
                                location="constructor",
                            ))

        # --- Detect instantiations in non-constructor methods ---
        # Simplistic: find all `new X()` in the class body OUTSIDE the ctor.
        if is_ruby:
            method_re = re.compile(r'def\s+(\w+)\s*(?:\([^)]*\))?\s*$', re.MULTILINE)
        elif is_go:
            # Go doesn't have methods inside struct bodies; skip.
            method_re = None
        else:
            method_re = re.compile(
                r'(?:public|private|protected|internal|static|async|override|virtual|open|final)?\s*'
                r'(?:(?:fun|func|function|def)\s+)?(\w+)\s*\([^)]*\)\s*(?:->?\s*\w+\s*)?[{:]',
                re.MULTILINE,
            )

        if method_re:
            for m_method in method_re.finditer(class_body):
                method_name = m_method.group(1)
                # Skip the constructor — already handled.
                if method_name in (class_name, "constructor", "__construct",
                                   "initialize", "init", f"New{class_name}"):
                    continue
                if is_ruby:
                    meth_end = _find_ruby_end(class_body, m_method.start())
                else:
                    brace_pos = class_body.find("{", m_method.start())
                    if brace_pos == -1:
                        continue
                    meth_end = _find_matching_brace(class_body, brace_pos)
                meth_body = class_body[m_method.start():meth_end + 1]

                if is_ruby:
                    inst_re_iter = re.finditer(r'([A-Z]\w+)\.new\b', meth_body)
                else:
                    inst_re_iter = _RE_NEW_INSTANTIATION.finditer(meth_body)

                for m_new in inst_re_iter:
                    inst_name = m_new.group(1)
                    if not is_safe_instantiation(inst_name):
                        abs_pos = class_start + m_method.start() + m_new.start()
                        ci.instantiations.append(Instantiation(
                            class_name=inst_name,
                            line=_line_number(source, abs_pos),
                            location=f"method:{method_name}",
                        ))

        result.classes.append(ci)

    # Evaluate all collected classes.
    for ci in result.classes:
        _evaluate_class(ci, result)

    return result


# ---------------------------------------------------------------------------
# Evaluation logic (shared between Python and regex paths)
# ---------------------------------------------------------------------------

def _evaluate_class(ci: ClassInfo, result: FileResult) -> None:
    """Turn raw ClassInfo into actionable Concern items."""
    if ci.is_abstract:
        return  # Abstract classes are defining contracts, not violating DIP.

    ctor_instantiations = [i for i in ci.instantiations if i.location == "constructor"]
    method_instantiations = [i for i in ci.instantiations if i.location.startswith("method:")]

    # 1. Constructor creates concrete dependencies.
    for inst in ctor_instantiations:
        result.concerns.append(Concern(
            kind="constructor_creates_dependency",
            message=f"Constructor creates concrete dependency: {inst.class_name}() (line {inst.line})",
            line=inst.line,
            class_name=ci.name,
            instantiated_class=inst.class_name,
            location="constructor",
        ))

    # 2. Methods create concrete dependencies.
    for inst in method_instantiations:
        meth_name = inst.location.split(":", 1)[1] if ":" in inst.location else inst.location
        result.concerns.append(Concern(
            kind="method_creates_dependency",
            message=f"Method {meth_name} creates concrete dependency: {inst.class_name}() (line {inst.line})",
            line=inst.line,
            class_name=ci.name,
            instantiated_class=inst.class_name,
            location=meth_name,
        ))

    # 3. No injection parameters but uses concrete collaborators.
    if not ci.constructor_params and ctor_instantiations and not ci.is_config_or_value:
        result.concerns.append(Concern(
            kind="no_injection_parameters",
            message=(
                "Constructor takes no parameters but creates concrete "
                f"dependencies: {', '.join(i.class_name for i in ctor_instantiations)}"
            ),
            line=ci.start_line,
            class_name=ci.name,
        ))

    # Build a suggestion if there are constructor-created dependencies.
    if ctor_instantiations and not ci.is_config_or_value:
        _build_suggestion(ci, ctor_instantiations, result)


def _build_suggestion(ci: ClassInfo, ctor_insts: List[Instantiation], result: FileResult) -> None:
    """Create injection suggestion text and optional rewrite snippet."""
    deps: List[Tuple[str, str]] = []  # (param_name, type_hint)
    for inst in ctor_insts:
        param = _to_param_name(inst.class_name)
        type_hint = _to_abstract_name(inst.class_name)
        deps.append((param, type_hint))

    existing = ci.constructor_params[:]

    if ci.language == "Python":
        all_params = ["self"] + existing + [f"{p}: {t}" for p, t in deps]
        sig = f"def __init__({', '.join(all_params)})"
        result.suggestions[ci.name] = f"Inject dependencies through the constructor:\n    {sig}"

        # Rewrite snippet
        body_lines = [f"        self.{p} = {p}" for p, _ in deps]
        rewrite = f"    {sig}:\n" + "\n".join(body_lines)
        result.rewrite_snippets[ci.name] = rewrite
    elif ci.language in ("Java", "C#", "Kotlin"):
        typed_params = [f"{t} {p}" for p, t in deps]
        all_params_str = ", ".join(existing + typed_params)
        sig = f"{ci.name}({all_params_str})"
        result.suggestions[ci.name] = f"Inject dependencies through the constructor:\n    {sig}"

        body_lines = [f"        this.{p} = {p};" for p, _ in deps]
        rewrite = f"    public {sig} {{\n" + "\n".join(body_lines) + "\n    }"
        result.rewrite_snippets[ci.name] = rewrite
    elif ci.language in ("TypeScript", "JavaScript"):
        typed_params = [f"{p}: {t}" for p, t in deps]
        all_params_str = ", ".join(existing + typed_params)
        sig = f"constructor({all_params_str})"
        result.suggestions[ci.name] = f"Inject dependencies through the constructor:\n    {sig}"

        body_lines = [f"        this.{p} = {p};" for p, _ in deps]
        rewrite = f"    {sig} {{\n" + "\n".join(body_lines) + "\n    }"
        result.rewrite_snippets[ci.name] = rewrite
    elif ci.language == "Ruby":
        all_params_str = ", ".join(existing + [p for p, _ in deps])
        sig = f"def initialize({all_params_str})"
        result.suggestions[ci.name] = f"Inject dependencies through the constructor:\n    {sig}"

        body_lines = [f"      @{p} = {p}" for p, _ in deps]
        rewrite = f"    {sig}\n" + "\n".join(body_lines) + "\n    end"
        result.rewrite_snippets[ci.name] = rewrite
    elif ci.language == "Swift":
        typed_params = [f"{p}: {t}" for p, t in deps]
        all_params_str = ", ".join(existing + typed_params)
        sig = f"init({all_params_str})"
        result.suggestions[ci.name] = f"Inject dependencies through the initializer:\n    {sig}"

        body_lines = [f"        self.{p} = {p}" for p, _ in deps]
        rewrite = f"    {sig} {{\n" + "\n".join(body_lines) + "\n    }"
        result.rewrite_snippets[ci.name] = rewrite
    elif ci.language == "Go":
        typed_params = [f"{p} {t}" for p, t in deps]
        all_params_str = ", ".join(existing + typed_params)
        sig = f"func New{ci.name}({all_params_str}) *{ci.name}"
        result.suggestions[ci.name] = f"Inject dependencies through the constructor function:\n    {sig}"

        body_lines = [f"        {p}: {p}," for p, _ in deps]
        rewrite = f"{sig} {{\n    return &{ci.name}{{\n" + "\n".join(body_lines) + "\n    }\n}"
        result.rewrite_snippets[ci.name] = rewrite
    elif ci.language == "PHP":
        typed_params = [f"{t} ${p}" for p, t in deps]
        all_params_str = ", ".join(existing + typed_params)
        sig = f"public function __construct({all_params_str})"
        result.suggestions[ci.name] = f"Inject dependencies through the constructor:\n    {sig}"

        body_lines = [f"        $this->{p} = ${p};" for p, _ in deps]
        rewrite = f"    {sig} {{\n" + "\n".join(body_lines) + "\n    }"
        result.rewrite_snippets[ci.name] = rewrite
    elif ci.language == "C++":
        typed_params = [f"{t}& {p}" for p, t in deps]
        all_params_str = ", ".join(existing + typed_params)
        sig = f"{ci.name}({all_params_str})"
        result.suggestions[ci.name] = f"Inject dependencies through the constructor:\n    {sig}"

        init_list = ", ".join(f"{p}_({p})" for p, _ in deps)
        rewrite = f"    {sig} : {init_list} {{}}"
        result.rewrite_snippets[ci.name] = rewrite
    else:
        all_params_str = ", ".join(existing + [f"{p}: {t}" for p, t in deps])
        result.suggestions[ci.name] = (
            f"Inject dependencies through the constructor:\n"
            f"    {ci.name}({all_params_str})"
        )


def _to_param_name(class_name: str) -> str:
    """Convert CamelCase class name to snake_case parameter name."""
    # MySQLDatabase -> mysql_database
    s1 = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', class_name)
    s2 = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', s1)
    return s2.lower()


def _to_abstract_name(class_name: str) -> str:
    """Guess an abstract/interface name for a concrete class.

    Examples:
        MySQLDatabase -> Database
        SmtpEmailSender -> EmailSender
        PdfGenerator -> PdfGenerator (no obvious prefix to strip)
    """
    # Common concrete prefixes to strip.
    prefixes = [
        "MySQL", "Postgres", "Sqlite", "Mongo", "Redis",
        "Smtp", "Http", "Tcp", "Udp", "Grpc",
        "Json", "Xml", "Csv", "Yaml",
        "Aws", "Gcp", "Azure",
        "Mock", "Fake", "Stub", "Dummy", "Spy",
        "Default", "Standard", "Basic", "Simple", "Concrete",
        "Real", "Live", "Production", "Actual",
        "Internal", "Local", "Remote",
        "InMemory", "File", "Disk",
    ]
    for prefix in prefixes:
        if class_name.startswith(prefix) and len(class_name) > len(prefix):
            remainder = class_name[len(prefix):]
            if remainder[0].isupper():
                return remainder
    return class_name


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def format_text(results: List[FileResult], verbose: bool = False) -> str:
    """Human-readable plain-text output."""
    parts: List[str] = []
    for fr in results:
        file_concerns = fr.concerns
        if not file_concerns and not verbose:
            continue

        parts.append(f"\n=== DIP Analysis: {fr.file_path} ===\n")

        # Group concerns by class.
        by_class: Dict[str, List[Concern]] = {}
        class_map: Dict[str, ClassInfo] = {}
        for ci in fr.classes:
            class_map[ci.name] = ci
            by_class.setdefault(ci.name, [])
        for c in file_concerns:
            by_class.setdefault(c.class_name, []).append(c)

        for cls_name, concerns in by_class.items():
            ci = class_map.get(cls_name)
            if ci:
                parts.append(f"Class: {cls_name} (lines {ci.start_line}-{ci.end_line})")
            else:
                parts.append(f"Class: {cls_name}")

            if not concerns:
                if ci and ci.is_config_or_value:
                    parts.append("  [OK] No DIP concerns detected (configuration/value classes are acceptable)")
                else:
                    parts.append("  [OK] No DIP concerns detected")
                parts.append("")
                continue

            for c in concerns:
                parts.append(f"  [WARNING] {c.message}")

            if cls_name in fr.suggestions:
                parts.append(f"  [SUGGESTION] {fr.suggestions[cls_name]}")

            parts.append("")

    if not parts:
        return "No DIP concerns found.\n"

    return "\n".join(parts) + "\n"


def format_json(results: List[FileResult]) -> str:
    """Machine-readable JSON output."""
    output: List[dict] = []
    for fr in results:
        file_obj: dict = {
            "file": fr.file_path,
            "language": fr.language,
            "classes": [],
            "concerns": [],
        }
        for ci in fr.classes:
            file_obj["classes"].append({
                "name": ci.name,
                "start_line": ci.start_line,
                "end_line": ci.end_line,
                "constructor_params": ci.constructor_params,
                "instantiations": [
                    {
                        "class_name": i.class_name,
                        "line": i.line,
                        "location": i.location,
                        "attribute": i.attribute,
                    }
                    for i in ci.instantiations
                ],
                "is_abstract": ci.is_abstract,
                "is_config_or_value": ci.is_config_or_value,
            })
        for c in fr.concerns:
            file_obj["concerns"].append({
                "kind": c.kind,
                "message": c.message,
                "line": c.line,
                "class_name": c.class_name,
                "instantiated_class": c.instantiated_class,
                "location": c.location,
            })
        output.append(file_obj)

    return json.dumps(output, indent=2)


def format_rewrite(results: List[FileResult]) -> str:
    """Output refactored code suggestions showing constructor injection."""
    parts: List[str] = []
    for fr in results:
        if not fr.rewrite_snippets:
            continue
        parts.append(f"\n=== Refactored Constructors: {fr.file_path} ===\n")
        for cls_name, snippet in fr.rewrite_snippets.items():
            parts.append(f"Class: {cls_name}")
            parts.append(f"  Replace the constructor with dependency injection:\n")
            for line in snippet.splitlines():
                parts.append(f"    {line}")
            parts.append("")
    if not parts:
        return "No refactoring suggestions (no DIP concerns found).\n"
    return "\n".join(parts) + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="check_dip",
        description="Detect Dependency Inversion Principle (DIP) violations in source code.",
    )
    parser.add_argument(
        "path",
        help="File or directory to analyze (recursively finds supported source files).",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        default=False,
        help="Show detailed output including classes with no concerns.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        dest="json_output",
        help="Output results as machine-readable JSON.",
    )
    parser.add_argument(
        "--rewrite",
        action="store_true",
        default=False,
        help="Output refactored code suggestions with dependency injection.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    target = Path(args.path)
    if not target.exists():
        print(f"Error: path does not exist: {target}", file=sys.stderr)
        return 2

    files = collect_files(target)
    if not files:
        print(f"Error: no supported source files found in: {target}", file=sys.stderr)
        return 2

    results: List[FileResult] = []
    for fp in files:
        try:
            source = fp.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"Warning: could not read {fp}: {exc}", file=sys.stderr)
            continue

        ext = fp.suffix
        language = LANGUAGE_MAP.get(ext, "Unknown")

        if language == "Python":
            result = analyze_python(fp, source)
        elif language != "Unknown":
            result = analyze_regex(fp, source, language)
        else:
            continue

        results.append(result)

    # Output
    if args.json_output:
        print(format_json(results))
    elif args.rewrite:
        print(format_rewrite(results))
    else:
        print(format_text(results, verbose=args.verbose))

    # Exit code
    has_concerns = any(r.concerns for r in results)
    return 1 if has_concerns else 0


if __name__ == "__main__":
    sys.exit(main())
