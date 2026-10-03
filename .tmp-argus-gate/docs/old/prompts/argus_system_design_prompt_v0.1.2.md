# argus Human System Design Generation Prompt v0.1.2

## Purpose and inputs

Generate a complete human-readable IT system design from the v0.1.2 Structured Design Model and the canonical artifacts named by it. Use System Design v0.1 as the base view. Do not treat this prompt as design authority and do not add unsupported design facts.

## Coverage, not compression

Do not reduce the document to an overview. Preserve the horizontal design range: purpose/background, overall system, investment processing, decision through execution fact and state reflection, startup and continuing runtime, state/data, external services, watch, operations, verification, improvement loop, responsibility boundaries, terminology, and canonical references. Detail may be delegated to contracts, but the capability, purpose, and boundary must remain visible.

## Human-facing representation

Write for Humans who need to understand and review argus, not for a single operator or implementer. Explain problems, purpose, policy, design intent, realization approach, effects, and constraints as appropriate. Use ordinary Japanese IT design language and define argus-specific terms at first use. Tables compare or organize information only when helpful. Diagrams explain system structure, processing order, state updates, boundaries, or feedback loops.

Use ordinary numbered Japanese headings, `表N　短い日本語タイトル`, and `図N　短い日本語タイトル`. Do not directly display SDM IDs, relation-type names, confidence, coverage records, classification notes, Table/Section Models, audit records, or machine-oriented relationship graphs. Hiding this internal notation must never be used as a reason to discard design scope.

## References and uncertainty

Use current repository paths and say when exact behavior is delegated to a contract. Do not manufacture content to fill a heading. Keep unsupported prompt-only information out of confirmed prose and state unresolved matters as Human Review items. Do not make “投資判断の妥当性を高める” a separate top-level purpose unless canonical source support is established.

## Completion checks

Confirm that the v0.1 major views remain, Policy/Rule and closed-loop operation are understandable, Watch/Allocation/operations/improvement are not absent, canonical references are current, and internal analysis notation is not exposed.

