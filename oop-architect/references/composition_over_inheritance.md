# Composition Over Inheritance

## Why Composition Is Preferred

Inheritance creates a tight, compile-time coupling between parent and child. Changes to the parent's implementation can break subclasses (the fragile base class problem). Inheritance hierarchies are rigid — a class can only inherit from one parent in most languages, and restructuring a hierarchy is expensive.

Composition creates a loose, runtime-configurable relationship. Components can be swapped, combined, and tested independently. New behavior is added by composing existing objects rather than extending existing classes.

**Inheritance locks in behavior at design time. Composition defers it to runtime.**

## When Inheritance Is Appropriate

- **True is-a relationships:** When the subclass genuinely represents a specialization (e.g., `SavingsAccount` is an `Account`). The subclass should pass the "is-a" test in all contexts, not just syntactically.
- **Framework requirements:** Some frameworks require extending base classes (e.g., Android Activities, Django Views). Use inheritance where the framework demands it, but keep the inheritance hierarchy shallow.
- **Template Method pattern:** When a fixed algorithm skeleton with overridable steps is the natural design.
- **Shared interface with shared implementation:** When subtypes share both the interface and a significant amount of implementation that would otherwise be duplicated.

**Test:** If you find yourself overriding most parent methods, or if the subclass only uses a fraction of the parent's API, inheritance is the wrong tool.

## Refactoring Inheritance to Composition

**Step 1:** Create an instance of the former parent class inside the former child class.

**Step 2:** Delegate method calls to the contained instance instead of inheriting them.

**Step 3:** Extract an interface for the methods the containing class actually uses.

**Step 4:** Accept the dependency through the constructor for testability and flexibility.

**Before:**
```
class EnhancedLogger(FileLogger):
    def log(self, message):
        message = self.add_timestamp(message)
        super().log(message)
```

**After:**
```
class EnhancedLogger:
    def __init__(self, logger: Logger):
        self.logger = logger

    def log(self, message):
        message = self.add_timestamp(message)
        self.logger.log(message)
```

Now `EnhancedLogger` works with any `Logger` implementation, not just `FileLogger`.

## Delegation Pattern

Delegation forwards requests to a contained object. The containing object exposes the same interface but adds or modifies behavior. This is the mechanical basis of composition — wherever you inherit to reuse behavior, you can instead hold a reference and delegate.

## Mixin and Trait Tradeoffs

Mixins (Python, Ruby) and traits (Scala, Rust, PHP) offer a middle ground: reusable behavior without single-inheritance constraints.

**Benefits:** Share behavior across unrelated classes. Avoid deep hierarchies. Combine multiple behaviors.

**Risks:** Name collisions between mixins. Complex method resolution order (MRO). Can create implicit coupling if mixins access shared state. Harder to reason about than explicit composition.

**Guideline:** Use mixins for stateless behavior (e.g., serialization, logging). Prefer composition for stateful collaborators.
