# argus Structured Design Model 変換プロンプト v0.1.4

```text
role: STRUCTURED_DESIGN_MODEL_TRANSFORMATION_INSTRUCTION
version: 0.1.4
status: CANDIDATE
canonical_design_source: docs/source/argus_design_source_v0.1.md
default_output: docs/model/argus_structured_design_model_v0.1.4.md
```

## 1. 目的

`docs/source/argus_design_source_v0.1.md`を唯一の設計正本として読み、設計意味を欠落・創作・弱化させず、後段のHuman System Design生成に利用できるStructured Design Model（SDM）へ変換する。

SDMはDesign Sourceの要約ではない。Sourceに含まれる設計対象を、意味単位、関係、根拠、確度、authority、未確定性を保ったまま構造化する中間表現である。

本プロンプトは設計authorityではない。本プロンプトの説明、例、見出し、分類候補を、新しい設計事実の根拠としてはならない。

## 2. v0.1.3失敗からの修正原則

次を本版の必須原則とする。

1. 設計領域名を一度記載しただけではCoverage成立としない。
2. 複数のPurpose、Policy、Rule、Function、Process、State、Dataを一段落へ列挙しただけの出力を禁止する。
3. Source Inventoryを生成Actorだけの一時情報にせず、`Source Coverage Register`としてSDM本体へ収録する。
4. 各確定設計要素に、型、意味、Source、確度、主要relationを持たせる。
5. Sourceの章題を並べ替えただけの文書と、Sourceを短く言い換えただけの文書をSDMとして受理しない。
6. Coverage、model completeness、provenanceを機械的に点検できない場合は成功としない。
7. SDMだけを生成・検証し、同一実行でHuman System Design生成へ進まない。

## 3. 入力とauthority

### 3.1 必須入力

- 設計正本: `docs/source/argus_design_source_v0.1.md`
- ADR: `docs/adr/argus_architecture_decision_records_v0.1.md`
- Contracts: `docs/contracts/`配下の適用対象文書
- Test Strategy: `docs/test/argus_test_strategy_v0.1.3.md`

Design Sourceを設計意味の正本とする。ADRは採用・棄却理由、Contractsは個別Capabilityの厳密な振る舞い、Test Strategyは検証体系とGateについて、それぞれのauthority範囲で利用する。

### 3.2 生成内容を決める入力にしないもの

- 既存のSDM
- 既存のHuman System Design
- 旧Generation Prompt
- Prompt Registry本文
- チャット履歴
- Capability RegistryやDevelopment Progressに記録された進捗状態

これらは生成完了後の回帰比較、由来確認、作業履歴確認にのみ利用できる。既存の派生文書にしかない意味を、新しいSDMへ継承してはならない。

### 3.3 衝突と不足

- Design Sourceと他artifactの実質的な矛盾を自動解消しない。
- artifactのauthority範囲を越えて、一列の優先順位を機械的に適用しない。
- 根拠が見つからない情報は確定事項にせず、`UNKNOWN`または`HUMAN_REVIEW_REQUIRED`とする。
- 設計判断が必要な場合は生成を止め、衝突箇所、双方の根拠、影響範囲を報告する。

## 4. 出力Contract

### 4.1 出力先

既定の出力先は`docs/model/argus_structured_design_model_v0.1.4.md`とする。既存ファイルを上書きしない。

出力はMarkdownとする。YAML、JSON、データベースへ移行しない。Coverage Registerは独立ファイルではなく、SDM本体の必須sectionとして収録する。

### 4.2 文書ヘッダー

冒頭に次をすべて記録する。

- `Document Role: STRUCTURED_DESIGN_MODEL`
- `Status: HUMAN_REVIEW_REQUIRED`
- Design SourceのpathとSHA-256
- 参照したADRのpathとSHA-256
- 参照した全ContractのpathとSHA-256
- Test StrategyのpathとSHA-256
- 使用した本プロンプトのpathとSHA-256
- 生成日時
- `Design Sourceから生成した派生文書であり、設計正本ではない`という宣言

一つでも欠けた場合は生成完了としない。

### 4.3 必須section

SDMは最低限、次のsectionをこの順序で持つ。

1. 文書の位置づけと入力provenance
2. Source Coverage Register
3. 用語・概念辞書
4. Environment / Assumption
5. Problem / Background
6. Purpose / Objective
7. Requirement / Constraint
8. Design Principle / Safety Invariant
9. Policy / Rule
10. Actor / Role / Authority
11. Function / Capability
12. Process / Step / Trigger
13. State / Lifecycle / Transition
14. Data / Artifact / Configuration
15. Responsibility / Ownership / Boundary
16. Realization Method / Design Choice
17. External Entity / External Service
18. Verification / Evidence / Gate
19. Effect / Outcome
20. Dependency / Relation
21. Current / Deferred / Future Scope
22. Human Review / Unknown
23. Model Completeness Report

