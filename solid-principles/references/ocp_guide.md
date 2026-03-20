# Open/Closed Principle (OCP)

## Principle Statement

Software entities (classes, modules, functions) should be open for extension but closed for modification. Add new behavior by writing new code, not by changing existing code.

## The Problem

When adding a new variant requires modifying existing code (e.g., adding another `elif` branch), every addition risks introducing bugs into previously working behavior. The modification ripples through tests, and the growing conditional becomes harder to understand and maintain.

## Detection Heuristics

- Long if/elif/else or switch/case chains that branch on type, category, or status
- Use of `isinstance()`, `instanceof`, `is`, `dynamic_cast`, or `.is_a?` in conditional logic
- Functions with a `type` or `kind` parameter that drives branching
- String comparisons against type-like values (`if status == "pending"`, `if shape == "circle"`)
- Adding a new feature requires touching multiple existing methods

## Refactoring Patterns

**Before — type-switching:**
```
def calculate_area(shape):
    if shape.type == "circle":
        return pi * shape.radius ** 2
    elif shape.type == "rectangle":
        return shape.width * shape.height
    elif shape.type == "triangle":
        return 0.5 * shape.base * shape.height
```

**After — polymorphism:**
```
class Shape:
    def area(self) -> float: ...

class Circle(Shape):
    def area(self):
        return pi * self.radius ** 2

class Rectangle(Shape):
    def area(self):
        return self.width * self.height

class Triangle(Shape):
    def area(self):
        return 0.5 * self.base * self.height
```

Adding a new shape means creating a new class — no existing code changes.

**Strategy Pattern** — when polymorphism via inheritance is too rigid:
```
class PaymentProcessor:
    def __init__(self, strategy: PaymentStrategy):
        self.strategy = strategy

    def process(self, amount):
        return self.strategy.execute(amount)
```

Register new strategies without modifying `PaymentProcessor`.

## Common Anti-Patterns

- **Type-Code Switch:** A central function with a growing switch/case on a type discriminator
- **Feature Envy Conditionals:** Methods that check the type of a collaborator to decide behavior — the behavior should live on the collaborator instead
- **Premature Closure:** Marking classes as `final`/`sealed` before understanding extension needs

## Design Guidance for New Code

Favor polymorphism over conditionals that branch on type. Design extension points (abstract methods, strategy parameters, event hooks) at variation boundaries. Use the Template Method pattern for algorithms with varying steps. When writing a conditional, ask: "Will I need to add more branches as the system grows?" If yes, refactor to polymorphism now.

## Edge Cases and Tradeoffs

- **Simple conditionals with a fixed, small set of cases** (e.g., boolean flags, enums with 2-3 values that won't grow) do not need polymorphism. The overhead of extra classes is not justified.
- **Performance-sensitive code** may benefit from direct conditionals over virtual dispatch. Profile before deciding.
- **Functional languages** achieve OCP through higher-order functions and pattern matching rather than inheritance — the principle still applies, just through different mechanisms.
- Not every conditional is an OCP violation. The test is whether the conditional will grow over time as requirements change.
