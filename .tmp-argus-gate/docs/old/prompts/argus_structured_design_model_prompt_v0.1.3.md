# argus Structured Design Model 変換プロンプト v0.1.3

```text
role: STRUCTURED_DESIGN_MODEL_TRANSFORMATION_INSTRUCTION
version: 0.1.3
status: CANDIDATE
canonical_design_source: docs/source/argus_design_source_v0.1.md
default_output: docs/model/argus_structured_design_model_v0.1.3.md
```

## 1. 目的

`docs/source/argus_design_source_v0.1.md` を唯一の設計正本として読み、設計意味を欠落・創作・弱化させず、後段の Human System Design 生成に利用できる Structured Design Model（SDM）へ変換する。

SDM は、人間向け設計書を短くするための要約ではない。Design Source に含まれる環境、課題、目的、要求、制約、方針、規則、機能、処理、状態、データ、責務境界、実現方式、検証、効果、依存関係、未確定事項を、追跡可能な形で構造化する中間表現である。

本プロンプトは設計authorityではない。本プロンプトの説明、例、見出し、分類候補を、新しい設計事実の根拠としてはならない。

## 2. 入力とauthority

### 2.1 必須入力

- 設計正本: `docs/source/argus_design_source_v0.1.md`
- ADR: `docs/adr/argus_architecture_decision_records_v0.1.md`
- Contracts: `docs/contracts/` 配下の適用対象文書
- Test Strategy: `docs/test/argus_test_strategy_v0.1.3.md`

Design Source を設計意味の正本とする。ADRは採用・棄却理由、Contractsは個別Capabilityの厳密な振る舞い、Test Strategyは検証体系とGateについて、それぞれのauthority範囲で利用する。

### 2.2 生成入力にしないもの

次は生成内容を決める入力にしない。

- 既存のSDM
- 既存のHuman System Design
- 旧Generation Prompt
- Prompt Registryの本文
- チャット履歴
- Capability RegistryやDevelopment Progressに記録された進捗状態

これらは生成完了後の回帰比較、由来確認、作業履歴確認にのみ利用できる。既存の派生文書にしかない意味を、新しいSDMへ無条件に継承してはならない。

### 2.3 衝突と不足

- Design Sourceと他artifactの実質的な矛盾を自動解消しない。
- artifactのauthority範囲を越えて、一列の優先順位を機械的に適用しない。
- 根拠が見つからない情報は確定事項にせず、`UNKNOWN`または`HUMAN_REVIEW_REQUIRED`とする。
- 設計判断が必要な場合は生成を止め、衝突箇所、両方の根拠、影響範囲を報告する。

## 3. 出力Contract

### 3.1 出力先

既定の出力先は `docs/model/argus_structured_design_model_v0.1.3.md` とする。既存ファイルを上書きしない。

出力はMarkdownとする。YAML、JSON、データベース、独立した恒久Coverage Mapへ移行しない。

### 3.2 文書ヘッダー

冒頭に最低限、次を記録する。

- `Document Role: STRUCTURED_DESIGN_MODEL`
- `Status: HUMAN_REVIEW_REQUIRED`
- 正本Design SourceのpathとSHA-256
- 参照したADR、Contracts、Test StrategyのpathとSHA-256
- 使用した本プロンプトのpathとSHA-256
- 生成日時
- `Design Sourceから再生成した派生文書であり、設計正本ではない`という宣言

### 3.3 安定した参照

- 分析用IDを使用する場合は文書内で一意かつ安定させる。
- 分析用IDをCanonical Capability IDや実装API名として扱わない。
- 各確定事項から、Design Sourceの章・節またはauthorityを持つ補助artifactへ追跡できるようにする。
- `Prompt IDのみ`、`チャットのみ`、`旧派生文書のみ`を恒久設計意味のSourceにしない。

## 4. 変換手順

次の順序を守る。先にHuman System Designの章立てや分量を決め、それに合わせて設計意味を削ってはならない。

### Step 1: 入力固定

1. 必須入力の存在を確認する。
2. 各入力のSHA-256を取得する。
3. Design Sourceが`DESIGN_SOURCE`かつFinal Freezeを持つことを確認する。
4. 入力不足、読取り不能、実質的衝突がある場合は生成を開始しない。

### Step 2: Source Inventory

Design Sourceを先頭から末尾まで読み、見出し単位を基本として設計意味を抽出する。表、箇条書き、図、コードブロック内の契約も対象にする。

各項目を少なくとも次のいずれかへ分類する。

1. `RETAINED`: SDM本文に保持
2. `DELEGATED_BUT_REPRESENTED`: 詳細は別authority artifactへ委譲するが、存在・目的・境界はSDMに保持
3. `OUT_OF_SCOPE`: 設計対象外。理由を記録
4. `HUMAN_REVIEW_REQUIRED`: 衝突・不足・解釈判断が必要

未分類項目を残さない。固定された節番号リストだけを抽出対象にしてはならない。

Source Inventoryは生成時の作業情報として扱い、独立した恒久artifactを新設しない。完了報告には領域別のCoverage結果を含める。

### Step 3: 用語と概念の抽出

設計概念単位で、次を整理する。

| Formal Name / English | 日本語表示名 | 意味 | Human Viewでの用例 | 使用上の注意 | Canonical Source |
|---|---|---|---|---|---|

- API、型、enum、識別子の正式名称は変更しない。
- 日本語化でCanonicalな意味を変えない。
- 同じ語でも意味が異なる場合は別概念として扱う。
- 訳語が一意でない場合は確定せず、Human Reviewへ送る。

### Step 4: 意味構造の抽出

