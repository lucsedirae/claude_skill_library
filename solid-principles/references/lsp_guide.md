# Liskov Substitution Principle (LSP)

## Principle Statement

Objects of a superclass should be replaceable with objects of any subclass without altering the correctness of the program. If S is a subtype of T, then objects of type T may be replaced with objects of type S without breaking expectations.

## The Problem

When a subclass violates the contract established by its base class, code that depends on the base class breaks in subtle ways. A function expecting a `Rectangle` that receives a `Square` may produce wrong results if setting width independently of height is part of the contract. These bugs are insidious because the type system does not catch them — the code compiles and runs, but behaves incorrectly.

## Detection Heuristics

- Subclass methods that raise `NotImplementedError`, `UnsupportedOperationException`, or similar
- Overridden methods with empty bodies (`pass`, `{}`, `return`)
- Overridden methods that change the return type to something incompatible
- Overridden methods that add preconditions the base class does not enforce
- Overridden methods that throw exceptions the base class does not declare
- Client code that checks `isinstance()` before calling a method — suggests the subtype cannot be trusted to behave like the base type

## Refactoring Patterns

**Before — LSP violation:**
```
class Bird:
    def fly(self):
        # move through the air

class Ostrich(Bird):
    def fly(self):
        raise NotImplementedError("Ostriches can't fly")
```

**After — correct hierarchy:**
```
class Bird:
    def move(self): ...

class FlyingBird(Bird):
    def fly(self): ...

class Ostrich(Bird):
    def move(self):
        # run on the ground

class Sparrow(FlyingBird):
    def fly(self):
        # fly through the air
```

**Composition over inheritance:**
```
class Bird:
    def __init__(self, movement: MovementStrategy):
        self.movement = movement

    def move(self):
        self.movement.execute()
```

## Common Anti-Patterns

- **Refused Bequest:** A subclass inherits methods it cannot use, overriding them to throw or no-op
- **Square-Rectangle Problem:** Square inherits from Rectangle but cannot honor independent width/height modification
- **Collection Restriction:** ReadOnlyList inherits from List but refuses mutation operations
- **Downcasting in Client Code:** Checking the actual subtype before calling methods

## Design Guidance for New Code

Before creating an inheritance relationship, verify: "Can every subclass honor the full contract of the base class?" If a subclass would need to disable, restrict, or fundamentally change a base class method, the hierarchy is wrong. Prefer composition over inheritance when behavior varies across subtypes. Design base classes with the weakest useful contract — promise only what every subtype can deliver.

## Edge Cases and Tradeoffs

- **Abstract base classes with all abstract methods** are typically safe — they define a contract without implementation to violate.
- **Template Method pattern** is LSP-friendly when the invariant steps are in the base class and only hook methods are overridden.
- **Covariant return types** (returning a more specific type in the subclass) are acceptable and do not violate LSP in most languages.
- **Contravariant parameters** (accepting a broader type in the subclass) are acceptable — the subclass handles more, not less.
- In dynamically typed languages, LSP is about behavioral compatibility, not type signatures. A duck-typed subclass satisfies LSP if it responds to the same messages with compatible behavior.
