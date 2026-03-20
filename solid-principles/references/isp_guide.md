# Interface Segregation Principle (ISP)

## Principle Statement

No client should be forced to depend on interfaces it does not use. Create small, focused interfaces rather than large, general-purpose ones.

## The Problem

When an interface is too broad, implementing classes are forced to provide methods they do not need. This leads to no-op implementations, `NotImplementedError` stubs, and unnecessary coupling. Clients that depend on the interface are also coupled to methods they never call, making the system harder to change and test.

## Detection Heuristics

- Interfaces or abstract classes with 5+ abstract methods
- Implementing classes with no-op method bodies (`pass`, `return None`, empty `{}`)
- Implementing classes that raise `NotImplementedError` for some inherited methods
- Clients that use only a fraction of an interface's methods
- A single interface serving multiple unrelated client types
- Frequent "partial implementations" across multiple classes

## Refactoring Patterns

**Before — fat interface:**
```
class Worker:
    def work(self): ...
    def eat(self): ...
    def sleep(self): ...
    def attend_meeting(self): ...
    def file_report(self): ...
```

A `Robot` implementing `Worker` must stub `eat()` and `sleep()`.

**After — segregated interfaces:**
```
class Workable:
    def work(self): ...

class Feedable:
    def eat(self): ...

class Restable:
    def sleep(self): ...

class Reportable:
    def attend_meeting(self): ...
    def file_report(self): ...

class HumanWorker(Workable, Feedable, Restable, Reportable):
    # implements all

class Robot(Workable):
    # implements only what it needs
```

**Role interfaces** — name interfaces after the role clients expect:
```
class Printable:
    def print(self): ...

class Saveable:
    def save(self): ...

class Exportable:
    def export(self, format): ...
```

Each client depends only on the role it needs.

## Common Anti-Patterns

- **Fat Interface:** A single interface with methods spanning multiple concerns
- **Header Interface:** An interface that mirrors a concrete class's entire public API instead of representing a role
- **Forced Implementation:** Classes that implement an interface solely because of one or two methods, dragging along stubs for the rest
- **God Interface:** An interface used by every module in the system, making it impossible to change without cascading effects

## Design Guidance for New Code

Design interfaces from the client's perspective: what does the caller need? Create one interface per role or capability. Prefer multiple small interfaces that a class can compose over one large interface. Name interfaces after what they provide (`Readable`, `Serializable`, `Cacheable`), not after the implementing class.

## Edge Cases and Tradeoffs

- **Languages without interfaces** (e.g., Python, Ruby) achieve ISP through abstract base classes, protocols, or duck typing. The principle still applies: keep abstract contracts small and focused.
- **Single-method interfaces** (functional interfaces) are ideal for ISP. In languages with first-class functions, a callback or lambda may be simpler than a one-method interface.
- **Over-segregation** creates an explosion of tiny interfaces that are hard to discover and compose. If methods always appear together in every client, they belong in one interface.
- **Marker interfaces** (interfaces with no methods) are a valid ISP-compliant pattern for tagging capabilities.
- In practice, 3-5 methods per interface is a reasonable sweet spot for most domains.
