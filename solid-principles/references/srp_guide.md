# Single Responsibility Principle (SRP)

## Principle Statement

A class should have only one reason to change. Each class encapsulates a single responsibility — one axis of change driven by one actor or stakeholder.

## The Problem

When a class handles multiple responsibilities, a change to one responsibility risks breaking another. Testing becomes harder because unrelated concerns are entangled. The class grows into a "God Object" that everyone depends on, creating a bottleneck for modification and a magnet for merge conflicts.

## Detection Heuristics

- Class has more than 10 public methods
- Class exceeds 200 lines
- Method names cluster into distinct verb groups (e.g., `save_*`, `render_*`, `send_*`)
- Class imports from many unrelated modules
- Class description requires the word "and" (e.g., "manages orders and sends emails")
- Multiple developers frequently modify the same class for unrelated reasons

## Refactoring Patterns

**Before — mixed responsibilities:**
```
class UserManager:
    validate_user(data)
    save_to_database(user)
    send_welcome_email(user)
    generate_report(user)
    log_activity(user, action)
```

**After — single responsibility each:**
```
class UserValidator:
    validate(data)

class UserRepository:
    save(user)

class WelcomeMailer:
    send(user)

class UserReporter:
    generate(user)

class ActivityLogger:
    log(user, action)
```

Coordinate these through a higher-level service or use case class that delegates to each.

## Common Anti-Patterns

- **God Class:** A single class that handles business logic, persistence, validation, and presentation
- **Manager/Handler/Processor suffix:** Often a sign the class has accumulated unrelated duties
- **Utility class bloat:** Static utility classes that grow unbounded because "it's just a helper"

## Design Guidance for New Code

Identify the single axis of change before writing a class. Name the class after its one responsibility. If the class description requires "and," split it. Ask: "Who is the one actor that would request changes to this class?" If the answer is multiple actors, the class has multiple responsibilities.

## Edge Cases and Tradeoffs

- **Simple data classes / DTOs** do not need decomposition — holding data is their single responsibility.
- **Facade classes** that coordinate multiple subsystems may appear to violate SRP but have a single responsibility: orchestration. The key is that they delegate rather than implement.
- **Over-splitting** creates excessive indirection. If two behaviors always change together for the same reason, they belong in the same class.
- In small projects or prototypes, strict SRP may add unnecessary complexity. Apply proportionally to the scale and expected lifetime of the code.
