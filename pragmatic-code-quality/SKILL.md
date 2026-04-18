---
name: pragmatic-code-quality
description: >
  Apply Pragmatic Programmer principles to raise code quality during planning and multi-class generation.
  TRIGGER AUTOMATICALLY (without user asking) whenever: (1) entering plan mode, (2) about to generate or
  modify 2+ files/classes in a single task, (3) creating new services, controllers, DTOs, or model classes,
  (4) scaffolding features or implementing approved plans. Run Phase 1 (Design Review) BEFORE writing code,
  and Phase 4 (Post-Generation Self-Review) AFTER writing code. For single-file edits, apply the Single-File
  Lint Check silently. This skill should be treated as a built-in quality gate, not an on-demand tool.
  IMPORTANT: Do not skip this skill during code generation even if the user does not explicitly invoke it.
---

# Pragmatic Code Quality

This skill guides Claude through a quality-minded planning and generation process rooted in the principles
from *The Pragmatic Programmer* by Andrew Hunt and David Thomas. The goal is to catch structural problems
*before* writing code, not refactor them afterward.

Code that repeats itself, that mixes concerns, or that makes callers reach into objects to extract state is
not just aesthetically bad — it creates fragility, slows future changes, and signals that the design hasn't
been fully thought through. This skill bakes in a brief but deliberate reflection pass at the right moments.

---

## When This Skill Applies

**This skill is a quality gate, not an on-demand tool. It triggers automatically.**

Activate this workflow whenever you are:

1. **In planning mode** — designing classes, modules, or service boundaries before writing
2. **Generating multiple files/classes at once** — 2 or more classes being written in the same task
3. **Implementing an approved plan** — translating a plan into code across multiple files
4. **Creating new services, controllers, models, or DTOs** — even if only one file, if it participates in a multi-class system

### Auto-Trigger Checklist

Before writing code, ask yourself:
- Am I about to create or modify 2+ files? → **Run Phase 1 before writing, Phase 4 after.**
- Am I implementing a plan with multiple phases? → **Run Phase 1 at the start of each phase.**
- Am I editing a single file? → **Apply Single-File Lint Check silently before committing the edit.**

The goal: every code generation pass gets at least a lightweight quality check. Phase 1 and Phase 4
do not need to be verbose — a few sentences per check is enough. The value is in *doing the pass*,
not in writing a report.

