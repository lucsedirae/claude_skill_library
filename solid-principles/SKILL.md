---
name: solid-principles
description: Use when designing, generating, reviewing, or refactoring object-oriented code. Provides analysis scripts that detect violations of SOLID principles (SRP, OCP, LSP, ISP, DIP) in any OOP language, and reference material that guides code generation toward SOLID-compliant designs. Activates for tasks involving class design, inheritance hierarchies, interface design, dependency management, code architecture, and design pattern application.
---

# SOLID Principles

## Overview

Enforce and apply the five SOLID principles of object-oriented design when generating, reviewing, or refactoring code. This skill provides analysis scripts for detecting violations and reference guides for writing SOLID-compliant code in any OOP language.

## When to Apply

- Designing new classes or class hierarchies
- Generating code involving inheritance or interfaces
- Reviewing existing code for design quality
- Refactoring code to improve maintainability
- Evaluating pull requests for architectural concerns

## Quick Reference

- **S — Single Responsibility (SRP):** A class should have only one reason to change.
- **O — Open/Closed (OCP):** Open for extension, closed for modification.
- **L — Liskov Substitution (LSP):** Subtypes must be substitutable for their base types.
- **I — Interface Segregation (ISP):** No client should depend on interfaces it does not use.
- **D — Dependency Inversion (DIP):** Depend on abstractions, not concrete implementations.

## Code Review Workflow

To analyze code for SOLID violations, run the corresponding script against a file or directory:

```
wsl python3 scripts/check_srp.py <path>
wsl python3 scripts/check_ocp.py <path>
wsl python3 scripts/check_lsp.py <path>
wsl python3 scripts/check_isp.py <path>
wsl python3 scripts/check_dip.py <path>
```

**Default behavior:** Summarize findings and suggest improvements.

**Rewrite mode:** Pass `--rewrite` to output refactored code that fixes detected violations. Only use when the user explicitly asks for code to be rewritten or fixed.

**Additional flags:**
- `--verbose` — detailed output with extended context
- `--json` — machine-readable JSON output

To perform a full SOLID audit, run all five scripts sequentially against the target path.

## Code Generation Guidelines

When generating new code, apply these directives:

1. **SRP:** Identify the single axis of change before writing a class. Name the class after its one responsibility. If a class description requires "and," split it.
2. **OCP:** Favor polymorphism over conditionals that branch on type. Design extension points (abstract methods, strategy parameters) rather than modifying existing code.
3. **LSP:** Ensure every subclass honors the base class contract — same preconditions, same or stronger postconditions, no surprising exceptions. If a subclass cannot fulfill a method, the hierarchy is wrong.
4. **ISP:** Create small, focused interfaces. If an implementing class would leave methods as no-ops, the interface is too broad — split it.
5. **DIP:** Accept dependencies through constructors or method parameters, not by instantiating concrete classes internally. High-level modules define the abstractions they need; low-level modules implement them.

## References

Load these reference guides for detailed patterns, anti-patterns, and refactoring examples:

- `references/srp_guide.md` — when working on class decomposition or when a class appears to have multiple responsibilities
- `references/ocp_guide.md` — when designing extension points or encountering type-switching conditionals
- `references/lsp_guide.md` — when designing inheritance hierarchies or evaluating subclass contracts
- `references/isp_guide.md` — when designing interfaces or when implementing classes leave methods unused
- `references/dip_guide.md` — when managing dependencies or designing module boundaries
