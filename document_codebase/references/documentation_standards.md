# Documentation Standards for Agent-Discoverable Code

A guide for writing and evaluating docstrings and doc comments. Use this when `check_doc_coverage.py` reports missing documentation or when assessing whether existing documentation is adequate.

---

## Why Documentation Matters for Agents

Agents navigate codebases by searching for relevant functions and types. A well-documented symbol is discoverable in two ways: its name is semantically meaningful, and its docstring provides enough context for the agent to determine whether it is the right symbol without reading the full implementation.

A docstring that only restates the function name adds noise and should be rewritten or removed. A docstring that explains the contract, constraints, and non-obvious behavior multiplies the value of the code for both agents and human readers.

---

## Minimum Viable Docstring

A docstring earns its place if it answers at least one of:

- What contract does this function fulfill? (not just what it does mechanically)
- What does the caller need to know that is not obvious from the signature?
- What side effects, exceptions, or edge behavior exist?
- What is the expected shape of the input or output when the type alone is ambiguous?

**A docstring that only restates the function name should be rewritten or removed.**

---

## Python (Google Style — preferred for agent readability)

### Function
```python
def process_payment(amount: Decimal, currency: str) -> PaymentResult:
    """Submit a payment request to the configured payment gateway.

    Idempotent when called with the same idempotency_key. Raises
    PaymentDeclinedError if the gateway rejects the charge; raises
    GatewayTimeoutError after 10 seconds with no response.

    Args:
        amount: Charge amount in the specified currency's minor units.
        currency: ISO 4217 three-letter currency code (e.g., "USD").

    Returns:
        PaymentResult with transaction_id and status fields populated.

    Raises:
        PaymentDeclinedError: When the gateway explicitly rejects the charge.
        GatewayTimeoutError: When the gateway does not respond within 10 s.
    """
```

### Class
```python
class OrderRepository:
    """Persistence layer for Order aggregates.

    All mutations go through save(); reads use get_by_id() or list_by_status().
    Does not enforce authorization — callers are responsible for access control.
    """
```

### Module
```python
"""Order processing domain.

Contains the Order aggregate, OrderRepository, and OrderService.
Import OrderService for all business logic; use OrderRepository
directly only in tests or migrations.
"""
```

---

## JavaScript / TypeScript (JSDoc)

```typescript
/**
 * Validates and normalizes a user-supplied address against the postal service API.
 *
 * Caches results for 24 hours to limit API calls. Returns null when the address
 * cannot be validated rather than throwing, so callers must check the return value.
 *
 * @param address - Raw address as entered by the user.
 * @returns Normalized address, or null if the address cannot be validated.
 * @throws {ValidationError} When the address is structurally invalid before
 *   reaching the postal service (e.g., missing country code).
 */
async function validateAddress(address: RawAddress): Promise<Address | null>
```

---

## C# (XML Documentation)

```csharp
/// <summary>
/// Calculates the effective discount for an order, applying promotional
/// rules in priority order. Returns zero when no rules apply.
/// </summary>
/// <param name="order">The order to evaluate. Must not be null.</param>
/// <returns>
/// Discount amount in the order's currency, always greater than or equal to zero.
/// Never exceeds the order total.
/// </returns>
/// <exception cref="ArgumentNullException">
/// Thrown when <paramref name="order"/> is null.
/// </exception>
public decimal CalculateDiscount(Order order)
```

---

## Java (Javadoc)

```java
/**
 * Resolves the effective permissions for a user in the given request context.
 *
 * <p>Permissions are evaluated lazily and cached per request. The cache is
 * invalidated when the user's role assignments change.
 *
 * @param userId  the ID of the user whose permissions to resolve
 * @param context the request context containing tenant and session info
 * @return an immutable set of permission strings; never null, may be empty
 * @throws UserNotFoundException if {@code userId} does not correspond to
 *         an existing user
 */
public Set<String> resolvePermissions(String userId, RequestContext context)
```

---

## Go (exported-name convention)

Go convention: the doc comment for an exported symbol starts with the symbol's name.

```go
// ProcessPayment submits a payment request to the configured gateway.
// It is idempotent for the same idempotencyKey. Returns ErrDeclined if
// the gateway rejects the charge or ErrTimeout after 10 seconds.
func ProcessPayment(amount int64, currency string) (*PaymentResult, error)

// OrderRepository handles persistence for Order aggregates.
// All writes go through Save; reads use GetByID or ListByStatus.
type OrderRepository struct { ... }
```

---

## Common Documentation Anti-Patterns

### Signature Restatement (rewrite or remove)
```python
def add(x: int, y: int) -> int:
    """Adds x and y and returns the result."""  # useless — remove
```

### Vague Intent (rewrite)
```python
def process(data):
    """Process the data."""  # says nothing — rewrite
```

### Outdated Contract (fix)
```python
def get_user(id: int) -> dict:
    """Returns a User object."""  # wrong type — fix to match reality
```

### Stale Parameter Docs (fix)
```java
/**
 * Connect to the host.
 * @param host the host to connect to
 * @param port the port number
 * (timeout was added later and is undocumented)
 */
void connect(String host, int port, int timeout)
```

### Prose That Repeats the Name (rewrite)
```typescript
/** This function handles the user login. */
function handleUserLogin(credentials: Credentials): Promise<Session>
// Better: describe the contract — what it returns, what can go wrong
```

---

## Minimum Documentation Checklist

For each public symbol, check before marking it documented:

- [ ] Name clearly describes what the function, class, or module does
- [ ] Docstring present (or the name is genuinely self-documenting with zero gotchas)
- [ ] Parameters documented when units, allowed values, or constraints matter beyond the type
- [ ] Return value documented when it can be null/undefined/None or has multiple shapes
- [ ] Exceptions or errors documented when callers must handle specific failure modes
- [ ] Side effects noted when the function mutates shared state, writes to disk, or makes network calls
- [ ] Non-obvious preconditions or invariants stated explicitly
