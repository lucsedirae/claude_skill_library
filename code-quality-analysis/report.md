Synthesize, deduplicate, rank. Use this exact block per finding:

### N. [Title] [HIGH | MEDIUM | LOW]
**Principle / concern**: SRP / OCP / LSP / ISP / DIP / Discoverability
**Observation**: One sentence. What is, not what should be.
**Evidence**: `file:line` references, ≥2 where the pattern repeats.
**Divergence**: How many variants exist, rough split.
**Agent impact**: What would an agent get wrong reading only part of the codebase?
**Session context**: *(omit if not applicable)* Note if this finding is in code touched by recent commits, or if a partial fix was applied in a prior session that left this behind.

Severity:
- HIGH — wrong assumption propagates to new code; or SOLID violation that compounds.
- MEDIUM — resolvable by reading multiple files; fix is local once resolved.
- LOW — cosmetic, isolated, easily inferred.

Before promoting a finding to HIGH, confirm: does the cited code actually contain the smell when read in full? Findings inferred from file names, directory placement, or function signatures without reading the body must be downgraded to MEDIUM or discarded.

End with a one-paragraph meta-summary: what is the dominant shape of the debt?

Do not propose solutions here. If patterns are evenly split, flag it as a team decision, not a violation.
