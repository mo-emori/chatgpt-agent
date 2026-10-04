# Context Harness trust acceptance

Context Harness separates observed state, an unaccepted candidate, and the trusted
baseline. Observation never establishes or advances trust. A trusted baseline is
usable only when its manifest identity is bound to an intact schema-v2 acceptance
receipt. Missing baselines produce initialization candidates; existing pre-tool
baselines are classified as legacy/unproven and are usable only as migration
comparison material.

## Normal reconciliation

Run the normal Context Harness job to inspect current sources and produce the
candidate. Review the generated delta and candidate, then copy the exact
`candidate_manifest_sha256` from the candidate record. Accept it explicitly:

```powershell
python -m context_trust accept `
  --root C:\path\to\FooProject `
  --cache-root C:\path\to\worker-cache `
  --workspace foo-workspace `
  --capability FOO-CAPABILITY `
  --candidate-sha256 <reviewed-candidate-sha256> `
  --expected-current-trusted-sha256 <current-trusted-sha256> `
  --operator <operator-id> `
  --reason "Reviewed reconciliation ticket <ticket-id>"
```

For first initialization, omit `--expected-current-trusted-sha256`; the absence of
a baseline is still compared as part of the operation. The command re-observes the
declared sources, rejects stale or ambiguous candidates, archives the prior
baseline, writes an immutable receipt, and atomically replaces the baseline. Its
single stdout line is deterministic JSON (`ACCEPTED` or `REJECTED`). Re-run the
normal job afterward; unchanged accepted state reports `NO_IMPACT`.

State is stored below the configured cache root:

- `context-candidates/<domain-sha256>.json`
- `context-baselines/<domain-sha256>.json`
- `context-acceptance-receipts/<domain-sha256>/<manifest-sha256>.json`
- `context-accepted-candidates/<domain-sha256>/<manifest-sha256>.json`
- `context-baseline-archive/<domain-sha256>/<previous-manifest-sha256>.json`

Receipt bytes and identity are validated on every baseline load. Deleting a
baseline, copying a manifest, or modifying a receipt cannot establish trust.

## One-time legacy migration

Do not delete or edit the legacy baseline. Deploy the tooling first, restart the
worker so loaders fail closed on the unproven baseline, then run one normal Context
Harness inspection. This creates a `LEGACY_MIGRATION` candidate while preserving
the legacy manifest as comparison material. Review that candidate and accept it
with the command above, supplying the legacy manifest hash as
`--expected-current-trusted-sha256`. Acceptance archives the exact legacy baseline
before writing the receipt-backed baseline.

Finally restart the worker if it is a long-running process that imported the old
code, run an unchanged internal job, and perform the separately authorized external
smoke. Migration and external smoke are operational actions; they are not performed
by installing this implementation.