If only editing a single existing file, skip to the [Single-File Lint Check](#single-file-lint-check) at the end.

---

## Phase 1 — Design Review (Before Writing Anything)

Before generating any code, pause and answer these questions. They map directly to Pragmatic Programmer
principles. Write brief notes — even a sentence each — so the reasoning is visible.

### 1.1 DRY Check — Don't Repeat Yourself
> *"Every piece of knowledge must have a single, authoritative, unambiguous representation in the system."*

- Is there logic I'm about to write in more than one place?
- Are there any method bodies, validation rules, or configuration values that will be duplicated?
- Are similar data transformations being done separately in multiple classes?

**If yes:** Extract to a shared method, base class, helper, or constant before writing.

### 1.2 Orthogonality — One Reason to Change
> *"Two things are orthogonal if changes to one do not affect the other."*

- Does each class I'm about to create have a single, clearly-named responsibility?
- If I changed the database schema, would it ripple into business logic classes?
- If I changed an API contract, would it ripple into persistence or UI logic?

**If yes:** Introduce a boundary — an interface, a mapping layer, or a separate service.

### 1.3 Tell, Don't Ask
> *"Don't ask an object for its state in order to make decisions on its behalf — tell it what to do."*

- Am I writing logic where a caller will extract values from an object and act on them?
- Is any class becoming a passive data bag that callers have to manage?

**If yes:** Push that behavior into the object itself. Callers should command, not interrogate.

### 1.4 Law of Demeter — Limit the Chain
> *"Only talk to your immediate friends."*

- Am I writing `a.GetB().GetC().DoSomething()`?
- Will callers need to know the internal structure of an object to use it?

**If yes:** Add a method to the intermediate class that hides the chain.

### 1.5 Stable Abstractions
> *"Abstractions should not depend on details. Details should depend on abstractions."*

- Are higher-level classes importing or instantiating lower-level concrete classes directly?
- Are there dependencies that should be injected rather than constructed inline?

**If yes:** Define an interface at the boundary and inject the implementation.

### 1.6 Reversibility — Flag Load-Bearing Decisions
> *"There are no final decisions."*

- Am I making a structural choice that would be painful to undo later — a specific pattern, a coupling
  between layers, or a shape of abstraction that everything else will depend on?
- Is this decision load-bearing in a way the user may not realize?

**If yes:** Don't silently proceed. Call it out briefly — name the decision, note what it locks in, and
ask the user to confirm before building on top of it. They may say not to worry about it, and that's fine.
The goal is that consequential choices are visible, not invisible.

---

## Phase 2 — Tracer Bullet Outline

Before writing full implementations, sketch the structure as a brief outline:

```
[ClassName] — [one-sentence responsibility]
  depends on: [interfaces or abstractions it will use]
  exposes: [key public methods or properties]
  does NOT: [things it explicitly will not do, to guard scope]
```

This takes 2–4 minutes and often reveals overlap or missing abstractions before a single line is written.
Share this outline with the user if in a planning conversation, or use it as your own checklist before coding.

If the outline reveals that two planned classes share responsibility — merge or split them before proceeding.

---

## Phase 2.5 — Decomposition Threshold Check

Before splitting any responsibility into a new class or interface, apply this test. Decomposition
has a real cost: more files, more indirection, more cognitive load for the next developer. A new
class or interface is only justified if it clears at least **two** of these three bars:

1. **The responsibility has a name** — you can describe what the new class does in one clear sentence
   without using the word "and". If you can't name it cleanly, the split is probably wrong.

2. **The boundary will actually be crossed** — there is a realistic scenario where the implementation
   behind an interface would change, or where the extracted logic would be reused elsewhere. Interfaces
   for things that will never vary add indirection with no payoff.

3. **The caller is meaningfully simpler** — after the split, the calling class is noticeably easier to
   read and reason about. If the orchestrator ends up with 8 injected dependencies to do what one
   coherent class did before, the decomposition has gone too far.

### Concrete signals that decomposition has gone too far:

- A class exists only to wrap a single method call on another class
- An interface has exactly one implementation and no realistic prospect of a second
- The constructor takes more than ~4–5 injected dependencies (a sign the orchestrator is now the
  complexity sink that the individual classes used to be)
- You are creating a new file for fewer than ~15 lines of logic that will never be reused

### The right instinct:

Prefer **extraction within a class** (private methods, local helpers) over **splitting into new classes**
unless the decomposition threshold above is clearly met. A well-organized single class with good private
method names is almost always better than three anemic classes stitched together by an orchestrator.

---

## Phase 3 — Generation Rules

When writing code, apply these rules throughout:

- **Extract immediately.** If you notice you are writing the same logic twice, stop and extract it before continuing.
- **Name for intent.** Class and method names should describe *what*, not *how*. `ProcessPayment` not `RunStripeLogicAndUpdateDb`.
- **Small methods.** If a method exceeds ~35 lines, it is a candidate for extraction. This accounts for whitespace and blank lines used for readability between logical blocks — count intent, not formatting.
- **Minimal surface area.** Make things private by default. Only expose what callers actually need.
- **No surprise side effects.** A method named `GetUser` should not also update a timestamp. Side effects belong in explicitly named methods.
- **Constructors must not trigger behavior.** Constructors initialize state — they do not call async methods,
  hit the network, create files, or start background work. If a class needs setup that involves I/O or async
  operations, expose an explicit `InitializeAsync()` method. Fire-and-forget async calls in constructors
  create race conditions and make the class impossible to test reliably.
- **Name your serialization shapes.** When building objects to serialize for API calls or external boundaries,
  watch for anonymous objects (`new { Name = ..., Volume = ... }`). An anonymous object is an invisible,
  unreferenceable, unvalidatable implicit DTO. If you use the same shape twice, or the shape has more than
  3-4 properties, make it a named type. This gives you one inspectable object at the boundary — easier to
  debug, validate, and reuse. One anonymous object used once with a few fields is fine; two or more is a smell.
- **Don't outrun your headlights.** When generating multiple classes, produce the core class first and
  confirm the interface feels right before continuing to the dependent classes. Sprinting through 6 files
  speculatively and getting the shape wrong means 6 files to revisit. Take the first step, check in, then proceed.

---

## Phase 4 — Post-Generation Self-Review

After generating all planned classes, do a brief pass before presenting to the user:

Read each class and ask:
- [ ] Is any logic copy-pasted or structurally duplicated from another class?
- [ ] Does this class do more than one thing?
- [ ] Are there method chains that reach through multiple objects?
- [ ] Are there callers extracting state from objects to make decisions?
- [ ] Are concrete classes directly instantiated inside business logic?
- [ ] Do any constructors trigger side effects (async calls, I/O, network, file creation)?
- [ ] Are there anonymous serialization objects reused across methods or with 4+ properties?

If any box is checked, fix it before responding. If the fix would be significant, note it to the user and explain what you changed and why.

---

## Single-File Lint Check

For single-file edits, apply a lighter version. Before submitting changes, scan for:
- Any logic that is being duplicated from elsewhere in the file
- Methods longer than ~35 lines that could be extracted
- Any `tell, don't ask` violations introduced by your changes

---

## Cross-Reference: Guard Tests

This skill works alongside the **guard-tests** skill. When implementing features or fixing bugs:

### Tests Are Part of the Deliverable

The guard-tests skill ensures every bug fix, feature, and improvement is protected by tests. When pragmatic-code-quality activates for planning or generation, guard-tests activates in parallel to ensure:

- Bug fixes include a regression test that reproduces the bug scenario
- Features include tests covering happy path, edge cases, and error handling
- Refactors are preceded by characterization tests that pin existing behavior

### Testability Validates Design

Well-designed code (following DRY, Orthogonality, Tell Don't Ask) is naturally easier to test. If you're struggling to write tests, revisit the Phase 1 design review — the friction often indicates a principle violation.

| Hard to Test | Likely Principle Violated |
|--------------|---------------------------|
| Can't isolate the unit | Orthogonality — too many concerns mixed |
| Need to mock 6+ dependencies | Decomposition threshold exceeded |
| Must reach through object chains | Law of Demeter violation |
| Test requires complex setup | Tell, Don't Ask — logic in wrong place |

### Workflow

1. **Plan** with pragmatic-code-quality (design review, tracer bullet outline)
2. **Write** production code following generation rules
3. **Guard** with tests per guard-tests skill
4. **Review** both code and tests before marking complete


---

## Reference File

For deeper guidance on specific principles, see `references/tpp-principles.md`.
This includes expanded explanations, C#/TypeScript examples, and edge cases.
Read it when: you are uncertain whether a design decision violates a principle, or when the user asks
for justification of a refactoring suggestion.

---

## A Note on Judgment

These principles are tools, not laws. The goal is not mechanical compliance — it is writing code that
is easy to understand, change, and extend.

The most common failure mode when applying these principles mechanically is **premature decomposition**:
splitting code into more classes and interfaces than the problem warrants because it *feels* more
principled. It isn't. A 40-line class that does one thing well and is easy to read is better than
four 10-line classes connected by three interfaces that exist for their own sake.

When in doubt, stay with the simpler structure. Extract and split when the threshold in Phase 2.5 is
clearly met — not before.

**When genuinely unsure — ask.** If a design decision is ambiguous, a boundary feels unclear, or
applying a principle would require a meaningful structural choice, pause and ask the user before
proceeding. A short question is cheaper than a refactor. The user may tell you not to worry about
it — and that's a valid answer. Take it at face value and move on without over-explaining.
