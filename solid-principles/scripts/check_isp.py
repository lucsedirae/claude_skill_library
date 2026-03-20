#!/usr/bin/env python3
"""
ISP (Interface Segregation Principle) Violation Detector

Detects violations of the Interface Segregation Principle across multiple
programming languages. The ISP states that no client should be forced to
depend on methods it does not use — prefer many small, specific interfaces
over one large, general-purpose interface.

Detections:
  1. Large interfaces or abstract classes with many abstract methods
  2. Classes implementing interfaces but leaving methods as no-ops, empty
     bodies, or raising NotImplementedError — a sign the interface forces
     unwanted dependencies
  3. Language-specific patterns (ABC/abstractmethod in Python, interface
     declarations in Java/TypeScript, etc.)

Supports: .py, .java, .ts, .js, .cs, .rb, .kt, .go, .swift, .cpp, .php

Usage:
    python check_isp.py path/to/code
    python check_isp.py path/to/code --verbose --max-methods 4
    python check_isp.py path/to/code --json
    python check_isp.py path/to/code --rewrite

Exit codes:
    0 — no concerns found
    1 — concerns found
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
from pathlib import Path
from typing import List, Optional, Dict, Tuple


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class MethodInfo:
    name: str
    line: int
    is_abstract: bool = False
    is_noop: bool = False


@dataclass
class InterfaceInfo:
    name: str
    file: str
    language: str
    line_start: int
    line_end: int
    abstract_methods: List[MethodInfo] = field(default_factory=list)

    @property
    def abstract_method_names(self) -> List[str]:
        return [m.name for m in self.abstract_methods]


@dataclass
class ClassInfo:
    name: str
    file: str
    language: str
    line_start: int
    line_end: int
    bases: List[str] = field(default_factory=list)
    methods: List[MethodInfo] = field(default_factory=list)

    @property
    def noop_methods(self) -> List[MethodInfo]:
        return [m for m in self.methods if m.is_noop]


@dataclass
class Concern:
    kind: str  # "large_interface" | "noop_methods"
    file: str
    entity_name: str
    line_start: int
    line_end: int
    message: str
    suggestion: str
    method_names: List[str] = field(default_factory=list)
    noop_count: int = 0
    total_abstract: int = 0


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

EXTENSION_MAP: Dict[str, str] = {
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
    ".php": "php",
}

SUPPORTED_EXTENSIONS = set(EXTENSION_MAP.keys())


def detect_language(filepath: str) -> Optional[str]:
    ext = Path(filepath).suffix.lower()
    return EXTENSION_MAP.get(ext)


def collect_files(root: str) -> List[str]:
    root_path = Path(root)
    if root_path.is_file():
        if root_path.suffix.lower() in SUPPORTED_EXTENSIONS:
            return [str(root_path)]
        return []
    results: List[str] = []
    for dirpath, _dirs, files in os.walk(root_path):
        for fname in files:
            full = os.path.join(dirpath, fname)
            if Path(fname).suffix.lower() in SUPPORTED_EXTENSIONS:
                results.append(full)
    results.sort()
    return results


# ---------------------------------------------------------------------------
# Python analysis via ast
# ---------------------------------------------------------------------------

class _PythonAnalyzer(ast.NodeVisitor):
    """Walk a Python AST to find abstract classes/interfaces and implementors."""

    def __init__(self, source: str, filepath: str):
        self.source = source
        self.filepath = filepath
        self.lines = source.splitlines()
        self.interfaces: List[InterfaceInfo] = []
        self.classes: List[ClassInfo] = []

    def analyze(self):
        try:
            tree = ast.parse(self.source, filename=self.filepath)
        except SyntaxError:
            return
        self.visit(tree)

    def _end_line(self, node: ast.AST) -> int:
        if hasattr(node, "end_lineno") and node.end_lineno is not None:
            return node.end_lineno
        return getattr(node, "lineno", 0)

    def _is_abc_base(self, node: ast.ClassDef) -> bool:
        for base in node.bases:
            name = self._resolve_name(base)
            if name in ("ABC", "ABCMeta", "abc.ABC", "abc.ABCMeta"):
                return True
        for kw in node.keywords:
            if kw.arg == "metaclass":
                name = self._resolve_name(kw.value)
                if name in ("ABCMeta", "abc.ABCMeta"):
                    return True
        return False

    @staticmethod
    def _resolve_name(node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            parts = []
            cur = node
            while isinstance(cur, ast.Attribute):
                parts.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name):
                parts.append(cur.id)
            return ".".join(reversed(parts))
        return ""

    def _has_abstractmethod(self, func: ast.FunctionDef) -> bool:
        for dec in func.decorator_list:
            name = self._resolve_name(dec)
            if name in ("abstractmethod", "abc.abstractmethod"):
                return True
        return False

    def _is_noop_body(self, body: List[ast.stmt]) -> bool:
        stmts = body
        # skip docstring
        if stmts and isinstance(stmts[0], ast.Expr) and isinstance(stmts[0].value, (ast.Constant, ast.Str)):
            stmts = stmts[1:]
        if not stmts:
            return True
        if len(stmts) == 1:
            s = stmts[0]
            # pass
            if isinstance(s, ast.Pass):
                return True
            # Ellipsis (...)
            if isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and s.value.value is ...:
                return True
            # raise NotImplementedError
            if isinstance(s, ast.Raise):
                exc = s.exc
                if exc is not None:
                    name = self._resolve_name(exc)
                    if not name and isinstance(exc, ast.Call):
                        name = self._resolve_name(exc.func)
                    if name in ("NotImplementedError", "NotImplemented"):
                        return True
            # return None
            if isinstance(s, ast.Return) and (s.value is None or (isinstance(s.value, ast.Constant) and s.value.value is None)):
                return True
        return False

    def visit_ClassDef(self, node: ast.ClassDef):
        base_names = [self._resolve_name(b) for b in node.bases]
        is_abc = self._is_abc_base(node)
        methods: List[MethodInfo] = []
        abstract_methods: List[MethodInfo] = []

        for item in ast.walk(node):
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item in node.body:
                is_abs = self._has_abstractmethod(item)
                is_noop = self._is_noop_body(item.body)
                mi = MethodInfo(
                    name=item.name,
                    line=item.lineno,
                    is_abstract=is_abs,
                    is_noop=is_noop,
                )
                methods.append(mi)
                if is_abs:
                    abstract_methods.append(mi)

        end = self._end_line(node)

        if is_abc and abstract_methods:
            self.interfaces.append(InterfaceInfo(
                name=node.name,
                file=self.filepath,
                language="python",
                line_start=node.lineno,
                line_end=end,
                abstract_methods=abstract_methods,
            ))

        # also record as a class so noop detection works
        self.classes.append(ClassInfo(
            name=node.name,
            file=self.filepath,
            language="python",
            line_start=node.lineno,
            line_end=end,
            bases=base_names,
            methods=methods,
        ))

        self.generic_visit(node)


def analyze_python(source: str, filepath: str) -> Tuple[List[InterfaceInfo], List[ClassInfo]]:
    analyzer = _PythonAnalyzer(source, filepath)
    analyzer.analyze()
    return analyzer.interfaces, analyzer.classes


# ---------------------------------------------------------------------------
# Regex-based heuristic analysis for other languages
# ---------------------------------------------------------------------------

def _find_blocks(source: str) -> List[Tuple[int, int, str]]:
    """Return (start_line, end_line, block_text) for brace-delimited blocks."""
    blocks: List[Tuple[int, int, str]] = []
    lines = source.splitlines()
    # Simple brace-matching heuristic
    i = 0
    while i < len(lines):
        line = lines[i]
        if "{" in line:
            depth = 0
            start = i
            buf: List[str] = []
            for j in range(i, len(lines)):
                buf.append(lines[j])
                depth += lines[j].count("{") - lines[j].count("}")
                if depth <= 0:
                    blocks.append((start + 1, j + 1, "\n".join(buf)))
                    break
        i += 1
    return blocks


# --- Java ---

_JAVA_IFACE_RE = re.compile(
    r"^\s*(?:public\s+)?interface\s+(\w+)(?:<[^>]+>)?(?:\s+extends\s+[\w,\s<>]+)?\s*\{",
    re.MULTILINE,
)
_JAVA_METHOD_SIG_RE = re.compile(
    r"^\s*(?:public\s+|default\s+|static\s+)*(?:[\w<>\[\],\s]+)\s+(\w+)\s*\([^)]*\)\s*;",
    re.MULTILINE,
)
_JAVA_CLASS_RE = re.compile(
    r"^\s*(?:public\s+|abstract\s+)*class\s+(\w+)(?:<[^>]+>)?\s+(?:extends\s+\w+\s+)?implements\s+([\w,\s]+)\s*\{",
    re.MULTILINE,
)
_JAVA_EMPTY_METHOD_RE = re.compile(
    r"(?:public|protected|private)?\s*(?:[\w<>\[\]]+\s+)?(\w+)\s*\([^)]*\)\s*\{[\s]*(?:return(?:\s+(?:null|0|false|"")?)?;?|throw\s+new\s+(?:Unsupported|NotImplemented)\w*\([^)]*\);?)?\s*\}",
    re.MULTILINE,
)


def analyze_java(source: str, filepath: str) -> Tuple[List[InterfaceInfo], List[ClassInfo]]:
    interfaces: List[InterfaceInfo] = []
    classes: List[ClassInfo] = []
    lines = source.splitlines()

    for m in _JAVA_IFACE_RE.finditer(source):
        name = m.group(1)
        start_line = source[:m.start()].count("\n") + 1
        # find the matching block
        depth = 0
        end_line = start_line
        for idx in range(start_line - 1, len(lines)):
            depth += lines[idx].count("{") - lines[idx].count("}")
            if depth <= 0:
                end_line = idx + 1
                break
        block = "\n".join(lines[start_line - 1:end_line])
        methods = []
        for sm in _JAVA_METHOD_SIG_RE.finditer(block):
            mname = sm.group(1)
            mline = start_line + block[:sm.start()].count("\n")
            methods.append(MethodInfo(name=mname, line=mline, is_abstract=True))
        interfaces.append(InterfaceInfo(
            name=name, file=filepath, language="java",
            line_start=start_line, line_end=end_line,
            abstract_methods=methods,
        ))

    for m in _JAVA_CLASS_RE.finditer(source):
        cname = m.group(1)
        impl_names = [s.strip() for s in m.group(2).split(",")]
        start_line = source[:m.start()].count("\n") + 1
        depth = 0
        end_line = start_line
        for idx in range(start_line - 1, len(lines)):
            depth += lines[idx].count("{") - lines[idx].count("}")
            if depth <= 0:
                end_line = idx + 1
                break
        block = "\n".join(lines[start_line - 1:end_line])
        methods = []
        for em in _JAVA_EMPTY_METHOD_RE.finditer(block):
            mname = em.group(1)
            mline = start_line + block[:em.start()].count("\n")
            body = em.group(0)
            is_noop = bool(re.search(r"\{\s*\}|\{\s*return\s*(null|0|false)?\s*;?\s*\}|\{\s*throw\s+new\s+(?:Unsupported|NotImplemented)", body))
            methods.append(MethodInfo(name=mname, line=mline, is_noop=is_noop))
        classes.append(ClassInfo(
            name=cname, file=filepath, language="java",
            line_start=start_line, line_end=end_line,
            bases=impl_names, methods=methods,
        ))

    return interfaces, classes


# --- TypeScript / JavaScript ---

_TS_IFACE_RE = re.compile(
    r"^\s*(?:export\s+)?interface\s+(\w+)(?:<[^>]+>)?(?:\s+extends\s+[\w,\s<>]+)?\s*\{",
    re.MULTILINE,
)
_TS_METHOD_SIG_RE = re.compile(
    r"^\s*(?:readonly\s+)?(\w+)\s*(?:<[^>]+>)?\s*\([^)]*\)\s*(?::\s*[^;{]+)?\s*;",
    re.MULTILINE,
)
_TS_CLASS_RE = re.compile(
    r"^\s*(?:export\s+)?(?:abstract\s+)?class\s+(\w+)(?:<[^>]+>)?\s+(?:extends\s+\w+\s+)?implements\s+([\w,\s<>]+)\s*\{",
    re.MULTILINE,
)
_TS_ABSTRACT_CLASS_RE = re.compile(
    r"^\s*(?:export\s+)?abstract\s+class\s+(\w+)(?:<[^>]+>)?(?:\s+(?:extends|implements)\s+[\w,\s<>]+)?\s*\{",
    re.MULTILINE,
)
_TS_ABSTRACT_METHOD_RE = re.compile(
    r"^\s*abstract\s+(\w+)\s*\([^)]*\)\s*(?::\s*[^;{]+)?\s*;",
    re.MULTILINE,
)


def _extract_block(source: str, lines: List[str], match_start: int) -> Tuple[int, int, str]:
    start_line = source[:match_start].count("\n") + 1
    depth = 0
    end_line = start_line
    for idx in range(start_line - 1, len(lines)):
        depth += lines[idx].count("{") - lines[idx].count("}")
        if depth <= 0:
            end_line = idx + 1
            break
    block = "\n".join(lines[start_line - 1:end_line])
    return start_line, end_line, block


def analyze_typescript(source: str, filepath: str) -> Tuple[List[InterfaceInfo], List[ClassInfo]]:
    interfaces: List[InterfaceInfo] = []
    classes: List[ClassInfo] = []
    lines = source.splitlines()

    # interfaces
    for m in _TS_IFACE_RE.finditer(source):
        name = m.group(1)
        start_line, end_line, block = _extract_block(source, lines, m.start())
        methods = []
        for sm in _TS_METHOD_SIG_RE.finditer(block):
            mname = sm.group(1)
            mline = start_line + block[:sm.start()].count("\n")
            methods.append(MethodInfo(name=mname, line=mline, is_abstract=True))
        interfaces.append(InterfaceInfo(
            name=name, file=filepath, language="typescript",
            line_start=start_line, line_end=end_line,
            abstract_methods=methods,
        ))

    # abstract classes
    for m in _TS_ABSTRACT_CLASS_RE.finditer(source):
        name = m.group(1)
        start_line, end_line, block = _extract_block(source, lines, m.start())
        methods = []
        for sm in _TS_ABSTRACT_METHOD_RE.finditer(block):
            mname = sm.group(1)
            mline = start_line + block[:sm.start()].count("\n")
            methods.append(MethodInfo(name=mname, line=mline, is_abstract=True))
        if methods:
            interfaces.append(InterfaceInfo(
                name=name, file=filepath, language="typescript",
                line_start=start_line, line_end=end_line,
                abstract_methods=methods,
            ))

    # implementing classes
    for m in _TS_CLASS_RE.finditer(source):
        cname = m.group(1)
        impl_names = [s.strip() for s in m.group(2).split(",")]
        start_line, end_line, block = _extract_block(source, lines, m.start())
        methods = _detect_noop_methods_brace(block, start_line)
        classes.append(ClassInfo(
            name=cname, file=filepath, language="typescript",
            line_start=start_line, line_end=end_line,
            bases=impl_names, methods=methods,
        ))

    return interfaces, classes


# --- C# ---

_CS_IFACE_RE = re.compile(
    r"^\s*(?:public\s+|internal\s+)?interface\s+(I\w+)(?:<[^>]+>)?(?:\s*:\s*[\w,\s<>]+)?\s*\{",
    re.MULTILINE,
)
_CS_METHOD_SIG_RE = re.compile(
    r"^\s*(?:[\w<>\[\]?]+\s+)(\w+)\s*\([^)]*\)\s*;",
    re.MULTILINE,
)
_CS_CLASS_RE = re.compile(
    r"^\s*(?:public\s+|internal\s+|private\s+)?(?:sealed\s+|abstract\s+)?class\s+(\w+)(?:<[^>]+>)?\s*:\s*([\w,\s<>]+)\s*\{",
    re.MULTILINE,
)


def analyze_csharp(source: str, filepath: str) -> Tuple[List[InterfaceInfo], List[ClassInfo]]:
    interfaces: List[InterfaceInfo] = []
    classes: List[ClassInfo] = []
    lines = source.splitlines()

    for m in _CS_IFACE_RE.finditer(source):
        name = m.group(1)
        start_line, end_line, block = _extract_block(source, lines, m.start())
        methods = []
        for sm in _CS_METHOD_SIG_RE.finditer(block):
            mname = sm.group(1)
            mline = start_line + block[:sm.start()].count("\n")
            methods.append(MethodInfo(name=mname, line=mline, is_abstract=True))
        interfaces.append(InterfaceInfo(
            name=name, file=filepath, language="csharp",
            line_start=start_line, line_end=end_line,
            abstract_methods=methods,
        ))

    for m in _CS_CLASS_RE.finditer(source):
        cname = m.group(1)
        bases = [s.strip() for s in m.group(2).split(",")]
        start_line, end_line, block = _extract_block(source, lines, m.start())
        methods = _detect_noop_methods_brace(block, start_line)
        classes.append(ClassInfo(
            name=cname, file=filepath, language="csharp",
            line_start=start_line, line_end=end_line,
            bases=bases, methods=methods,
        ))

    return interfaces, classes


# --- Kotlin ---

_KT_IFACE_RE = re.compile(
    r"^\s*(?:public\s+|internal\s+)?interface\s+(\w+)(?:<[^>]+>)?(?:\s*:\s*[\w,\s<>]+)?\s*\{",
    re.MULTILINE,
)
_KT_ABSTRACT_FUN_RE = re.compile(
    r"^\s*(?:abstract\s+)?fun\s+(\w+)\s*\([^)]*\)(?:\s*:\s*\S+)?\s*$",
    re.MULTILINE,
)
_KT_CLASS_RE = re.compile(
    r"^\s*(?:open\s+|data\s+)?class\s+(\w+)(?:<[^>]+>)?(?:\s*\([^)]*\))?\s*:\s*([\w,\s<>()]+)\s*\{",
    re.MULTILINE,
)


def analyze_kotlin(source: str, filepath: str) -> Tuple[List[InterfaceInfo], List[ClassInfo]]:
    interfaces: List[InterfaceInfo] = []
    classes: List[ClassInfo] = []
    lines = source.splitlines()

    for m in _KT_IFACE_RE.finditer(source):
        name = m.group(1)
        start_line, end_line, block = _extract_block(source, lines, m.start())
        methods = []
        for sm in _KT_ABSTRACT_FUN_RE.finditer(block):
            mname = sm.group(1)
            mline = start_line + block[:sm.start()].count("\n")
            methods.append(MethodInfo(name=mname, line=mline, is_abstract=True))
        interfaces.append(InterfaceInfo(
            name=name, file=filepath, language="kotlin",
            line_start=start_line, line_end=end_line,
            abstract_methods=methods,
        ))

    for m in _KT_CLASS_RE.finditer(source):
        cname = m.group(1)
        raw_bases = m.group(2)
        bases = [s.strip().split("(")[0].strip() for s in raw_bases.split(",")]
        start_line, end_line, block = _extract_block(source, lines, m.start())
        methods = _detect_noop_methods_brace(block, start_line)
        classes.append(ClassInfo(
            name=cname, file=filepath, language="kotlin",
            line_start=start_line, line_end=end_line,
            bases=bases, methods=methods,
        ))

    return interfaces, classes


# --- Go ---

_GO_IFACE_RE = re.compile(
    r"^\s*type\s+(\w+)\s+interface\s*\{",
    re.MULTILINE,
)
_GO_METHOD_SIG_RE = re.compile(
    r"^\s*(\w+)\s*\([^)]*\)(?:\s*(?:\([^)]*\)|[\w*]+))?",
    re.MULTILINE,
)


def analyze_go(source: str, filepath: str) -> Tuple[List[InterfaceInfo], List[ClassInfo]]:
    interfaces: List[InterfaceInfo] = []
    lines = source.splitlines()

    for m in _GO_IFACE_RE.finditer(source):
        name = m.group(1)
        start_line, end_line, block = _extract_block(source, lines, m.start())
        methods = []
        # skip the first line (type X interface {) and last line (})
        inner = "\n".join(block.splitlines()[1:-1])
        for sm in _GO_METHOD_SIG_RE.finditer(inner):
            mname = sm.group(1)
            if mname[0].isupper():  # exported methods only
                mline = start_line + 1 + inner[:sm.start()].count("\n")
                methods.append(MethodInfo(name=mname, line=mline, is_abstract=True))
        interfaces.append(InterfaceInfo(
            name=name, file=filepath, language="go",
            line_start=start_line, line_end=end_line,
            abstract_methods=methods,
        ))

    return interfaces, []


# --- Swift ---

_SWIFT_PROTOCOL_RE = re.compile(
    r"^\s*(?:public\s+|internal\s+)?protocol\s+(\w+)(?:\s*:\s*[\w,\s]+)?\s*\{",
    re.MULTILINE,
)
_SWIFT_FUNC_SIG_RE = re.compile(
    r"^\s*(?:mutating\s+)?func\s+(\w+)\s*\([^)]*\)(?:\s*->\s*\S+)?\s*$",
    re.MULTILINE,
)
_SWIFT_CLASS_RE = re.compile(
    r"^\s*(?:public\s+|internal\s+|final\s+)?class\s+(\w+)(?:<[^>]+>)?\s*:\s*([\w,\s<>]+)\s*\{",
    re.MULTILINE,
)


def analyze_swift(source: str, filepath: str) -> Tuple[List[InterfaceInfo], List[ClassInfo]]:
    interfaces: List[InterfaceInfo] = []
    classes: List[ClassInfo] = []
    lines = source.splitlines()

    for m in _SWIFT_PROTOCOL_RE.finditer(source):
        name = m.group(1)
        start_line, end_line, block = _extract_block(source, lines, m.start())
        methods = []
        for sm in _SWIFT_FUNC_SIG_RE.finditer(block):
            mname = sm.group(1)
            mline = start_line + block[:sm.start()].count("\n")
            methods.append(MethodInfo(name=mname, line=mline, is_abstract=True))
        interfaces.append(InterfaceInfo(
            name=name, file=filepath, language="swift",
            line_start=start_line, line_end=end_line,
            abstract_methods=methods,
        ))

    for m in _SWIFT_CLASS_RE.finditer(source):
        cname = m.group(1)
        bases = [s.strip() for s in m.group(2).split(",")]
        start_line, end_line, block = _extract_block(source, lines, m.start())
        methods = _detect_noop_methods_brace(block, start_line)
        classes.append(ClassInfo(
            name=cname, file=filepath, language="swift",
            line_start=start_line, line_end=end_line,
            bases=bases, methods=methods,
        ))

    return interfaces, classes


# --- C++ ---

_CPP_CLASS_RE = re.compile(
    r"^\s*class\s+(\w+)(?:\s*:\s*(?:public|protected|private)\s+([\w,\s:]+))?\s*\{",
    re.MULTILINE,
)
_CPP_PURE_VIRTUAL_RE = re.compile(
    r"^\s*virtual\s+(?:[\w:<>&*\s]+)\s+(\w+)\s*\([^)]*\)(?:\s*const)?\s*=\s*0\s*;",
    re.MULTILINE,
)


def analyze_cpp(source: str, filepath: str) -> Tuple[List[InterfaceInfo], List[ClassInfo]]:
    interfaces: List[InterfaceInfo] = []
    classes: List[ClassInfo] = []
    lines = source.splitlines()

    for m in _CPP_CLASS_RE.finditer(source):
        cname = m.group(1)
        bases_raw = m.group(2)
        bases = []
        if bases_raw:
            bases = [re.sub(r"(public|protected|private)\s+", "", s).strip() for s in bases_raw.split(",")]
        start_line, end_line, block = _extract_block(source, lines, m.start())
        pure_virtuals = []
        for sm in _CPP_PURE_VIRTUAL_RE.finditer(block):
            mname = sm.group(1)
            mline = start_line + block[:sm.start()].count("\n")
            pure_virtuals.append(MethodInfo(name=mname, line=mline, is_abstract=True))
        if pure_virtuals:
            interfaces.append(InterfaceInfo(
                name=cname, file=filepath, language="cpp",
                line_start=start_line, line_end=end_line,
                abstract_methods=pure_virtuals,
            ))
        if bases:
            methods = _detect_noop_methods_brace(block, start_line)
            classes.append(ClassInfo(
                name=cname, file=filepath, language="cpp",
                line_start=start_line, line_end=end_line,
                bases=bases, methods=methods,
            ))

    return interfaces, classes


# --- PHP ---

_PHP_IFACE_RE = re.compile(
    r"^\s*interface\s+(\w+)(?:\s+extends\s+[\w,\s\\]+)?\s*\{",
    re.MULTILINE,
)
_PHP_METHOD_SIG_RE = re.compile(
    r"^\s*public\s+function\s+(\w+)\s*\([^)]*\)(?:\s*:\s*\S+)?\s*;",
    re.MULTILINE,
)
_PHP_CLASS_RE = re.compile(
    r"^\s*class\s+(\w+)(?:\s+extends\s+\w+)?\s+implements\s+([\w,\s\\]+)\s*\{",
    re.MULTILINE,
)


def analyze_php(source: str, filepath: str) -> Tuple[List[InterfaceInfo], List[ClassInfo]]:
    interfaces: List[InterfaceInfo] = []
    classes: List[ClassInfo] = []
    lines = source.splitlines()

    for m in _PHP_IFACE_RE.finditer(source):
        name = m.group(1)
        start_line, end_line, block = _extract_block(source, lines, m.start())
        methods = []
        for sm in _PHP_METHOD_SIG_RE.finditer(block):
            mname = sm.group(1)
            mline = start_line + block[:sm.start()].count("\n")
            methods.append(MethodInfo(name=mname, line=mline, is_abstract=True))
        interfaces.append(InterfaceInfo(
            name=name, file=filepath, language="php",
            line_start=start_line, line_end=end_line,
            abstract_methods=methods,
        ))

    for m in _PHP_CLASS_RE.finditer(source):
        cname = m.group(1)
        bases = [s.strip() for s in m.group(2).split(",")]
        start_line, end_line, block = _extract_block(source, lines, m.start())
        methods = _detect_noop_methods_brace(block, start_line)
        classes.append(ClassInfo(
            name=cname, file=filepath, language="php",
            line_start=start_line, line_end=end_line,
            bases=bases, methods=methods,
        ))

    return interfaces, classes


# --- Ruby ---

_RB_MODULE_RE = re.compile(
    r"^\s*module\s+(\w+)",
    re.MULTILINE,
)
_RB_RAISE_METHOD_RE = re.compile(
    r"^\s*def\s+(\w+)(?:\([^)]*\))?\s*\n\s*raise\s+NotImplementedError",
    re.MULTILINE,
)
_RB_CLASS_RE = re.compile(
    r"^\s*class\s+(\w+)\s*<\s*(\w+)",
    re.MULTILINE,
)


def analyze_ruby(source: str, filepath: str) -> Tuple[List[InterfaceInfo], List[ClassInfo]]:
    interfaces: List[InterfaceInfo] = []
    classes: List[ClassInfo] = []
    lines = source.splitlines()

    # Modules with raise NotImplementedError act as interfaces
    for m in _RB_MODULE_RE.finditer(source):
        name = m.group(1)
        start_line = source[:m.start()].count("\n") + 1
        # scan until matching end
        depth = 1
        end_line = start_line
        for idx in range(start_line, len(lines)):
            ln = lines[idx].strip()
            if re.match(r"(class|module|def|do|if|unless|case|begin|while|until|for)\b", ln) and not ln.endswith("end"):
                depth += 1
            if ln == "end":
                depth -= 1
                if depth <= 0:
                    end_line = idx + 1
                    break
        block = "\n".join(lines[start_line - 1:end_line])
        methods = []
        for sm in _RB_RAISE_METHOD_RE.finditer(block):
            mname = sm.group(1)
            mline = start_line + block[:sm.start()].count("\n")
            methods.append(MethodInfo(name=mname, line=mline, is_abstract=True))
        if methods:
            interfaces.append(InterfaceInfo(
                name=name, file=filepath, language="ruby",
                line_start=start_line, line_end=end_line,
                abstract_methods=methods,
            ))

    # Classes
    for m in _RB_CLASS_RE.finditer(source):
        cname = m.group(1)
        base = m.group(2)
        start_line = source[:m.start()].count("\n") + 1
        depth = 1
        end_line = start_line
        for idx in range(start_line, len(lines)):
            ln = lines[idx].strip()
            if re.match(r"(class|module|def|do|if|unless|case|begin|while|until|for)\b", ln) and not ln.endswith("end"):
                depth += 1
            if ln == "end":
                depth -= 1
                if depth <= 0:
                    end_line = idx + 1
                    break
        block = "\n".join(lines[start_line - 1:end_line])
        methods = _detect_noop_methods_ruby(block, start_line)
        classes.append(ClassInfo(
            name=cname, file=filepath, language="ruby",
            line_start=start_line, line_end=end_line,
            bases=[base], methods=methods,
        ))

    return interfaces, classes


# --- Generic noop detection for brace-delimited languages ---

_BRACE_METHOD_RE = re.compile(
    r"(?:(?:public|private|protected|internal|override|virtual|static|final|open|mutating|async|func|fun|function)\s+)*"
    r"(?:[\w<>\[\]?*&:]+\s+)?(\w+)\s*\([^)]*\)(?:\s*(?::\s*[\w<>\[\]?]+|->?\s*[\w<>\[\]?]+))?\s*\{([^{}]*)\}",
    re.MULTILINE | re.DOTALL,
)

_NOOP_BODY_PATTERNS = [
    re.compile(r"^\s*$"),
    re.compile(r"^\s*return\s*;?\s*$"),
    re.compile(r"^\s*return\s+(?:null|nil|false|0|undefined|void|None)\s*;?\s*$"),
    re.compile(r"^\s*throw\s+new\s+(?:NotImplemented|Unsupported|UnsupportedOperation)\w*\([^)]*\)\s*;?\s*$"),
    re.compile(r"^\s*raise\s+NotImplementedError.*$"),
    re.compile(r"^\s*pass\s*$"),
    re.compile(r"^\s*fatalError\s*\(.*\)\s*$"),
    re.compile(r"^\s*panic\s*\(.*\)\s*$"),
    re.compile(r"^\s*todo!\s*\(.*\)\s*$"),
]


def _is_noop_body_text(body: str) -> bool:
    stripped = body.strip()
    if not stripped:
        return True
    for pat in _NOOP_BODY_PATTERNS:
        if pat.match(stripped):
            return True
    return False


def _detect_noop_methods_brace(block: str, base_line: int) -> List[MethodInfo]:
    methods: List[MethodInfo] = []
    for m in _BRACE_METHOD_RE.finditer(block):
        name = m.group(1)
        body = m.group(2)
        # skip common false positives
        if name in ("if", "else", "for", "while", "switch", "catch", "try", "return", "new", "get", "set"):
            continue
        line = base_line + block[:m.start()].count("\n")
        is_noop = _is_noop_body_text(body)
        methods.append(MethodInfo(name=name, line=line, is_noop=is_noop))
    return methods


def _detect_noop_methods_ruby(block: str, base_line: int) -> List[MethodInfo]:
    methods: List[MethodInfo] = []
    rb_def_re = re.compile(r"^\s*def\s+(\w+)(?:\([^)]*\))?", re.MULTILINE)
    for m in rb_def_re.finditer(block):
        name = m.group(1)
        line = base_line + block[:m.start()].count("\n")
        # look at next meaningful line(s) before "end"
        rest = block[m.end():]
        next_lines = []
        for ln in rest.splitlines():
            s = ln.strip()
            if s == "end":
                break
            if s:
                next_lines.append(s)
        is_noop = False
        if not next_lines:
            is_noop = True
        elif len(next_lines) == 1:
            l = next_lines[0]
            if l.startswith("raise NotImplementedError") or l == "nil" or l == "# TODO":
                is_noop = True
        methods.append(MethodInfo(name=name, line=line, is_noop=is_noop))
    return methods


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

ANALYZERS = {
    "python": analyze_python,
    "java": analyze_java,
    "typescript": analyze_typescript,
    "javascript": analyze_typescript,  # reuse TS analyzer
    "csharp": analyze_csharp,
    "kotlin": analyze_kotlin,
    "go": analyze_go,
    "swift": analyze_swift,
    "cpp": analyze_cpp,
    "php": analyze_php,
    "ruby": analyze_ruby,
}


def analyze_file(filepath: str) -> Tuple[List[InterfaceInfo], List[ClassInfo]]:
    lang = detect_language(filepath)
    if lang is None:
        return [], []
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            source = f.read()
    except OSError:
        return [], []
    analyzer = ANALYZERS.get(lang)
    if analyzer is None:
        return [], []
    return analyzer(source, filepath)


# ---------------------------------------------------------------------------
# Concern detection
# ---------------------------------------------------------------------------

def detect_concerns(
    interfaces: List[InterfaceInfo],
    classes: List[ClassInfo],
    max_methods: int,
) -> List[Concern]:
    concerns: List[Concern] = []

    # 1. Large interfaces
    for iface in interfaces:
        count = len(iface.abstract_methods)
        if count >= max_methods:
            names = iface.abstract_method_names
            concerns.append(Concern(
                kind="large_interface",
                file=iface.file,
                entity_name=iface.name,
                line_start=iface.line_start,
                line_end=iface.line_end,
                message=(
                    f"Interface has {count} abstract methods "
                    f"(threshold: {max_methods}) \u2014 may be too broad"
                ),
                suggestion=(
                    f"Split into smaller interfaces grouped by responsibility "
                    f"(e.g., separate concerns like "
                    f"{_suggest_groups(names)})"
                ),
                method_names=names,
                total_abstract=count,
            ))

    # 2. Classes with noop methods (potential ISP violation)
    # Build a set of interface names for cross-reference
    iface_names = {iface.name for iface in interfaces}

    for cls in classes:
        noops = cls.noop_methods
        if len(noops) < 2:
            continue
        # bonus: if any base is a known interface, stronger signal
        is_implementing = any(b in iface_names for b in cls.bases)
        noop_names = [m.name for m in noops]
        base_desc = ", ".join(cls.bases) if cls.bases else "its interface"
        concerns.append(Concern(
            kind="noop_methods",
            file=cls.file,
            entity_name=f"{cls.name}({', '.join(cls.bases)})" if cls.bases else cls.name,
            line_start=cls.line_start,
            line_end=cls.line_end,
            message=(
                f"{len(noops)} methods are no-ops or raise NotImplementedError: "
                f"{', '.join(noop_names)}"
            ),
            suggestion=(
                f"{cls.name} should not implement the full {base_desc} interface. "
                f"Extract a narrower interface with only the methods {cls.name} actually needs."
            ),
            method_names=noop_names,
            noop_count=len(noops),
        ))

    return concerns


def _suggest_groups(names: List[str]) -> str:
    """Produce a very rough grouping suggestion from method names."""
    if len(names) <= 3:
        return ", ".join(names)
    mid = len(names) // 2
    group_a = ", ".join(names[:mid])
    group_b = ", ".join(names[mid:])
    return f"[{group_a}] and [{group_b}]"


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------

def format_default(filepath: str, concerns: List[Concern], verbose: bool) -> str:
    if not concerns:
        if verbose:
            return f"=== ISP Analysis: {filepath} ===\n  No concerns found.\n"
        return ""

    parts = [f"=== ISP Analysis: {filepath} ===\n"]
    for c in concerns:
        parts.append(f"  {c.entity_name} (lines {c.line_start}-{c.line_end})")
        parts.append(f"    [WARNING] {c.message}")
        if c.method_names and verbose:
            parts.append(f"    Methods: {', '.join(c.method_names)}")
        parts.append(f"    [SUGGESTION] {c.suggestion}")
        parts.append("")

    return "\n".join(parts)


def format_json_output(all_concerns: Dict[str, List[Concern]]) -> str:
    data: Dict[str, list] = {}
    for filepath, concerns in all_concerns.items():
        data[filepath] = []
        for c in concerns:
            data[filepath].append({
                "kind": c.kind,
                "entity": c.entity_name,
                "line_start": c.line_start,
                "line_end": c.line_end,
                "message": c.message,
                "suggestion": c.suggestion,
                "methods": c.method_names,
                "noop_count": c.noop_count,
                "total_abstract": c.total_abstract,
            })
    return json.dumps(data, indent=2)


# ---------------------------------------------------------------------------
# Rewrite suggestions
# ---------------------------------------------------------------------------

def generate_rewrite(interfaces: List[InterfaceInfo], classes: List[ClassInfo], max_methods: int) -> str:
    """Produce suggested refactored interface splits and updated class declarations."""
    parts: List[str] = []

    for iface in interfaces:
        count = len(iface.abstract_methods)
        if count < max_methods:
            continue
        names = iface.abstract_method_names
        # heuristic split into groups of ~3
        chunk_size = max(2, min(3, count // 2))
        groups: List[List[str]] = []
        for i in range(0, len(names), chunk_size):
            groups.append(names[i:i + chunk_size])

        lang = iface.language
        parts.append(f"# Suggested split for {iface.name} ({lang})")
        parts.append(f"# Original: {count} abstract methods")
        parts.append("")

        if lang == "python":
            for idx, group in enumerate(groups):
                iname = f"{iface.name}_{_group_label(group)}"
                parts.append(f"class {iname}(ABC):")
                for mname in group:
                    parts.append(f"    @abstractmethod")
                    parts.append(f"    def {mname}(self): ...")
                parts.append("")
        elif lang in ("java", "kotlin", "php"):
            for idx, group in enumerate(groups):
                iname = f"{iface.name}_{_group_label(group)}"
                parts.append(f"interface {iname} {{")
                for mname in group:
                    parts.append(f"    void {mname}();")
                parts.append("}")
                parts.append("")
        elif lang in ("typescript", "javascript"):
            for idx, group in enumerate(groups):
                iname = f"{iface.name}_{_group_label(group)}"
                parts.append(f"interface {iname} {{")
                for mname in group:
                    parts.append(f"    {mname}(): void;")
                parts.append("}}")
                parts.append("")
        elif lang == "csharp":
            for idx, group in enumerate(groups):
                iname = f"I{iface.name}_{_group_label(group)}"
                parts.append(f"interface {iname} {{")
                for mname in group:
                    parts.append(f"    void {mname}();")
                parts.append("}}")
                parts.append("")
        elif lang == "go":
            for idx, group in enumerate(groups):
                iname = f"{iface.name}{_group_label(group).title()}"
                parts.append(f"type {iname} interface {{")
                for mname in group:
                    parts.append(f"    {mname}()")
                parts.append("}")
                parts.append("")
        elif lang == "swift":
            for idx, group in enumerate(groups):
                iname = f"{iface.name}{_group_label(group).title()}"
                parts.append(f"protocol {iname} {{")
                for mname in group:
                    parts.append(f"    func {mname}()")
                parts.append("}}")
                parts.append("")
        elif lang == "cpp":
            for idx, group in enumerate(groups):
                iname = f"I{_group_label(group).title()}"
                parts.append(f"class {iname} {{")
                parts.append("public:")
                for mname in group:
                    parts.append(f"    virtual void {mname}() = 0;")
                parts.append("};")
                parts.append("")
        elif lang == "ruby":
            for idx, group in enumerate(groups):
                iname = f"{iface.name}{_group_label(group).title()}"
                parts.append(f"module {iname}")
                for mname in group:
                    parts.append(f"  def {mname}")
                    parts.append(f"    raise NotImplementedError")
                    parts.append(f"  end")
                parts.append("end")
                parts.append("")
        else:
            # generic
            for idx, group in enumerate(groups):
                iname = f"{iface.name}_{_group_label(group)}"
                parts.append(f"// Interface {iname}:")
                for mname in group:
                    parts.append(f"//   {mname}()")
                parts.append("")

    # Suggest updated class declarations
    iface_map = {iface.name: iface for iface in interfaces if len(iface.abstract_methods) >= max_methods}
    for cls in classes:
        noop_names = {m.name for m in cls.noop_methods}
        if len(noop_names) < 2:
            continue
        for base in cls.bases:
            if base in iface_map:
                iface = iface_map[base]
                needed = [n for n in iface.abstract_method_names if n not in noop_names]
                parts.append(f"# {cls.name} should implement only interfaces containing: {', '.join(needed)}")
                parts.append(f"# Instead of implementing the full {base} interface")
                parts.append("")

    return "\n".join(parts) if parts else "# No rewrite suggestions — no large interfaces found.\n"


def _group_label(methods: List[str]) -> str:
    """Derive a short label from method names."""
    if len(methods) == 1:
        return methods[0].title().replace("_", "")
    # use first method as representative
    return methods[0].split("_")[0].title() if "_" in methods[0] else methods[0][:8].title()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Detect Interface Segregation Principle (ISP) violations in source code.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            examples:
              %(prog)s src/
              %(prog)s myfile.py --verbose
              %(prog)s project/ --json
              %(prog)s project/ --rewrite --max-methods 4
        """),
    )
    parser.add_argument(
        "path",
        help="File or directory to analyze (recursive)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show detailed output including method lists and clean files",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output results as JSON",
    )
    parser.add_argument(
        "--rewrite",
        action="store_true",
        help="Output suggested refactored interface splits",
    )
    parser.add_argument(
        "--max-methods",
        type=int,
        default=5,
        metavar="N",
        help="Threshold for abstract methods before flagging (default: 5)",
    )

    args = parser.parse_args()

    target = args.path
    if not os.path.exists(target):
        print(f"Error: path does not exist: {target}", file=sys.stderr)
        return 2

    files = collect_files(target)
    if not files:
        print(f"Error: no supported source files found in: {target}", file=sys.stderr)
        return 2

    all_concerns: Dict[str, List[Concern]] = {}
    all_interfaces: List[InterfaceInfo] = []
    all_classes: List[ClassInfo] = []
    found_any = False

    for filepath in files:
        interfaces, classes = analyze_file(filepath)
        all_interfaces.extend(interfaces)
        all_classes.extend(classes)
        concerns = detect_concerns(interfaces, classes, args.max_methods)
        all_concerns[filepath] = concerns
        if concerns:
            found_any = True

    # Output
    if args.json_output:
        print(format_json_output(all_concerns))
    elif args.rewrite:
        # also print default report
        for filepath in files:
            output = format_default(filepath, all_concerns[filepath], args.verbose)
            if output:
                print(output)
        print("=" * 60)
        print("SUGGESTED REFACTORING")
        print("=" * 60)
        print()
        print(generate_rewrite(all_interfaces, all_classes, args.max_methods))
    else:
        any_output = False
        for filepath in files:
            output = format_default(filepath, all_concerns[filepath], args.verbose)
            if output:
                print(output)
                any_output = True
        if not any_output:
            print("No ISP concerns found.")

    total = sum(len(c) for c in all_concerns.values())
    if not args.json_output:
        print(f"--- Scanned {len(files)} file(s), found {total} concern(s) ---")

    return 1 if found_any else 0


if __name__ == "__main__":
    sys.exit(main())
