#!/usr/bin/env python3
"""
check_architecture.py — Architecture Layer Violation Detector

Analyzes source code directories to detect architecture layer violations.
Works language-agnostically: uses Python's ast module for .py files and
regex-based heuristics for all other supported languages.

Supported languages:
    .py, .java, .ts, .js, .cs, .rb, .kt, .go, .swift, .cpp, .hpp, .php

Detections:
    1. Layer violations — a lower layer importing from a higher layer
       (e.g., domain importing from presentation or infrastructure)
    2. Mixed concerns — a single file importing from 3+ different
       inferred architecture layers
    3. Missing service layer — presentation layer files (controllers)
       directly importing from data layer (repositories) without going
       through a service layer
    4. Domain leakage — domain/models layer importing infrastructure
       concerns such as database drivers, HTTP libraries, or framework
       modules

Exit codes:
    0 — No architecture concerns found
    1 — One or more architecture concerns found
    2 — Input error (path not found, not a directory, no eligible files)

Usage:
    python check_architecture.py path/to/directory
    python check_architecture.py src/ --verbose
    python check_architecture.py src/ --json
    python check_architecture.py src/ --rewrite
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

# ---------------------------------------------------------------------------
# Layer definitions
# ---------------------------------------------------------------------------

# Maps directory names to their architecture layer.
LAYER_PRESENTATION_DIRS: set[str] = {
    "controllers", "views", "presentation", "handlers",
    "routes", "api", "ui", "pages", "screens",
}

LAYER_DOMAIN_DIRS: set[str] = {
    "services", "domain", "business", "usecases", "use_cases",
    "core", "application",
}

LAYER_DATA_DIRS: set[str] = {
    "repositories", "data", "infrastructure", "persistence",
    "db", "database", "storage", "adapters",
}

LAYER_MODELS_DIRS: set[str] = {
    "models", "entities", "schemas", "types", "dtos",
}

# Canonical layer names and their hierarchy (lower index = lower layer).
LAYER_PRESENTATION = "presentation"
LAYER_DOMAIN = "domain"
LAYER_DATA = "data"
LAYER_MODELS = "models"

# Allowed dependency directions.  Key may import from values.
# presentation -> domain, models
# domain       -> models
# data         -> domain, models
# models       -> (nothing)
ALLOWED_IMPORTS: dict[str, set[str]] = {
    LAYER_PRESENTATION: {LAYER_DOMAIN, LAYER_MODELS},
    LAYER_DOMAIN:       {LAYER_MODELS},
    LAYER_DATA:         {LAYER_DOMAIN, LAYER_MODELS},
    LAYER_MODELS:       set(),
}

# Infrastructure concern patterns — imports that should not appear in
# domain or models layers.
INFRA_CONCERN_MODULES: dict[str, str] = {
    # Database drivers and ORMs
    "psycopg2":     "database driver",
    "psycopg":      "database driver",
    "pymongo":      "database driver",
    "pymysql":      "database driver",
    "sqlite3":      "database driver",
    "sqlalchemy":   "ORM / database",
    "peewee":       "ORM / database",
    "tortoise":     "ORM / database",
    "mongoengine":  "ORM / database",
    "sequelize":    "ORM / database",
    "typeorm":      "ORM / database",
    "prisma":       "ORM / database",
    "hibernate":    "ORM / database",
    "knex":         "database query builder",
    "mongoose":     "ORM / database",
    "diesel":       "ORM / database",
    "gorm":         "ORM / database",
    "activerecord": "ORM / database",
    "ecto":         "ORM / database",
    "dapper":       "ORM / database",
    "efcore":       "ORM / database",
    "entity_framework": "ORM / database",
    # HTTP libraries
    "requests":     "HTTP library",
    "httpx":        "HTTP library",
    "aiohttp":      "HTTP library",
    "urllib":       "HTTP library",
    "axios":        "HTTP library",
    "fetch":        "HTTP library",
    "got":          "HTTP library",
    "superagent":   "HTTP library",
    "okhttp":       "HTTP library",
    "retrofit":     "HTTP library",
    "alamofire":    "HTTP library",
    "restsharp":    "HTTP library",
    "httpclient":   "HTTP library",
    # Web frameworks
    "flask":        "web framework",
    "django":       "web framework",
    "fastapi":      "web framework",
    "starlette":    "web framework",
    "sanic":        "web framework",
    "tornado":      "web framework",
    "express":      "web framework",
    "koa":          "web framework",
    "nestjs":       "web framework",
    "hapi":         "web framework",
    "spring":       "web framework",
    "springboot":   "web framework",
    "rails":        "web framework",
    "sinatra":      "web framework",
    "gin":          "web framework",
    "echo":         "web framework",
    "fiber":        "web framework",
    "actix":        "web framework",
    "rocket":       "web framework",
    "asp":          "web framework",
    "laravel":      "web framework",
    "symfony":      "web framework",
    "ktor":         "web framework",
    "vapor":        "web framework",
    "nextjs":       "web framework",
    "nuxt":         "web framework",
}

# ---------------------------------------------------------------------------
# Import extraction patterns for non-Python languages
# ---------------------------------------------------------------------------

_IMPORT_PATTERNS: dict[str, list[re.Pattern]] = {
    "Java": [
        re.compile(r"^\s*import\s+([\w.]+)\s*;", re.MULTILINE),
    ],
    "TypeScript": [
        re.compile(r"""^\s*import\s+.*?\s+from\s+['"]([^'"]+)['"]""", re.MULTILINE),
        re.compile(r"""^\s*import\s+['"]([^'"]+)['"]""", re.MULTILINE),
        re.compile(r"""require\(\s*['"]([^'"]+)['"]\s*\)""", re.MULTILINE),
    ],
    "JavaScript": [
        re.compile(r"""^\s*import\s+.*?\s+from\s+['"]([^'"]+)['"]""", re.MULTILINE),
        re.compile(r"""^\s*import\s+['"]([^'"]+)['"]""", re.MULTILINE),
        re.compile(r"""require\(\s*['"]([^'"]+)['"]\s*\)""", re.MULTILINE),
    ],
    "C#": [
        re.compile(r"^\s*using\s+([\w.]+)\s*;", re.MULTILINE),
    ],
    "Ruby": [
        re.compile(r"""^\s*require\s+['"]([^'"]+)['"]""", re.MULTILINE),
        re.compile(r"""^\s*require_relative\s+['"]([^'"]+)['"]""", re.MULTILINE),
    ],
    "Kotlin": [
        re.compile(r"^\s*import\s+([\w.]+)", re.MULTILINE),
    ],
    "Go": [
        re.compile(r"""^\s*"([^"]+)"$""", re.MULTILINE),
        re.compile(r"""^\s*\w+\s+"([^"]+)"$""", re.MULTILINE),
    ],
    "Swift": [
        re.compile(r"^\s*import\s+(\w+)", re.MULTILINE),
    ],
    "C++": [
        re.compile(r"""^\s*#include\s+[<"]([^>"]+)[>"]""", re.MULTILINE),
    ],
    "PHP": [
        re.compile(r"^\s*use\s+([\w\\]+)\s*;", re.MULTILINE),
        re.compile(r"""^\s*require(?:_once)?\s+['"]([^'"]+)['"]""", re.MULTILINE),
        re.compile(r"""^\s*include(?:_once)?\s+['"]([^'"]+)['"]""", re.MULTILINE),
    ],
}

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ImportInfo:
    """A single import statement extracted from a source file."""
    raw: str          # the full import path as written
    module: str       # the root module / first path segment
    line_number: int  # 1-based line number
    line_text: str    # the original source line (stripped)


