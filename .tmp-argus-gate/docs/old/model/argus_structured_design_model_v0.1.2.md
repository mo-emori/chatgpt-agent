# argus Structured Design Model v0.1.2

**Document Role:** `STRUCTURED_DESIGN_MODEL`  
**Base:** v0.1  
**Status:** `HUMAN_REVIEW_REQUIRED`

## 1. Authority and scope

This Markdown model preserves design meaning extracted from Design Source, ADRs, contracts, and Test Strategy. It does not change their authority. Source disagreements and prompt-only assertions remain Human Review items.

## 2. Purpose, environment, and constraints

argus is a local-first investment-decision support system. Human retains investment decision and broker application operation; Broker API automated ordering is excluded. TEST, PAPER, and LIVE are isolated. Risk, environment binding, approval, and cost controls fail closed. Canonical State has a single writer and atomic commit. Raw source data is immutable. External APIs are isolated from business logic by gateway and provider-adapter boundaries.

## 3. Investment functions and policy/rules

The investment domain includes universe/selection, independent analysis, contradiction preservation, report generation, human decision, allocation, portfolio management, execution-fact intake, and state reflection. Investment policy/rule knowledge includes holding horizon, portfolio policy, high-dividend core handling, sell and loss-cut rules, deterministic risk rules, allocation constraints, proposal TTL, approval/revalidation, and Human correction. Their exact thresholds and schemas remain in their canonical design/contract locations.

## 4. Watch, re-evaluation, and break

Position Watch, Candidate Watch, and Market Watch maintain attention to holdings, candidates, and market conditions. Re-evaluation and break mechanisms ensure that changed evidence or invalidated assumptions can return work to the appropriate decision process. Decision Queue, incidents, severity, priority, notification windows, and User Status provide the durable Human coordination boundary.

## 5. Runtime, state, and data

Bootstrap resolves the correct entry, checks identity/environment/storage, establishes valid configuration, and assesses recovery before normal operation. Runner manages persistent execution; jobs perform retryable work and preserve durable intermediate results as required. State includes canonical portfolio/runtime status; data includes raw and normalized data, reports, execution facts, logs and evidence; configuration fixes runtime conditions. Backup, archive, restore, and storage-pressure behavior preserve recoverability.

## 6. Identity and environment binding

Runtime Identity and Environment Binding protect startup and writes. Data Root paths and drive letters locate storage but do not establish logical identity. Marker identity and expected binding prevent mixing of TEST/PAPER/LIVE data. Exact parsing, storage, and failure behavior are delegated to their contracts.

## 7. External services and cost governance

J-Quants, EDINET, and a Model Provider candidate are external dependencies. ExternalServiceGateway and Provider Adapter isolate provider schema and operational variation. Paid services, upgrades, add-ons, budgets, and paid fallback require explicit Human approval. Historical validation and local/test providers remain subject to their stated interface and test boundaries.

## 8. Verification and improvement

Verification covers fixture/data architecture, historical-time gates, provider verification, paper operation, regression, security/secret controls, human-load and cost checks, baseline/oracle/evidence, and critical-path review. Improvement uses evidence, review, correction, and capability feedback without silently rewriting canonical facts.

## 9. Coverage closure

The source inventory retains: source purposes; policy/rules; selection/report/decision; portfolio/holding/sell/loss-cut; watch/allocation; bootstrap/runtime/state/data; external services; operations including status/queue/notification; verification; and improvement. Exact detail may be delegated to ADR, contracts, or Test Strategy, but each area remains represented here.

## 10. Human Review

- Prompt-derived working hours and runtime particulars require canonical support before confirmation.
- The exact meaning of data timestamps, historical/local-provider operation policy, and startup manifest schema remain unresolved where no authority artifact fixes them.
- The phrase “投資判断の妥当性を高める” is not adopted as an independent top-level purpose because this base authority does not establish it.

