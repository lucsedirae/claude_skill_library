# Structural and Behavioral Design Patterns

## Structural Patterns

Structural patterns compose classes and objects into larger structures while keeping them flexible and efficient.

### Adapter
Converts the interface of a class into one that clients expect. Use when integrating third-party libraries or legacy code with incompatible interfaces.

### Bridge
Separates an abstraction from its implementation so both can vary independently. Use when a class has two orthogonal dimensions of variation (e.g., platform + feature).

### Composite
Composes objects into tree structures to represent part-whole hierarchies. Use when clients should treat individual objects and compositions uniformly (e.g., file system, UI component trees).

### Decorator
Attaches additional behavior to objects dynamically by wrapping them. Use instead of subclassing when responsibilities need to be added/removed at runtime or combined in various ways.

**Prefer Decorator over inheritance when:** Behavior combinations would cause a class explosion. Behavior should be added/removed dynamically. The core object should remain unmodified.

### Facade
Provides a simplified interface to a complex subsystem. Use to reduce coupling between clients and subsystem internals. Does not prevent direct access — it offers a convenient default.

### Proxy
Controls access to another object. Variants: lazy initialization (virtual proxy), access control (protection proxy), logging (logging proxy), caching (caching proxy).

## Behavioral Patterns

Behavioral patterns define how objects interact and distribute responsibility.

### Strategy
Defines a family of interchangeable algorithms. Use when multiple algorithms exist for a task and the choice should be configurable at runtime. Eliminates conditional logic for algorithm selection.

### Observer
Establishes a one-to-many dependency so that when one object changes state, all dependents are notified. Use for event systems, pub/sub, reactive updates. Avoid when the notification chain becomes circular or debugging becomes difficult.

### Command
Encapsulates a request as an object, enabling parameterization, queuing, logging, and undo/redo. Use for action histories, macro recording, or transaction-based operations.

### State
Allows an object to alter its behavior when its internal state changes, appearing to change its class. Use for state machines where transitions and behaviors are state-dependent. Eliminates large state-based conditionals.

### Template Method
Defines the skeleton of an algorithm in a base class, letting subclasses override specific steps without changing the overall structure. Use when multiple classes share the same algorithm structure but differ in specific steps.

### Iterator
Provides a way to access elements of a collection sequentially without exposing its internal structure. Most languages provide built-in iterator support.

### Chain of Responsibility
Passes a request along a chain of handlers, each deciding whether to process it or pass it along. Use for middleware pipelines, event bubbling, or validation chains.

## Pattern Selection by Problem Type

| Problem | Consider |
|---------|----------|
| Incompatible interfaces | Adapter |
| Adding behavior dynamically | Decorator |
| Simplifying complex API | Facade |
| Swappable algorithms | Strategy |
| Event notification | Observer |
| Undo/redo, command queuing | Command |
| State-dependent behavior | State |
| Shared algorithm skeleton | Template Method |
| Request pipeline | Chain of Responsibility |
| Tree structures | Composite |
| Lazy/controlled access | Proxy |
| Two-dimensional variation | Bridge |
