---
name: guard-tests
description: >
  Proactively write regression and guard tests when fixing bugs, implementing features, or making improvements.
  ALWAYS trigger this skill when Claude Code is: fixing a bug, resolving an issue, implementing a feature,
  refactoring code, making an improvement, or modifying existing behavior. Also trigger when the user mentions
  "fix", "bug", "feature", "improvement", "refactor", "change", "update", "modify", or references a ticket/issue.
  This skill ensures every code change is protected by tests that guard against regressions. Do NOT skip this
  skill just because the user didn't explicitly ask for tests — test coverage is required, not optional.
---

# Guard Tests Skill

Write regression and guard tests for every bug fix, feature, and improvement. Tests are not optional — they are a required deliverable alongside the code change.

## Core Principle

**Every code change must be guarded by tests.** Before considering any fix, feature, or improvement complete, a test must exist that:
- For bugs: Reproduces the bug scenario and verifies it no longer occurs
- For features: Verifies the feature works as specified
- For improvements/refactors: Captures existing behavior to ensure it remains unchanged

## When This Skill Applies

This skill activates automatically whenever you are:
- Fixing a bug or defect
- Implementing a new feature or requirement
- Refactoring or restructuring code
- Making performance improvements
- Modifying existing behavior
- Addressing a ticket, issue, or user-reported problem

**You do not need to be asked to write tests.** Tests are part of the deliverable.

## Test Timing: Flexible Approach

Choose the appropriate timing based on context:

### Test-First (Preferred for Bugs)
1. Write a failing test that reproduces the bug
2. Run the test to confirm it fails (proves the bug exists)
3. Implement the fix
4. Run the test to confirm it passes (proves the bug is fixed)

This approach is especially valuable for bugs because it:
- Proves you understand the bug before fixing it
- Guarantees the specific scenario is covered
- Prevents "fix the symptom, miss the cause" mistakes

### Test-After (Acceptable for Features/Improvements)
1. Implement the feature or improvement
2. Write tests that verify the new behavior
3. Run tests to confirm they pass

This approach works when:
- The implementation helps clarify what tests are needed
- You're exploring a solution and tests would slow discovery
- The feature involves UI or integration that's easier to test after seeing it work

### Refactor Safety Net
1. Write characterization tests that capture current behavior
2. Make the refactor
3. Run tests to confirm behavior is unchanged

## Test Requirements

### For Bug Fixes
Write a test that:
- Sets up the exact conditions that triggered the bug
- Performs the action that caused the failure
- Asserts the correct behavior (not the buggy behavior)
- Includes a comment or test name referencing the bug (e.g., ticket number, brief description)

```csharp
[Fact]
public void CalculateTotal_WithNullLineItems_ReturnsZero_NotNull()
{
    // Regression: Previously threw NullReferenceException when LineItems was null
    var order = new Order { LineItems = null };
    
    var result = order.CalculateTotal();
    
    Assert.Equal(0m, result);
}
```

### For Features
Write tests that:
- Cover the happy path (feature works as expected)
- Cover edge cases relevant to the feature
- Cover at least one error/invalid input scenario

### For Refactors
Write tests that:
- Pin down current behavior before changing structure
- Cover the public API being refactored
- Remain unchanged after the refactor (proving behavior is preserved)

## .NET Testing Guidelines

Use the project's existing test framework. If none exists or you're starting fresh, prefer **xUnit** for new .NET projects.

### Project Structure
```
src/
  MyProject/
    Services/
      OrderService.cs
tests/
  MyProject.Tests/
    Services/
      OrderServiceTests.cs
```

### Test Naming
Use descriptive names that document the scenario:
```
[MethodUnderTest]_[Scenario]_[ExpectedOutcome]
```

Examples:
- `ProcessPayment_WithExpiredCard_ThrowsPaymentException`
- `GetUser_WhenUserNotFound_ReturnsNull`
- `CalculateDiscount_WithPremiumMember_AppliesTwentyPercent`