@dataclass
class FileInfo:
    """Metadata about a single source file relative to the project."""
    filepath: str
    relative_path: str   # relative to the analysis root
    language: str
    layer: Optional[str]          # inferred layer or None
    layer_directory: Optional[str] # the directory name that triggered the layer
    imports: list[ImportInfo] = field(default_factory=list)


@dataclass
class ArchWarning:
    """A single architecture warning for a file."""
    kind: str  # "layer_violation" | "mixed_concerns" | "missing_service_layer" | "domain_leakage"
    message: str
    line_number: Optional[int] = None
    line_text: Optional[str] = None
    suggestion: Optional[str] = None
    details: dict = field(default_factory=dict)


@dataclass
class FileReport:
    """Full analysis report for one file."""
    file_info: FileInfo
    warnings: list[ArchWarning] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def has_concerns(self) -> bool:
        return len(self.warnings) > 0


@dataclass
class LayerMap:
    """Discovered layer-to-directory mappings for the project."""
    presentation: list[str] = field(default_factory=list)
    domain: list[str] = field(default_factory=list)
    data: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.presentation or self.domain or self.data or self.models)


# ---------------------------------------------------------------------------
# Layer inference
# ---------------------------------------------------------------------------

def _infer_layer_from_path(relative_path: str) -> Tuple[Optional[str], Optional[str]]:
    """Infer the architecture layer of a file from its directory path.

    Returns (layer_name, matching_directory_name) or (None, None).
    """
    parts = Path(relative_path).parts
    # Walk from the file towards the root, looking for a recognized directory.
    for part in parts:
        part_lower = part.lower()
        if part_lower in LAYER_PRESENTATION_DIRS:
            return LAYER_PRESENTATION, part
        if part_lower in LAYER_DOMAIN_DIRS:
            return LAYER_DOMAIN, part
        if part_lower in LAYER_DATA_DIRS:
            return LAYER_DATA, part
        if part_lower in LAYER_MODELS_DIRS:
            return LAYER_MODELS, part
    return None, None