Sourceに該当意味がないsectionは、空にせず`該当なし`と根拠を記載する。見出しを埋めるために設計を創作してはならない。

## 5. Source Coverage Register

### 5.1 対象

Design SourceのMarkdown見出しを先頭から末尾まで抽出する。`#`、`##`、`###`を対象とし、コードブロック内の見出し風文字列は除外する。

本文中のサンプル文書見出し、Version History、Review History、変更履歴も無視せず、設計本文、例、履歴、metadataのいずれかへ分類する。

### 5.2 必須列

Source Coverage Registerは、一つのSource見出しにつき最低一行を持つ。

| Source locator | Source heading | Source kind | Coverage disposition | SDM target | Model IDs | Delegated authority | Notes |
|---|---|---|---|---|---|---|---|

列の意味:

- `Source locator`: Source pathと章節。重複見出しを識別できること。
- `Source heading`: 見出し本文。
- `Source kind`: `DESIGN` / `EXAMPLE` / `HISTORY` / `METADATA`。
- `Coverage disposition`: `RETAINED` / `DELEGATED_BUT_REPRESENTED` / `OUT_OF_SCOPE` / `HUMAN_REVIEW_REQUIRED`。
- `SDM target`: 本SDM内のsection名。
- `Model IDs`: 対応するmodel entry ID。複数可。
- `Delegated authority`: 詳細委譲先。委譲しない場合は`N/A`。
- `Notes`: 除外理由、衝突、未確定性等。

### 5.3 Coverage成立条件

次をすべて満たした場合だけSource Coverageを成立とする。

- 抽出したSource見出し数とCoverage Register行数が一致する。
- `DESIGN`に分類した行の`Model IDs`が空でない。
- `DELEGATED_BUT_REPRESENTED`でも、対応model entryが存在する。
- `OUT_OF_SCOPE`に具体的理由がある。
- `HUMAN_REVIEW_REQUIRED`に問題点と影響範囲がある。
- 同じ少数のModel IDを無関係な多数sectionへ機械的に割り当てていない。
- Coverage Registerに記載したModel IDが実際のmodel sectionに存在する。

章名を本文中で一度言及しただけでは`RETAINED`にしない。

## 6. Model Entry Contract

### 6.1 共通必須項目

設計意味を表す各entryは、最低限次を持つ。

| 項目 | 内容 |
|---|---|
| ID | 文書内で一意かつ安定した分析用ID |
| Type | 本プロンプトで定義した意味型 |
| Name | 正式名または明確な短い名称 |
| Meaning | 何を意味するか |
| Design significance | なぜ設計上必要か、何を防ぐ・実現するか |
| Source | Design Sourceの章節またはauthority artifact |
| Confidence | `EXPLICIT` / `SYNTHESIZED` / `INFERRED` / `UNKNOWN` |
| Relations | 他entryとの主要relation |

型別に追加情報が必要な場合は列を追加する。

### 6.2 型別必須情報

- `Problem`: 何が起きると困るか、影響対象。
- `Purpose`: 達成状態、対象、上位Purpose。
- `Requirement / Constraint`: 拘束対象、必須条件、違反時の扱い。
- `Safety Invariant`: 保護対象、作用点、確認不能時の挙動。
- `Policy / Rule`: 適用対象、条件、許可・禁止・要求、例外、未設定時挙動。
- `Actor / Role / Authority`: 責務、入力、出力、できること、してはならないこと、最終authority。
- `Function`: 入力、処理責務、出力、副作用、失敗時挙動、非責務。
- `Process / Step`: trigger、precondition、順序、actor、input、output、failure path。
- `State`: owner、lifecycle、transition trigger、許可遷移、禁止遷移、永続化。
- `Data / Artifact / Configuration`: owner、writer、reader、immutability、retention、environment boundary。
- `Boundary`: 内側と外側、許可経路、禁止経路、failure propagation。
- `Realization Method`: 解決するProblem/Purpose、交換可能性、採用理由、制約。
- `Verification / Gate`: 検証対象、oracle、evidence、pass/fail authority、fail時挙動。

型別必須情報を一行へ無理に圧縮しない。情報がSourceにない場合は`UNKNOWN`とし、創作しない。

### 6.3 Relation Contract

relationは最低限次を区別する。

- `CONTAINS`
- `COMPOSED_OF`
- `PRECEDES`
- `TRANSITIONS_TO`
- `DEPENDS_ON_RUNTIME`
- `DEPENDS_ON_DEVELOPMENT`
- `READS`
- `WRITES`
- `PRODUCES`
- `CONSUMES`
- `OWNED_BY`
- `CONSTRAINED_BY`
- `VERIFIED_BY`
- `PROTECTED_BY`
- `DELEGATES_TO`
- `REFERENCES`
- `TRIGGERS`
- `SUPERSEDES`