### Arrange-Act-Assert Pattern
```csharp
[Fact]
public void MethodName_Scenario_ExpectedOutcome()
{
    // Arrange
    var service = new OrderService();
    var order = new Order { /* setup */ };
    
    // Act
    var result = service.ProcessOrder(order);
    
    // Assert
    Assert.NotNull(result);
    Assert.Equal(OrderStatus.Completed, result.Status);
}
```

### Common Assertions
```csharp
Assert.Equal(expected, actual);
Assert.NotNull(result);
Assert.True(condition);
Assert.False(condition);
Assert.Throws<ExceptionType>(() => action());
Assert.Contains(item, collection);
Assert.Empty(collection);
```

### Mocking Dependencies
Use Moq or NSubstitute for mocking:
```csharp
[Fact]
public void ProcessOrder_CallsPaymentGateway()
{
    // Arrange
    var mockGateway = new Mock<IPaymentGateway>();
    mockGateway.Setup(g => g.Charge(It.IsAny<decimal>()))
               .Returns(new PaymentResult { Success = true });
    
    var service = new OrderService(mockGateway.Object);
    
    // Act
    service.ProcessOrder(new Order { Total = 100m });
    
    // Assert
    mockGateway.Verify(g => g.Charge(100m), Times.Once);
}
```

## Completion Checklist

Before marking any bug fix, feature, or improvement as complete, verify:

- [ ] **Test exists**: At least one test guards this change
- [ ] **Test is specific**: The test targets the exact scenario being addressed
- [ ] **Test would have caught it**: For bugs, the test would fail on the old code
- [ ] **Test runs green**: The test passes with the new code
- [ ] **Test is in the right place**: Test file follows project conventions

## Firm Guidance

**Do not skip tests.** If you're about to say "the fix is complete" without having written a test, stop. Write the test first.

If a test seems difficult to write, that's often a sign the code needs refactoring for testability. Consider:
- Extracting dependencies to interfaces
- Breaking down large methods
- Separating pure logic from I/O

If truly untestable (e.g., deep framework integration), document why and suggest what manual verification should be done.

## Communicating About Tests

When presenting your work, always include the test:

```
## Bug Fix: NullReferenceException in CalculateTotal

**Problem**: `CalculateTotal()` threw when `LineItems` was null.

**Fix**: Added null check with early return of 0.

**Guard Test**: `CalculateTotal_WithNullLineItems_ReturnsZero_NotNull`
- Reproduces the null scenario
- Verifies we return 0 instead of throwing
- Will catch this regression if it's ever reintroduced
```

This makes it clear that the change is properly guarded.

---

## Cross-Reference: Pragmatic Code Quality

This skill works alongside the **pragmatic-code-quality** skill. When writing tests:

### Apply Pragmatic Principles to Test Code

- **DRY in tests**: Extract shared setup into helper methods or fixtures — don't copy-paste arrangement logic across tests
- **Orthogonality**: Each test should verify one behavior. If a test fails, you should know exactly what broke.
- **Tell, Don't Ask**: Tests should call methods and assert outcomes, not extract internal state to verify
- **Small methods**: Test methods should be concise. If a test exceeds ~20 lines, consider extracting setup helpers.

### When Both Skills Apply

When fixing a bug or implementing a feature:

1. **pragmatic-code-quality** guides the *production code* — design review, DRY checks, decomposition thresholds
2. **guard-tests** ensures the *test code* exists — regression tests, guard tests, completion checklist

Both skills activate together. Write well-structured production code *and* guard it with tests.

### Testability as a Design Signal

If code is hard to test, that's often a signal that pragmatic principles are being violated:

| Hard to Test | Likely Principle Violated |
|--------------|---------------------------|
| Can't isolate the unit | Orthogonality — too many concerns mixed |
| Need to mock 6+ dependencies | Decomposition gone too far, or missing abstraction |
| Must reach through object chains | Law of Demeter violation |
| Test requires complex setup | Tell, Don't Ask — logic in wrong place |

When you hit testability friction, consult pragmatic-code-quality to refactor the production code first, *then* write the test.
