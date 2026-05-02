Synthesize, deduplicate, and rank findings across all four lenses.

**Cross-lens merge rule**: If the same file, module, or pattern appears in findings from multiple agents, merge them into one block. List all applicable lenses and principles in that block's fields. Do not report the same location twice under different headings.

Use this exact block per finding:

### N. [Title] [HIGH | MEDIUM | LOW]
**Lens**: SOLID-Backend / SOLID-Frontend / Discoverability / Orphan
**Principle / concern**: SRP / OCP / LSP / ISP / DIP / Pattern legibility / Naming / Convergence / Orphan — [type: route | code | file | test | asset]
**Observation**: One sentence. What is, not what should be.
**Evidence**: `file:line` references, ≥2 where the pattern repeats. Single citation acceptable for unique orphan findings.
**Divergence**: How many variants or instances exist, rough split. (Omit for orphan findings with a single instance.)
**Agent impact**: What would a new agent get wrong reading only part of the codebase?
**Session context**: *(omit if not applicable)* Note if this finding is in code touched by recent commits, or if a partial fix was applied in a prior session that left this behind.

---

**Severity guidance**

- **HIGH** — a wrong assumption propagates to new code; a SOLID violation that compounds; an orphan that is large, central, or likely to be mistaken for live code by an agent.
- **MEDIUM** — resolvable by reading multiple files; fix is local once identified; an orphan that is small or at the project periphery.
- **LOW** — cosmetic, isolated, easily inferred; trivially removable asset or commented-out line.

Before promoting any finding to HIGH, confirm: does the cited code actually contain the smell when read in full? Findings inferred from file names, directory placement, or function signatures without reading the body must be downgraded to MEDIUM or discarded.

For orphan findings, confirm with a second-pass grep before assigning HIGH.

---

End with a one-paragraph **meta-summary**: what is the dominant shape of the debt across all four lenses?

Do not propose solutions in the report. If patterns are evenly split, flag it as a team decision, not a violation.