def _infer_layer_from_import(import_path: str) -> Optional[str]:
    """Infer which layer an import target belongs to based on path segments."""
    # Normalize separators.
    normalized = import_path.replace("\\", "/").replace(".", "/").lower()
    segments = [s for s in normalized.split("/") if s]
    for seg in segments:
        if seg in LAYER_PRESENTATION_DIRS:
            return LAYER_PRESENTATION
        if seg in LAYER_DOMAIN_DIRS:
            return LAYER_DOMAIN
        if seg in LAYER_DATA_DIRS:
            return LAYER_DATA
        if seg in LAYER_MODELS_DIRS:
            return LAYER_MODELS
    return None


def _build_layer_map(file_infos: list[FileInfo]) -> LayerMap:
    """Build a project-wide layer map from all discovered files."""
    lm = LayerMap()
    seen: set[str] = set()
    for fi in file_infos:
        if fi.layer and fi.layer_directory and fi.layer_directory not in seen:
            seen.add(fi.layer_directory)
            if fi.layer == LAYER_PRESENTATION:
                lm.presentation.append(fi.layer_directory + "/")
            elif fi.layer == LAYER_DOMAIN:
                lm.domain.append(fi.layer_directory + "/")
            elif fi.layer == LAYER_DATA:
                lm.data.append(fi.layer_directory + "/")
            elif fi.layer == LAYER_MODELS:
                lm.models.append(fi.layer_directory + "/")
    # Sort for deterministic output.
    lm.presentation.sort()
    lm.domain.sort()
    lm.data.sort()
    lm.models.sort()
    return lm


# ---------------------------------------------------------------------------
# Import extraction
# ---------------------------------------------------------------------------

def _extract_python_imports(filepath: str, source: str) -> list[ImportInfo]:
    """Use the ast module to extract imports from a Python file."""
    try:
        tree = ast.parse(source, filename=filepath)
    except SyntaxError:
        return []

    source_lines = source.splitlines()
    imports: list[ImportInfo] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                line_no = node.lineno
                line_text = source_lines[line_no - 1].strip() if line_no <= len(source_lines) else ""
                imports.append(ImportInfo(
                    raw=alias.name,
                    module=alias.name.split(".")[0],
                    line_number=line_no,
                    line_text=line_text,
                ))
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                line_no = node.lineno
                line_text = source_lines[line_no - 1].strip() if line_no <= len(source_lines) else ""
                imports.append(ImportInfo(
                    raw=node.module,
                    module=node.module.split(".")[0],
                    line_number=line_no,
                    line_text=line_text,
                ))
    return imports


