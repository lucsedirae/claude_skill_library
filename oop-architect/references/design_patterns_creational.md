# Creational Design Patterns

## Pattern Selection Guide

Choose the simplest creational pattern that solves the problem. Unnecessary abstraction adds complexity without benefit.

### Factory Method

Use when a class cannot anticipate the type of object it needs to create, or when subclasses should decide which class to instantiate. The factory method defines an interface for creation but lets subclasses alter the type.

**When to use:** Object type depends on runtime conditions. Multiple related classes share a creation interface. Client code should be decoupled from concrete product classes.

**Avoid when:** There is only one product type with no foreseeable variation.

### Abstract Factory

Use when a system must create families of related objects without specifying their concrete classes. Each factory produces a consistent set of products.

**When to use:** Multiple product families exist (e.g., UI themes where buttons, menus, and dialogs must match). Switching families should require changing only the factory.

**Avoid when:** Only one product type varies — Factory Method suffices.

### Builder

Use when constructing a complex object step by step, especially when the object has many optional parameters or multiple valid configurations.

**When to use:** Constructors have 4+ parameters. Object construction involves multiple steps. The same construction process should create different representations.

**Avoid when:** The object is simple enough for a constructor or factory method.

### Singleton

Legitimate uses: loggers, configuration registries, connection pools — resources where exactly one instance is correct by nature, not by convenience.

**Abuse indicators:** Using Singleton for global mutable state, as a service locator, or to avoid passing dependencies. These create tight coupling, hidden dependencies, and testing difficulty.

**Prefer instead:** Dependency injection with a single instance managed by the composition root.

### Prototype

Use when creating new objects by cloning an existing instance is cheaper or simpler than constructing from scratch. Useful for objects with complex initialization or when the system should be independent of how products are created.

**When to use:** Object creation is expensive. Configuration comes from runtime state. The number of classes to create is not known in advance.

## Decision Table

| Scenario | Pattern |
|----------|---------|
| One product type, type varies at runtime | Factory Method |
| Families of related products | Abstract Factory |
| Complex object, many optional params | Builder |
| Exactly one instance by nature | Singleton (with caution) |
| Clone existing configured object | Prototype |
| Simple object, known type | Constructor (no pattern needed) |

## Common Mistakes

- **Over-factoring:** Creating factories for objects that never vary
- **Singleton as global state:** Using Singleton to share mutable data across the application
- **Builder without complexity:** Applying Builder to objects with 2-3 simple fields
- **Abstract Factory for one product:** Using Abstract Factory when Factory Method would suffice
