#!/usr/bin/env python3
"""
check_oop_principles.py — OOP Principles Violation Detector

Analyzes source code files to detect violations of core OOP principles:
coupling, cohesion, encapsulation, and inheritance design.
Works language-agnostically: uses Python's ast module for .py files and
regex-based heuristics for all other supported languages.

Supported languages:
    .py, .java, .ts, .js, .cs, .rb, .kt, .go, .swift, .cpp, .hpp, .php

Detections:
    1. Tight coupling — classes referencing 8+ distinct concrete classes
       (high efferent coupling)
    2. Low cohesion (LCOM) — methods access disjoint sets of instance
       attributes, indicating the class should be split
    3. Inheritance abuse — deep hierarchies (depth >3), excessive overrides,
       empty/stub overrides suggesting misuse of inheritance
    4. Poor encapsulation — classes with many public data attributes but
       few/no methods (data bags without behavior)
    5. Composition opportunity — classes that inherit but rarely call super()
       or use only a small subset of parent's API

Exit codes:
    0 — No OOP concerns found
    1 — One or more OOP concerns found
    2 — Input error (file/directory not found, no eligible files, etc.)

Usage:
    python check_oop_principles.py path/to/file_or_directory
    python check_oop_principles.py path/ --verbose
    python check_oop_principles.py path/ --json
    python check_oop_principles.py path/ --rewrite
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
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

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

# Threshold: a class referencing this many distinct concrete classes is tightly coupled.
COUPLING_THRESHOLD = 8

# Threshold: inheritance depth beyond which we flag hierarchy abuse.
INHERITANCE_DEPTH_THRESHOLD = 3

# Threshold: ratio of public attributes to methods below which we flag poor encapsulation.
# If public_attrs >= this AND methods < public_attrs, flag it.
PUBLIC_ATTR_THRESHOLD = 4

# Threshold: fraction of overridden methods that are stubs/empty to flag.
STUB_OVERRIDE_RATIO = 0.5

# Minimum number of methods to consider LCOM analysis meaningful.
MIN_METHODS_FOR_LCOM = 3

# Classes/types to ignore when counting coupling (builtins, common types).
COUPLING_IGNORE: set[str] = {
    # Python builtins
    "int", "str", "float", "bool", "list", "dict", "set", "tuple",
    "bytes", "bytearray", "frozenset", "type", "object", "None",
    "Exception", "ValueError", "TypeError", "RuntimeError", "KeyError",
    "AttributeError", "IOError", "OSError", "IndexError", "StopIteration",
    "NotImplementedError", "ImportError", "FileNotFoundError",
    # Common stdlib
    "Path", "Optional", "List", "Dict", "Set", "Tuple", "Any",
    "Union", "Callable", "Iterator", "Generator", "Sequence",
    "Mapping", "MutableMapping", "Iterable", "Type",
    # Java/C#/general
    "String", "Integer", "Double", "Float", "Boolean", "Object",
    "Long", "Short", "Byte", "Character", "Void",
    "ArrayList", "HashMap", "HashSet", "LinkedList",
    "List", "Map", "Set", "Collection", "Iterator",
    "Date", "LocalDate", "LocalDateTime", "Instant",
    "BigDecimal", "BigInteger", "UUID",
    # JS/TS
    "Array", "Map", "Set", "Promise", "Error", "RegExp",
    "Date", "Number", "Symbol", "URL",
    # Interfaces/abstract markers
    "ABC", "ABCMeta", "Protocol", "Interface",
}

# Patterns that identify abstract/interface base classes.
ABSTRACT_BASE_NAMES: set[str] = {
    "ABC", "ABCMeta", "Protocol", "Interface",
    "Abstract", "AbstractBase", "BaseClass",
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class MethodDetail:
    """Detail about a single method for OOP analysis."""
    name: str
    line_start: int
    line_end: int
    accessed_attrs: set[str] = field(default_factory=set)
    calls_super: bool = False
    is_override: bool = False
    is_stub: bool = False  # empty body / pass / raise NotImplementedError


@dataclass
class ClassDetail:
    """All information gathered about a single class for OOP analysis."""
    name: str
    line_start: int
    line_end: int
    language: str
    methods: list[MethodDetail] = field(default_factory=list)
    base_classes: list[str] = field(default_factory=list)
    inheritance_depth: int = 0
    referenced_classes: set[str] = field(default_factory=set)
    public_attrs: list[str] = field(default_factory=list)
    private_attrs: list[str] = field(default_factory=list)
    all_instance_attrs: set[str] = field(default_factory=set)


@dataclass
class OOPWarning:
    """A single OOP-related warning for a class."""
    kind: str  # "tight_coupling" | "low_cohesion" | "deep_inheritance" |
    # "stub_overrides" | "poor_encapsulation" | "composition_opportunity"
    severity: str  # "WARNING" | "SUGGESTION"
    message: str
    details: dict = field(default_factory=dict)


@dataclass
class ClassReport:
    """Full analysis report for one class."""
    cls: ClassDetail
    warnings: list[OOPWarning] = field(default_factory=list)

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

class _PythonOOPVisitor(ast.NodeVisitor):
    """Walk a Python AST and collect ClassDetail for each class."""

    def __init__(self, source_lines: list[str], filepath: str):
        self.source_lines = source_lines
        self.filepath = filepath
        self.classes: list[ClassDetail] = []
        # Map class name -> list of base names for hierarchy depth calc.
        self._class_bases: dict[str, list[str]] = {}

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        line_end = self._node_end_line(node)
        bases = []
        for b in node.bases:
            bn = self._extract_name(b)
            if bn:
                bases.append(bn)
        self._class_bases[node.name] = bases

        cls = ClassDetail(
            name=node.name,
            line_start=node.lineno,
            line_end=line_end,
            language="Python",
            base_classes=bases,
            inheritance_depth=0,  # computed later
        )

        # Gather referenced classes, methods, and attributes.
        referenced: set[str] = set()
        public_attrs: list[str] = []
        private_attrs: list[str] = []
        all_instance_attrs: set[str] = set()

        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                md = self._analyze_method(item, node)
                cls.methods.append(md)
            elif isinstance(item, ast.Assign):
                # Class-level assignments (class attributes).
                for target in item.targets:
                    if isinstance(target, ast.Name):
                        if target.id.startswith("_"):
                            private_attrs.append(target.id)
                        else:
                            public_attrs.append(target.id)

        # Walk entire class body for Name references to identify coupling.
        for child in ast.walk(node):
            if isinstance(child, ast.Name):
                if child.id and child.id[0].isupper() and child.id not in COUPLING_IGNORE:
                    if child.id != node.name:  # don't count self-references
                        referenced.add(child.id)
            elif isinstance(child, ast.Attribute):
                # Track self.x attribute accesses for cohesion and encapsulation.
                if isinstance(child.value, ast.Name) and child.value.id == "self":
                    all_instance_attrs.add(child.attr)

        # Separate public vs private instance attributes from __init__.
        init_method = None
        for m in cls.methods:
            if m.name == "__init__":
                init_method = m
                break

        if init_method:
            for attr in init_method.accessed_attrs:
                if attr.startswith("_"):
                    if attr not in private_attrs:
                        private_attrs.append(attr)
                else:
                    if attr not in public_attrs:
                        public_attrs.append(attr)

        cls.referenced_classes = referenced
        cls.public_attrs = public_attrs
        cls.private_attrs = private_attrs
        cls.all_instance_attrs = all_instance_attrs

        self.classes.append(cls)
        # Don't generic_visit to avoid double-processing nested classes.

    def _analyze_method(self, node: ast.FunctionDef, class_node: ast.ClassDef) -> MethodDetail:
        """Analyze a single method within a class."""
        line_end = self._node_end_line(node)
        md = MethodDetail(
            name=node.name,
            line_start=node.lineno,
            line_end=line_end,
        )

        # Track which self.x attributes this method accesses.
        accessed: set[str] = set()
        calls_super = False
        is_stub = False

        for child in ast.walk(node):
            # self.attr access
            if isinstance(child, ast.Attribute):
                if isinstance(child.value, ast.Name) and child.value.id == "self":
                    accessed.add(child.attr)
            # super() call
            if isinstance(child, ast.Call):
                if isinstance(child.func, ast.Name) and child.func.id == "super":
                    calls_super = True
                elif isinstance(child.func, ast.Attribute):
                    if isinstance(child.func.value, ast.Call):
                        if isinstance(child.func.value.func, ast.Name) and child.func.value.func.id == "super":
                            calls_super = True

        # Check if method body is a stub (pass, ..., raise NotImplementedError).
        body = node.body
        if len(body) == 1:
            stmt = body[0]
            if isinstance(stmt, ast.Pass):
                is_stub = True
            elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
                if stmt.value.value is ...:
                    is_stub = True
            elif isinstance(stmt, ast.Raise):
                if isinstance(stmt.exc, ast.Call):
                    exc_name = self._extract_name(stmt.exc.func)
                    if exc_name == "NotImplementedError":
                        is_stub = True
        elif len(body) == 2:
            # docstring + pass/...
            first = body[0]
            second = body[1]
            is_docstring = isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str)
            if is_docstring:
                if isinstance(second, ast.Pass):
                    is_stub = True
                elif isinstance(second, ast.Expr) and isinstance(second.value, ast.Constant) and second.value.value is ...:
                    is_stub = True

        # Check if this is an override (same-name method in parent).
        is_override = False
        if class_node.bases:
            # Heuristic: methods decorated with @override or common override pattern.
            for dec in node.decorator_list:
                dn = self._extract_name(dec)
                if dn and dn.lower() == "override":
                    is_override = True
            # Also: if class has bases and method name doesn't start with _,
            # it *might* be an override. We'll mark known patterns.

        md.accessed_attrs = accessed
        md.calls_super = calls_super
        md.is_stub = is_stub
        md.is_override = is_override
        return md

    def compute_inheritance_depths(self) -> None:
        """Compute inheritance depth for each class using local class definitions."""
        for cls in self.classes:
            cls.inheritance_depth = self._compute_depth(cls.name, set())

    def _compute_depth(self, class_name: str, visited: set[str]) -> int:
        """Recursively compute inheritance depth."""
        if class_name in visited:
            return 0
        visited.add(class_name)
        bases = self._class_bases.get(class_name, [])
        if not bases:
            return 0
        # Filter out object and abstract bases.
        real_bases = [b for b in bases if b not in ("object",) and b not in ABSTRACT_BASE_NAMES]
        if not real_bases:
            return 0
        max_depth = 0
        for base in real_bases:
            depth = 1 + self._compute_depth(base, visited)
            if depth > max_depth:
                max_depth = depth
        return max_depth

    def _node_end_line(self, node: ast.AST) -> int:
        if hasattr(node, "end_lineno") and node.end_lineno is not None:
            return node.end_lineno
        end = getattr(node, "lineno", 1)
        for child in ast.walk(node):
            child_end = getattr(child, "end_lineno", None) or getattr(child, "lineno", 0)
            if child_end > end:
                end = child_end
        return end

    @staticmethod
    def _extract_name(node: ast.expr) -> Optional[str]:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return None


def _analyze_python_file(filepath: str, source: str) -> list[ClassDetail]:
    """Use the ast module to extract OOP details from a Python file."""
    try:
        tree = ast.parse(source, filename=filepath)
    except SyntaxError:
        return []

    lines = source.splitlines()
    visitor = _PythonOOPVisitor(lines, filepath)
    visitor.visit(tree)
    visitor.compute_inheritance_depths()
    return visitor.classes


# ---------------------------------------------------------------------------
# Regex-based analysis for other languages
# ---------------------------------------------------------------------------

_CLASS_PATTERNS: dict[str, re.Pattern] = {
    "Java":       re.compile(r"^\s*(?:public\s+|private\s+|protected\s+)?(?:abstract\s+|final\s+)?class\s+(\w+)(?:\s+extends\s+(\w+))?", re.MULTILINE),
    "TypeScript": re.compile(r"^\s*(?:export\s+)?(?:abstract\s+)?class\s+(\w+)(?:\s+extends\s+(\w+))?", re.MULTILINE),
    "JavaScript": re.compile(r"^\s*(?:export\s+)?class\s+(\w+)(?:\s+extends\s+(\w+))?", re.MULTILINE),
    "C#":         re.compile(r"^\s*(?:public\s+|private\s+|protected\s+|internal\s+)?(?:abstract\s+|sealed\s+|static\s+|partial\s+)*class\s+(\w+)(?:\s*:\s*(\w+))?", re.MULTILINE),
    "Ruby":       re.compile(r"^\s*class\s+(\w+)(?:\s*<\s*(\w+))?", re.MULTILINE),
    "Kotlin":     re.compile(r"^\s*(?:open\s+|abstract\s+|data\s+|sealed\s+)?class\s+(\w+)(?:[^:]*:\s*(\w+))?", re.MULTILINE),
    "Go":         re.compile(r"^\s*type\s+(\w+)\s+struct\s*\{", re.MULTILINE),
    "Swift":      re.compile(r"^\s*(?:open\s+|public\s+|internal\s+|fileprivate\s+|private\s+)?(?:final\s+)?class\s+(\w+)(?:\s*:\s*(\w+))?", re.MULTILINE),
    "C++":        re.compile(r"^\s*class\s+(\w+)(?:\s*:\s*(?:public|protected|private)\s+(\w+))?", re.MULTILINE),
    "PHP":        re.compile(r"^\s*(?:abstract\s+|final\s+)?class\s+(\w+)(?:\s+extends\s+(\w+))?", re.MULTILINE),
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

# Patterns to find class references (for coupling analysis).
_CLASS_REF_PATTERN = re.compile(r"\b([A-Z][a-zA-Z0-9]+)\b")

# Patterns to detect override keyword per language.
_OVERRIDE_PATTERNS: dict[str, re.Pattern] = {
    "Java":       re.compile(r"@Override", re.MULTILINE),
    "C#":         re.compile(r"\boverride\b", re.MULTILINE),
    "Kotlin":     re.compile(r"\boverride\b", re.MULTILINE),
    "Swift":      re.compile(r"\boverride\b", re.MULTILINE),
    "C++":        re.compile(r"\boverride\b", re.MULTILINE),
}

# Patterns to detect super calls per language.
_SUPER_PATTERNS: dict[str, re.Pattern] = {
    "Java":       re.compile(r"\bsuper\.", re.MULTILINE),
    "TypeScript": re.compile(r"\bsuper\.", re.MULTILINE),
    "JavaScript": re.compile(r"\bsuper\.", re.MULTILINE),
    "C#":         re.compile(r"\bbase\.", re.MULTILINE),
    "Kotlin":     re.compile(r"\bsuper\.", re.MULTILINE),
    "Ruby":       re.compile(r"\bsuper\b", re.MULTILINE),
    "Swift":      re.compile(r"\bsuper\.", re.MULTILINE),
    "C++":        re.compile(r"\b\w+::\w+\(", re.MULTILINE),
    "PHP":        re.compile(r"\bparent::", re.MULTILINE),
}

# Patterns to detect field/attribute access (this.x / self.x / @x).
_FIELD_ACCESS_PATTERNS: dict[str, re.Pattern] = {
    "Java":       re.compile(r"\bthis\.(\w+)"),
    "TypeScript": re.compile(r"\bthis\.(\w+)"),
    "JavaScript": re.compile(r"\bthis\.(\w+)"),
    "C#":         re.compile(r"\bthis\.(\w+)"),
    "Kotlin":     re.compile(r"\bthis\.(\w+)"),
    "Ruby":       re.compile(r"@(\w+)"),
    "Swift":      re.compile(r"\bself\.(\w+)"),
    "C++":        re.compile(r"\bthis->(\w+)"),
    "Go":         re.compile(r"\b\w+\.(\w+)"),  # receiver.field
    "PHP":        re.compile(r"\$this->(\w+)"),
}

# Patterns to detect public fields (for encapsulation analysis).
_PUBLIC_FIELD_PATTERNS: dict[str, re.Pattern] = {
    "Java":       re.compile(r"^\s*public\s+(?!.*\()(?:\w+(?:<[^>]*>)?)\s+(\w+)\s*[;=]", re.MULTILINE),
    "TypeScript": re.compile(r"^\s*(?:public\s+)?(\w+)\s*(?::\s*\w+)?\s*[;=]", re.MULTILINE),
    "C#":         re.compile(r"^\s*public\s+(?!.*\()(?:\w+(?:<[^>]*>)?)\s+(\w+)\s*\{?\s*(?:get|set)?", re.MULTILINE),
    "Kotlin":     re.compile(r"^\s*(?:val|var)\s+(\w+)", re.MULTILINE),
    "Swift":      re.compile(r"^\s*(?:public\s+)?(?:var|let)\s+(\w+)", re.MULTILINE),
    "C++":        re.compile(r"^\s*(?:public:\s*\n)?\s*(\w+)\s+\w+\s*;", re.MULTILINE),
    "PHP":        re.compile(r"^\s*public\s+(?:\?\w+\s+)?\$(\w+)", re.MULTILINE),
}


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
                    return i + 1
    return len(lines)


def _find_ruby_class_end(lines: list[str], start_line: int) -> int:
    """Find the matching 'end' for a Ruby class definition."""
    depth = 0
    for i in range(start_line - 1, len(lines)):
        stripped = lines[i].strip()
        if re.match(r"(class|module|def|do|if|unless|case|while|until|for|begin)\b", stripped):
            depth += 1
        if stripped == "end" or re.match(r"end\b", stripped):
            depth -= 1
            if depth <= 0:
                return i + 1
    return len(lines)


def _analyze_regex_file(filepath: str, source: str, language: str) -> list[ClassDetail]:
    """Heuristic regex-based extraction for non-Python files."""
    class_pat = _CLASS_PATTERNS.get(language)
    method_pat = _METHOD_PATTERNS.get(language)
    if class_pat is None:
        return []

    lines = source.splitlines()
    classes: list[ClassDetail] = []
    # Track class names for inheritance depth computation.
    class_bases_map: dict[str, str] = {}

    class_matches: list[Tuple[int, str, str, int]] = []  # (line, name, base, offset)
    for m in class_pat.finditer(source):
        line_no = source[:m.start()].count("\n") + 1
        name = m.group(1)
        base = m.group(2) if m.lastindex and m.lastindex >= 2 else ""
        class_matches.append((line_no, name, base or "", m.start()))
        if base:
            class_bases_map[name] = base

    for idx, (line_no, name, base, char_offset) in enumerate(class_matches):
        if language == "Ruby":
            line_end = _find_ruby_class_end(lines, line_no)
        else:
            line_end = _find_brace_end(lines, line_no)

        if idx + 1 < len(class_matches):
            next_start = class_matches[idx + 1][0]
            if line_end >= next_start:
                line_end = next_start - 1

        cls = ClassDetail(
            name=name,
            line_start=line_no,
            line_end=line_end,
            language=language,
            base_classes=[base] if base else [],
        )

        class_source = "\n".join(lines[line_no - 1:line_end])

        # --- Methods ---
        false_positives = {"if", "for", "while", "switch", "catch", "class", "new", "return", "else"}
        if method_pat:
            for mm in method_pat.finditer(class_source):
                m_line = class_source[:mm.start()].count("\n") + line_no
                mname = mm.group(1)
                if mname in false_positives:
                    continue

                # Find method body extent (approximate).
                m_end = m_line
                if language == "Ruby":
                    m_end_idx = _find_ruby_class_end(class_source.splitlines(), class_source[:mm.start()].count("\n") + 1)
                    m_end = line_no + m_end_idx - 1
                else:
                    brace_pos = class_source.find("{", mm.start())
                    if brace_pos != -1:
                        cs_lines = class_source.splitlines()
                        m_end_idx = _find_brace_end(cs_lines, class_source[:brace_pos].count("\n") + 1)
                        m_end = line_no + m_end_idx - 1

                method_body_start = mm.start()
                method_body_end = min(len(class_source), method_body_start + (m_end - m_line + 1) * 120)
                method_body = class_source[method_body_start:method_body_end]

                # Detect field accesses within method.
                field_pat = _FIELD_ACCESS_PATTERNS.get(language)
                accessed_attrs: set[str] = set()
                if field_pat:
                    for fm in field_pat.finditer(method_body):
                        accessed_attrs.add(fm.group(1))

                # Detect super calls.
                calls_super = False
                super_pat = _SUPER_PATTERNS.get(language)
                if super_pat and super_pat.search(method_body):
                    calls_super = True

                # Detect override.
                is_override = False
                override_pat = _OVERRIDE_PATTERNS.get(language)
                if override_pat:
                    # Check lines around method declaration for @Override or override keyword.
                    decl_line_idx = class_source[:mm.start()].count("\n")
                    check_start = max(0, decl_line_idx - 1)
                    check_lines = class_source.splitlines()[check_start:decl_line_idx + 1]
                    check_text = "\n".join(check_lines)
                    if override_pat.search(check_text):
                        is_override = True

                # Detect stub body.
                is_stub = _is_stub_body(method_body, language)

                cls.methods.append(MethodDetail(
                    name=mname,
                    line_start=m_line,
                    line_end=m_end,
                    accessed_attrs=accessed_attrs,
                    calls_super=calls_super,
                    is_override=is_override,
                    is_stub=is_stub,
                ))

        # --- Coupling: count class references ---
        referenced: set[str] = set()
        for ref_match in _CLASS_REF_PATTERN.finditer(class_source):
            ref_name = ref_match.group(1)
            if ref_name not in COUPLING_IGNORE and ref_name != name:
                referenced.add(ref_name)
        cls.referenced_classes = referenced

        # --- Public fields (encapsulation) ---
        pub_field_pat = _PUBLIC_FIELD_PATTERNS.get(language)
        public_attrs: list[str] = []
        if pub_field_pat:
            for pf in pub_field_pat.finditer(class_source):
                fname = pf.group(1)
                if fname not in false_positives:
                    public_attrs.append(fname)
        cls.public_attrs = public_attrs

        # --- Instance attributes ---
        field_pat = _FIELD_ACCESS_PATTERNS.get(language)
        all_attrs: set[str] = set()
        if field_pat:
            for fm in field_pat.finditer(class_source):
                all_attrs.add(fm.group(1))
        cls.all_instance_attrs = all_attrs

        classes.append(cls)

    # Compute inheritance depths.
    def compute_depth(cname: str, visited: set[str]) -> int:
        if cname in visited:
            return 0
        visited.add(cname)
        parent = class_bases_map.get(cname)
        if not parent or parent in ABSTRACT_BASE_NAMES:
            return 0
        return 1 + compute_depth(parent, visited)

    for cls in classes:
        cls.inheritance_depth = compute_depth(cls.name, set())

    return classes


def _is_stub_body(method_body: str, language: str) -> bool:
    """Heuristic check if a method body is essentially a stub."""
    # Extract body content (after opening brace for brace-based languages).
    brace_pos = method_body.find("{")
    if brace_pos == -1:
        return False

    # Find matching close brace.
    depth = 0
    body_start = -1
    body_end = -1
    for i in range(brace_pos, len(method_body)):
        if method_body[i] == "{":
            if depth == 0:
                body_start = i + 1
            depth += 1
        elif method_body[i] == "}":
            depth -= 1
            if depth == 0:
                body_end = i
                break

    if body_start == -1 or body_end == -1:
        return False

    inner = method_body[body_start:body_end].strip()
    if not inner:
        return True  # empty body

    # Common stub patterns.
    stub_patterns = [
        r"^\s*(?:throw\s+new\s+(?:Unsupported|NotImplemented)\w*\s*\(.*\)\s*;?\s*)$",
        r"^\s*(?:raise\s+NotImplementedError.*)\s*$",
        r"^\s*(?:pass)\s*$",
        r"^\s*(?:return\s*;?\s*)$",
        r"^\s*(?://\s*TODO.*)\s*$",
        r"^\s*(?:#\s*TODO.*)\s*$",
        r"^\s*(?:\.\.\.)\s*$",
    ]
    for pat in stub_patterns:
        if re.match(pat, inner, re.DOTALL | re.IGNORECASE):
            return True

    return False


# ---------------------------------------------------------------------------
# Analysis logic
# ---------------------------------------------------------------------------

def _analyze_coupling(cls: ClassDetail, report: ClassReport) -> None:
    """Check for tight coupling (high efferent coupling)."""
    ref_count = len(cls.referenced_classes)
    if ref_count >= COUPLING_THRESHOLD:
        refs_sorted = sorted(cls.referenced_classes)
        report.warnings.append(OOPWarning(
            kind="tight_coupling",
            severity="WARNING",
            message=f"Tight coupling: references {ref_count} concrete classes (threshold: {COUPLING_THRESHOLD})",
            details={
                "count": ref_count,
                "threshold": COUPLING_THRESHOLD,
                "referenced": refs_sorted,
            },
        ))


def _analyze_cohesion(cls: ClassDetail, report: ClassReport) -> None:
    """Check for low cohesion using LCOM-like metric (disjoint attribute groups)."""
    # Filter to instance methods that access at least one attribute.
    eligible_methods = [
        m for m in cls.methods
        if m.accessed_attrs
        and m.name not in ("__init__", "__new__", "__del__", "constructor", "initialize")
        and not (m.name.startswith("__") and m.name.endswith("__"))
    ]

    if len(eligible_methods) < MIN_METHODS_FOR_LCOM:
        return

    # Build a graph: methods are connected if they share at least one attribute.
    method_names = [m.name for m in eligible_methods]
    method_attrs = [m.accessed_attrs for m in eligible_methods]
    n = len(eligible_methods)

    # Union-Find for connected components.
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(n):
        for j in range(i + 1, n):
            if method_attrs[i] & method_attrs[j]:  # shared attributes
                union(i, j)

    # Group methods by component.
    components: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        components[find(i)].append(i)

    if len(components) >= 2:
        groups: list[dict] = []
        for comp_id, indices in sorted(components.items()):
            attrs = set()
            methods = []
            for i in indices:
                attrs |= method_attrs[i]
                methods.append(method_names[i])
            groups.append({
                "attributes": sorted(attrs),
                "methods": sorted(methods),
            })

        report.warnings.append(OOPWarning(
            kind="low_cohesion",
            severity="WARNING",
            message=f"Low cohesion: methods access disjoint attribute sets ({len(components)} groups)",
            details={
                "num_groups": len(components),
                "groups": groups,
            },
        ))


def _analyze_inheritance(cls: ClassDetail, report: ClassReport) -> None:
    """Check for inheritance abuse: deep hierarchies, stub overrides."""
    # Deep hierarchy.
    if cls.inheritance_depth > INHERITANCE_DEPTH_THRESHOLD:
        report.warnings.append(OOPWarning(
            kind="deep_inheritance",
            severity="WARNING",
            message=f"Deep inheritance hierarchy: depth {cls.inheritance_depth} (threshold: {INHERITANCE_DEPTH_THRESHOLD})",
            details={
                "depth": cls.inheritance_depth,
                "threshold": INHERITANCE_DEPTH_THRESHOLD,
                "bases": cls.base_classes,
            },
        ))

    # Excessive/stub overrides.
    overrides = [m for m in cls.methods if m.is_override]
    stub_overrides = [m for m in overrides if m.is_stub]

    if overrides and stub_overrides:
        ratio = len(stub_overrides) / len(overrides) if overrides else 0
        if ratio >= STUB_OVERRIDE_RATIO and len(stub_overrides) >= 2:
            report.warnings.append(OOPWarning(
                kind="stub_overrides",
                severity="WARNING",
                message=f"Inheritance abuse: {len(stub_overrides)} of {len(overrides)} overridden methods are stubs/empty",
                details={
                    "stub_methods": [m.name for m in stub_overrides],
                    "all_overrides": [m.name for m in overrides],
                    "ratio": round(ratio, 2),
                },
            ))


def _analyze_encapsulation(cls: ClassDetail, report: ClassReport) -> None:
    """Check for poor encapsulation: many public attrs, few methods."""
    # Count non-dunder, non-init methods.
    real_methods = [
        m for m in cls.methods
        if not (m.name.startswith("__") and m.name.endswith("__"))
    ]

    num_public_attrs = len(cls.public_attrs)

    if num_public_attrs >= PUBLIC_ATTR_THRESHOLD and len(real_methods) < num_public_attrs:
        report.warnings.append(OOPWarning(
            kind="poor_encapsulation",
            severity="WARNING",
            message=f"Poor encapsulation: {num_public_attrs} public attributes but only {len(real_methods)} methods",
            details={
                "public_attrs": cls.public_attrs[:20],
                "num_public_attrs": num_public_attrs,
                "num_methods": len(real_methods),
            },
        ))


def _analyze_composition_opportunity(cls: ClassDetail, report: ClassReport) -> None:
    """Detect classes that inherit but should prefer composition."""
    if not cls.base_classes:
        return
    # Filter out abstract/interface bases.
    real_bases = [b for b in cls.base_classes if b not in ABSTRACT_BASE_NAMES and b != "object"]
    if not real_bases:
        return

    # Check if the class rarely calls super().
    methods_with_logic = [
        m for m in cls.methods
        if m.name not in ("__init__", "__new__", "constructor", "initialize")
        and not (m.name.startswith("__") and m.name.endswith("__"))
    ]

    if not methods_with_logic:
        return

    super_callers = [m for m in methods_with_logic if m.calls_super]
    super_ratio = len(super_callers) / len(methods_with_logic) if methods_with_logic else 0

    # If the class inherits but less than 20% of methods call super,
    # and it has 3+ methods, suggest composition.
    if len(methods_with_logic) >= 3 and super_ratio < 0.2:
        report.warnings.append(OOPWarning(
            kind="composition_opportunity",
            severity="SUGGESTION",
            message=(
                f"Composition over inheritance: inherits from {', '.join(real_bases)} "
                f"but only {len(super_callers)}/{len(methods_with_logic)} methods use "
                f"parent's API"
            ),
            details={
                "base_classes": real_bases,
                "total_methods": len(methods_with_logic),
                "super_callers": len(super_callers),
                "super_ratio": round(super_ratio, 2),
                "methods_calling_super": [m.name for m in super_callers],
            },
        ))


# ---------------------------------------------------------------------------
# Analysis orchestrator
# ---------------------------------------------------------------------------

def analyze_file(filepath: str) -> FileReport:
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
    elif language != "Unknown":
        classes = _analyze_regex_file(filepath, source, language)
    else:
        return report

    for cls in classes:
        cr = ClassReport(cls=cls)

        _analyze_coupling(cls, cr)
        _analyze_cohesion(cls, cr)
        _analyze_inheritance(cls, cr)
        _analyze_encapsulation(cls, cr)
        _analyze_composition_opportunity(cls, cr)

        report.class_reports.append(cr)

    return report


# ---------------------------------------------------------------------------
# Output formatting -- plain text
# ---------------------------------------------------------------------------

def _format_plain(reports: list[FileReport], verbose: bool, rewrite: bool) -> str:
    """Format reports as human-readable plain text."""
    parts: list[str] = []

    for report in reports:
        parts.append(f"=== OOP Principles Analysis: {report.filepath} ===")
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
            parts.append(
                f"Class: {cls.name} (lines {cls.line_start}-{cls.line_end})"
            )

            if not cr.has_concerns:
                if verbose:
                    parts.append("  [OK] No OOP concerns detected")
            else:
                for w in cr.warnings:
                    parts.append(f"  [{w.severity}] {w.message}")

                    if verbose or w.kind == "tight_coupling":
                        refs = w.details.get("referenced")
                        if refs:
                            parts.append(f"    Referenced: {', '.join(refs)}")

                    if verbose or w.kind == "low_cohesion":
                        groups = w.details.get("groups")
                        if groups:
                            for i, grp in enumerate(groups, 1):
                                attrs_str = ", ".join(grp["attributes"])
                                methods_str = ", ".join(grp["methods"])
                                parts.append(f"    Group {i} ({attrs_str}): {methods_str}")

                    if verbose and w.kind == "stub_overrides":
                        stubs = w.details.get("stub_methods", [])
                        if stubs:
                            parts.append(f"    Stub overrides: {', '.join(stubs)}")

                    if verbose and w.kind == "poor_encapsulation":
                        attrs = w.details.get("public_attrs", [])
                        if attrs:
                            parts.append(f"    Public attributes: {', '.join(attrs)}")

                    if verbose and w.kind == "composition_opportunity":
                        super_methods = w.details.get("methods_calling_super", [])
                        if super_methods:
                            parts.append(f"    Methods using parent: {', '.join(super_methods)}")

                # Append suggestions for each warning type.
                suggestions = _generate_suggestions(cr)
                for s in suggestions:
                    parts.append(f"  [SUGGESTION] {s}")

            parts.append("")

        if rewrite:
            rewrite_text = _generate_rewrite(report)
            if rewrite_text:
                parts.append(rewrite_text)
                parts.append("")

    return "\n".join(parts)


def _generate_suggestions(cr: ClassReport) -> list[str]:
    """Generate actionable suggestions based on warnings."""
    suggestions: list[str] = []

    for w in cr.warnings:
        if w.kind == "tight_coupling":
            suggestions.append(
                "Reduce coupling by depending on abstractions (interfaces/protocols) "
                "instead of concrete classes"
            )
        elif w.kind == "low_cohesion":
            groups = w.details.get("groups", [])
            if len(groups) == 2:
                g1_methods = ", ".join(groups[0]["methods"])
                g2_methods = ", ".join(groups[1]["methods"])
                suggestions.append(
                    f"Split into two classes: one for ({g1_methods}), "
                    f"another for ({g2_methods})"
                )
            elif len(groups) > 2:
                suggestions.append(
                    f"Split into {len(groups)} focused classes, one per cohesive method group"
                )
        elif w.kind == "deep_inheritance":
            suggestions.append(
                "Flatten the hierarchy or use composition/mixins to reduce inheritance depth"
            )
        elif w.kind == "stub_overrides":
            suggestions.append(
                "Consider using the Interface Segregation Principle: "
                "split the parent interface so subclasses don't need empty overrides"
            )
        elif w.kind == "poor_encapsulation":
            suggestions.append(
                "Encapsulate public attributes behind methods/properties; "
                "consider converting to a proper class with behavior or use a data class"
            )
        elif w.kind == "composition_opportunity":
            bases = w.details.get("base_classes", [])
            suggestions.append(
                f"Consider wrapping {', '.join(bases)} via composition instead of inheriting from it"
            )

    return suggestions


# ---------------------------------------------------------------------------
# Output formatting -- JSON
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
                "base_classes": cls.base_classes,
                "inheritance_depth": cls.inheritance_depth,
                "num_methods": len(cls.methods),
                "num_referenced_classes": len(cls.referenced_classes),
                "num_public_attrs": len(cls.public_attrs),
                "has_concerns": cr.has_concerns,
                "warnings": [
                    {
                        "kind": w.kind,
                        "severity": w.severity,
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
    """Generate refactored code suggestions for all problematic classes."""
    parts: list[str] = []
    for cr in report.class_reports:
        if not cr.has_concerns:
            continue
        text = _generate_rewrite_for_class(cr)
        if text:
            parts.append(text)
    return "\n".join(parts)


def _generate_rewrite_for_class(cr: ClassReport) -> str:
    """Generate a refactored code suggestion for a single class."""
    cls = cr.cls
    lang = cls.language
    lines: list[str] = []
    lines.append(f"--- Suggested refactoring for {cls.name} ({lang}) ---")
    lines.append("")

    for w in cr.warnings:
        if w.kind == "tight_coupling":
            lines.extend(_rewrite_coupling(cls, w))
        elif w.kind == "low_cohesion":
            lines.extend(_rewrite_cohesion(cls, w))
        elif w.kind == "poor_encapsulation":
            lines.extend(_rewrite_encapsulation(cls, w))
        elif w.kind == "composition_opportunity":
            lines.extend(_rewrite_composition(cls, w))
        elif w.kind == "deep_inheritance":
            lines.extend(_rewrite_deep_inheritance(cls, w))
        elif w.kind == "stub_overrides":
            lines.extend(_rewrite_stub_overrides(cls, w))

    lines.append(f"--- End of suggested refactoring for {cls.name} ---")
    return "\n".join(lines)


def _rewrite_coupling(cls: ClassDetail, w: OOPWarning) -> list[str]:
    """Suggest abstracting tightly-coupled references."""
    lines: list[str] = []
    refs = w.details.get("referenced", [])

    if cls.language == "Python":
        lines.append("# Extract abstractions for concrete dependencies:")
        lines.append("from abc import ABC, abstractmethod")
        lines.append("")
        for ref in refs[:5]:  # show first 5
            iface = f"{ref}Interface"
            lines.append(f"class {iface}(ABC):")
            lines.append(f'    """Abstract interface for {ref}."""')
            lines.append(f"    ...")
            lines.append("")
        lines.append(f"class {cls.name}:")
        lines.append(f"    def __init__(self, {', '.join(f'{_to_snake_case(r)}: {r}Interface' for r in refs[:5])}):")
        for ref in refs[:5]:
            snake = _to_snake_case(ref)
            lines.append(f"        self._{snake} = {snake}")
        lines.append("")
    else:
        lines.append("// Extract interfaces for concrete dependencies:")
        for ref in refs[:5]:
            lines.append(f"interface I{ref} {{ /* ... */ }}")
        lines.append("")
        lines.append(f"class {cls.name} {{")
        lines.append(f"    // Inject abstractions instead of using concrete classes directly")
        for ref in refs[:5]:
            snake = _to_snake_case(ref)
            lines.append(f"    private I{ref} {snake};")
        lines.append("}")
        lines.append("")

    return lines


def _rewrite_cohesion(cls: ClassDetail, w: OOPWarning) -> list[str]:
    """Suggest splitting a low-cohesion class."""
    lines: list[str] = []
    groups = w.details.get("groups", [])

    if cls.language == "Python":
        for i, grp in enumerate(groups):
            grp_name = f"{cls.name}{''.join(a.capitalize() for a in grp['attributes'][:2])}"
            lines.append(f"class {grp_name}:")
            lines.append(f'    """Handles: {", ".join(grp["methods"])}"""')
            lines.append("")
            for attr in grp["attributes"]:
                lines.append(f"    # self.{attr}")
            lines.append("")
            for method in grp["methods"]:
                lines.append(f"    def {method}(self, ...):")
                lines.append(f"        ...")
            lines.append("")
    else:
        for i, grp in enumerate(groups):
            grp_name = f"{cls.name}Group{i + 1}"
            lines.append(f"// New class: {grp_name}")
            lines.append(f"class {grp_name} {{")
            for method in grp["methods"]:
                lines.append(f"    {method}(...) {{")
                lines.append(f"        // Moved from {cls.name}")
                lines.append(f"    }}")
            lines.append("}")
            lines.append("")

    return lines


def _rewrite_encapsulation(cls: ClassDetail, w: OOPWarning) -> list[str]:
    """Suggest encapsulating public attributes."""
    lines: list[str] = []
    attrs = w.details.get("public_attrs", [])

    if cls.language == "Python":
        lines.append(f"class {cls.name}:")
        lines.append(f"    def __init__(self, {', '.join(attrs[:6])}):")
        for attr in attrs[:6]:
            lines.append(f"        self._{attr} = {attr}  # private")
        lines.append("")
        for attr in attrs[:6]:
            lines.append(f"    @property")
            lines.append(f"    def {attr}(self):")
            lines.append(f"        return self._{attr}")
            lines.append("")
            lines.append(f"    @{attr}.setter")
            lines.append(f"    def {attr}(self, value):")
            lines.append(f"        # Add validation here")
            lines.append(f"        self._{attr} = value")
            lines.append("")
    else:
        lines.append(f"class {cls.name} {{")
        for attr in attrs[:6]:
            lines.append(f"    private __{attr};")
        lines.append("")
        for attr in attrs[:6]:
            cap = attr[0].upper() + attr[1:]
            lines.append(f"    get{cap}() {{ return this.__{attr}; }}")
            lines.append(f"    set{cap}(value) {{ this.__{attr} = value; }}")
        lines.append("}")
        lines.append("")

    return lines


def _rewrite_composition(cls: ClassDetail, w: OOPWarning) -> list[str]:
    """Suggest wrapping parent class via composition."""
    lines: list[str] = []
    bases = w.details.get("base_classes", [])

    if cls.language == "Python":
        for base in bases:
            snake = _to_snake_case(base)
            lines.append(f"class {cls.name}:")
            lines.append(f'    """Uses composition instead of inheriting from {base}."""')
            lines.append("")
            lines.append(f"    def __init__(self, {snake}: {base}):")
            lines.append(f"        self._{snake} = {snake}")
            lines.append("")
            lines.append(f"    # Delegate only the methods you actually need:")
            lines.append(f"    def some_method(self, ...):")
            lines.append(f"        return self._{snake}.some_method(...)")
            lines.append("")
    else:
        for base in bases:
            snake = _to_snake_case(base)
            lines.append(f"// Use composition instead of inheriting from {base}")
            lines.append(f"class {cls.name} {{")
            lines.append(f"    private {base} {snake};")
            lines.append("")
            lines.append(f"    {cls.name}({base} {snake}) {{")
            lines.append(f"        this.{snake} = {snake};")
            lines.append(f"    }}")
            lines.append("")
            lines.append(f"    // Delegate only needed methods")
            lines.append(f"}}")
            lines.append("")

    return lines


def _rewrite_deep_inheritance(cls: ClassDetail, w: OOPWarning) -> list[str]:
    """Suggest flattening deep inheritance."""
    lines: list[str] = []
    depth = w.details.get("depth", 0)

    if cls.language == "Python":
        lines.append(f"# Flatten hierarchy (depth {depth}) using mixins or composition:")
        lines.append(f"class {cls.name}Mixin:")
        lines.append(f'    """Extract shared behavior as a mixin."""')
        lines.append(f"    ...")
        lines.append("")
        lines.append(f"class {cls.name}({cls.name}Mixin):")
        lines.append(f'    """Reduced inheritance depth via mixin composition."""')
        lines.append(f"    ...")
        lines.append("")
    else:
        lines.append(f"// Flatten hierarchy (depth {depth}): use interfaces + composition")
        lines.append(f"interface I{cls.name}Behavior {{ /* shared behavior contract */ }}")
        lines.append("")
        lines.append(f"class {cls.name} implements I{cls.name}Behavior {{")
        lines.append(f"    // Compose behavior instead of deep inheritance")
        lines.append(f"}}")
        lines.append("")

    return lines


def _rewrite_stub_overrides(cls: ClassDetail, w: OOPWarning) -> list[str]:
    """Suggest splitting interface to avoid stub overrides."""
    lines: list[str] = []
    stubs = w.details.get("stub_methods", [])
    all_overrides = w.details.get("all_overrides", [])
    real_overrides = [m for m in all_overrides if m not in stubs]

    if cls.language == "Python":
        lines.append("# Split the parent interface using ISP:")
        if real_overrides:
            lines.append(f"class Core{cls.base_classes[0] if cls.base_classes else 'Base'}(ABC):")
            for m in real_overrides:
                lines.append(f"    @abstractmethod")
                lines.append(f"    def {m}(self): ...")
            lines.append("")
        if stubs:
            lines.append(f"class Extended{cls.base_classes[0] if cls.base_classes else 'Base'}(ABC):")
            for m in stubs:
                lines.append(f"    @abstractmethod")
                lines.append(f"    def {m}(self): ...")
            lines.append("")
        lines.append(f"# {cls.name} only implements the interface it needs")
        lines.append("")
    else:
        lines.append("// Split the parent interface using ISP:")
        if real_overrides:
            lines.append(f"interface ICoreOperations {{")
            for m in real_overrides:
                lines.append(f"    {m}(...);")
            lines.append("}")
        if stubs:
            lines.append(f"interface IExtendedOperations {{")
            for m in stubs:
                lines.append(f"    {m}(...);")
            lines.append("}")
        lines.append(f"// {cls.name} only implements ICoreOperations")
        lines.append("")

    return lines


def _to_snake_case(s: str) -> str:
    """Convert CamelCase to snake_case."""
    s1 = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
    s2 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1)
    return s2.lower()


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
            _dirs[:] = [
                d for d in _dirs
                if not d.startswith(".")
                and d not in ("node_modules", "__pycache__", "venv", ".venv",
                              "dist", "build", "vendor", "target", "bin", "obj")
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
        prog="check_oop_principles",
        description="Detect OOP principle violations (coupling, cohesion, encapsulation, inheritance) in source code.",
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
        help="Output results as structured JSON.",
    )
    parser.add_argument(
        "--rewrite",
        action="store_true",
        default=False,
        help="Include suggested refactored code for problematic classes.",
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
        report = analyze_file(filepath)
        reports.append(report)

    if args.json_output:
        print(_format_json(reports, args.rewrite))
    else:
        print(_format_plain(reports, args.verbose, args.rewrite))

    has_any_concern = any(r.has_concerns for r in reports)
    return 1 if has_any_concern else 0


if __name__ == "__main__":
    sys.exit(main())
