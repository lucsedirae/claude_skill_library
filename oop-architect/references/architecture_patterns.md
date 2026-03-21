# Architecture Patterns

## MVC / MVP / MVVM Comparison

| Aspect | MVC | MVP | MVVM |
|--------|-----|-----|------|
| **View-Model link** | View reads Model directly | Presenter mediates all interaction | ViewModel exposes bindable state |
| **Controller/Presenter role** | Handles input, updates Model | Updates View and Model | ViewModel transforms Model data |
| **Data binding** | None | Manual | Two-way binding |
| **Best for** | Web backends, request/response | Desktop/mobile with testable UI | Reactive UI frameworks |

**Key principle across all three:** Separate presentation from domain logic. Views should not contain business rules. Models should not know about the UI.

## Repository Pattern

Abstracts data access behind a collection-like interface. The domain layer works with repositories as if they were in-memory collections, unaware of the underlying persistence mechanism.

**When to use:** Isolating domain logic from database queries. Enabling testability by swapping real repositories with in-memory fakes. Supporting multiple data sources behind a unified interface.

**Structure:** Define a repository interface in the domain layer. Implement it in the infrastructure layer. Inject the implementation at the composition root.

## Service Layer

Defines an application's boundary with a layer of services that establishes a set of available operations and coordinates the application's response to each operation.

**When to use:** Business operations span multiple domain objects. Controllers would otherwise contain business logic. The same operation is triggered from multiple entry points (API, CLI, events).

**Avoid when:** The application is simple CRUD — the overhead is not justified.

## Clean Architecture / Hexagonal Architecture

**Core principle (Dependency Rule):** Dependencies point inward. Inner layers define abstractions; outer layers implement them.

**Layers (inside to outside):**
1. **Entities / Domain:** Business rules and domain objects. No external dependencies.
2. **Use Cases / Application:** Application-specific business rules. Orchestrates entity behavior.
3. **Interface Adapters:** Controllers, presenters, gateways. Converts data between domain and external formats.
4. **Frameworks & Infrastructure:** Databases, web frameworks, external services. Implements interfaces defined by inner layers.

**Hexagonal (Ports & Adapters):** The domain defines "ports" (interfaces). External systems connect through "adapters" that implement these ports. Inbound adapters (controllers) drive the application. Outbound adapters (repositories, API clients) are driven by the application.

**Benefits:** Domain logic is testable without infrastructure. Infrastructure is swappable. The system's intent is visible from the use case layer.

## DDD Tactical Patterns

- **Entity:** An object with a distinct identity that persists through state changes (e.g., User, Order). Identity, not attributes, determines equality.
- **Value Object:** An immutable object defined by its attributes, not identity (e.g., Money, Address). Two Value Objects with the same attributes are equal.
- **Aggregate:** A cluster of Entities and Value Objects treated as a single unit for data changes. One Entity is the Aggregate Root — all external access goes through it. Aggregates enforce consistency boundaries.

**Guideline:** Keep Aggregates small. Reference other Aggregates by ID, not by direct object reference. Each transaction should modify at most one Aggregate.

## Layer Communication Rules

- Outer layers may depend on inner layers, never the reverse
- Each layer communicates with adjacent layers through defined interfaces
- Domain objects should not depend on frameworks, ORMs, or HTTP libraries
- Infrastructure concerns (logging, caching, transactions) belong in the outer layers or cross-cutting concerns