def _extract_regex_imports(source: str, language: str) -> list[ImportInfo]:
    """Use regex patterns to extract imports for non-Python languages."""
    patterns = _IMPORT_PATTERNS.get(language, [])
    source_lines = source.splitlines()
    imports: list[ImportInfo] = []
    seen_lines: set[int] = set()

    for pat in patterns:
        for m in pat.finditer(source):
            line_no = source[:m.start()].count("\n") + 1
            if line_no in seen_lines:
                continue
            seen_lines.add(line_no)
            raw = m.group(1)
            # Derive the root module from the import path.
            module = _root_module(raw, language)
            line_text = source_lines[line_no - 1].strip() if line_no <= len(source_lines) else ""
            imports.append(ImportInfo(
                raw=raw,
                module=module,
                line_number=line_no,
                line_text=line_text,
            ))
    return imports


def _root_module(raw_import: str, language: str) -> str:
    """Extract the root module or first meaningful path segment."""
    # Strip leading . or / for relative imports.
    cleaned = raw_import.lstrip("./\\@")
    # Split on common separators.
    for sep in (".", "/", "\\"):
        if sep in cleaned:
            parts = [p for p in cleaned.split(sep) if p]
            if parts:
                return parts[0].lower()
    return cleaned.lower()


# ---------------------------------------------------------------------------
# File discovery and information gathering
# ---------------------------------------------------------------------------

def discover_files(root_path: str) -> list[str]:
    """Recursively discover supported source files under a directory."""
    target = Path(root_path)
    if not target.is_dir():
        return []
    files: list[str] = []
    for root, dirs, filenames in os.walk(target):
        # Skip hidden directories and common non-source directories.
        dirs[:] = [
            d for d in dirs
            if not d.startswith(".")
            and d not in (
                "node_modules", "__pycache__", "venv", ".venv",
                "dist", "build", "vendor", "target", "bin", "obj",
            )
        ]
        for fname in sorted(filenames):
            if Path(fname).suffix.lower() in SUPPORTED_EXTENSIONS:
                files.append(os.path.join(root, fname))
    return files


def _gather_file_info(filepath: str, root_path: str) -> FileInfo:
    """Build a FileInfo for a single file."""
    ext = Path(filepath).suffix.lower()
    language = LANGUAGE_MAP.get(ext, "Unknown")
    relative = os.path.relpath(filepath, root_path)
    layer, layer_dir = _infer_layer_from_path(relative)
    return FileInfo(
        filepath=filepath,
        relative_path=relative,
        language=language,
        layer=layer,
        layer_directory=layer_dir,
    )


# ---------------------------------------------------------------------------
# Analysis — individual checks
# ---------------------------------------------------------------------------

def _check_layer_violation(fi: FileInfo, imp: ImportInfo) -> Optional[ArchWarning]:
    """Check if an import violates the layer dependency rule."""
    if fi.layer is None:
        return None
    target_layer = _infer_layer_from_import(imp.raw)
    if target_layer is None:
        return None
    if target_layer == fi.layer:
        return None  # same layer, no issue
    allowed = ALLOWED_IMPORTS.get(fi.layer, set())
    if target_layer not in allowed:
        # Violation found.
        suggestion = _layer_violation_suggestion(fi.layer, target_layer)
        return ArchWarning(
            kind="layer_violation",
            message=f"{fi.layer} layer imports from {target_layer} layer",
            line_number=imp.line_number,
            line_text=imp.line_text,
            suggestion=suggestion,
            details={"source_layer": fi.layer, "target_layer": target_layer},
        )
    return None


