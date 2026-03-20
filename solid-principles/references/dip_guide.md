# Dependency Inversion Principle (DIP)

## Principle Statement

High-level modules should not depend on low-level modules. Both should depend on abstractions. Abstractions should not depend on details. Details should depend on abstractions.

## The Problem

When a high-level class directly instantiates or references a low-level concrete class, changes to the low-level class cascade upward. The high-level class cannot be tested in isolation, reused with different implementations, or reconfigured without modification. The dependency arrow points the wrong way — policy depends on mechanism instead of the other way around.

## Detection Heuristics

- Direct instantiation of concrete classes inside constructors (`self.db = MySQLDatabase()`)
- Constructors that take no parameters but use concrete collaborators internally
- Importing concrete implementation modules in high-level business logic
- `new ConcreteClass()` inside methods of other classes
- No interface or abstract type between a class and its dependencies
- Difficulty writing unit tests without connecting to real databases, APIs, or file systems

## Refactoring Patterns

**Before — direct dependency:**
```
class OrderService:
    def __init__(self):
        self.db = MySQLDatabase()
        self.mailer = SmtpEmailSender()

    def place_order(self, order):
        self.db.save(order)
        self.mailer.send_confirmation(order)
```

**After — dependency injection:**
```
class OrderService:
    def __init__(self, db: OrderRepository, mailer: EmailSender):
        self.db = db
        self.mailer = mailer

    def place_order(self, order):
        self.db.save(order)
        self.mailer.send_confirmation(order)
```

The abstractions (`OrderRepository`, `EmailSender`) are defined by the high-level module. Low-level modules (`MySQLDatabase`, `SmtpEmailSender`) implement them. The dependency arrow now points from concrete to abstract.

**Composition root** — wire dependencies at the entry point:
```
# main.py / composition root
db = MySQLDatabase(config.db_url)
mailer = SmtpEmailSender(config.smtp_host)
service = OrderService(db, mailer)
```

## Common Anti-Patterns

- **Hidden Dependencies:** A class uses concrete dependencies internally without declaring them — impossible to test or swap
- **Service Locator Abuse:** Using a global registry to look up dependencies hides the dependency graph and makes it implicit
- **Constructor Bloat from DI Abuse:** Injecting 10+ dependencies into a constructor — often a sign the class violates SRP too
- **New is Glue:** Every `new ConcreteClass()` inside a method is a hardcoded dependency that cannot be substituted

## Design Guidance for New Code

Accept dependencies through constructors or method parameters. High-level modules define the abstractions (interfaces) they need — they own the abstraction, not the low-level module. Use a composition root (typically `main()` or an application bootstrap) to wire concrete implementations. For tests, inject mocks or fakes through the same constructor parameters.

## Edge Cases and Tradeoffs

- **Value objects and data structures** (DTOs, configs, collections) can be instantiated directly — they are not "dependencies" in the DIP sense because they carry no behavior to vary.
- **Factory methods** are acceptable when the concrete type must be decided at runtime, but the factory itself should be injected.
- **Simple scripts and small programs** may not benefit from DIP — the added abstraction layer is not justified when there is only one implementation and no testing need.
- **Framework-managed dependencies** (e.g., Spring, Django, ASP.NET DI containers) handle wiring automatically. DIP still applies to the design even when the framework manages instantiation.
- **Not every class needs an interface.** Create abstractions at module boundaries and variation points, not for every concrete class. An interface with exactly one implementation and no test double is premature abstraction.
