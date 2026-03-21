#!/usr/bin/env python3
"""
check_design_patterns.py - Design Pattern Misuse and Opportunity Detector

Detects anti-patterns in how design patterns are used (or not used) in source
code, and suggests appropriate pattern applications.

Detected patterns:
  1. Singleton abuse — classes using _instance + getInstance, module-level
     mutable globals acting as singletons
  2. God Factory — factory methods with 5+ branches creating different types
  3. Strategy opportunity — repeated if/elif or switch blocks branching on a
     type/mode/kind parameter to select behavior (4+ branches)
  4. Observer opportunity — manual callback/listener management with
     add_listener/remove_listener/notify methods and callback lists
  5. Deep inheritance suggesting Decorator — inheritance depth >3 where child
     classes primarily wrap/extend a single method

Supports: .py, .java, .ts, .js, .cs, .rb, .kt, .go, .swift, .cpp, .hpp, .php

Usage:
  python check_design_patterns.py path/to/file_or_directory
  python check_design_patterns.py path/ --verbose
  python check_design_patterns.py path/ --json
  python check_design_patterns.py path/ --rewrite

Exit codes:
  0 - No design pattern concerns found
  1 - Concerns or suggestions found
  2 - Input error (bad path, no files found, etc.)
"""

import argparse
import ast
import json
import os
import re
import sys
import textwrap
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import List, Optional, Dict, Tuple, Set


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SUPPORTED_EXTENSIONS: Set[str] = {
    ".py", ".java", ".ts", ".js", ".cs", ".rb", ".kt", ".go", ".swift",
    ".cpp", ".hpp", ".php",
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

GOD_FACTORY_BRANCH_THRESHOLD = 5
STRATEGY_BRANCH_THRESHOLD = 4
DEEP_INHERITANCE_THRESHOLD = 3

DISCRIMINATOR_NAMES = re.compile(
    r"\b(type|kind|category|variant|mode|action|command|event_type|"
    r"item_type|node_type|msg_type|message_type|op|operation|status|role)\b",
    re.IGNORECASE,
)

FACTORY_NAME_PATTERN = re.compile(
    r"\b(factory|create|make|build|construct|produce|generate|new_)\w*",
    re.IGNORECASE,
)

OBSERVER_METHOD_NAMES = re.compile(
    r"\b(add_listener|remove_listener|on_event|subscribe|unsubscribe|"
    r"notify|add_observer|remove_observer|register_callback|"
    r"unregister_callback|add_handler|remove_handler|emit|dispatch|"
    r"addEventListener|removeEventListener|addObserver|removeObserver|"
    r"addListener|removeListener|on_notify)\b",
)

CALLBACK_LIST_NAMES = re.compile(
    r"\b(listeners|observers|callbacks|handlers|subscribers|_listeners|"
    r"_observers|_callbacks|_handlers|_subscribers|event_handlers|"
    r"_event_handlers)\b",
)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class Severity(str, Enum):
    WARNING = "WARNING"
    SUGGESTION = "SUGGESTION"
    INFO = "INFO"


@dataclass
class Finding:
    severity: Severity
    message: str
    line: int
    end_line: Optional[int] = None
    detail: str = ""
    suggestion: str = ""
    rewrite_hint: str = ""


@dataclass
class ScopeResult:
    """Results for a class or top-level function."""
    name: str
    kind: str  # "Class", "Function", "Module"
    start_line: int
    end_line: int
    findings: List[Finding] = field(default_factory=list)


@dataclass
class FileResult:
    path: str
    language: str
    scopes: List[ScopeResult] = field(default_factory=list)
    parse_error: Optional[str] = None

    @property
    def has_concerns(self) -> bool:
        return any(s.findings for s in self.scopes)


# ---------------------------------------------------------------------------
# Python AST-based analyser
# ---------------------------------------------------------------------------

class PythonAnalyser:
    """Analyses Python files using the ast module for all five pattern checks."""

    def __init__(self, source: str):
        self.source = source
        self.lines = source.splitlines()

    def analyse(self) -> List[ScopeResult]:
        try:
            tree = ast.parse(self.source)
        except SyntaxError:
            return []

        results: List[ScopeResult] = []

        # Module-level singleton globals check
        module_scope = self._check_module_level_globals(tree)
        if module_scope and module_scope.findings:
            results.append(module_scope)

        # Build inheritance map for depth analysis
        inheritance_map = self._build_inheritance_map(tree)

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                scope = self._analyse_class(node, inheritance_map)
                if scope.findings:
                    results.append(scope)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scope = self._analyse_function(node, top_level=True)
                if scope.findings:
                    results.append(scope)

        return results

    # -- module-level checks ------------------------------------------------

    def _check_module_level_globals(self, tree: ast.Module) -> Optional[ScopeResult]:
        """Detect module-level mutable globals acting as singletons."""
        scope = ScopeResult(
            name="<module>",
            kind="Module",
            start_line=1,
            end_line=len(self.lines),
        )

        mutable_globals: List[Tuple[str, int]] = []

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    name = self._node_name(target)
                    if not name or name.startswith("_"):
                        continue
                    # Check if assigned a mutable value (dict, list, set, class instance)
                    if isinstance(node.value, (ast.Dict, ast.List, ast.Set, ast.Call)):
                        mutable_globals.append((name, node.lineno))

        # Only flag if there are multiple mutable globals suggesting state management
        if len(mutable_globals) >= 3:
            names_str = ", ".join(f"{n} (line {ln})" for n, ln in mutable_globals[:5])
            scope.findings.append(Finding(
                severity=Severity.WARNING,
                message=(
                    f"Module has {len(mutable_globals)} mutable global variables "
                    f"acting as shared state: {names_str}"
                ),
                line=mutable_globals[0][1],
                end_line=mutable_globals[-1][1],
                detail="Mutable module-level globals create hidden singletons with shared mutable state",
                suggestion=(
                    "Consider encapsulating shared state in a class and using "
                    "dependency injection -- create one instance at the composition "
                    "root and pass it to consumers"
                ),
                rewrite_hint=self._rewrite_global_state(mutable_globals),
            ))

        return scope

    # -- class-level checks -------------------------------------------------

    def _analyse_class(
        self, cls_node: ast.ClassDef, inheritance_map: Dict[str, List[str]]
    ) -> ScopeResult:
        end_line = self._end_line(cls_node)
        scope = ScopeResult(
            name=cls_node.name,
            kind="Class",
            start_line=cls_node.lineno,
            end_line=end_line,
        )

        # 1. Singleton abuse detection
        self._check_singleton_abuse(cls_node, scope)

        # 4. Observer opportunity detection
        self._check_observer_opportunity(cls_node, scope)

        # 5. Deep inheritance detection
        self._check_deep_inheritance(cls_node, inheritance_map, scope)

        # Check methods for factory and strategy patterns
        for node in ast.iter_child_nodes(cls_node):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fn_scope = self._analyse_function(node, top_level=False)
                scope.findings.extend(fn_scope.findings)

        return scope

    def _check_singleton_abuse(
        self, cls_node: ast.ClassDef, scope: ScopeResult
    ) -> None:
        """Detect Singleton pattern: _instance class variable + getInstance classmethod."""
        has_instance_var = False
        instance_var_line = 0
        has_get_instance = False
        get_instance_line = 0
        has_mutable_state = False

        for node in ast.iter_child_nodes(cls_node):
            # Look for _instance class variable
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    name = self._node_name(target)
                    if name in ("_instance", "_Instance", "__instance", "instance"):
                        has_instance_var = True
                        instance_var_line = node.lineno

            # Look for getInstance / get_instance classmethod/staticmethod
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                lower_name = node.name.lower()
                if lower_name in (
                    "getinstance", "get_instance", "instance", "shared",
                    "shared_instance", "default",
                ):
                    for decorator in node.decorator_list:
                        dec_name = self._node_name(decorator)
                        if dec_name in ("classmethod", "staticmethod"):
                            has_get_instance = True
                            get_instance_line = node.lineno
                            break

                # Check for mutable state (instance attributes)
                if node.name == "__init__":
                    for stmt in ast.walk(node):
                        if isinstance(stmt, ast.Assign):
                            for target in stmt.targets:
                                if (
                                    isinstance(target, ast.Attribute)
                                    and isinstance(target.value, ast.Name)
                                    and target.value.id == "self"
                                    and isinstance(stmt.value, (ast.Dict, ast.List, ast.Set, ast.Call))
                                ):
                                    has_mutable_state = True

        if has_instance_var and has_get_instance:
            detail_parts = []
            if instance_var_line:
                detail_parts.append(f"_instance class variable at line {instance_var_line}")
            if get_instance_line:
                detail_parts.append(f"get_instance() at line {get_instance_line}")

            msg = "Singleton pattern detected"
            if has_mutable_state:
                msg += " with mutable state"

            scope.findings.append(Finding(
                severity=Severity.WARNING,
                message=msg,
                line=instance_var_line or cls_node.lineno,
                end_line=get_instance_line or self._end_line(cls_node),
                detail=", ".join(detail_parts),
                suggestion=(
                    "Consider dependency injection -- create one instance at the "
                    "composition root and pass it to consumers. Singletons hide "
                    "dependencies and make testing difficult"
                ),
                rewrite_hint=self._rewrite_singleton(cls_node),
            ))

    def _check_observer_opportunity(
        self, cls_node: ast.ClassDef, scope: ScopeResult
    ) -> None:
        """Detect manual callback/listener management suggesting Observer pattern."""
        observer_methods: List[Tuple[str, int]] = []
        has_callback_list = False
        callback_list_line = 0

        for node in ast.iter_child_nodes(cls_node):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if OBSERVER_METHOD_NAMES.search(node.name):
                    observer_methods.append((node.name, node.lineno))

                # Check __init__ for callback list attributes
                if node.name == "__init__":
                    for stmt in ast.walk(node):
                        if isinstance(stmt, ast.Assign):
                            for target in stmt.targets:
                                if isinstance(target, ast.Attribute):
                                    attr_name = target.attr
                                    if CALLBACK_LIST_NAMES.search(attr_name):
                                        has_callback_list = True
                                        callback_list_line = stmt.lineno

            # Also check class-level assignments for callback lists
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    name = self._node_name(target)
                    if name and CALLBACK_LIST_NAMES.search(name):
                        has_callback_list = True
                        callback_list_line = node.lineno

        # Need at least 2 observer-like methods and a callback list
        if len(observer_methods) >= 2 and has_callback_list:
            methods_str = ", ".join(f"{name}() at line {ln}" for name, ln in observer_methods)
            scope.findings.append(Finding(
                severity=Severity.SUGGESTION,
                message="Manual observer/listener management detected",
                line=callback_list_line or cls_node.lineno,
                end_line=self._end_line(cls_node),
                detail=(
                    f"Callback list at line {callback_list_line}, "
                    f"observer methods: {methods_str}"
                ),
                suggestion=(
                    "Consider using a formal Observer pattern implementation or "
                    "an event bus/emitter library to decouple publishers from "
                    "subscribers and reduce boilerplate"
                ),
                rewrite_hint=self._rewrite_observer(cls_node, observer_methods),
            ))
        elif len(observer_methods) >= 3:
            # Many observer methods even without detected list attribute
            methods_str = ", ".join(f"{name}() at line {ln}" for name, ln in observer_methods)
            scope.findings.append(Finding(
                severity=Severity.SUGGESTION,
                message=(
                    f"Class has {len(observer_methods)} observer/listener methods"
                ),
                line=observer_methods[0][1],
                end_line=observer_methods[-1][1],
                detail=f"Observer methods: {methods_str}",
                suggestion=(
                    "Consider extracting event management into a reusable "
                    "Observer/EventEmitter base class to reduce duplication"
                ),
            ))

    def _check_deep_inheritance(
        self,
        cls_node: ast.ClassDef,
        inheritance_map: Dict[str, List[str]],
        scope: ScopeResult,
    ) -> None:
        """Detect deep inheritance chains (depth >3) suggesting Decorator pattern."""
        depth = self._compute_inheritance_depth(cls_node.name, inheritance_map)
        if depth <= DEEP_INHERITANCE_THRESHOLD:
            return

        # Check if child classes primarily override/wrap a single method
        method_names = [
            n.name for n in ast.iter_child_nodes(cls_node)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name != "__init__"
        ]
        wraps_single = len(method_names) == 1

        bases_str = ", ".join(
            self._node_name(b) or "<expr>" for b in cls_node.bases
        )

        msg = f"Deep inheritance chain (depth {depth})"
        if wraps_single and method_names:
            msg += f", primarily overrides single method: {method_names[0]}()"

        scope.findings.append(Finding(
            severity=Severity.WARNING if wraps_single else Severity.SUGGESTION,
            message=msg,
            line=cls_node.lineno,
            end_line=self._end_line(cls_node),
            detail=f"Inherits from: {bases_str}",
            suggestion=(
                "Consider using the Decorator pattern instead of deep "
                "inheritance -- wrap behavior around a base component via "
                "composition rather than extending a long class hierarchy"
            ),
            rewrite_hint=self._rewrite_decorator(cls_node, method_names),
        ))

    def _build_inheritance_map(self, tree: ast.Module) -> Dict[str, List[str]]:
        """Build a map of class_name -> [parent_class_names] from the AST."""
        result: Dict[str, List[str]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                parents = []
                for base in node.bases:
                    name = self._node_name(base)
                    if name:
                        parents.append(name)
                result[node.name] = parents
        return result

    def _compute_inheritance_depth(
        self, class_name: str, inheritance_map: Dict[str, List[str]],
        visited: Optional[Set[str]] = None,
    ) -> int:
        """Compute the maximum inheritance depth for a class within the file."""
        if visited is None:
            visited = set()
        if class_name in visited:
            return 0  # Circular reference guard
        visited.add(class_name)

        parents = inheritance_map.get(class_name, [])
        if not parents:
            return 1

        max_depth = 0
        for parent in parents:
            if parent in inheritance_map:
                depth = self._compute_inheritance_depth(
                    parent, inheritance_map, visited
                )
                max_depth = max(max_depth, depth)
            else:
                # External parent counts as depth 1
                max_depth = max(max_depth, 1)

        return max_depth + 1

    # -- function-level checks ----------------------------------------------

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

        # 2. God Factory detection
        self._check_god_factory(fn_node, scope)

        # 3. Strategy opportunity detection
        self._check_strategy_opportunity(fn_node, scope)

        return scope

    def _check_god_factory(
        self, fn_node: ast.FunctionDef, scope: ScopeResult
    ) -> None:
        """Detect factory methods with 5+ branches creating different types."""
        name_lower = fn_node.name.lower()
        if not FACTORY_NAME_PATTERN.search(name_lower):
            return

        # Count if/elif branches
        branch_count = 0
        branch_types: List[str] = []
        first_if_line = 0

        for node in ast.walk(fn_node):
            if isinstance(node, ast.If):
                chain = self._collect_if_chain(node)
                chain_len = len(chain)
                if chain_len >= GOD_FACTORY_BRANCH_THRESHOLD:
                    if not first_if_line:
                        first_if_line = node.lineno
                    branch_count = max(branch_count, chain_len)
                    for cnode in chain:
                        label = self._extract_comparison_label(cnode.test)
                        if label:
                            branch_types.append(label)

            # Also check match/case (Python 3.10+)
            if isinstance(node, ast.Match):
                cases = node.cases
                if len(cases) >= GOD_FACTORY_BRANCH_THRESHOLD:
                    if not first_if_line:
                        first_if_line = node.lineno
                    branch_count = max(branch_count, len(cases))
                    for case in cases:
                        label = self._pattern_label(case.pattern)
                        if label:
                            branch_types.append(label)

        if branch_count >= GOD_FACTORY_BRANCH_THRESHOLD:
            types_str = ""
            if branch_types:
                types_str = f" creating: {', '.join(branch_types[:8])}"
                if len(branch_types) > 8:
                    types_str += f" (and {len(branch_types) - 8} more)"

            scope.findings.append(Finding(
                severity=Severity.WARNING,
                message=(
                    f"God Factory: {branch_count}-branch conditional creating "
                    f"different handler types"
                ),
                line=first_if_line or fn_node.lineno,
                end_line=self._end_line(fn_node),
                detail=types_str.strip(),
                suggestion=(
                    "Use Abstract Factory or a registry pattern to make "
                    "handler creation extensible without modifying the factory"
                ),
                rewrite_hint=self._rewrite_factory(fn_node, branch_types),
            ))

    def _check_strategy_opportunity(
        self, fn_node: ast.FunctionDef, scope: ScopeResult
    ) -> None:
        """Detect repeated branching on type/mode/kind parameter suggesting Strategy."""
        # Collect all if/elif chains
        for node in ast.walk(fn_node):
            if not isinstance(node, ast.If):
                continue

            chain = self._collect_if_chain(node)
            if len(chain) < STRATEGY_BRANCH_THRESHOLD:
                continue

            # Check if branching on a discriminator parameter
            discriminator = None
            branch_labels: List[str] = []

            for cnode in chain:
                test_dump = ast.dump(cnode.test).lower()
                for param in self._get_params(fn_node):
                    if DISCRIMINATOR_NAMES.search(param) and param.lower() in test_dump:
                        discriminator = param
                        break
                label = self._extract_comparison_label(cnode.test)
                if label:
                    branch_labels.append(label)

            if not discriminator:
                # Also check for self.attribute discriminators
                for cnode in chain:
                    for attr_node in ast.walk(cnode.test):
                        if (
                            isinstance(attr_node, ast.Attribute)
                            and DISCRIMINATOR_NAMES.search(attr_node.attr)
                        ):
                            discriminator = f"self.{attr_node.attr}"
                            break
                    if discriminator:
                        break

            if discriminator:
                scope.findings.append(Finding(
                    severity=Severity.SUGGESTION,
                    message=(
                        f"Strategy opportunity: {len(chain)}-branch conditional "
                        f"on discriminator '{discriminator}'"
                    ),
                    line=node.lineno,
                    end_line=self._end_line(chain[-1]),
                    detail=(
                        f"Branches: {', '.join(branch_labels[:6])}"
                        if branch_labels else ""
                    ),
                    suggestion=(
                        f"Extract each branch into a Strategy class implementing "
                        f"a common interface, then select strategy based on "
                        f"'{discriminator}' via a registry or map"
                    ),
                    rewrite_hint=self._rewrite_strategy(
                        fn_node, discriminator, branch_labels
                    ),
                ))

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _get_params(fn_node: ast.FunctionDef) -> List[str]:
        """Get parameter names from a function, excluding 'self' and 'cls'."""
        return [
            a.arg for a in fn_node.args.args
            if a.arg not in ("self", "cls")
        ]

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
    def _pattern_label(pattern: ast.pattern) -> Optional[str]:
        if isinstance(pattern, ast.MatchValue):
            if isinstance(pattern.value, ast.Constant):
                return str(pattern.value.value)
        if isinstance(pattern, ast.MatchClass):
            if isinstance(pattern.cls, ast.Name):
                return pattern.cls.id
        return None

    @staticmethod
    def _node_name(node: ast.expr) -> Optional[str]:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return None

    @staticmethod
    def _end_line(node: ast.AST) -> int:
        return getattr(node, "end_lineno", getattr(node, "lineno", 0))

    # -- rewrite generators -------------------------------------------------

    @staticmethod
    def _rewrite_singleton(cls_node: ast.ClassDef) -> str:
        name = cls_node.name
        lines = [
            f"# Before: {name} was a Singleton accessed via {name}.get_instance()",
            f"# After: create once and inject via constructor",
            f"",
            f"class {name}:",
            f"    def __init__(self):",
            f"        ...  # normal initialization, no _instance needed",
            f"",
            f"# At composition root (e.g. main.py):",
            f"{name.lower()} = {name}()",
            f"service = SomeService({name.lower()})  # inject the dependency",
        ]
        return "\n".join(lines)

    @staticmethod
    def _rewrite_global_state(
        globals_list: List[Tuple[str, int]],
    ) -> str:
        names = [n for n, _ in globals_list[:5]]
        lines = [
            f"# Before: module-level mutable globals ({', '.join(names)})",
            f"# After: encapsulate in a configuration/state class",
            f"",
            f"from dataclasses import dataclass, field",
            f"",
            f"@dataclass",
            f"class AppState:",
        ]
        for name in names:
            lines.append(f"    {name}: dict = field(default_factory=dict)")
        lines.extend([
            f"",
            f"# Create at composition root and pass to consumers:",
            f"# state = AppState()",
            f"# service = MyService(state)",
        ])
        return "\n".join(lines)

    @staticmethod
    def _rewrite_factory(
        fn_node: ast.FunctionDef, branch_types: List[str],
    ) -> str:
        if not branch_types:
            return ""
        base_name = fn_node.name.replace("create_", "").replace("make_", "")
        base_name = base_name.replace("build_", "").replace("factory", "")
        base_name = base_name.replace("_", " ").title().replace(" ", "") or "Product"
        lines = [
            f"from abc import ABC, abstractmethod",
            f"from typing import Dict, Type",
            f"",
            f"class {base_name}(ABC):",
            f"    @abstractmethod",
            f"    def execute(self):",
            f"        ...",
            f"",
        ]
        for b in branch_types[:6]:
            cls_name = (
                b.replace(" ", "").replace("-", "")
                .replace("_", " ").title().replace(" ", "")
            )
            if not cls_name.isidentifier():
                cls_name = f"Type{b[:16]}"
            lines.append(f"class {cls_name}{base_name}({base_name}):")
            lines.append(f"    def execute(self):")
            lines.append(f"        ...  # logic for '{b}'")
            lines.append(f"")
        if len(branch_types) > 6:
            lines.append(f"# ... and {len(branch_types) - 6} more product classes")
            lines.append(f"")
        lines.extend([
            f"# Registry-based factory:",
            f"_registry: Dict[str, Type[{base_name}]] = {{",
        ])
        for b in branch_types[:6]:
            cls_name = (
                b.replace(" ", "").replace("-", "")
                .replace("_", " ").title().replace(" ", "")
            )
            if not cls_name.isidentifier():
                cls_name = f"Type{b[:16]}"
            lines.append(f'    "{b}": {cls_name}{base_name},')
        lines.extend([
            f"}}",
            f"",
            f"def {fn_node.name}(kind: str) -> {base_name}:",
            f"    cls = _registry.get(kind)",
            f"    if cls is None:",
            f'        raise ValueError(f"Unknown type: {{kind}}")',
            f"    return cls()",
        ])
        return "\n".join(lines)

    @staticmethod
    def _rewrite_strategy(
        fn_node: ast.FunctionDef,
        discriminator: str,
        branch_labels: List[str],
    ) -> str:
        base = fn_node.name.replace("_", " ").title().replace(" ", "")
        iface = f"{base}Strategy"
        lines = [
            f"from abc import ABC, abstractmethod",
            f"from typing import Dict, Type",
            f"",
            f"class {iface}(ABC):",
            f"    @abstractmethod",
            f"    def {fn_node.name}(self):",
            f"        ...",
            f"",
        ]
        for b in branch_labels[:6]:
            cls_name = (
                b.replace(" ", "").replace("-", "")
                .replace("_", " ").title().replace(" ", "")
            )
            if not cls_name.isidentifier():
                cls_name = f"Variant{b[:16]}"
            lines.append(f"class {cls_name}Strategy({iface}):")
            lines.append(f"    def {fn_node.name}(self):")
            lines.append(f"        ...  # logic for '{b}'")
            lines.append(f"")
        if len(branch_labels) > 6:
            lines.append(f"# ... and {len(branch_labels) - 6} more strategies")
            lines.append(f"")
        lines.extend([
            f"# Select strategy based on '{discriminator}':",
            f"# strategy = strategies[{discriminator}]",
            f"# strategy.{fn_node.name}()",
        ])
        return "\n".join(lines)

    @staticmethod
    def _rewrite_observer(
        cls_node: ast.ClassDef,
        methods: List[Tuple[str, int]],
    ) -> str:
        name = cls_node.name
        lines = [
            f"from abc import ABC, abstractmethod",
            f"from typing import List, Any",
            f"",
            f"class Observer(ABC):",
            f"    @abstractmethod",
            f"    def update(self, event: str, data: Any = None) -> None:",
            f"        ...",
            f"",
            f"class EventEmitter:",
            f"    def __init__(self):",
            f"        self._observers: List[Observer] = []",
            f"",
            f"    def subscribe(self, observer: Observer) -> None:",
            f"        self._observers.append(observer)",
            f"",
            f"    def unsubscribe(self, observer: Observer) -> None:",
            f"        self._observers.remove(observer)",
            f"",
            f"    def notify(self, event: str, data: Any = None) -> None:",
            f"        for observer in self._observers:",
            f"            observer.update(event, data)",
            f"",
            f"# {name} can now inherit from EventEmitter:",
            f"class {name}(EventEmitter):",
            f"    ...  # no need to manually manage listener lists",
        ]
        return "\n".join(lines)

    @staticmethod
    def _rewrite_decorator(
        cls_node: ast.ClassDef, method_names: List[str],
    ) -> str:
        name = cls_node.name
        method = method_names[0] if method_names else "execute"
        lines = [
            f"from abc import ABC, abstractmethod",
            f"",
            f"class {name}Base(ABC):",
            f"    @abstractmethod",
            f"    def {method}(self):",
            f"        ...",
            f"",
            f"class {name}Decorator({name}Base):",
            f"    def __init__(self, wrapped: {name}Base):",
            f"        self._wrapped = wrapped",
            f"",
            f"    def {method}(self):",
            f"        return self._wrapped.{method}()",
            f"",
            f"class Extended{name}({name}Decorator):",
            f"    def {method}(self):",
            f"        # Add behavior before/after delegation",
            f"        result = super().{method}()",
            f"        return result",
            f"",
            f"# Usage: compose decorators at runtime",
            f"# obj = Extended{name}(Another{name}(Base{name}()))",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Regex-based analyser for non-Python languages
# ---------------------------------------------------------------------------

# Singleton patterns per language
_SINGLETON_PATTERNS: Dict[str, re.Pattern] = {
    "java": re.compile(
        r"private\s+static\s+\w+\s+instance\b.*?public\s+static\s+\w+\s+getInstance\s*\(",
        re.DOTALL,
    ),
    "typescript": re.compile(
        r"private\s+static\s+instance\b.*?(?:public\s+)?static\s+getInstance\s*\(",
        re.DOTALL,
    ),
    "javascript": re.compile(
        r"static\s+#?instance\b.*?static\s+getInstance\s*\(",
        re.DOTALL,
    ),
    "csharp": re.compile(
        r"private\s+static\s+\w+\s+_?instance\b.*?public\s+static\s+\w+\s+(?:Instance|GetInstance)\b",
        re.DOTALL,
    ),
    "kotlin": re.compile(
        r"companion\s+object\s*\{.*?(?:instance|INSTANCE)\b",
        re.DOTALL,
    ),
    "swift": re.compile(
        r"static\s+(?:let|var)\s+shared\b",
    ),
    "cpp": re.compile(
        r"static\s+\w+[&*]\s+(?:getInstance|instance)\s*\(",
    ),
    "php": re.compile(
        r"private\s+static\s+\$instance\b.*?public\s+static\s+function\s+getInstance\s*\(",
        re.DOTALL,
    ),
    "ruby": re.compile(
        r"@@instance\b.*?def\s+self\.instance\b",
        re.DOTALL,
    ),
    "go": re.compile(
        r"var\s+instance\s+\*?\w+.*?sync\.Once|once\.Do",
        re.DOTALL,
    ),
}

# Factory method patterns
_FACTORY_FUNC_PATTERN: Dict[str, re.Pattern] = {
    lang: re.compile(
        r"(?:public\s+|private\s+|protected\s+|static\s+)*"
        r"(?:func(?:tion)?|def|fun)\s+"
        r"((?:create|make|build|factory)\w*)\s*\(",
        re.IGNORECASE,
    )
    for lang in LANGUAGE_MAP.values()
}

_OBSERVER_METHOD_REGEX = re.compile(
    r"\b(?:def|function|func|fun|void|public|private|protected)\s+"
    r"(add[_A-Z]?[Ll]istener|remove[_A-Z]?[Ll]istener|"
    r"add[_A-Z]?[Oo]bserver|remove[_A-Z]?[Oo]bserver|"
    r"subscribe|unsubscribe|notify|on[_A-Z]?[Ee]vent|"
    r"addEventListener|removeEventListener|"
    r"register[_A-Z]?[Cc]allback|unregister[_A-Z]?[Cc]allback|emit)\s*\(",
    re.MULTILINE,
)

_CALLBACK_LIST_REGEX = re.compile(
    r"\b(?:listeners|observers|callbacks|handlers|subscribers|"
    r"_listeners|_observers|_callbacks|_handlers|_subscribers|"
    r"eventHandlers|event_handlers)\b",
)

_IF_ELIF_PATTERN = re.compile(
    r"^\s*(?:if|elif|else\s+if|elseif|elsif|}\s*else\s+if)\b", re.MULTILINE
)

_SWITCH_PATTERN = re.compile(r"\bswitch\s*\(|\bwhen\s*[\({]|\bcase\s", re.MULTILINE)

_CASE_BRANCH_PATTERN = re.compile(r"^\s*case\b|^\s*when\b", re.MULTILINE)

_CLASS_PATTERN = re.compile(
    r"^\s*(?:(?:public|private|protected|internal|abstract|open|data|export|static)\s+)*"
    r"(?:class|struct)\s+(\w+)",
    re.MULTILINE,
)

_EXTENDS_PATTERN = re.compile(
    r"class\s+(\w+)\s*(?:extends|:)\s*(\w+)",
    re.MULTILINE,
)


class RegexAnalyser:
    """Heuristic analyser for non-Python languages."""

    def __init__(self, source: str, language: str):
        self.source = source
        self.lines = source.splitlines()
        self.language = language

    def analyse(self) -> List[ScopeResult]:
        results: List[ScopeResult] = []
        results.extend(self._detect_singleton())
        results.extend(self._detect_god_factory())
        results.extend(self._detect_strategy_opportunity())
        results.extend(self._detect_observer_opportunity())
        results.extend(self._detect_deep_inheritance())
        return self._merge_scopes(results)

    # -- 1. Singleton -------------------------------------------------------

    def _detect_singleton(self) -> List[ScopeResult]:
        pattern = _SINGLETON_PATTERNS.get(self.language)
        if not pattern:
            return []

        results: List[ScopeResult] = []
        for match in pattern.finditer(self.source):
            line_no = self.source[:match.start()].count("\n") + 1
            class_name = self._enclosing_class(match.start()) or "<unknown>"

            results.append(ScopeResult(
                name=class_name,
                kind="Class",
                start_line=line_no,
                end_line=line_no + match.group().count("\n"),
                findings=[Finding(
                    severity=Severity.WARNING,
                    message="Singleton pattern detected (private static instance + getInstance)",
                    line=line_no,
                    suggestion=(
                        "Consider dependency injection -- create one instance "
                        "at the composition root and pass it to consumers"
                    ),
                )],
            ))
        return results

    # -- 2. God Factory -----------------------------------------------------

    def _detect_god_factory(self) -> List[ScopeResult]:
        results: List[ScopeResult] = []

        # Find factory-named functions
        for match in FACTORY_NAME_PATTERN.finditer(self.source):
            pos = match.start()
            line_no = self.source[:pos].count("\n") + 1

            # Look at the surrounding block (up to 3000 chars ahead)
            block_end = min(pos + 3000, len(self.source))
            block = self.source[pos:block_end]

            # Count if/elif branches
            if_count = len(_IF_ELIF_PATTERN.findall(block[:2000]))

            # Count switch/case branches
            case_count = len(_CASE_BRANCH_PATTERN.findall(block[:2000]))

            branch_count = max(if_count, case_count)
            if branch_count >= GOD_FACTORY_BRANCH_THRESHOLD:
                func_name = match.group()
                results.append(ScopeResult(
                    name=func_name,
                    kind="Function",
                    start_line=line_no,
                    end_line=line_no + block[:2000].count("\n"),
                    findings=[Finding(
                        severity=Severity.WARNING,
                        message=(
                            f"God Factory: {branch_count}-branch conditional "
                            f"creating different handler types"
                        ),
                        line=line_no,
                        suggestion=(
                            "Use Abstract Factory or a registry pattern to "
                            "make handler creation extensible"
                        ),
                    )],
                ))
        return results

    # -- 3. Strategy opportunity --------------------------------------------

    def _detect_strategy_opportunity(self) -> List[ScopeResult]:
        results: List[ScopeResult] = []

        # Find long if/elif chains with discriminator parameters
        chain_starts: List[Tuple[int, int, int]] = []  # (line, pos, length)
        current_chain: List[Tuple[int, int]] = []

        for m in _IF_ELIF_PATTERN.finditer(self.source):
            line_no = self.source[:m.start()].count("\n") + 1
            if current_chain:
                last_line = current_chain[-1][0]
                if line_no - last_line <= 20:
                    current_chain.append((line_no, m.start()))
                else:
                    if len(current_chain) >= STRATEGY_BRANCH_THRESHOLD:
                        chain_starts.append((
                            current_chain[0][0],
                            current_chain[0][1],
                            len(current_chain),
                        ))
                    current_chain = [(line_no, m.start())]
            else:
                current_chain = [(line_no, m.start())]

        if len(current_chain) >= STRATEGY_BRANCH_THRESHOLD:
            chain_starts.append((
                current_chain[0][0],
                current_chain[0][1],
                len(current_chain),
            ))

        for start_line, pos, chain_len in chain_starts:
            context = self.source[max(0, pos - 300):pos + 1500]
            # Check for discriminator names in context
            disc_match = DISCRIMINATOR_NAMES.search(context)
            if disc_match:
                discriminator = disc_match.group()
                results.append(ScopeResult(
                    name=self._enclosing_func(pos) or "<unknown>",
                    kind="Function",
                    start_line=start_line,
                    end_line=start_line + chain_len,
                    findings=[Finding(
                        severity=Severity.SUGGESTION,
                        message=(
                            f"Strategy opportunity: {chain_len}-branch conditional "
                            f"on discriminator '{discriminator}'"
                        ),
                        line=start_line,
                        suggestion=(
                            f"Extract each branch into a Strategy class implementing "
                            f"a common interface, then select strategy based on "
                            f"'{discriminator}' via a registry or map"
                        ),
                    )],
                ))
        return results

    # -- 4. Observer opportunity --------------------------------------------

    def _detect_observer_opportunity(self) -> List[ScopeResult]:
        results: List[ScopeResult] = []

        # Find observer-like method declarations
        observer_methods = list(_OBSERVER_METHOD_REGEX.finditer(self.source))
        has_callback_list = bool(_CALLBACK_LIST_REGEX.search(self.source))

        if len(observer_methods) >= 2 and has_callback_list:
            # Group by enclosing class
            groups: Dict[str, List[Tuple[str, int]]] = defaultdict(list)
            for m in observer_methods:
                line_no = self.source[:m.start()].count("\n") + 1
                class_name = self._enclosing_class(m.start()) or "<module>"
                groups[class_name].append((m.group(1), line_no))

            for class_name, methods in groups.items():
                if len(methods) < 2:
                    continue
                methods_str = ", ".join(
                    f"{name}() at line {ln}" for name, ln in methods
                )
                results.append(ScopeResult(
                    name=class_name,
                    kind="Class",
                    start_line=methods[0][1],
                    end_line=methods[-1][1],
                    findings=[Finding(
                        severity=Severity.SUGGESTION,
                        message="Manual observer/listener management detected",
                        line=methods[0][1],
                        end_line=methods[-1][1],
                        detail=f"Observer methods: {methods_str}",
                        suggestion=(
                            "Consider using a formal Observer pattern implementation "
                            "or an event bus library to decouple publishers from "
                            "subscribers and reduce boilerplate"
                        ),
                    )],
                ))
        return results

    # -- 5. Deep inheritance ------------------------------------------------

    def _detect_deep_inheritance(self) -> List[ScopeResult]:
        results: List[ScopeResult] = []

        # Build inheritance map from extends/: patterns
        inheritance: Dict[str, str] = {}
        for m in _EXTENDS_PATTERN.finditer(self.source):
            child, parent = m.group(1), m.group(2)
            inheritance[child] = parent

        # Compute depths
        for child in inheritance:
            depth = 0
            current = child
            visited: Set[str] = set()
            while current in inheritance and current not in visited:
                visited.add(current)
                current = inheritance[current]
                depth += 1

            if depth > DEEP_INHERITANCE_THRESHOLD:
                # Find the class line
                class_match = re.search(
                    rf"class\s+{re.escape(child)}\b", self.source
                )
                line_no = 1
                if class_match:
                    line_no = self.source[:class_match.start()].count("\n") + 1

                # Build chain string
                chain = [child]
                current = child
                visited_chain: Set[str] = set()
                while current in inheritance and current not in visited_chain:
                    visited_chain.add(current)
                    current = inheritance[current]
                    chain.append(current)

                results.append(ScopeResult(
                    name=child,
                    kind="Class",
                    start_line=line_no,
                    end_line=line_no,
                    findings=[Finding(
                        severity=Severity.WARNING,
                        message=f"Deep inheritance chain (depth {depth})",
                        line=line_no,
                        detail=f"Chain: {' -> '.join(chain)}",
                        suggestion=(
                            "Consider using the Decorator pattern instead of "
                            "deep inheritance -- wrap behavior around a base "
                            "component via composition rather than extending "
                            "a long class hierarchy"
                        ),
                    )],
                ))
        return results

    # -- helpers ------------------------------------------------------------

    def _enclosing_class(self, pos: int) -> Optional[str]:
        text_before = self.source[:pos]
        matches = list(_CLASS_PATTERN.finditer(text_before))
        if matches:
            return matches[-1].group(1)
        return None

    def _enclosing_func(self, pos: int) -> Optional[str]:
        text_before = self.source[:pos]
        func_pat = re.compile(
            r"(?:function|def|func|fun)\s+(\w+)\s*\(", re.MULTILINE
        )
        matches = list(func_pat.finditer(text_before))
        if matches:
            return matches[-1].group(1)
        return None

    @staticmethod
    def _merge_scopes(scopes: List[ScopeResult]) -> List[ScopeResult]:
        """Merge findings that belong to the same scope."""
        merged: Dict[str, ScopeResult] = {}
        for s in scopes:
            key = f"{s.name}:{s.kind}"
            if key in merged:
                merged[key].findings.extend(s.findings)
                merged[key].end_line = max(merged[key].end_line, s.end_line)
            else:
                merged[key] = ScopeResult(
                    name=s.name,
                    kind=s.kind,
                    start_line=s.start_line,
                    end_line=s.end_line,
                    findings=list(s.findings),
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


def analyse_file(filepath: Path) -> FileResult:
    language = LANGUAGE_MAP.get(filepath.suffix, "unknown")
    result = FileResult(path=str(filepath), language=language)

    try:
        source = filepath.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        result.parse_error = str(exc)
        return result

    if not source.strip():
        return result

    if language == "python":
        analyser = PythonAnalyser(source)
    else:
        analyser = RegexAnalyser(source, language)

    try:
        result.scopes = analyser.analyse()
    except Exception as exc:
        result.parse_error = f"Analysis error: {exc}"

    return result


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------

def format_text(
    results: List[FileResult], verbose: bool, show_rewrite: bool,
) -> str:
    lines: List[str] = []
    for fr in results:
        if not fr.has_concerns and not fr.parse_error:
            if verbose:
                lines.append(f"=== Design Pattern Analysis: {fr.path} ===")
                lines.append("  No concerns found.")
                lines.append("")
            continue

        lines.append(f"=== Design Pattern Analysis: {fr.path} ===")
        if fr.parse_error:
            lines.append(f"  [ERROR] {fr.parse_error}")
            lines.append("")
            continue

        lines.append("")
        for scope in fr.scopes:
            if not scope.findings:
                continue
            scope_header = f"{scope.kind}: {scope.name}"
            if (
                scope.start_line
                and scope.end_line
                and scope.start_line != scope.end_line
            ):
                scope_header += f" (lines {scope.start_line}-{scope.end_line})"
            elif scope.start_line:
                scope_header += f" (line {scope.start_line})"
            lines.append(scope_header)

            for f in scope.findings:
                severity_label = f.severity.value
                lines.append(f"  [{severity_label}] {f.message}")
                if f.detail:
                    lines.append(f"    {f.detail}")
                if f.suggestion:
                    lines.append(f"  [SUGGESTION] {f.suggestion}")

                if show_rewrite and f.rewrite_hint:
                    lines.append("")
                    lines.append("  [REWRITE] Suggested refactored structure:")
                    for rline in f.rewrite_hint.splitlines():
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
                "findings": [],
            }
            for f in scope.findings:
                scope_data["findings"].append({
                    "severity": f.severity.value,
                    "message": f.message,
                    "line": f.line,
                    "end_line": f.end_line,
                    "detail": f.detail,
                    "suggestion": f.suggestion,
                    "rewrite_hint": f.rewrite_hint if f.rewrite_hint else None,
                })
            file_data["scopes"].append(scope_data)
        output.append(file_data)
    return json.dumps(output, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="check_design_patterns",
        description=(
            "Detect design pattern misuse and opportunities in source code. "
            "Finds singleton abuse, god factories, strategy/observer "
            "opportunities, and deep inheritance chains."
        ),
    )
    parser.add_argument(
        "path",
        help="File or directory to analyse (recursively for directories)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show detailed output including clean files",
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
        help="Include suggested refactored code with pattern improvements",
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
        results.append(analyse_file(f))

    if args.json_output:
        print(format_json(results))
    else:
        output = format_text(results, args.verbose, args.rewrite)
        if output:
            print(output, end="")
        elif not args.verbose:
            print("No design pattern concerns found.")

    has_concerns = any(r.has_concerns for r in results)
    return 1 if has_concerns else 0


if __name__ == "__main__":
    sys.exit(main())