def _layer_violation_suggestion(source_layer: str, target_layer: str) -> str:
    """Generate a human-readable suggestion for a layer violation."""
    if source_layer == LAYER_DOMAIN and target_layer == LAYER_PRESENTATION:
        return (
            "Domain logic should not depend on the presentation layer. "
            "Invert the dependency: have presentation call into domain instead."
        )
    if source_layer == LAYER_DOMAIN and target_layer == LAYER_DATA:
        return (
            "Domain logic should not depend on the data/infrastructure layer directly. "
            "Define repository interfaces in the domain layer and implement them in infrastructure."
        )
    if source_layer == LAYER_MODELS and target_layer == LAYER_PRESENTATION:
        return (
            "Model/entity layer should not depend on presentation. "
            "Models should be plain data structures with no UI awareness."
        )
    if source_layer == LAYER_MODELS and target_layer == LAYER_DATA:
        return (
            "Model/entity layer should not depend on data/infrastructure. "
            "Keep models free of persistence logic; use a separate repository layer."
        )
    if source_layer == LAYER_MODELS and target_layer == LAYER_DOMAIN:
        return (
            "Model/entity layer should not depend on the domain/service layer. "
            "Models should be self-contained data definitions."
        )
    if source_layer == LAYER_DATA and target_layer == LAYER_PRESENTATION:
        return (
            "Data/infrastructure layer should not depend on presentation. "
            "Repositories should be unaware of controllers or views."
        )
    return (
        f"The {source_layer} layer should not import from the {target_layer} layer. "
        "Review the dependency direction and consider introducing an abstraction."
    )


def _check_missing_service_layer(fi: FileInfo, imports: list[ImportInfo]) -> Optional[ArchWarning]:
    """Check if a presentation-layer file imports directly from the data layer."""
    if fi.layer != LAYER_PRESENTATION:
        return None
    data_imports = []
    has_domain_import = False
    for imp in imports:
        target_layer = _infer_layer_from_import(imp.raw)
        if target_layer == LAYER_DATA:
            data_imports.append(imp)
        elif target_layer == LAYER_DOMAIN:
            has_domain_import = True
    if not data_imports:
        return None
    # Only flag if the file skips the service layer entirely or mixes both.
    first = data_imports[0]
    return ArchWarning(
        kind="missing_service_layer",
        message=(
            "presentation layer imports directly from data layer, "
            "bypassing the service/domain layer"
        ),
        line_number=first.line_number,
        line_text=first.line_text,
        suggestion=(
            "Controllers should depend on the service layer, not directly on repositories. "
            "Introduce a service class to mediate between the controller and the repository."
        ),
        details={
            "data_imports": [imp.raw for imp in data_imports],
            "has_domain_import": has_domain_import,
        },
    )


def _check_domain_leakage(fi: FileInfo, imports: list[ImportInfo]) -> list[ArchWarning]:
    """Check if domain/models layer imports infrastructure concerns."""
    if fi.layer not in (LAYER_DOMAIN, LAYER_MODELS):
        return []
    warnings: list[ArchWarning] = []
    for imp in imports:
        module_lower = imp.module.lower()
        # Check against the known infrastructure modules.
        concern_desc = INFRA_CONCERN_MODULES.get(module_lower)
        if concern_desc is None:
            # Also check the raw import path segments.
            raw_lower = imp.raw.lower()
            for infra_mod, desc in INFRA_CONCERN_MODULES.items():
                if infra_mod in raw_lower.split(".") or infra_mod in raw_lower.split("/"):
                    concern_desc = desc
                    break
        if concern_desc is not None:
            suggestion = _domain_leakage_suggestion(fi.layer, concern_desc)
            warnings.append(ArchWarning(
                kind="domain_leakage",
                message=f"{fi.layer} layer imports infrastructure concern ({concern_desc})",
                line_number=imp.line_number,
                line_text=imp.line_text,
                suggestion=suggestion,
                details={"module": imp.raw, "concern": concern_desc},
            ))
    return warnings


def _domain_leakage_suggestion(layer: str, concern: str) -> str:
    """Generate a suggestion for domain leakage."""
    if "database" in concern.lower() or "orm" in concern.lower():
        return (
            "Domain logic should not depend on database drivers. "
            "Use a repository interface instead."
        )
    if "http" in concern.lower():
        return (
            "Domain logic should not depend on HTTP libraries. "
            "Define a gateway or port interface and implement it in the infrastructure layer."
        )
    if "framework" in concern.lower():
        return (
            "Domain logic should not depend on web framework modules. "
            "Keep domain code framework-agnostic by using ports and adapters."
        )
    return (
        f"Domain logic should not depend on infrastructure concerns ({concern}). "
        "Use an abstraction (interface/port) in the domain layer and implement it in infrastructure."
    )


