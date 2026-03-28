# Obsolete Comment Patterns

A guide for distinguishing comments that should be removed from comments that carry genuine value. Use this when reviewing findings from `check_obsolete_comments.py` or when auditing comments manually.

---

## The Three Categories

### 1. Commented-Out Code

**Definition:** Source code that has been commented out rather than deleted.

**Why it is always removable:** Version control preserves every previous state. If the code is needed again, `git log` will find it. Commented-out code creates noise, confuses readers, and can silently drift out of sync with the surrounding codebase.

**How to confirm:**
- The comment lines contain code tokens: `=`, `{`, `}`, `->`, `;`, `return`, `if`, `for`, `class`, type keywords, `self.`, `this.`
- Two or more consecutive comment lines all contain code-like content
- The block appears to be a function, loop, or assignment rather than prose

**Examples of commented-out code (safe to remove):**
```python
# user = User.objects.get(id=user_id)
# if user.is_active:
#     send_welcome_email(user)
```

```java
// OrderService service = new OrderService(repo);
// Result result = service.process(order);
// if (!result.isSuccess()) { throw new RuntimeException(); }
```

---

### 2. Redundant (Restating) Comments

**Definition:** A comment that conveys no information beyond what the immediately adjacent code already says in the same words.

**The key test:** Does the comment answer *why* or does it only restate *what*? If "why," keep it. If only "what" — and the code already says "what" — remove it.

**Examples of REDUNDANT comments (safe to remove):**
```python
# increment counter
counter += 1

# call save
self.save()

# set name to empty string
name = ""
```

```javascript
// loop through users
for (const user of users) { ... }

// return result
return result;
```

**Examples of LEGITIMATE comments (do NOT remove):**
```python
# normalize to avoid floating-point drift in downstream comparisons
value = round(value, 6)

# per ISO 8601, weeks start on Monday (Python's weekday() returns 0)
day_index = date.weekday()

# retry once — the payment gateway returns 503 on the first attempt ~5% of the time
response = _call_with_retry(endpoint, payload, max_attempts=2)
```

The second group answers "why this specific approach" or "what non-obvious contract is being respected." Keep all such comments.

---

### 3. Noise Markers

**Definition:** Section dividers, decoration, or filler that add visual structure without carrying content.

```python
########################################
# -------- helper functions ---------- #
########################################
```

Flag these for removal unless the team has a documented style guide that mandates them. They are common in older codebases and tend to become stale (the section heading no longer matches the code below it).

---

## Language-Specific Edge Cases

These comment patterns look like candidates for removal but **must never be flagged**:

### Python
| Pattern | Reason to keep |
|---|---|
| `# type: ignore` | Suppresses mypy/pyright type errors — removing breaks type checking |
| `# noqa` / `# noqa: E501` | Suppresses linting errors — removing may fail CI |
| `# pragma: no cover` | Excludes coverage measurement |
| `# -*- coding: utf-8 -*-` | Encoding declaration for non-UTF-8 files |
| `#!/usr/bin/env python3` | Shebang — required for executable scripts |

### JavaScript / TypeScript
| Pattern | Reason to keep |
|---|---|
| `// @ts-ignore` | Suppresses TypeScript type errors |
| `// @ts-expect-error` | Documents expected type errors — removing breaks strict mode |
| `// eslint-disable` | Suppresses lint rules |
| `/* istanbul ignore next */` | Excludes coverage measurement |
| `// prettier-ignore` | Preserves intentional formatting |

### Java / Kotlin
| Pattern | Reason to keep |
|---|---|
| `// noinspection` | JetBrains IDE directive |
| `// @formatter:off` | Eclipse/IntelliJ formatting directive |
| `// <editor-fold>` | IDE code-folding markers — ask before removing |

### All Languages
| Pattern | Reason to keep |
|---|---|
| License headers | Legal requirement — never remove |
| Copyright notices | Legal requirement — never remove |
| File-level JSDoc / Javadoc / module docstrings | These are documentation, not noise |
| `TODO` / `FIXME` with issue links | Active work items — use `check_stale_todos.py` instead |

---

## Decision Checklist

Before recommending any comment for removal, verify:

- [ ] Is it a license, copyright, or legal notice? → **Keep**
- [ ] Is it a tooling directive (`noqa`, `@ts-ignore`, `eslint-disable`, `pragma`)? → **Keep**
- [ ] Is it an API doc comment (JSDoc, Javadoc, Python docstring, XML doc)? → **Keep**
- [ ] Does it explain *why* the code does something non-obvious? → **Keep**
- [ ] Does it warn about a known gotcha, edge case, or external dependency behavior? → **Keep**
- [ ] Is it a shebang or encoding declaration? → **Keep**
- [ ] Does it only restate what the adjacent code visibly does, word for word? → **Flag as redundant**
- [ ] Does it contain code tokens inside a comment line (=, {, keywords)? → **Flag as commented-out code**
- [ ] Is it a section divider with no meaningful content? → **Flag as noise**

When in doubt, keep the comment. False negatives (keeping a harmless comment) cost nothing. False positives (removing useful context) can introduce bugs or break tooling.
