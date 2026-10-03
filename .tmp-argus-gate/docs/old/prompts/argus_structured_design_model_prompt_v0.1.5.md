# argus Structured Design Model 段階変換プロンプト v0.1.5

```text
role: RESUMABLE_STRUCTURED_DESIGN_MODEL_TRANSFORMATION_INSTRUCTION
version: 0.1.5
status: CANDIDATE
base_prompt: docs/development/prompts/argus_structured_design_model_prompt_v0.1.4.md
canonical_design_source: docs/source/argus_design_source_v0.1.md
working_root: docs/model/work/argus_structured_design_model_v0.1.5
final_output: docs/model/argus_structured_design_model_v0.1.5.md
```

## 1. 目的

`docs/development/prompts/argus_structured_design_model_prompt_v0.1.4.md`の意味保存、Model Entry Contract、Relation Contract、Coverage、authority、Hard Completion Gateを維持したまま、巨大なDesign Sourceを複数の再開可能な工程へ分割してStructured Design Modelを生成する。

本版はv0.1.4を置き換える段階実行仕様である。v0.1.4の要件と本版が衝突する場合、中間成果物の作成、段階実行、状態管理、再開方法についてのみ本版を適用する。設計意味、authority、安全条件、Coverage、最終Hard Gateを緩和してはならない。

## 2. 解決する問題

v0.1.4では、完成版だけを書き込める条件の下で、Source Coverage Register、Model Entry、Relation、Domain Coverage、Hard Gateを一回の実行で完成させる必要があった。その結果、作業量の大きさを認識した生成Actorが、途中成果を保存できず、Phase 1後に`BLOCKED`となった。

本版では次を可能にする。

- 中間成果を非正本working artifactsへ保存する。
- 一つの実行では一つのPhaseまたは限定batchだけを完了できる。
- 後続実行が、hashとstatusを確認して安全に再開できる。
- 未完了を`IN_PROGRESS`として扱い、真の阻害状態`BLOCKED`と区別する。
- 全Hard GateがPASSした場合だけ完成版を作成する。

## 3. Authorityと継承

### 3.1 必須Prompt

実行Actorは次を両方全文読む。

1. 本プロンプト
2. `docs/development/prompts/argus_structured_design_model_prompt_v0.1.4.md`

### 3.2 維持するv0.1.4要件

次は変更しない。

- Design Sourceを唯一の設計正本とすること
- 入力artifactごとのauthority範囲
- Source Coverage Register
- 23必須section
- Model Entry Contract
- 型別必須情報
- Relation Contract
- Domain Coverage 18領域
- 横断安全条件
- 確度とHuman Review
- 禁止事項
- Hard Completion Gates A〜G
- 完了報告
- Human System Designを同じ工程で生成しないこと

### 3.3 本版で変更する事項

次については本版を優先する。

- 中間成果物の作成を許可する。
- Source Coverage Registerをworking artifactとして段階的に作成できる。
- Model EntryとRelationをbatch単位のworking artifactへ保存できる。
- 一つの実行で最終成果を完成できなくても`IN_PROGRESS`として正常終了できる。
- 全Phase完了後にworking artifactsを統合し、最終SDM本体へ収録する。

## 4. Working artifacts

### 4.1 Working Root

中間成果物は次の配下に限定する。

`docs/model/work/argus_structured_design_model_v0.1.5/`

このdirectoryの成果物はすべて`NONCANONICAL_WORKING_ARTIFACT`であり、完成版SDMでも設計正本でもない。

### 4.2 必須working artifacts

```text
docs/model/work/argus_structured_design_model_v0.1.5/
├─ phase_status.md
├─ input_manifest.md
├─ source_heading_inventory.md
├─ source_coverage_register.md
├─ batches/
│  ├─ batch_01.md
│  ├─ batch_02.md
│  ├─ batch_03.md
│  ├─ batch_04.md
│  └─ batch_05.md
├─ model_entries.md
├─ relations.md
├─ domain_coverage.md
├─ safety_coverage.md
├─ human_review.md
├─ completeness_report.md
└─ assembled_draft.md
```

必要な場合はbatchを追加・分割できる。ただし`phase_status.md`とCoverage Registerに反映する。

### 4.3 Working artifact header

各working artifactは冒頭に次を持つ。

