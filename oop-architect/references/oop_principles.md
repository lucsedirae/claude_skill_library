# Core OOP Principles

## Encapsulation

Encapsulation has two aspects:

- **Data hiding:** Restricting direct access to an object's internal state. Use private/protected fields with public methods (getters/setters only when necessary).
- **Information hiding:** Concealing implementation details so clients depend on behavior, not structure. A well-encapsulated class can change its internals without affecting clients.

**Violation indicators:** Public mutable fields. Classes that expose internal data structures (returning mutable references to internal lists/maps). Clients that manipulate an object's state directly instead of asking the object to perform operations.

## Polymorphism

- **Subtype polymorphism:** Objects of different types respond to the same interface. The foundation of OOP extensibility.
- **Method overriding:** Subclass provides specific implementation of a method defined in its superclass.
- **Method overloading:** Same method name with different parameter types/counts (compile-time, not all languages support it).
- **Parametric polymorphism:** Generics/templates — code that works with any type satisfying constraints.

**Best practice:** Program to interfaces, not implementations. Declare variables and parameters using the most general type that provides the needed behavior.

## Abstraction

Choose the right level of abstraction — too high loses essential detail, too low exposes implementation. An abstraction should represent a concept in the problem domain, not an implementation convenience.

**Signs of wrong abstraction level:** Clients frequently need to downcast. The abstraction leaks implementation details through its API. The abstraction forces unrelated concepts together.

## Coupling

Coupling measures how strongly one module depends on another.

- **Afferent coupling (Ca):** Number of modules that depend on this module (incoming)
- **Efferent coupling (Ce):** Number of modules this module depends on (outgoing)
- **Instability:** Ce / (Ca + Ce) — ranges from 0 (maximally stable) to 1 (maximally unstable)

**Goal:** Loose coupling. Modules should depend on abstractions, not concrete implementations. Changes in one module should minimally affect others.

**Tight coupling indicators:** Class references 8+ concrete classes. A change in one class requires changes in many others. Cannot test a class without instantiating its dependencies.

## Cohesion

Cohesion measures how strongly related the responsibilities within a module are.

**Types (best to worst):**
1. **Functional:** All elements contribute to a single well-defined task
2. **Sequential:** Output of one element serves as input to the next
3. **Communicational:** Elements operate on the same data
4. **Procedural:** Elements are related by the order of execution
5. **Temporal:** Elements are related by when they execute
6. **Logical:** Elements are related by a broad category
7. **Coincidental:** Elements have no meaningful relationship

**Goal:** High functional cohesion. Each class should have a clear, singular purpose.

**Low cohesion indicators (LCOM):** Methods within a class access disjoint sets of instance attributes. The class can be split into two independent classes without any method needing fields from both halves.

## Common Anti-Patterns

- **God Object:** A class that knows too much or does too much — high coupling, low cohesion
- **Feature Envy:** A method that accesses data from another class more than its own — the method should move to the other class
- **Inappropriate Intimacy:** Two classes that access each other's private details — merge them or introduce an intermediary
- **Shotgun Surgery:** A single change requires modifications across many classes — consolidate related behavior
