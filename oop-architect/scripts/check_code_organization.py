#!/usr/bin/env python3
"""
check_code_organization.py — Code Organization Issue Detector

Analyzes source code directories to detect code organization problems
including circular dependencies, package coupling, and oversized modules.
Works language-agnostically: uses Python's ast module for .py files and
regex-based heuristics for all other supported languages.

Supported languages:
    .py, .java, .ts, .js, .cs, .rb, .kt, .go, .swift, .cpp, .hpp, .php

Detections:
    1. Circular dependencies — file-level dependency cycles found via
       iterative DFS on the import graph (e.g., A -> B -> C -> A)
    2. Bidirectional package coupling — two packages (directories) that
       each contain files importing from the other
    3. Package instability — packages with both high afferent coupling
       (Ca >= 3) and high efferent coupling (Ce >= 3), with instability
       between 0.3 and 0.7, making them risky to change
    4. Oversized modules — files exceeding a line count or top-level
       definition count threshold
    5. Missing barrel exports (verbose only) — Python packages without
       __init__.py, or TypeScript/JavaScript directories without
       index.ts / index.js

Exit codes:
    0 — No organization concerns found
    1 — One or more organization concerns found
    2 — Input error (path not found, not a directory, no eligible files)

Usage:
    python check_code_organization.py path/to/directory
    python check_code_organization.py src/ --verbose
    python check_code_organization.py src/ --json
    python check_code_organization.py src/ --rewrite
    python check_code_organization.py src/ --max-lines 300 --max-definitions 8
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
from typing import Dict, List, Optional, Set, Tuple

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

# Regex patterns for import statements by language family.
IMPORT_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "Java": [
        re.compile(r"^\s*import\s+(?:static\s+)?(?P<module>[\w.]+)\s*;", re.MULTILINE),
    ],
    "TypeScript": [
        re.compile(r"""^\s*import\s+.*?\s+from\s+['"](?P<module>[^'"]+)['"]""", re.MULTILINE),
        re.compile(r"""^\s*(?:import|require)\s*\(\s*['"](?P<module>[^'"]+)['"]\s*\)""", re.MULTILINE),
    ],
    "JavaScript": [
        re.compile(r"""^\s*import\s+.*?\s+from\s+['"](?P<module>[^'"]+)['"]""", re.MULTILINE),
        re.compile(r"""(?:require)\s*\(\s*['"](?P<module>[^'"]+)['"]\s*\)""", re.MULTILINE),
    ],
    "C#": [
        re.compile(r"^\s*using\s+(?:static\s+)?(?P<module>[\w.]+)\s*;", re.MULTILINE),
    ],
    "Ruby": [
        re.compile(r"""^\s*require(?:_relative)?\s+['"](?P<module>[^'"]+)['"]""", re.MULTILINE),
    ],
    "Kotlin": [
        re.compile(r"^\s*import\s+(?P<module>[\w.]+)", re.MULTILINE),
    ],
    "Go": [
        re.compile(r'^\s*"(?P<module>[^"]+)"', re.MULTILINE),
        re.compile(r'^\s*import\s+"(?P<module>[^"]+)"', re.MULTILINE),
    ],
    "Swift": [
        re.compile(r"^\s*import\s+(?P<module>\w+)", re.MULTILINE),
    ],
    "C++": [
        re.compile(r"""^\s*#\s*include\s+["<](?P<module>[^">]+)[">]""", re.MULTILINE),
    ],
    "PHP": [
        re.compile(r"""^\s*(?:require|include)(?:_once)?\s+['"](?P<module>[^'"]+)['"]""", re.MULTILINE),
        re.compile(r"^\s*use\s+(?P<module>[\w\\]+)\s*;", re.MULTILINE),
    ],
}

