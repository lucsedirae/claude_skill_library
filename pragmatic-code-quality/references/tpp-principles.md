# The Pragmatic Programmer — Principles Reference

This file provides expanded explanations and code examples for the principles used in the
`pragmatic-code-quality` skill. Read specific sections as needed rather than loading the whole file.

## Table of Contents

1. [DRY — Don't Repeat Yourself](#1-dry)
2. [Orthogonality](#2-orthogonality)
3. [Tell, Don't Ask](#3-tell-dont-ask)
4. [Law of Demeter](#4-law-of-demeter)
5. [Stable Abstractions / DIP](#5-stable-abstractions)
6. [Design by Contract](#6-design-by-contract)
7. [Tracer Bullets vs Prototypes](#7-tracer-bullets)

---

## 1. DRY

**Definition:** Every piece of *knowledge* — not just every line of code — should have one authoritative
representation. DRY is violated not just by copy-paste but by parallel structures that express the same rule.

**Common violations in generated code:**
- Validation logic duplicated in a service and a controller
- The same mapping between two types written in two different methods
- Constants written as magic literals in multiple places
- Similar query filters repeated across repository methods

**C# example — DRY violation:**
```csharp
// In OrderService:
if (order.Status == "pending" && order.CreatedAt < DateTime.UtcNow.AddDays(-7))
    throw new InvalidOperationException("Order expired");

// In OrderController (duplicated check):
if (order.Status == "pending" && order.CreatedAt < DateTime.UtcNow.AddDays(-7))
    return BadRequest("Order expired");
```

**Fix:** Express this rule once, in the domain:
```csharp
public class Order
{
    public bool IsExpired =>
        Status == OrderStatus.Pending && CreatedAt < DateTime.UtcNow.AddDays(-7);
}
```

**TypeScript example — DRY violation:**
```typescript
// In two different Vue composables:
const isAdmin = user.roles.includes('admin') || user.roles.includes('superuser');
// ... repeated in another file
const canEdit = user.roles.includes('admin') || user.roles.includes('superuser');
```

**Fix:**
```typescript
// permissions.ts
export const isPrivilegedUser = (user: User) =>
  user.roles.includes('admin') || user.roles.includes('superuser');
```

---

## 2. Orthogonality

**Definition:** Two components are orthogonal if changing one does not require changing the other.
Non-orthogonal systems are fragile — a change in one place breaks something elsewhere unexpectedly.

**Signs of poor orthogonality:**
- A service class that queries a database AND sends emails AND formats a response
- A repository that applies business rules
- A UI component that makes direct API calls and transforms domain data

**C# example — poor orthogonality:**
```csharp
public class InvoiceService
{
    public async Task ProcessInvoice(Invoice invoice)
    {
        // DB concern
        await _db.SaveAsync(invoice);
        // Email concern
        await _mailer.SendAsync(invoice.CustomerEmail, "Invoice processed");
        // Formatting concern
        var pdf = _pdfRenderer.Render(invoice);
        await _storage.UploadAsync(pdf);
    }
}
```

**Fix:** Each responsibility gets its own class. `InvoiceProcessor` orchestrates, delegates to
`InvoiceRepository`, `InvoiceNotifier`, and `InvoiceDocumentService`.

---

## 3. Tell, Don't Ask

**Definition:** Rather than asking an object for its data and making decisions with it externally,
tell the object what outcome you want and let it manage its own state.

**The smell:** A caller that reads fields from an object and then uses if-statements to decide
what to do to that object. This suggests the logic belongs *inside* the object.

**C# violation:**
```csharp
if (account.Balance >= amount && account.IsActive && !account.IsFrozen)
{
    account.Balance -= amount;
    account.LastTransactionDate = DateTime.UtcNow;
}
```

**Fix:**
```csharp
account.Withdraw(amount); // Account enforces its own rules internally
```

**TypeScript violation:**
```typescript
if (cart.items.length > 0 && cart.coupon !== null && cart.coupon.isValid) {
    cart.total = cart.total - (cart.total * cart.coupon.discountPercent / 100);
}
```

**Fix:**
```typescript
cart.applyCoupon(coupon); // Cart decides whether and how to apply it
```

---

## 4. Law of Demeter

**Definition:** A method should only call methods on:
- Itself
- Its direct parameters
- Objects it creates
- Its direct component/field objects

It should NOT call methods on objects *returned* by another call.

**The smell:** Method chains: `a.GetB().GetC().DoThing()`

**C# violation:**
```csharp
var city = order.GetCustomer().GetAddress().GetCity();
```

**Fix:** Add a delegation method:
```csharp
// On Order:
public string CustomerCity => _customer.City; // Customer handles its own address
```

**Important nuance:** Fluent APIs and LINQ chains are deliberate design choices and are not
Demeter violations. `list.Where(...).Select(...).ToList()` is fine — these all operate on
the same conceptual object (a sequence). Demeter applies to *reaching into collaborating objects*.

---

## 5. Stable Abstractions

**Definition (Dependency Inversion Principle):**
- High-level modules should not depend on low-level modules — both should depend on abstractions
- Abstractions should not depend on details — details should depend on abstractions

**The smell:** Business logic classes that `new` up infrastructure classes (repositories, HTTP clients,
email senders) directly, or import concrete implementations.

**C# violation:**
```csharp
public class OrderService
{
    private readonly SqlOrderRepository _repo = new SqlOrderRepository(); // hard dependency
}
```

**Fix:**
```csharp
public class OrderService
{
    private readonly IOrderRepository _repo;
    public OrderService(IOrderRepository repo) => _repo = repo;
}
```

**TypeScript/Vue violation:**
```typescript
// Composable directly instantiating an axios instance and calling an endpoint
import axios from 'axios';
const { data } = await axios.get('/api/orders');
```

**Fix:** Inject or import an `OrderApiService` that encapsulates the HTTP call. The composable
depends on the service contract, not the HTTP library.

---

## 6. Design by Contract

**Definition:** Define explicit agreements about what a method expects (preconditions), what it
guarantees (postconditions), and what must always be true (invariants).

This is less about formal contract libraries and more about making assumptions explicit.

**In practice:**
- Guard clauses at the top of methods that make preconditions visible
- Return types that encode success/failure rather than throwing unexpectedly
- Naming that communicates guarantees (`GetOrThrow` vs `TryGet`)

**C# example:**
```csharp
public Order GetById(Guid id)
{
    // Precondition explicit
    ArgumentException.ThrowIfNullOrEmpty(id.ToString());

    var order = _db.Find(id);

    // Postcondition: caller is told what to expect
    return order ?? throw new NotFoundException($"Order {id} not found");
}
```

---

## 7. Tracer Bullets vs Prototypes

**Tracer Bullet:** A thin, working, end-to-end slice of the real system. Used to validate architecture.
Code is kept. Think: "get one feature working across all layers" before fleshing out.

**Prototype:** Exploratory throwaway code to answer a specific question. Code is discarded.

**When generating multi-class code**, prefer the tracer bullet mindset: sketch real class shapes with
correct names and interfaces but minimal implementations, then fill in. Avoid the prototype trap of
writing dense implementation without first validating the structural connections.