- `Artifact Role: NONCANONICAL_WORKING_ARTIFACT`
- 対象SDM version
- Phaseまたはbatch ID
- 入力pathとSHA-256
- 更新日時
- status: `NOT_STARTED` / `IN_PROGRESS` / `COMPLETE` / `BLOCKED`
- 次に実行すべき工程

### 4.4 書込み規則

- 一つの実行で完成したPhaseまたはbatchだけを`COMPLETE`にする。
- 部分的な内容を`COMPLETE`にしない。
- 後続実行は既存内容とhashを確認してから追記・更新する。
- 前工程の内容を無理由に短縮・置換しない。
- 同一entryを統合する場合はSource locatorをすべて保持する。
- working artifactを最終成果物として扱わない。

## 5. Phase State Model

### 5.1 Status

- `NOT_STARTED`: 未着手。
- `IN_PROGRESS`: 作業は正常に進行しているが、後続Phaseが残る。
- `COMPLETE`: 当該Phaseの完了条件を満たした。
- `BLOCKED`: 外部条件または設計判断が必要で、安全に進められない。

### 5.2 IN_PROGRESSとBLOCKEDの区別

次は`IN_PROGRESS`であり、`BLOCKED`ではない。

- 後続Phaseが残っている。
- Sourceまたは出力が大きい。
- 一回の実行で全batchを処理できない。
- 次の実行で継続できる。
- working artifactへ安全にcheckpointできた。

次の場合だけ`BLOCKED`にできる。

- 必須入力が存在しない、読めない、またはhash不一致。
- authority artifact間にHuman判断を要する実質的衝突がある。
- working artifactが破損し、安全に再開できない。
- filesystem、権限、tool障害により継続不能。
- 最終Assembly後、Hard Completion Gateを満たせず、同じ入力からの修復方法がない。

作業量、残りPhase、残りbatch、想定出力量だけを理由に`BLOCKED`としてはならない。

## 6. phase_status.md Contract

`phase_status.md`は工程状態の単一管理表とする。

| Phase | Description | Input artifact | Output artifact | Status | Verification | Next action |
|---|---|---|---|---|---|---|

最低限、次のPhaseを管理する。

| Phase | Description |
|---|---|
| P0 | Input manifest and hash verification |
| P1 | Source heading inventory |
| P2 | Initial Source Coverage Register |
| P3-01..N | Source batch analysis |
| P4 | Model entry integration |
| P5 | Relation construction |
| P6 | Domain and safety coverage |
| P7 | Human Review and unknown consolidation |
| P8 | Draft assembly |
| P9 | Completeness and Hard Gate verification |
| P10 | Final output creation |

後続実行は、最初の`NOT_STARTED`または`IN_PROGRESS`のPhaseから再開する。既に`COMPLETE`のPhaseを理由なく再実行しない。

## 7. 段階実行手順

### P0: Input manifest

`input_manifest.md`へ、v0.1.4が要求する全入力のpath、role、authority、SHA-256、読取り可否を記録する。

完了条件:

- 全必須入力が存在する。
- hashを取得した。
- Design SourceのDocument RoleとFinal Freezeを確認した。
- 使用するPrompt 2本のhashを記録した。

### P1: Source heading inventory

Design Sourceのコードブロック外にある`#`、`##`、`###`見出しを抽出し、`source_heading_inventory.md`へ記録する。

各行は最低限次を持つ。

| Heading ID | Level | Source locator | Parent Heading ID | Heading text | Preliminary source kind |
|---|---|---|---|---|---|

完了条件:

- コードブロック内の見出し風文字列を除外した。
- 重複見出しを一意に識別した。
- 見出し総数を記録した。

### P2: Initial Source Coverage Register

`source_heading_inventory.md`の各行に対して、v0.1.4の必須列を持つ`source_coverage_register.md`を作成する。

この段階ではModel ID未割当を`PENDING_MODEL_ID`としてよい。ただし空欄にしない。

P2完了は最終Gate BのPASSを意味しない。全headingにCoverage rowが存在することだけを確認する。

### P3: Source batch analysis

Sourceを見出し境界で複数batchへ分割する。各batchは一つの実行で安全に完了できる大きさにする。

各`batches/batch_NN.md`は最低限次を持つ。

- 対象Heading ID範囲
- 読んだSource locator
- 抽出したModel Entry候補
- Relation候補
- Coverage disposition更新
- Coverage Registerへ割り当てるModel ID
- Delegated authority
- Human Review候補
- batch completeness check