# Regex patterns for counting top-level definitions by language.
DEFINITION_PATTERNS: dict[str, re.Pattern[str]] = {
    "Python": re.compile(r"^(?:class|def)\s+\w+", re.MULTILINE),
    "Java": re.compile(r"^\s*(?:public|private|protected|static|abstract|final|\s)*\s*(?:class|interface|enum|record)\s+\w+", re.MULTILINE),
    "TypeScript": re.compile(r"^(?:export\s+)?(?:class|interface|function|enum|type|const\s+\w+\s*=\s*(?:\(|async))\s*", re.MULTILINE),
    "JavaScript": re.compile(r"^(?:export\s+)?(?:class|function|const\s+\w+\s*=\s*(?:\(|async))\s*", re.MULTILINE),
    "C#": re.compile(r"^\s*(?:public|private|protected|internal|static|abstract|sealed|\s)*\s*(?:class|interface|enum|struct|record)\s+\w+", re.MULTILINE),
    "Ruby": re.compile(r"^(?:class|module|def)\s+\w+", re.MULTILINE),
    "Kotlin": re.compile(r"^(?:class|object|interface|fun|enum\s+class)\s+\w+", re.MULTILINE),
    "Go": re.compile(r"^(?:func|type)\s+\w+", re.MULTILINE),
    "Swift": re.compile(r"^(?:class|struct|protocol|enum|func)\s+\w+", re.MULTILINE),
    "C++": re.compile(r"^(?:class|struct|namespace)\s+\w+|^\w[\w\s*&:<>]*\s+\w+\s*\(", re.MULTILINE),
    "PHP": re.compile(r"^\s*(?:class|interface|trait|function)\s+\w+", re.MULTILINE),
}

# Barrel export file names by language.
BARREL_FILES: dict[str, list[str]] = {
    "Python": ["__init__.py"],
    "TypeScript": ["index.ts", "index.tsx"],
    "JavaScript": ["index.js", "index.jsx"],
}

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class FileInfo:
    """Metadata for a single source file."""
    path: Path
    rel_path: str
    language: str
    line_count: int = 0
    definition_count: int = 0
    imports: list[str] = field(default_factory=list)
    raw_import_strings: list[str] = field(default_factory=list)


@dataclass
class Concern:
    """A single detected organization concern."""
    category: str
    severity: str  # WARNING, INFO
    message: str
    suggestion: str = ""
    details: list[str] = field(default_factory=list)


@dataclass
class PackageMetrics:
    """Coupling metrics for a package (directory)."""
    name: str
    ca: int = 0  # afferent coupling (incoming)
    ce: int = 0  # efferent coupling (outgoing)
    instability: float = 0.0
    files: list[str] = field(default_factory=list)


@dataclass
class AnalysisResult:
    """Aggregated analysis results."""
    root: str
    files_scanned: int = 0
    concerns: list[Concern] = field(default_factory=list)
    dependency_graph: dict[str, list[str]] = field(default_factory=dict)
    cycles: list[list[str]] = field(default_factory=list)
    package_metrics: dict[str, PackageMetrics] = field(default_factory=dict)
    bidirectional_pairs: list[Tuple[str, str, list[str], list[str]]] = field(
        default_factory=list,
    )


# ---------------------------------------------------------------------------
# File discovery and language detection
# ---------------------------------------------------------------------------


def discover_files(root: Path) -> list[FileInfo]:
    """Walk *root* and return FileInfo for every supported source file."""
    files: list[FileInfo] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for fname in filenames:
            ext = Path(fname).suffix.lower()
            if ext not in SUPPORTED_EXTENSIONS:
                continue
            full = Path(dirpath) / fname
            rel = full.relative_to(root).as_posix()
            files.append(FileInfo(
                path=full,
                rel_path=rel,
                language=LANGUAGE_MAP[ext],
            ))
    return files


