# argus Structured Design Model Generation Prompt v0.1.2

## Purpose

Create a Markdown Structured Design Model that preserves argus design meaning for later design generation, implementation planning, and verification. This is an internal design knowledge artifact, not a human-facing system-design view and not an authority for new design decisions.

## Inputs and authority

Read `docs/source/argus_design_source_v0.1.md`, the v0.1 SDM and System Design, ADRs, applicable contracts, and the Test Strategy. Use each artifact only within its authority: Design Source for stated system design, ADR for recorded architecture decisions, contracts for exact component behavior, and Test Strategy for verification governance. A prompt, prompt example, temporary human note, or Prompt Registry item is not alone a canonical design fact. Do not automatically resolve a substantive conflict between artifacts; record it for Human Review.

## Version and preservation

Use SDM v0.1 as the base. Preserve its stable identifiers, source references, confidence, typed relations, problem/purpose/causal information, and existing scope unless an authority artifact shows it is invalid. Keep Markdown. Do not create YAML, JSON, an independent Coverage Map, or fixed per-entry depth rules.

## Meaning to preserve

Maintain traceable knowledge for Environment; Problem/Background; Purpose; Requirement/Constraint; Design Principle; Function; Policy/Rule; Actor/Role; Process/Step; State; Data; Configuration; realization method; external entity; Verification; Artifact; Effect; Dependency; and unresolved matters. Use distinctions only when they preserve meaning; do not mechanically turn every item into a separate type.

Policy/Rule is required where it preserves investment policy and rules, including holding periods, sell/loss-cut rules, high-dividend core policy, allocation, risk limits, approval, cost, and retention rules. Do not invent rule values.

## Coverage closure

Build a source inventory while reading. Classify every discovered design meaning as: retained in the SDM; detailed in another canonical artifact but represented in the SDM; outside design scope; or Human Review. Do not omit an item merely because a fixed list of source sections did not mention it. The completion report, not a new persistent map artifact, records this check.

At minimum check: source purposes; selection/analysis/reporting; human decision; allocation/portfolio; holding/sell/loss-cut; position/candidate/market watch; re-evaluation/break; runtime/bootstrap/runner/jobs/durable stages; state/data/configuration; environment binding/runtime identity; queue/user status/approval/cost; external gateway/adapters; historical/paper/regression/evidence/review; correction and improvement loop; local-first/current/future runtime.

## Provenance and uncertainty

Every confirmed design meaning must point to a suitable canonical artifact. If only a prompt supports it, either find canonical support or retain it as UNKNOWN/Human Review. In particular, do not promote prompt-derived working hours, runtime environment, or the phrase “投資判断の妥当性を高める” into an independent top-level purpose without source support.

## Completion checks

Confirm that v0.1 scope is not lost; source purposes remain traceable; Policy/Rule survives; watch, operations, and feedback loops remain; the source inventory has no unclassified items; and no unsupported fact was made certain.