batch完了時に`source_coverage_register.md`の該当行を更新する。

### P4: Model entry integration

全`COMPLETE` batchの候補を`model_entries.md`へ型別に統合する。

v0.1.4のModel Entry Contractを満たす。Source locatorを失う統合は禁止する。

Coverage Registerの全`DESIGN`行から、実在するModel IDへ到達できることを確認する。

### P5: Relation construction

`relations.md`へv0.1.4のRelation Contractを満たすrelationを記録する。

重要entryが孤立していないこと、包含、順序、状態遷移、実行依存、開発依存を混同していないことを確認する。

### P6: Domain and safety coverage

- `domain_coverage.md`: v0.1.4の18領域を検証する。
- `safety_coverage.md`: 横断安全条件と作用点へのrelationを検証する。

単語の存在だけをPASS根拠にしない。Model ID、Relation、Source locatorを列挙する。

### P7: Human Review consolidation

`human_review.md`へ、INFERRED、UNKNOWN、authority衝突、未確定値を統合する。

確定entryとHuman Review entryを分離する。

### P8: Draft assembly

`assembled_draft.md`へ、v0.1.4が要求する23sectionを組み立てる。

次をSDM本体へ統合する。

- input provenance
- Source Coverage Register
- model entries
- relations
- scope
- Human Review
- completeness reportの初期値

working artifactへの参照だけで済ませず、完成版に必要な内容を本文へ収録する。

### P9: Completeness and Hard Gate verification

`completeness_report.md`へ、v0.1.4が要求する全件数とHard Completion Gates A〜Gの判定根拠を記録する。

FAILが修正可能な場合は、該当Phaseを`IN_PROGRESS`へ戻して修正する。直ちに`BLOCKED`へしない。

### P10: Final output creation

P0〜P9がすべて`COMPLETE`かつHard Gate A〜GがすべてPASSの場合だけ、次を作成する。

`docs/model/argus_structured_design_model_v0.1.5.md`

完成版は`assembled_draft.md`の単純コピーではなく、最終hash、生成日時、Completeness Reportを反映する。

## 8. Working artifact lifecycle

- working artifactsは完成版生成後も直ちに削除しない。
- Humanが完成版を受入れるまで保持する。
- working artifactsをGit commit対象とするか、受入後に削除するかはHumanが決定する。
- 生成Actorが勝手に削除、移動、archiveしない。
- working artifactsの存在を完成版のauthority根拠にしない。

## 9. 一回の実行の完了条件

一回の実行は、最終SDMが未完成でも正常完了できる。

最低限次を満たす。

1. 対象Phaseまたはbatchを明示した。
2. 対象工程のworking artifactを作成または更新した。
3. 当該工程の完了条件を検証した。
4. `phase_status.md`を更新した。
5. 次のPhaseまたはbatchを明示した。
6. 指定外ファイルを変更していない。

途中正常終了時の最終行:

```text
STRUCTURED_DESIGN_MODEL_V0_1_5_GENERATION_IN_PROGRESS
```

## 10. 最終完了条件

v0.1.4のHard Completion Gates A〜Gをすべて満たし、P10が完了した場合だけ最終成功とする。

最終成功時の最終行:

```text
STRUCTURED_DESIGN_MODEL_V0_1_5_GENERATED_AND_GATES_PASSED
```

真の阻害状態により継続不能な場合の最終行:

```text
STRUCTURED_DESIGN_MODEL_V0_1_5_GENERATION_BLOCKED
```

## 11. 一回の実行報告

各実行で次を報告する。

1. 実行したPhaseまたはbatch
2. 読んだ入力とSHA-256
3. 作成・更新したworking artifacts
4. 当該工程の検証結果
5. phase_statusの現在値
6. 累積Coverage件数
7. 新規Model Entry数
8. 新規Relation数
9. Human Review候補
10. 次に実行すべきPhaseまたはbatch
11. 指定外ファイルを変更していないこと

`IN_PROGRESS`を失敗またはBLOCKEDとして報告しない。

## 12. 最終報告

P10完了時は、v0.1.4の完了報告全項目に加え、次を報告する。

- working artifact一覧とSHA-256
- 各Phaseの実行履歴
- batch数と対象範囲
- 最終出力pathとSHA-256
- working artifactsを保持していること
