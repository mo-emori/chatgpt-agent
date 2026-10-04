# Context Harness の trust acceptance

Context Harness は、観測済み状態、未受理 candidate、trusted baseline を分離する。
観測だけで trust が確立・更新されることはない。trusted baseline を利用できるのは、
manifest identity が破損していない schema-v2 acceptance receipt に結び付いている場合だけである。
baseline が存在しない場合は初期化 candidate を生成する。現行ツール導入前の baseline は
legacy/unproven と分類し、移行時の比較材料としてのみ利用する。

## 通常の reconciliation

通常の Context Harness job を実行して現在の source を観測し、candidate を生成する。
生成された delta と candidate をレビューし、candidate record の正確な
`candidate_manifest_sha256` を指定して明示的に受理する。

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

初回初期化では `--expected-current-trusted-sha256` を省略する。baseline が存在しないことも
操作中の比較条件に含まれる。コマンドは宣言済み source を再観測し、古い candidate、曖昧な
trust domain、CAS 不一致を拒否する。通常受理の receipt には
`acceptance_type = HUMAN_EXPLICIT` が記録される。従来 baseline を archive し、不変 receipt を
作成してから baseline を atomic に置換する。標準出力は machine-readable な JSON 1 行
（`ACCEPTED` または `REJECTED`）である。受理後に通常 job を再実行すると、変更がなければ
`NO_IMPACT` になる。

状態は設定済み cache root の次の場所に保存される。

- `context-candidates/<domain-sha256>.json`
- `context-baselines/<domain-sha256>.json`
- `context-acceptance-receipts/<domain-sha256>/<manifest-sha256>.json`
- `context-accepted-candidates/<domain-sha256>/<manifest-sha256>.json`
- `context-baseline-archive/<domain-sha256>/<previous-manifest-sha256>.json`

baseline のロード時には毎回 receipt bytes と identity を検証する。baseline の削除、manifest
のコピー、receipt の改変によって trust を確立することはできない。

## v0.1 の一回限り legacy auto-migration 境界

v0.1 以前の legacy trust state については、項目ごとの人手承認を要求しない。その代わり、
明示的な専用コマンド `legacy-auto-migrate` だけが、通常の Context Harness 観測経路で生成された
現在有効な candidate を一回限り自動受理できる。worker の起動や job 実行からこの操作が
自動的に呼ばれることはない。

legacy baseline を削除・編集してはならない。新しい tooling を配備し、長時間稼働 worker が
旧コードを import 済みなら停止または再起動して、通常の Context Harness inspection を 1 回
実行する。これにより legacy manifest を比較材料として保持したまま `LEGACY_MIGRATION`
candidate が生成される。ライブ移行は、次の別途認可された運用コマンドで実施する。

```powershell
python -m context_trust legacy-auto-migrate `
  --root C:\path\to\FooProject `
  --cache-root C:\path\to\worker-cache `
  --workspace foo-workspace `
  --capability FOO-CAPABILITY `
  --candidate-sha256 <observed-candidate-sha256> `
  --expected-legacy-trusted-sha256 <legacy-trusted-manifest-sha256> `
  --operator <operator-id> `
  --reason "v0.1 legacy trust migration"
```

専用操作は、次の全条件を満たさない限り拒否する。

- 現在の baseline が hash-valid かつ acceptance provenance のない legacy/unproven 状態である。
- candidate が同一の workspace/capability について通常観測経路から生成されている。
- candidate identity が `--candidate-sha256` と完全一致する。
- 宣言・source を再観測した結果が candidate と完全一致し、candidate が stale でない。
- legacy baseline identity が `--expected-legacy-trusted-sha256` と完全一致する。
- trust domain が一意で、candidate envelope の domain と一致する。
- provenance-valid な新形式 baseline/receipt がまだ存在しない。
- candidate が reconciliation evidence の適格条件を満たす。

`accept` は `LEGACY_MIGRATION` candidate を受理できず、`legacy-auto-migrate` は通常の
`RECONCILIATION` candidate を受理できない。このため legacy 操作を一般的な auto-accept の
抜け道として利用できない。