def _check_mixed_concerns(fi: FileInfo, imports: list[ImportInfo]) -> Optional[ArchWarning]:
    """Check if a file imports from 3+ distinct architecture layers."""
    layers_seen: dict[str, list[ImportInfo]] = defaultdict(list)
    for imp in imports:
        target_layer = _infer_layer_from_import(imp.raw)
        if target_layer is not None:
            layers_seen[target_layer].append(imp)
    if len(layers_seen) >= 3:
        layer_names = sorted(layers_seen.keys())
        return ArchWarning(
            kind="mixed_concerns",
            message=f"imports from {len(layers_seen)} layers ({', '.join(layer_names)})",
            suggestion=(
                "This file spans too many architectural boundaries. "
                "Consider splitting responsibilities so each file depends on "
                "at most two adjacent layers."
            ),
            details={
                "layers": {
                    layer: [imp.raw for imp in imps]
                    for layer, imps in layers_seen.items()
                },
            },
        )
    return None


# ---------------------------------------------------------------------------
# Analysis orchestrator
# ---------------------------------------------------------------------------

def analyze_file(fi: FileInfo) -> FileReport:
    """Analyze a single file for architecture violations."""
    report = FileReport(file_info=fi)

    try:
        with open(fi.filepath, "r", encoding="utf-8", errors="replace") as f:
            source = f.read()
    except OSError as exc:
        report.error = str(exc)
        return report

    # Extract imports.
    if fi.language == "Python":
        fi.imports = _extract_python_imports(fi.filepath, source)
    else:
        fi.imports = _extract_regex_imports(source, fi.language)

    if not fi.imports:
        return report

    # Check 1: Layer violations (per-import).
    for imp in fi.imports:
        warning = _check_layer_violation(fi, imp)
        if warning:
            report.warnings.append(warning)

    # Check 2: Missing service layer.
    warning = _check_missing_service_layer(fi, fi.imports)
    if warning:
        report.warnings.append(warning)

    # Check 3: Domain leakage.
    leakage_warnings = _check_domain_leakage(fi, fi.imports)
    report.warnings.extend(leakage_warnings)

    # Check 4: Mixed concerns.
    warning = _check_mixed_concerns(fi, fi.imports)
    if warning:
        report.warnings.append(warning)

    return report


# ---------------------------------------------------------------------------
# Output formatting — plain text
# ---------------------------------------------------------------------------

def _format_plain(
    root_path: str,
    layer_map: LayerMap,
    reports: list[FileReport],
    verbose: bool,
    rewrite: bool,
) -> str:
    """Format reports as human-readable plain text."""
    parts: list[str] = []
    root_display = os.path.basename(root_path.rstrip("/\\")) + "/"
    parts.append(f"=== Architecture Analysis: {root_display} ===")
    parts.append("")

    # Layer map.
    if not layer_map.is_empty:
        parts.append("Layer Map:")
        if layer_map.presentation:
            parts.append(f"  Presentation: {', '.join(layer_map.presentation)}")
        if layer_map.domain:
            parts.append(f"  Domain: {', '.join(layer_map.domain)}")
        if layer_map.data:
            parts.append(f"  Data: {', '.join(layer_map.data)}")
        if layer_map.models:
            parts.append(f"  Models: {', '.join(layer_map.models)}")
        parts.append("")

    # Filter to files with concerns (or show all in verbose mode).
    files_to_show = [r for r in reports if r.has_concerns or (verbose and r.file_info.layer)]
    if not files_to_show and not any(r.has_concerns for r in reports):
        parts.append("No architecture concerns detected.")
        parts.append("")
        return "\n".join(parts)

    for report in reports:
        if not report.has_concerns and not verbose:
            continue

        fi = report.file_info
        parts.append(f"File: {fi.relative_path}")

        if report.error:
            parts.append(f"  [ERROR] {report.error}")
            parts.append("")
            continue

        if not report.has_concerns:
            if verbose and fi.layer:
                parts.append(f"  [OK] No architecture concerns (layer: {fi.layer})")
            parts.append("")
            continue

        for w in report.warnings:
            parts.append(f"  [WARNING] {_warning_label(w.kind)}: {w.message}")
            if w.line_number and w.line_text:
                parts.append(f"    Line {w.line_number}: {w.line_text}")

            if verbose and w.kind == "mixed_concerns" and "layers" in w.details:
                for layer, imps in sorted(w.details["layers"].items()):
                    parts.append(f"    - {layer}: {', '.join(imps)}")

            if w.suggestion:
                parts.append(f"  [SUGGESTION] {w.suggestion}")

        if rewrite:
            rewrite_text = _generate_rewrite_text(report)
            if rewrite_text:
                parts.append("")
                parts.append(rewrite_text)

        parts.append("")

    return "\n".join(parts)