relation rowは次を持つ。

| From ID | Relation | To ID | Meaning | Source | Confidence |
|---|---|---|---|---|---|

包含、処理順序、状態遷移、実行依存、開発依存を同じrelationとして扱わない。

## 7. 変換手順

### Phase 0: 入力固定

1. 必須入力の存在を確認する。
2. 各入力のSHA-256を取得する。
3. Design Sourceが`DESIGN_SOURCE`かつFinal Freezeを持つことを確認する。
4. 入力不足、読取り不能、実質的衝突がある場合は生成を開始しない。

### Phase 1: 見出し抽出

1. コードブロック内を除外してSource見出しを抽出する。
2. heading level、heading text、親headingを保持する。
3. 見出し総数を記録する。
4. Source Coverage Registerの全行を先に作る。

### Phase 2: 分割読取り

Sourceを一括要約しない。Sourceの順序を維持して複数batchへ分け、各batchで次を行う。

1. 設計意味を抽出する。
2. model entry候補を作る。
3. relation候補を作る。
4. Coverage RegisterへModel IDを割り当てる。
5. 未確定・衝突を記録する。

一つのbatchで抽出した意味を、後続batchの似た用語へ安易に統合しない。統合時はSourceと意味が同一であることを確認する。

### Phase 3: 型別統合

batch別候補を、必須sectionとModel Entry Contractへ統合する。

- 同義entryはSourceを失わず統合する。
- 同名異義entryは分離する。
- 実現方式からProblemやPurposeを逆算して創作しない。
- Current、Deferred、Future、TBD、UNCONFIGUREDを分離する。
- 進捗状態を恒久設計へ混入させない。

### Phase 4: Relation構築

model entry間の主要relationを作る。最低限、次の流れを追跡可能にする。

```text
Environment / Background
  → Problem
  → Purpose
  → Requirement / Constraint
  → Design Principle / Policy
  → Function / Responsibility
  → Process / State / Data
  → Realization Method
  → Effect
  → Verification
```

全entryがこの全段を持つ必要はないが、孤立した重要entryには理由を記録する。

### Phase 5: 横断安全条件検証

次の意味について、該当model entryとrelationを確認する。

- Human-in-the-loop
- Broker APIによる自動発注禁止
- TEST / PAPER / LIVE分離
- Risk / Environment Binding / Approval / Cost Controlのfail-closed
- Canonical StateのSingle Writer + Atomic Commit
- ExternalServiceGateway / Provider Adapter境界
- 有料サービス、増額、有料Fallbackの無断追加禁止
- Raw immutable data
- marker identity検証
- state/dataのenvironment間混在禁止

単独entryが存在するだけでは不十分である。起動、投資判断、外部接続、状態更新、運用、検証の作用点へrelationで接続する。

### Phase 6: Domain Coverage検証

最低限、次の領域について関連entry、relation、Source locatorを列挙して確認する。

1. システム目的と対象外
2. Local-firstと停止・復帰
3. 投資候補形成
4. 独立分析、矛盾保持、Report
5. Deterministic Risk Validation
6. Human Decision、Broker Operation、Execution Fact
7. Portfolio、Allocation、Canonical State
8. 保持期間、売却、縮小、損切、再評価
9. Position / Candidate / Market Watch
10. Runtime Identity、Environment Binding、Bootstrap
11. Runner、Job、retry、Durable Stage、recovery
12. Data、Raw / Normalized / Archive / Backup
13. User Status、Decision Queue、notification、alert、incident、correction
14. ExternalServiceGateway、Provider Adapter、cost governance
15. TEST / PAPER / LIVE、Historical Validation、Paper Trading
16. Capability Verification、evidence、review、approval
17. Counterfactual、learning、Break、改善ループ
18. Current / Deferred / Future scope

この一覧はSource Coverage Registerの代わりではない。Sourceから見つかった追加領域も保持する。

### Phase 7: Completeness検証

Model Completeness Reportに次を記録する。

- Source heading総数
- Coverage Register行数
- Source kind別件数
- Coverage disposition別件数
- Model Type別entry数
- Relation Type別件数
- Source未設定entry数
- Relationを持たない重要entry数と理由
- `INFERRED`件数
- `UNKNOWN`件数
- Human Review件数
- 未解決衝突
- Domain Coverage 18領域のPASS / FAIL
- 横断安全条件のPASS / FAIL

件数が0であること自体を成功条件にはしない。Sourceと整合することを確認する。

## 8. Policy / Rule保持の追加条件

投資方針と運用規則を一般的な機能説明へ吸収して消してはならない。少なくとも次を個別entryまたは明示的に分離されたentry群として扱う。

