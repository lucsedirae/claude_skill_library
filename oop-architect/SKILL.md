---
name: oop-architect
description: Use when designing, generating, reviewing, or refactoring object-oriented code architecture, including refactoring existing codebases. Provides analysis scripts that detect design pattern misuse, coupling and cohesion problems, architecture layer violations, circular dependencies, and code organization issues. Provides reference material for applying GoF design patterns, choosing composition over inheritance, structuring layered and hexagonal architectures, and organizing packages and modules. Complements the solid-principles skill by covering patterns, architecture, and structural concerns beyond SOLID.
---

# OOP Architect

## Overview

Analyze and guide object-oriented code architecture across four domains: design patterns, core OOP principles, architecture patterns, and code organization. This skill complements the `solid-principles` skill — use both together for comprehensive OOP design quality.

## When to Apply

- Selecting appropriate design patterns for a problem
- Evaluating inheritance vs composition tradeoffs
- Designing or reviewing architecture layers (MVC, Clean Architecture, Hexagonal)
- Organizing packages, modules, and namespaces
- Reviewing dependency graphs for circular dependencies or coupling issues
- Refactoring existing codebases for better OOP design

## Quick Reference

- **Design Patterns:** Apply GoF creational, structural, and behavioral patterns appropriately. Prefer the simplest pattern that solves the problem. Avoid Singleton for global mutable state.
- **OOP Principles:** Favor composition over inheritance. Keep coupling loose and cohesion high. Encapsulate what varies. Program to interfaces.
- **Architecture Patterns:** Separate concerns into layers. Dependencies point inward (Clean Architecture). Isolate domain logic from infrastructure.
- **Code Organization:** One concept per module. No circular dependencies. Explicit public API per package. Prefer package-by-feature over package-by-layer.

## Code Review Workflow

To analyze code for OOP architecture issues, run the corresponding script:

```
wsl python3 scripts/check_design_patterns.py <path>
wsl python3 scripts/check_oop_principles.py <path>
wsl python3 scripts/check_architecture.py <path>
wsl python3 scripts/check_code_organization.py <path>
```

**Default behavior:** Summarize findings and suggest improvements.

**Rewrite mode:** Pass `--rewrite` to output refactored code that addresses detected issues. Only use when the user explicitly asks for code to be rewritten or fixed.

**Additional flags:**
- `--verbose` — detailed output with extended context
- `--json` — machine-readable JSON output

To perform a full OOP architecture audit, run all four scripts sequentially against the target path. For SOLID-specific analysis, also run the `solid-principles` scripts.

## Code Generation Guidelines

When generating new code, apply these directives:

1. **Pattern selection:** Choose the simplest pattern that solves the problem. Do not introduce a Factory when a constructor suffices. Do not use Singleton for convenience — only for resources that are genuinely singular by nature.
2. **Composition over inheritance:** Default to composition. Use inheritance only for true is-a relationships where the subclass can fully substitute for the parent. Keep hierarchies shallow (depth 3 or less).
3. **Coupling and cohesion:** Each class should depend on abstractions, not concrete implementations. Methods within a class should operate on the same set of instance data. If a class can be split into two independent halves, it should be.
4. **Architecture layers:** Separate presentation, business logic, and data access. Inner layers define interfaces; outer layers implement them. Domain objects must not depend on frameworks or infrastructure.
5. **Module structure:** Organize by feature, not by technical layer. Keep modules focused on one concept. Define explicit public APIs. Avoid circular dependencies between modules.

## Refactoring Existing Codebases

When refactoring code the agent did not build, run all analysis scripts first to identify issues, then prioritize fixes by impact:
1. Circular dependencies (break these first — they block all other improvements)
2. Architecture layer violations (establish clean boundaries)
3. Coupling and cohesion issues (improve class design)
4. Design pattern opportunities (introduce patterns where they simplify the code)

## References

Load these reference guides for detailed patterns and decision aids:

- `references/design_patterns_creational.md` — when choosing between Factory, Builder, Singleton, or Prototype
- `references/design_patterns_structural_behavioral.md` — when selecting structural or behavioral patterns (Strategy, Observer, Decorator, etc.)
- `references/oop_principles.md` — when evaluating coupling metrics, cohesion types, or diagnosing anti-patterns like God Object or Feature Envy
- `references/architecture_patterns.md` — when designing MVC/MVP/MVVM, Clean Architecture, or DDD tactical patterns
- `references/code_organization.md` — when structuring packages, resolving circular dependencies, or designing module boundaries
- `references/composition_over_inheritance.md` — when deciding between inheritance and composition, or refactoring from one to the other
