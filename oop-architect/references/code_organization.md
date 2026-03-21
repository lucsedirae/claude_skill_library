# Code Organization

## Package-by-Feature vs Package-by-Layer

**Package-by-layer** organizes by technical role:
```
src/
  controllers/
  services/
  repositories/
  models/
```

**Package-by-feature** organizes by domain concept:
```
src/
  users/
    user_controller, user_service, user_repository, user_model
  orders/
    order_controller, order_service, order_repository, order_model
```

**Package-by-feature is generally preferred** because: related code is co-located, features can be developed independently, feature boundaries are visible in the file system, and removing a feature means deleting one directory.

**Package-by-layer** works well for small projects or when features share significant infrastructure. Can be combined with package-by-feature at the top level.

## Namespace and Module Design

- **One concept per module:** A module should represent a single concept or a tightly related group. If a module requires "and" in its description, consider splitting.
- **Explicit public API:** Define what a module exports. In Python, use `__init__.py` with explicit `__all__`. In TypeScript, use barrel files (`index.ts`). In Java, use package-info and access modifiers.
- **Internal vs external:** Mark implementation details as private/internal. Only expose what external consumers need.
- **Flat over deep:** Prefer shallow directory hierarchies (2-3 levels) over deeply nested structures. Deep nesting makes navigation harder and often indicates over-categorization.

## Dependency Graph Health

A healthy dependency graph is a directed acyclic graph (DAG). Key metrics:

- **No circular dependencies:** If A depends on B and B depends on A, extract the shared concept into a third module C that both depend on.
- **Stable dependencies principle:** Depend in the direction of stability. Volatile modules should depend on stable modules, not the reverse.
- **Stable abstractions principle:** Stable packages should be abstract. If a widely-depended-upon package is concrete, changes to it cascade everywhere.

## Circular Dependency Resolution

When cycles are detected, resolve them by:

1. **Extract shared interface:** If A and B depend on each other, extract the interface that B implements into a new module C. A depends on C; B implements C.
2. **Dependency inversion:** Have the lower-level module define an interface that the higher-level module implements (or inject).
3. **Event/callback:** Replace direct calls with events or callbacks to break the compile-time dependency.
4. **Merge:** If two modules are truly inseparable, they may belong together as one module.

## Monorepo Module Boundaries

In monorepos with multiple packages/services:

- **Define clear ownership:** Each package has one owning team or domain.
- **Minimize cross-package dependencies:** Shared code goes into dedicated library packages with stable APIs.
- **Version internal libraries:** Or use trunk-based development with comprehensive tests to catch breaking changes.
- **Avoid diamond dependencies:** If package A and B both depend on library C, ensure they use compatible versions.

## Sizing Guidelines

- **Module/file:** 100-500 lines. Over 500 suggests splitting. Under 50 may indicate over-fragmentation.
- **Class:** 50-300 lines. Over 300 suggests multiple responsibilities.
- **Function/method:** 5-30 lines. Over 50 is almost always too long.
- **Package/directory:** 5-20 modules. Over 30 suggests sub-packaging. Under 3 may indicate over-categorization.

These are guidelines, not rules. Context determines appropriate size.