def _read_file(path: Path) -> str:
    """Read a file, returning empty string on failure."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# Import extraction
# ---------------------------------------------------------------------------


def _extract_python_imports(source: str) -> list[str]:
    """Use AST to extract import targets from Python source."""
    imports: list[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return imports
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
    return imports


def _extract_regex_imports(source: str, language: str) -> list[str]:
    """Use regex patterns to extract import targets for non-Python languages."""
    imports: list[str] = []
    patterns = IMPORT_PATTERNS.get(language, [])
    for pat in patterns:
        for match in pat.finditer(source):
            module = match.group("module")
            if module:
                imports.append(module)
    return imports


def extract_imports(source: str, language: str) -> list[str]:
    """Extract import targets from source code."""
    if language == "Python":
        return _extract_python_imports(source)
    return _extract_regex_imports(source, language)


# ---------------------------------------------------------------------------
# Definition counting
# ---------------------------------------------------------------------------


def _count_python_definitions(source: str) -> int:
    """Count top-level class and function definitions via AST."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return 0
    count = 0
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            count += 1
    return count


def _count_regex_definitions(source: str, language: str) -> int:
    """Count definitions via regex for non-Python languages."""
    pattern = DEFINITION_PATTERNS.get(language)
    if pattern is None:
        return 0
    return len(pattern.findall(source))


def count_definitions(source: str, language: str) -> int:
    """Count top-level definitions in source code."""
    if language == "Python":
        return _count_python_definitions(source)
    return _count_regex_definitions(source, language)


# ---------------------------------------------------------------------------
# Import resolution — map raw import strings to project file paths
# ---------------------------------------------------------------------------


def _build_module_index(files: list[FileInfo]) -> dict[str, str]:
    """Build a mapping from plausible module names to relative file paths.

    For each file we register several possible keys:
      - the dotted form of its path without extension (a.b.c)
      - the filename without extension (c)
      - for relative imports: ./b/c, ../b/c, etc.
    """
    index: dict[str, str] = {}
    for fi in files:
        stem = Path(fi.rel_path).with_suffix("").as_posix()
        # dotted form
        dotted = stem.replace("/", ".")
        index[dotted] = fi.rel_path
        # slash form
        index[stem] = fi.rel_path
        # filename only (last resort)
        base = Path(fi.rel_path).stem
        if base not in index:
            index[base] = fi.rel_path
        # relative forms with ./ prefix
        index["./" + stem] = fi.rel_path
    return index


def _resolve_import(raw: str, module_index: dict[str, str]) -> Optional[str]:
    """Resolve a raw import string to a project-relative file path."""
    # Direct lookup
    if raw in module_index:
        return module_index[raw]
    # Strip leading dots for relative imports
    stripped = raw.lstrip(".")
    if stripped in module_index:
        return module_index[stripped]
    # Try partial matches — the import may reference a parent package
    for key, val in module_index.items():
        if key.endswith("." + raw) or key.endswith("/" + raw):
            return val
    # Try matching import to any file whose dotted path starts with the import
    for key, val in module_index.items():
        if key.startswith(raw + ".") or key.startswith(raw + "/"):
            return val
    return None


# ---------------------------------------------------------------------------
# Dependency graph construction
# ---------------------------------------------------------------------------


def build_dependency_graph(
    files: list[FileInfo],
) -> dict[str, list[str]]:
    """Build a file-level dependency graph.

    Returns a dict mapping each file's relative path to a list of
    relative paths it depends on (only project-internal dependencies).
    """
    module_index = _build_module_index(files)
    graph: dict[str, list[str]] = {fi.rel_path: [] for fi in files}
    for fi in files:
        seen: set[str] = set()
        for raw in fi.imports:
            target = _resolve_import(raw, module_index)
            if target and target != fi.rel_path and target not in seen:
                graph[fi.rel_path].append(target)
                seen.add(target)
    return graph


# ---------------------------------------------------------------------------
# Circular dependency detection — iterative DFS
# ---------------------------------------------------------------------------