def _warning_label(kind: str) -> str:
    """Human-readable label for a warning kind."""
    labels = {
        "layer_violation": "Layer violation",
        "mixed_concerns": "Mixed concerns",
        "missing_service_layer": "Missing service layer",
        "domain_leakage": "Domain leakage",
    }
    return labels.get(kind, kind)


# ---------------------------------------------------------------------------
# Output formatting — JSON
# ---------------------------------------------------------------------------

def _format_json(
    root_path: str,
    layer_map: LayerMap,
    reports: list[FileReport],
    rewrite: bool,
) -> str:
    """Format reports as structured JSON."""
    output: dict = {
        "root": root_path,
        "layer_map": {
            "presentation": layer_map.presentation,
            "domain": layer_map.domain,
            "data": layer_map.data,
            "models": layer_map.models,
        },
        "files": [],
        "summary": {
            "total_files": len(reports),
            "files_with_concerns": sum(1 for r in reports if r.has_concerns),
            "total_warnings": sum(len(r.warnings) for r in reports),
            "warnings_by_kind": dict(_count_by_kind(reports)),
        },
    }

    for report in reports:
        fi = report.file_info
        file_obj: dict = {
            "filepath": fi.relative_path,
            "language": fi.language,
            "layer": fi.layer,
            "has_concerns": report.has_concerns,
            "warnings": [],
        }
        if report.error:
            file_obj["error"] = report.error

        for w in report.warnings:
            w_obj: dict = {
                "kind": w.kind,
                "message": w.message,
                "details": w.details,
            }
            if w.line_number is not None:
                w_obj["line_number"] = w.line_number
            if w.line_text is not None:
                w_obj["line_text"] = w.line_text
            if w.suggestion:
                w_obj["suggestion"] = w.suggestion
            file_obj["warnings"].append(w_obj)

        if rewrite and report.has_concerns:
            file_obj["rewrite_suggestions"] = _generate_rewrite_data(report)

        output["files"].append(file_obj)

    return json.dumps(output, indent=2, ensure_ascii=False)


def _count_by_kind(reports: list[FileReport]) -> list[Tuple[str, int]]:
    """Count warnings grouped by kind."""
    counts: dict[str, int] = defaultdict(int)
    for r in reports:
        for w in r.warnings:
            counts[w.kind] += 1
    return sorted(counts.items())


# ---------------------------------------------------------------------------
# Rewrite suggestions
# ---------------------------------------------------------------------------

