# argus Structured Design Model v0.1.3

**Document Role:** `STRUCTURED_DESIGN_MODEL`  
**Status:** `HUMAN_REVIEW_REQUIRED`  
**Design Source:** `docs/source/argus_design_source_v0.1.md`  
**Design Source SHA-256:** `234504a7dba9b4435729af079c04bdca8f624b544acbb594ea94b9fa4fcf6d6e`  
**Transformation Prompt:** `docs/development/prompts/argus_structured_design_model_prompt_v0.1.3.md`  
**Prompt SHA-256:** `283ea988c34a86580918bf5c55985b686a1e63908df45112fa8ed415df4decb8`

Design Source is the canonical design input. ADRs record decisions, contracts define applicable capability behavior, and the Test Strategy defines verification governance. This model introduces no design meaning that lacks support in those artifacts.

## 1. Environment, background, and safety

argus is local-first and must tolerate intermittent availability. It maintains Human-in-the-loop investment operation: Human makes investment decisions and manually operates the broker application; Broker API automated ordering is prohibited. TEST, PAPER, and LIVE are isolated. Risk, Environment Binding, approval, and cost control fail closed. Canonical State is updated through a Single Writer and Atomic Commit. Raw data is immutable. External APIs are isolated from business logic through ExternalServiceGateway and Provider Adapter boundaries.

## 2. Investment decision domain

The model retains candidate selection, independent analysis, contradiction preservation, report generation, Human decision, allocation, portfolio management, execution-fact intake, and portfolio commit. Risk validation is deterministic before a decision can proceed. A Decision Queue keeps decisions and incidents available for Human response. Execution Fact is distinct from the decision and is the basis for state reflection.

### Policy and rule

Investment meaning includes holding horizons, portfolio policy, high-dividend core policy, allocation, sell/loss-cut rules, thesis/risk/price/time stop conditions, proposal TTL, after-close revalidation, and Human correction. Exact parameter values belong to their source-defined policies and contracts; this model preserves their existence and purpose.

## 3. Watch, operation, and feedback

Position Watch, Candidate Watch, and Market Watch maintain relevant observations. Re-evaluation and Break mechanisms return invalidated assumptions or changed evidence to appropriate work. User Status, human capacity, notification windows, alerts, incidents, and severity/priority coordinate Human response. Daily, weekly, monthly, quarterly, and event-driven operation connect monitoring, decision, correction, and improvement.

## 4. Runtime, state, data, and storage

Bootstrap resolves the permitted runtime entry, validates runtime identity and environment/storage binding, establishes configuration, and assesses recovery. Runner manages persistent execution; jobs support retry, durable intermediate stages, recovery, and safe commit. Runtime and portfolio state are current state; raw/normalized data, reports, decision records, execution facts, configuration snapshots, logs, archives, and backups have distinct preservation roles. Storage-pressure degradation and backup/restore behavior preserve safety.

## 5. Environment binding and external services

Data Root path and drive letter locate storage but do not establish identity. Marker identity and expected environment binding protect start and write operations. J-Quants, EDINET, and Model Provider candidates are external entities. Gateway and Provider Adapter isolate provider variation. Paid service activation, plan increases, add-ons, and paid fallback require explicit Human approval. Current, deferred, future, and unconfigured scope remain distinct.

## 6. Verification and evidence

The model retains small fixtures, provider fixtures, historical datasets, generated failure fixtures, historical-time/look-ahead prevention, paper validation, regression, human-load, cost, security/secret tests, baseline/oracle/evidence, CV/RV, static analysis, and critical-path review. Evidence and Human Review support an improvement loop rather than silently replacing canonical design facts.

## 7. Coverage and provenance

| Source area | SDM status | Detail authority |
|---|---|---|
| Purpose, local-first runtime, investment process | RETAINED | Design Source |
| Policy/rule, Portfolio, Allocation, stop rules | RETAINED | Design Source |
| Watch, User Status, Queue, operation, improvement | RETAINED | Design Source |
| Identity, marker binding, bootstrap | DELEGATED_BUT_REPRESENTED | applicable contracts |
| Provider choices and paid-service governance | DELEGATED_BUT_REPRESENTED | ADR |
| Baseline, evidence, CV/RV, review | DELEGATED_BUT_REPRESENTED | Test Strategy |

No Prompt Registry, chat history, old SDM, or old System Design is used as a confirmed design source.

## 8. Human Review

- Any unresolved cross-artifact conflict requires Human Review.
- Exact operational values and schemas not fixed by an authority artifact remain unconfirmed.