def detect_cycles(graph: dict[str, list[str]]) -> list[list[str]]:
    """Detect all elementary cycles in *graph* using iterative DFS.

    Returns a list of cycles, where each cycle is a list of nodes
    forming the loop (e.g., [A, B, C, A]).
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {node: WHITE for node in graph}
    parent: dict[str, Optional[str]] = {node: None for node in graph}
    cycles: list[list[str]] = []
    seen_cycle_keys: set[str] = set()

    for start in graph:
        if color[start] != WHITE:
            continue
        stack: list[Tuple[str, int]] = [(start, 0)]
        color[start] = GRAY
        while stack:
            node, idx = stack[-1]
            neighbors = graph.get(node, [])
            if idx < len(neighbors):
                stack[-1] = (node, idx + 1)
                neighbor = neighbors[idx]
                if color.get(neighbor) == GRAY:
                    # Found a cycle — reconstruct path.
                    cycle = [neighbor]
                    for sn, _ in reversed(stack):
                        cycle.append(sn)
                        if sn == neighbor:
                            break
                    cycle.reverse()
                    # Deduplicate: normalize by rotating to smallest element.
                    min_idx = cycle.index(min(cycle[:-1]))
                    normalized = cycle[min_idx:-1] + cycle[min_idx:]
                    key = " -> ".join(normalized)
                    if key not in seen_cycle_keys:
                        seen_cycle_keys.add(key)
                        cycles.append(cycle)
                elif color.get(neighbor, WHITE) == WHITE:
                    color[neighbor] = GRAY
                    parent[neighbor] = node
                    stack.append((neighbor, 0))
            else:
                color[node] = BLACK
                stack.pop()
    return cycles


# ---------------------------------------------------------------------------
# Package-level analysis
# ---------------------------------------------------------------------------


def _package_of(rel_path: str) -> str:
    """Return the top-level package (first directory component) of a path."""
    parts = Path(rel_path).parts
    if len(parts) <= 1:
        return "."
    return parts[0]


def compute_package_coupling(
    graph: dict[str, list[str]],
) -> Tuple[
    dict[str, PackageMetrics],
    list[Tuple[str, str, list[str], list[str]]],
]:
    """Compute per-package coupling metrics and detect bidirectional coupling.

    Returns:
        metrics: dict mapping package name to PackageMetrics
        bidirectional: list of (pkgA, pkgB, filesA->B, filesB->A) tuples
    """
    # Collect files per package.
    pkg_files: dict[str, list[str]] = defaultdict(list)
    for node in graph:
        pkg_files[_package_of(node)].append(node)

    # Track cross-package edges.
    # edges_out[pkg] = set of packages it imports from
    # edges_in[pkg]  = set of packages importing it
    edges_out: dict[str, set[str]] = defaultdict(set)
    edges_in: dict[str, set[str]] = defaultdict(set)
    # Detailed: which files create cross-package links
    cross_links: dict[Tuple[str, str], list[str]] = defaultdict(list)

    for src, deps in graph.items():
        src_pkg = _package_of(src)
        for dep in deps:
            dep_pkg = _package_of(dep)
            if dep_pkg != src_pkg:
                edges_out[src_pkg].add(dep_pkg)
                edges_in[dep_pkg].add(src_pkg)
                cross_links[(src_pkg, dep_pkg)].append(
                    f"{src} imports {dep}"
                )

    # Build metrics.
    metrics: dict[str, PackageMetrics] = {}
    all_pkgs = set(pkg_files.keys())
    for pkg in all_pkgs:
        ca = len(edges_in.get(pkg, set()))
        ce = len(edges_out.get(pkg, set()))
        instability = ce / (ca + ce) if (ca + ce) > 0 else 0.0
        metrics[pkg] = PackageMetrics(
            name=pkg,
            ca=ca,
            ce=ce,
            instability=round(instability, 2),
            files=pkg_files[pkg],
        )

    # Detect bidirectional coupling.
    bidirectional: list[Tuple[str, str, list[str], list[str]]] = []
    checked: set[Tuple[str, str]] = set()
    for pkg_a in all_pkgs:
        for pkg_b in edges_out.get(pkg_a, set()):
            pair = tuple(sorted([pkg_a, pkg_b]))
            if pair in checked:
                continue
            checked.add(pair)
            if pkg_a in edges_out.get(pkg_b, set()):
                links_ab = cross_links.get((pkg_a, pkg_b), [])
                links_ba = cross_links.get((pkg_b, pkg_a), [])
                bidirectional.append((pkg_a, pkg_b, links_ab, links_ba))

    return metrics, bidirectional


# ---------------------------------------------------------------------------
# Missing barrel exports
# ---------------------------------------------------------------------------


def detect_missing_barrels(
    root: Path,
    files: list[FileInfo],
) -> list[Concern]:
    """Detect directories that lack barrel export files."""
    concerns: list[Concern] = []
    # Determine which languages are present.
    languages: set[str] = {fi.language for fi in files}
    # Gather all directories containing source files.
    dirs_with_files: set[Path] = set()
    for fi in files:
        parent = fi.path.parent
        if parent != root:
            dirs_with_files.add(parent)

    for d in sorted(dirs_with_files):
        rel = d.relative_to(root).as_posix()
        missing_for: list[str] = []
        if "Python" in languages:
            barrel = d / "__init__.py"
            if not barrel.exists():
                missing_for.append("Python (__init__.py)")
        if "TypeScript" in languages:
            if not (d / "index.ts").exists() and not (d / "index.tsx").exists():
                missing_for.append("TypeScript (index.ts)")
        if "JavaScript" in languages:
            if not (d / "index.js").exists() and not (d / "index.jsx").exists():
                missing_for.append("JavaScript (index.js)")
        if missing_for:
            concerns.append(Concern(
                category="Missing Barrel Exports",
                severity="INFO",
                message=f"Directory '{rel}' has no barrel export file",
                details=[f"Missing: {', '.join(missing_for)}"],
                suggestion="Add a barrel export file to define the package's public API",
            ))
    return concerns


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------


def analyze(
    root: Path,
    *,
    max_lines: int = 500,
    max_definitions: int = 10,
    verbose: bool = False,
) -> AnalysisResult:
    """Run all organization checks on *root* and return results."""
    result = AnalysisResult(root=root.as_posix())
    files = discover_files(root)
    result.files_scanned = len(files)

    if not files:
        return result

    # --- Phase 1: Read files and extract metadata ---
    for fi in files:
        source = _read_file(fi.path)
        fi.line_count = source.count("\n") + (1 if source and not source.endswith("\n") else 0)
        fi.imports = extract_imports(source, fi.language)
        fi.raw_import_strings = list(fi.imports)
        fi.definition_count = count_definitions(source, fi.language)

    # --- Phase 2: Build dependency graph ---
    graph = build_dependency_graph(files)
    result.dependency_graph = graph

    # --- Phase 3: Detect circular dependencies ---
    cycles = detect_cycles(graph)
    result.cycles = cycles
    for cycle in cycles:
        cycle_str = " -> ".join(cycle)
        result.concerns.append(Concern(
            category="Circular Dependencies",
            severity="WARNING",
            message=f"Cycle detected: {cycle_str}",
            suggestion="Break the cycle by extracting shared interfaces into a common module",
        ))

    # --- Phase 4: Package coupling ---
    metrics, bidirectional = compute_package_coupling(graph)
    result.package_metrics = metrics
    result.bidirectional_pairs = bidirectional

    for pkg_a, pkg_b, links_ab, links_ba in bidirectional:
        details = []
        for link in links_ab[:5]:
            details.append(f"  {pkg_a} -> {pkg_b}: {link}")
        for link in links_ba[:5]:
            details.append(f"  {pkg_b} -> {pkg_a}: {link}")
        result.concerns.append(Concern(
            category="Bidirectional Package Coupling",
            severity="WARNING",
            message=f"Packages '{pkg_a}' and '{pkg_b}' have mutual dependencies",
            details=details,
            suggestion=(
                "Extract shared types into a common package, or use "
                "events/interfaces to decouple"
            ),
        ))

    # --- Phase 5: Package instability ---
    for pkg, m in sorted(metrics.items()):
        if pkg == ".":
            continue
        if m.ca >= 3 and m.ce >= 3 and 0.3 <= m.instability <= 0.7:
            result.concerns.append(Concern(
                category="Package Metrics",
                severity="WARNING",
                message=(
                    f"Package '{pkg}' is both heavily depended upon "
                    f"(Ca={m.ca}) and heavily dependent (Ce={m.ce}) "
                    f"— changes are risky"
                ),
                suggestion=(
                    f"Stabilize '{pkg}' by reducing its outgoing "
                    f"dependencies or extracting volatile parts"
                ),
            ))
        elif verbose and (m.ca > 0 or m.ce > 0):
            result.concerns.append(Concern(
                category="Package Metrics",
                severity="INFO",
                message=(
                    f"Package '{pkg}': Ca={m.ca}, Ce={m.ce}, "
                    f"Instability={m.instability}"
                ),
            ))

    # --- Phase 6: Oversized modules ---
    for fi in files:
        if fi.line_count > max_lines:
            result.concerns.append(Concern(
                category="Oversized Modules",
                severity="WARNING",
                message=(
                    f"{fi.rel_path}: {fi.line_count} lines "
                    f"(threshold: {max_lines})"
                ),
                suggestion="Split into smaller, focused modules",
            ))
        if fi.definition_count > max_definitions:
            result.concerns.append(Concern(
                category="Oversized Modules",
                severity="WARNING",
                message=(
                    f"{fi.rel_path}: {fi.definition_count} top-level "
                    f"definitions (threshold: {max_definitions})"
                ),
                suggestion="Split into smaller, focused modules",
            ))

    # --- Phase 7: Missing barrel exports (verbose only) ---
    if verbose:
        barrel_concerns = detect_missing_barrels(root, files)
        result.concerns.extend(barrel_concerns)

    return result


# ---------------------------------------------------------------------------
# Output formatting — human-readable
# ---------------------------------------------------------------------------


def _format_text(result: AnalysisResult, *, rewrite: bool = False) -> str:
    """Format the analysis result as human-readable text."""
    lines: list[str] = []
    root_display = Path(result.root).name or result.root
    lines.append(f"=== Code Organization Analysis: {root_display}/ ===")
    lines.append("")

    # Group concerns by category, preserving insertion order.
    by_category: dict[str, list[Concern]] = {}
    for c in result.concerns:
        by_category.setdefault(c.category, []).append(c)

    warning_count = 0
    if not by_category:
        lines.append("No organization concerns detected.")
        lines.append("")
    else:
        for category, concerns in by_category.items():
            lines.append(f"{category}:")
            suggestion_printed = False
            for c in concerns:
                tag = c.severity
                lines.append(f"  [{tag}] {c.message}")
                for detail in c.details:
                    lines.append(f"    {detail}")
                if c.severity == "WARNING":
                    warning_count += 1
                if c.suggestion and not suggestion_printed:
                    lines.append(f"  [SUGGESTION] {c.suggestion}")
                    suggestion_printed = True
            lines.append("")

    # Rewrite suggestions
    if rewrite and warning_count > 0:
        lines.append("=== Rewrite Suggestions ===")
        lines.append("")
        if result.cycles:
            lines.append("Circular Dependency Resolution:")
            for cycle in result.cycles:
                cycle_names = [Path(c).stem for c in cycle[:-1]]
                lines.append(
                    f"  - Extract shared logic from {', '.join(cycle_names)} "
                    f"into a new module (e.g., '{cycle_names[0]}_base' or "
                    f"'shared_{cycle_names[0]}')"
                )
                lines.append(
                    f"    Then have each module import from the shared module "
                    f"instead of from each other."
                )
            lines.append("")
        if result.bidirectional_pairs:
            lines.append("Bidirectional Coupling Resolution:")
            for pkg_a, pkg_b, _, _ in result.bidirectional_pairs:
                lines.append(
                    f"  - Create a 'common' or 'shared' package for types "
                    f"used by both '{pkg_a}' and '{pkg_b}'"
                )
                lines.append(
                    f"    Alternatively, introduce an interface/protocol layer "
                    f"so one package depends on abstractions, not the other "
                    f"package directly."
                )
            lines.append("")
        oversized = [
            c for c in result.concerns
            if c.category == "Oversized Modules" and c.severity == "WARNING"
        ]
        if oversized:
            lines.append("Module Splitting:")
            for c in oversized:
                fname = c.message.split(":")[0]
                lines.append(
                    f"  - Split '{fname}' by grouping related "
                    f"classes/functions into separate files"
                )
                lines.append(
                    f"    Use a barrel/index file to re-export the public API "
                    f"so callers are not affected."
                )
            lines.append("")
        unstable = [
            c for c in result.concerns
            if c.category == "Package Metrics" and c.severity == "WARNING"
        ]
        if unstable:
            lines.append("Dependency Restructuring:")
            for c in unstable:
                # Extract package name from message.
                parts = c.message.split("'")
                pkg_name = parts[1] if len(parts) >= 2 else "package"
                lines.append(
                    f"  - Review '{pkg_name}' for outgoing imports that "
                    f"could be replaced with dependency injection or "
                    f"event-based communication"
                )
                lines.append(
                    f"    Move volatile utilities out of '{pkg_name}' into "
                    f"leaf packages that are less depended upon."
                )
            lines.append("")

    concern_count = sum(
        1 for c in result.concerns if c.severity == "WARNING"
    )
    lines.append(
        f"--- Scanned {result.files_scanned} file(s), "
        f"found {concern_count} concern(s) ---"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Output formatting — JSON
# ---------------------------------------------------------------------------


def _format_json(result: AnalysisResult) -> str:
    """Format the analysis result as structured JSON."""
    data = {
        "root": result.root,
        "files_scanned": result.files_scanned,
        "concern_count": sum(
            1 for c in result.concerns if c.severity == "WARNING"
        ),
        "dependency_graph": result.dependency_graph,
        "cycles": result.cycles,
        "package_metrics": {
            name: {
                "ca": m.ca,
                "ce": m.ce,
                "instability": m.instability,
                "files": m.files,
            }
            for name, m in result.package_metrics.items()
        },
        "bidirectional_coupling": [
            {
                "package_a": a,
                "package_b": b,
                "links_a_to_b": lab,
                "links_b_to_a": lba,
            }
            for a, b, lab, lba in result.bidirectional_pairs
        ],
        "concerns": [
            {
                "category": c.category,
                "severity": c.severity,
                "message": c.message,
                "suggestion": c.suggestion,
                "details": c.details,
            }
            for c in result.concerns
        ],
    }
    return json.dumps(data, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser."""
    parser = argparse.ArgumentParser(
        description=(
            "Detect code organization issues: circular dependencies, "
            "package coupling, and oversized modules."
        ),
    )
    parser.add_argument(
        "path",
        type=str,
        help="Root directory to analyze",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Include informational messages and missing barrel export checks",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        dest="json_output",
        help="Output results as structured JSON",
    )
    parser.add_argument(
        "--rewrite",
        action="store_true",
        default=False,
        help="Include rewrite suggestions for resolving issues",
    )
    parser.add_argument(
        "--max-lines",
        type=int,
        default=500,
        metavar="N",
        help="Line count threshold for oversized modules (default: 500)",
    )
    parser.add_argument(
        "--max-definitions",
        type=int,
        default=10,
        metavar="N",
        help="Definition count threshold for oversized modules (default: 10)",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """Entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)
    root = Path(args.path)

    if not root.exists():
        print(f"Error: path '{root}' does not exist.", file=sys.stderr)
        return 2
    if not root.is_dir():
        print(f"Error: path '{root}' is not a directory.", file=sys.stderr)
        return 2

    result = analyze(
        root,
        max_lines=args.max_lines,
        max_definitions=args.max_definitions,
        verbose=args.verbose,
    )

    if result.files_scanned == 0:
        print(
            f"Error: no supported source files found in '{root}'.",
            file=sys.stderr,
        )
        return 2

    if args.json_output:
        print(_format_json(result))
    else:
        print(_format_text(result, rewrite=args.rewrite))

    concern_count = sum(
        1 for c in result.concerns if c.severity == "WARNING"
    )
    return 1 if concern_count > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