- 銘柄選定と分析方針
- Portfolio Policy
- Allocation
- 保持期間
- 売却、縮小、損切、再評価
- 高配当Core等の役割・分類
- Position / Candidate / Market Watch
- Risk limitとFail Closed
- Human approvalとDecision
- Proposal TTLと再検証
- Cost / paid service governance
- Data retention、Raw immutable、backup
- TEST / PAPER / LIVE分離
- Correction、Break、改善、変更統制

複数の異なる規則を「投資方針を保持する」の一文へまとめない。値や条件をSourceから確認できない場合は創作しない。

## 9. 確度とHuman Review

確度を次で区別する。

- `EXPLICIT`: authority artifactに明示
- `SYNTHESIZED`: 複数の明示事項を意味変更なく統合
- `INFERRED`: 推論を含むためHuman Reviewが必要
- `UNKNOWN`: 根拠不足

`INFERRED`をHuman Review前に確定しない。代替解釈がある場合は一つへ丸めない。

Human Review entryは最低限次を持つ。

| Review ID | Question / Conflict | Competing sources or missing source | Affected Model IDs | Risk if assumed | Required Human decision |
|---|---|---|---|---|---|

## 10. 禁止事項

- Design Sourceまたは参照artifactを変更しない。
- 既存SDMやSystem Designを上書きしない。
- Human System Designを同じ実行で生成しない。
- SourceにないPurpose、Problem、Policy、Rule、値、運用条件を創作しない。
- Prompt内の例を設計事実へ昇格させない。
- 既存派生文書の誤りを新しいSDMへ継承しない。
- 設計範囲を短い概要へ圧縮しない。
- 複数の異なる設計要素を単語列挙だけで保持済みにしない。
- Source Coverage Registerを省略しない。
- Model Entry Contractの必須項目を黙って省略しない。
- 進捗状態を恒久設計へ混入させない。
- 不明点を一般論やベストプラクティスで補わない。
- 完了条件を満たさない状態で成功markerを出さない。

## 11. Hard Completion Gates

次をすべて満たした場合だけ生成完了とする。一つでも満たさない場合は`BLOCKED`とする。

### Gate A: Input Provenance

- 文書ヘッダーの必須項目がすべて存在する。
- pathとSHA-256が実体と一致する。

### Gate B: Source Coverage

- Source heading総数とCoverage Register行数が一致する。
- 未分類行がない。
- `DESIGN`行に有効なModel IDがある。
- Coverage Register内のModel IDが実在する。

### Gate C: Model Structure

- 必須23sectionが存在する。
- 各model entryが共通必須項目を持つ。
- 型別必須情報が、Sourceに存在する範囲で保持される。
- 重要entryがrelationで設計全体へ接続されている。

### Gate D: Meaning Preservation

- Domain Coverage 18領域がすべてPASSまたは理由付きHuman Reviewである。
- Policy / Ruleが個別に追跡可能である。
- Watch、運用、検証、改善、閉ループが保持されている。
- Current / Deferred / Futureが混同されていない。

### Gate E: Safety

- 横断安全条件がすべてmodel entryとして存在する。
- 各安全条件が作用点へrelationで接続されている。
- 確認不能時のfail-closedが弱化されていない。

### Gate F: Authority

- 各確定entryに適切なSourceがある。
- Prompt、チャット、旧派生文書だけを根拠とする確定entryがない。
- 衝突、推論、根拠不足がHuman Reviewへ分離されている。

### Gate G: Anti-compression

- 主要な異種設計要素を単一段落または単一entryへ列挙しただけの箇所がない。
- Nameだけ存在し、Meaning、Design significance、Source、Relationsが欠落した重要要素がない。
- 後段のHuman System DesignがSourceを再解析しなくても、SDMから主要Viewを構築できる。

## 12. 完了報告

次を報告する。

1. 入力pathとSHA-256
2. 出力pathとSHA-256
3. Source heading総数とCoverage Register行数
4. Source kind別件数
5. Coverage disposition別件数
6. Model Type別entry数
7. Relation Type別件数
8. Domain Coverage 18領域の結果
9. 横断安全条件の結果
10. `OUT_OF_SCOPE`項目と理由
11. `HUMAN_REVIEW_REQUIRED`項目
12. 未解決衝突
13. Hard Completion Gates A〜GのPASS / FAILと根拠
14. 既存ファイルと正本を変更していないこと
15. Human System Designを生成していないこと

成功時の最終行:

```text
STRUCTURED_DESIGN_MODEL_V0_1_4_GENERATED_AND_GATES_PASSED
```

一つでもHard Completion Gateを満たさない場合の最終行:

```text
STRUCTURED_DESIGN_MODEL_V0_1_4_GENERATION_BLOCKED
```