def _generate_rewrite_text(report: FileReport) -> str:
    """Generate plain-text restructuring suggestions for a file."""
    if not report.has_concerns:
        return ""

    fi = report.file_info
    lines: list[str] = []
    lines.append(f"  --- Restructuring suggestions for {fi.relative_path} ---")

    for w in report.warnings:
        if w.kind == "layer_violation":
            target_layer = w.details.get("target_layer", "unknown")
            source_layer = w.details.get("source_layer", "unknown")
            lines.append(f"  * Remove direct import of {target_layer}-layer code.")
            if source_layer == LAYER_DOMAIN and target_layer == LAYER_DATA:
                lines.append(
                    "    Define an abstract repository interface in the domain layer:"
                )
                if fi.language == "Python":
                    lines.append("      from abc import ABC, abstractmethod")
                    lines.append("")
                    lines.append("      class UserRepositoryPort(ABC):")
                    lines.append("          @abstractmethod")
                    lines.append("          def find_by_id(self, user_id: str) -> User: ...")
                else:
                    lines.append("      interface UserRepositoryPort {")
                    lines.append("          findById(userId: string): User;")
                    lines.append("      }")
                lines.append(
                    "    Implement it in the data/infrastructure layer and inject via constructor."
                )
            elif source_layer == LAYER_DOMAIN and target_layer == LAYER_PRESENTATION:
                lines.append(
                    "    Invert the dependency: have the presentation layer call domain, "
                    "not the other way around."
                )

        elif w.kind == "missing_service_layer":
            data_imports = w.details.get("data_imports", [])
            lines.append("  * Introduce a service class to mediate between controller and repository.")
            lines.append("    Instead of:")
            for imp in data_imports[:3]:
                lines.append(f"      import {imp}  # direct repo import in controller")
            lines.append("    Use:")
            lines.append("      import <corresponding_service>  # controller -> service -> repository")

        elif w.kind == "domain_leakage":
            module = w.details.get("module", "")
            concern = w.details.get("concern", "")
            lines.append(f"  * Remove infrastructure import ({module}) from domain code.")
            lines.append(
                f"    Define a port/interface for the {concern} dependency in the domain layer "
                "and implement it in infrastructure."
            )

        elif w.kind == "mixed_concerns":
            layer_details = w.details.get("layers", {})
            lines.append(
                "  * Split this file so each part depends on at most two adjacent layers:"
            )
            for layer, imps in sorted(layer_details.items()):
                lines.append(f"    - {layer} imports: {', '.join(imps[:5])}")

    lines.append(f"  --- End of suggestions for {fi.relative_path} ---")
    return "\n".join(lines)


def _generate_rewrite_data(report: FileReport) -> list[dict]:
    """Generate structured rewrite suggestions for JSON output."""
    suggestions: list[dict] = []
    for w in report.warnings:
        entry: dict = {"kind": w.kind, "actions": []}
        if w.kind == "layer_violation":
            entry["actions"].append({
                "action": "replace_import",
                "current": w.line_text or "",
                "reason": w.message,
                "recommended": (
                    f"Import from the {report.file_info.layer} or "
                    f"{', '.join(ALLOWED_IMPORTS.get(report.file_info.layer, set()))} layer instead"
                ),
            })
        elif w.kind == "missing_service_layer":
            entry["actions"].append({
                "action": "introduce_service",
                "data_imports": w.details.get("data_imports", []),
                "reason": "Controllers should depend on services, not directly on repositories",
            })
        elif w.kind == "domain_leakage":
            entry["actions"].append({
                "action": "extract_port",
                "module": w.details.get("module", ""),
                "concern": w.details.get("concern", ""),
                "reason": "Domain should not depend on infrastructure concerns",
            })
        elif w.kind == "mixed_concerns":
            entry["actions"].append({
                "action": "split_file",
                "layers": w.details.get("layers", {}),
                "reason": "File spans too many architectural boundaries",
            })
        if entry["actions"]:
            suggestions.append(entry)
    return suggestions


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="check_architecture",
        description="Detect architecture layer violations in a source code directory.",
    )
    parser.add_argument(
        "path",
        help="Directory to analyze (must be a directory, not a single file)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show detailed output including per-layer import listings and clean files",
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
        help="Include suggestions for restructuring imports",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    target = args.path
    if not os.path.exists(target):
        print(f"Error: path does not exist: {target}", file=sys.stderr)
        return 2

    if not os.path.isdir(target):
        print(f"Error: path must be a directory: {target}", file=sys.stderr)
        return 2

    files = discover_files(target)
    if not files:
        print(f"Error: no supported source files found in: {target}", file=sys.stderr)
        return 2

    # Gather file information and infer layers.
    file_infos: list[FileInfo] = []
    for filepath in files:
        fi = _gather_file_info(filepath, target)
        file_infos.append(fi)

    # Build project layer map.
    layer_map = _build_layer_map(file_infos)

    # Analyze each file.
    reports: list[FileReport] = []
    for fi in file_infos:
        report = analyze_file(fi)
        reports.append(report)

    # Output.
    if args.json_output:
        print(_format_json(target, layer_map, reports, args.rewrite))
    else:
        print(_format_plain(target, layer_map, reports, args.verbose, args.rewrite))

    has_any_concern = any(r.has_concerns for r in reports)
    return 1 if has_any_concern else 0


if __name__ == "__main__":
    sys.exit(main())