少なくとも次の意味を保持対象として探索する。

- Environment / Assumption
- Problem / Background
- Purpose / Objective
- Requirement / Constraint
- Design Principle / Safety Invariant
- Policy / Rule
- Function / Capability
- Actor / Role / Authority
- Process / Step / Trigger
- State / Lifecycle / Transition
- Data / Artifact / Configuration
- Responsibility / Ownership / Boundary
- Realization Method / Design Choice
- External Entity / External Service
- Verification / Evidence / Gate
- Effect / Outcome
- Dependency
- Deferred Scope / TBD / UNCONFIGURED / Human Review

すべてを機械的に独立Entity Typeへ分解する必要はない。しかし、異なる意味を同じ分類へ押し込み、後段で区別不能にしてはならない。

### Step 5: Policy / Ruleの保持

投資方針と運用規則を、一般的な機能説明へ吸収して消してはならない。少なくとも次を明示的に探索する。

- 銘柄選定と分析方針
- PortfolioとAllocation
- 保持期間
- 売却、縮小、損切、再評価
- 高配当Core等の役割・分類
- Position / Candidate / Market Watch
- Risk limitとFail Closed
- Human approvalとDecision
- Cost / paid service governance
- Data retention、Raw immutable、backup
- TEST / PAPER / LIVE分離
- 改善、Break、変更統制

値や条件をSourceから確認できない場合は創作しない。

### Step 6: 関係と因果の構造化

少なくとも次を混同しない。

- 包含関係
- 構成関係
- 処理順序
- 状態遷移
- 実行時依存
- 開発依存
- 入出力
- 所有・責務
- 制約
- 検証
- 参照
- 横断的作用

主要設計領域では、可能な範囲で次を追跡可能にする。

```text
環境・背景
  → 困ること
  → 目的
  → 要求・制約
  → 設計原則・Policy
  → 機能・責務
  → 実現方式
  → 期待する効果
  → 検証
```

現在の実現方式から、存在しない課題や目的を逆算して作らない。

### Step 7: 横断安全条件の固定

次の意味を省略・弱化・局所化しない。

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

これらが各領域へどう作用するかを関係として保持する。

### Step 8: 不確実性の記録

確度を次で区別する。

- `EXPLICIT`: authority artifactに明示
- `SYNTHESIZED`: 複数の明示事項を意味変更なく統合
- `INFERRED`: 推論を含むためHuman Reviewが必要
- `UNKNOWN`: 根拠不足

`INFERRED`をHuman Review前に確定しない。代替解釈がある場合は一つへ丸めない。

## 5. 必須Coverage領域

最低限、次の領域ごとにSourceの存在、SDMでの保持位置、詳細委譲先、未確定事項を確認する。

1. システム目的と対象外
2. Local-firstと停止・復帰
3. 投資候補形成
4. 独立分析、矛盾保持、Report
5. Deterministic Risk Validation
6. Human Decision、Broker Operation、Execution Fact
7. Portfolio、Allocation、Canonical State
8. 保持、売却、損切、再評価
9. Position / Candidate / Market Watch
10. Runtime Identity、Environment Binding、Bootstrap
11. Runner、Job、retry、Durable Stage、recovery
12. Data、Raw / Normalized / Archive / Backup
13. User Status、Decision Queue、notification、incident
14. ExternalServiceGateway、Provider Adapter、cost governance
15. TEST / PAPER / LIVEとHistorical Validation
16. Capability Verification、evidence、review、approval
17. Correction、learning、Break、改善ループ
18. Current / Deferred / Future scope

この一覧はSource Inventoryの代わりではない。Sourceから見つかった追加領域も保持する。

## 6. 禁止事項

- Design Sourceまたは参照artifactを変更しない。
- 既存SDMやSystem Designを上書きしない。
- SourceにないPurpose、Problem、Policy、Rule、値、運用条件を創作しない。
- Prompt内の例を設計事実へ昇格させない。
- 既存派生文書の誤りを新しいSDMへ継承しない。
- 設計範囲を短い概要へ圧縮しない。
- Human System Designの見出しに合わせて意味を落とさない。
- 進捗状態を恒久設計へ混入させない。
- 不明点を一般論やベストプラクティスで補わない。

## 7. 完了条件

次をすべて満たした場合だけ生成完了とする。

- Design Sourceを全範囲走査した。
- Source Inventoryに未分類項目がない。
- 必須Coverage領域が追跡可能である。
- Sourceの主要Purpose、Policy、Ruleが失われていない。
- Watch、運用、検証、改善、閉ループが保持されている。
- 横断安全条件が保持されている。
- 各確定事項に適切な根拠がある。
- Prompt、チャット、旧派生文書だけを根拠とする確定事項がない。
- 衝突・不足・推論がHuman Reviewとして明示されている。
- 出力が後段のHuman System Design生成に十分な構造と情報量を持つ。

## 8. 完了報告

生成物とは別に、次を簡潔に報告する。

1. 入力pathとSHA-256
2. 出力pathとSHA-256
3. 領域別Coverage結果
4. `DELEGATED_BUT_REPRESENTED`とした領域と委譲先
5. `OUT_OF_SCOPE`とした項目と理由
6. Human Review項目
7. Promptのみ、チャットのみ、旧派生文書のみに由来する情報を確定事項へ採用していないこと
8. 既存ファイルと正本を変更していないこと

成功時の最終行:

```text
STRUCTURED_DESIGN_MODEL_GENERATED_FROM_CANONICAL_SOURCE
```

生成を安全に完了できない場合の最終行:

```text
STRUCTURED_DESIGN_MODEL_GENERATION_BLOCKED
```