成功時は legacy baseline を content-addressed な固有パスへ先に archive し、同一内容の既存
archive だけを再利用する。異なる内容での上書きは拒否する。domain lock、expected legacy CAS、
exclusive receipt、atomic baseline replacement により、中断後も旧状態または新状態のどちらかが
有効になり、half-state の baseline は生じない。成功後の再実行は、新形式 baseline が既にある
ため安全に拒否される。

migration receipt は通常受理と永続的に区別され、少なくとも次を記録する。

- `acceptance_type = LEGACY_AUTO_MIGRATION`
- trust/migration schema version
- workspace と capability からなる trust domain
- 以前の legacy trusted manifest identity と archive path
- 受理した candidate manifest identity と保存 path
- declaration identity と各 source identity
- UTC timestamp
- system migration actor と initiating operator
- reason
- tool version と、取得可能な場合は Git commit
- historical provenance を項目ごとに再検証していないことを示す明示的な `false` marker

移行後の authority change は従来どおり candidate を生成し、
`POTENTIAL_AUTHORITY_CHANGE` / `NEEDS_RECONCILIATION` として block する。通常受理には引き続き
明示的な人手ポリシーが必要であり、legacy migration receipt が後続変更を自動受理することはない。

ライブ移行後、worker が旧コードを import したままなら再起動し、変更のない internal job を
実行する。その後、別途認可された external smoke を行う。tooling の実装・テスト、ライブ移行、
external smoke はそれぞれ別の操作であり、この実装変更だけではライブ移行を実行しない。

## Worker control plane（protocol v3）

Slack から trust を検査・受理・legacy migration する場合は、通常の actor job ではなく
`operation: "TRUST_CONTROL"` を使う。許可される `control_action` は
`TRUST_INSPECT`、`TRUST_ACCEPT`、`TRUST_LEGACY_AUTO_MIGRATE` の閉じた集合だけである。
control request は `actor`、`mode`、`instruction_ref`、prompt、review metadata を持てず、
通常 job は control field を持てない。actor 実行への暗黙 fallback や PRE_ACTOR skip flag はない。

trust transition は actor に渡す data-plane context の準備ではなく、Worker 自身の control-plane
状態遷移である。Worker はこの操作を Context Harness PRE_ACTOR/ENFORCE より前に dispatch し、
常に `actor_started=false`、`actor=null`、`effective_model=null` として結果を返す。これは検証の
bypass ではない。mutation は従来と同じ `context_trust` 実装を呼び、workspace/capability domain、
candidate identity、現 trusted baseline の CAS、現 source の再観測による freshness、declaration/source、
receipt/provenance、operator、reason、および legacy eligibility をすべて検証する。

リクエストが指定できるのは capability と action ごとの hash/operator/reason だけである。
workspace root と cache root は Worker 設定から導出され、path、file content、任意 payload は拒否される。

```json
{
  "protocol_version": "3",
  "job_id": "TRUST-LEGACY-MIGRATE-20261004-001",
  "workspace": "foo-workspace",
  "operation": "TRUST_CONTROL",
  "control_action": "TRUST_LEGACY_AUTO_MIGRATE",
  "trust": {
    "capability": "FOO-CAPABILITY",
    "expected_candidate_sha256": "<candidate-sha256>",
    "expected_legacy_trusted_sha256": "<legacy-baseline-sha256>",
    "operator": "<operator-id>",
    "reason": "v0.1 legacy trust migration"
  }
}
```

`TRUST_INSPECT` の `trust` は `capability` だけを持つ。`TRUST_ACCEPT` は candidate hash、
operator、reason を必須とし、既存 baseline がある場合は `expected_current_trusted_sha256` による
CAS を指定できる。成功結果には action、trust domain、candidate/old/new identity、receipt/archive
path が含まれる。拒否結果には stable な `validation_reason_codes` が含まれ、callback は通常どおり
試行される。control result は actor input を持たないため `effective_input_sha256` や
`actor_input_sha256` を掲載しない。導入後は実行中 Worker の再起動が必要である。
