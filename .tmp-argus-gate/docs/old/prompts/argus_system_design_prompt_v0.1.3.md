# argus Human System Design 変換プロンプト v0.1.3

```text
role: HUMAN_SYSTEM_DESIGN_TRANSFORMATION_INSTRUCTION
version: 0.1.3
status: CANDIDATE
required_model: docs/model/argus_structured_design_model_v0.1.3.md
canonical_design_source: docs/source/argus_design_source_v0.1.md
default_output: docs/design/argus_system_design_v0.1.3.md
```

## 1. 目的

検証済みStructured Design Modelを、人間がargusの設計を理解・レビュー・実装判断できるHuman System Designへ変換する。

本工程はSDMを短く言い換える要約処理ではない。SDMが保持する設計範囲と意味を維持しながら、内部分析記法を、人間に適した説明、構成、図、表、参照へ投影する。

本プロンプトは設計authorityではない。見出しを埋めるために、SourceまたはSDMに存在しない設計を創作してはならない。

## 2. 入力と生成Gate

### 2.1 必須入力

- SDM: `docs/model/argus_structured_design_model_v0.1.3.md`
- 設計正本: `docs/source/argus_design_source_v0.1.md`
- ADR: `docs/adr/argus_architecture_decision_records_v0.1.md`
- Contracts: `docs/contracts/` 配下の適用対象文書
- Test Strategy: `docs/test/argus_test_strategy_v0.1.3.md`

SDMは文書構造と設計意味の直接入力、Design Sourceは意味保存とauthority確認、ADR・Contracts・Test Strategyは各範囲の詳細確認に使用する。

### 2.2 生成前Gate

次を満たさない場合は生成しない。

- SDMが指定pathに存在する。
- SDMが本プロンプトと同じ実行系列でDesign Sourceから生成されている。
- SDMにDesign Sourceと本プロンプト系列のprovenanceがある。
- SDMのCoverage確認が完了している。
- SDMに未解決のauthority衝突がない。
- `HUMAN_REVIEW_REQUIRED`が、本文の確定事項と区別されている。

Gate不成立時は、System Designを局所的に補完せず、SDM工程へ戻す。

### 2.3 回帰比較専用入力

既存のSystem Designは、生成完了後のCoverage回帰と表現品質比較にのみ使用する。既存文書をテンプレートとして写したり、そこにしかない設計意味を無条件に継承したりしない。

## 3. 出力Contract

### 3.1 出力先

既定の出力先は `docs/design/argus_system_design_v0.1.3.md` とする。既存ファイルを上書きしない。

### 3.2 文書ヘッダー

冒頭に最低限、次を記録する。

- `Document Role: HUMAN_SYSTEM_DESIGN`
- `Status: HUMAN_REVIEW_REQUIRED`
- 入力SDMのpathとSHA-256
- Design SourceのpathとSHA-256
- 使用した本プロンプトのpathとSHA-256
- 生成日時
- `Design SourceからSDMを経由して生成した派生文書であり、設計正本ではない`という宣言

### 3.3 想定読者

主対象は、argusの設計全体を理解・レビューするHumanとする。単一の運用者、実装者、監査者だけを対象にしない。

読者が次を理解できることを目標にする。

- なぜこのシステムが必要か
- 何を行い、何を行わないか
- 人間とシステムの責任境界
- 投資判断から結果反映・監視・再評価までの閉ループ
- 安全条件がどこで作用するか
- 実行・停止・復旧・データ保存の考え方
- 外部サービスと費用の統制
- 何をどのartifactで厳密に確認するか
- 何が未確定か

## 4. 変換原則

### 4.1 意味保存

- SDMの設計範囲を理由なく削らない。
- Problem、Purpose、Policy、Rule、Function、Process、State、Data、Boundary、Verificationの違いを保つ。
- Coverageと説明量を分ける。詳細を短くしても、Capabilityや設計領域の存在・目的・境界を消さない。
- Contractへ詳細を委譲しても、そのCapabilityがなぜ存在し、何を守り、他領域とどう接続するかを本文に残す。
- Current、Deferred、Future、TBD、UNCONFIGUREDを混同しない。

### 4.2 人間向け表現

- 本文は課題、目的、方針、設計意図、責務、効果を説明する。
- 図は構造、境界、処理順序、状態遷移、閉ループを理解させる場合に使う。
- 表は同種要素の比較、分類、対応関係を理解させる場合に使う。
- 一般的なIT設計用語は使用してよい。
- argus固有語は初出で日本語説明とFormal Nameを対応付ける。
- API名、型名、enum値、artifact pathは正確に記載する。
- 抽象語だけで済ませず、「何が起きると困るか」「何を止めるか」「誰が判断するか」を具体化する。

### 4.3 内部分析記法の非露出

次をHuman本文へ直接表示しない。

- SDM内部ID
- Relation Type名
- confidence値
- Source Inventory
- Coverage分類記号
- 分類監査記録
- machine-oriented graph表現
- 生成過程の自己評価

ただし、内部記法を隠すことを情報量削減の理由にしてはならない。内部IDの代わりに、人間が理解できる設計説明へ変換する。

### 4.4 省略規則

次の実装レベル詳細は、本文の理解に不要なら完全列挙せず、正確な参照先へ委譲できる。

- Test IDごとのExact Expected Result
- Acceptance Criteria全文
- WinError等の数値一覧
- byte-level encoding仕様
- frozen baselineの具体値
- implementation-specific enumの全値
- module dependency DAGの完全形

一方、値集合そのものが人間の判断、運用、監査、安全条件を構成するclosed setは省略しない。

- TEST / PAPER / LIVE
- User Status
- Priority / Severity
- Runner Lifecycle State
- Human approval state
- System Critical closed class
- その他、値により安全挙動が変化する分類

省略時は黙って削除せず、次の形で参照を残す。

```text
詳細は `<canonical artifact path>` の該当節を参照。
```

### 4.5 横断安全条件

次を本文から削除・弱化しない。

- Human-in-the-loop
- Broker APIによる自動発注禁止
- TEST / PAPER / LIVE分離
- Fail Closed
- Risk Control
- Environment Binding
- Human Approval
- Cost Control
- Single Writer
- Atomic Commit
- Canonical State
- 外部APIのBusiness Logicからの直接呼出し禁止
- ExternalServiceGateway / Provider Adapter境界
- 有料サービス、増額、有料Fallbackの無断追加禁止
- Raw immutable data
- marker identity検証

安全条件を独立章へ隔離するだけでなく、起動、投資処理、状態更新、外部接続、運用、検証の各作用点にも接続する。

## 5. 必須View

章構成は読みやすさに応じて決めてよいが、最低限、次のViewを欠落させない。

1. 文書の位置づけ、正本、対象読者
2. 目的、背景、対象範囲、対象外
3. システム全体像と主要責務
4. Human、argus、AI、External Serviceの役割とauthority
5. 投資候補形成、独立分析、矛盾保持、Report
6. Risk検証、Human Decision、Broker Operation、Execution Fact
7. Portfolio、Allocation、保持・売却・損切
8. Position / Candidate / Market Watchと再評価
9. 投資処理の閉ループ
10. Runtime Identity、Environment Binding、起動
11. Runner、Job、停止・再開・retry・recovery
12. Canonical State、Data、Config、Raw / Normalized / Archive / Backup
13. Human operation、User Status、Queue、notification、incident、correction
14. ExternalServiceGateway、Provider Adapter、service health、cost governance
15. TEST / PAPER / LIVE、Historical Validation、Paper Trading、Live Readiness
16. Capability Verification、evidence、review、approval
17. 学習、Break、改善、変更統制
18. Current / Deferred / Future scopeと未確定事項
19. 用語集とcanonical artifact案内

見出しを埋めるためにSourceにない設計を追加してはならない。該当内容が本当に存在しない場合は、空の説明を作らずCoverageまたはHuman Review上の問題として報告する。

## 6. 図表規則

### 6.1 図

- Mermaidまたはtext diagramを使用できる。
- 一つの図に包含、処理順序、状態遷移を混在させない。
- Humanが担当する判断・操作・入力は、文言だけでも識別できるようにする。
- 色だけに意味を依存させない。
- 図には `図N　短い日本語タイトル` を付け、本文で読み方を説明する。

### 6.2 表

- 表には `表N　短い日本語タイトル` を付ける。
- 単なる箇条書きを表へ変換しない。
- 列の意味が異なるものを一つの列へ混在させない。
- Source参照、責務、やらないこと、Failure時の挙動など、判断に必要な列を省略しない。

## 7. 生成手順

### Step 1: 入力固定

必須入力を確認し、pathとSHA-256を固定する。生成Gateに失敗した場合は停止する。

### Step 2: SDM Coverageの投影計画

SDMの主要意味領域ごとに、System Designの記載位置と表現方法を決める。

- 本文で説明
- 図で補助
- 表で整理
- 詳細は別artifactへ委譲するが本文で存在・目的・境界を保持
- Human Reviewとして記載

投影先のない主要意味を残さない。この対応は生成作業中の確認情報とし、独立した恒久artifactを新設しない。

### Step 3: 本文生成

課題から機構名を説明する順にする。機構名の羅列から始めない。各主要領域で、必要に応じて次を追跡できるようにする。

```text
何が困るか
  → 何を守る・実現するか
  → 誰が責任を持つか
  → どの仕組みを使うか
  → 失敗時にどう振る舞うか
  → どこで厳密仕様を確認するか
```

### Step 4: 参照検証

- pathが現在のrepositoryに存在するか確認する。
- `System Design §...`のような自己参照で、本来のDesign Source参照を置換しない。
- Contract名とpathを一致させる。
- Test Strategyの参照節が検証内容と一致するか確認する。
- 存在しない節番号を推測しない。

### Step 5: 意味保存検証

SDMの主要Purpose、Policy、Rule、Function、Process、State、Data、Boundary、Verificationが、本文または明示的参照として追跡できることを確認する。

### Step 6: Reader Test

可能であれば、生成Actorとは別のActorが、Design SourceやSDMを見ずに生成文書だけを読み、次を説明できるか確認する。

- argusの目的と対象外
- Humanとargusの責任境界
- 投資処理の閉ループ
- 自動発注しない理由と境界
- 状態更新と環境分離の安全条件
- 停止・復帰時の考え方
- 外部サービスと費用の統制
- 検証と改善の流れ
- 未確定事項の所在

回答不能な項目は、読者の知識で補完せず文書不足候補として記録する。その後、Design SourceとSDMを使って意味保存を再確認する。

## 8. 禁止事項

- Design Source、SDM、ADR、Contracts、Test Strategyを変更しない。
- 既存System Designを上書きしない。
- SDMにない設計分類を本文で新設しない。
- 説明を自然にするためにPurpose、Policy、Rule、運用条件を創作しない。
- 詳細委譲を理由にCapabilityや安全境界を消さない。
- 分量を減らすために安全条件を削らない。
- 開発進捗や一時的なCapability stateを恒久設計へ混入させない。
- System Designを分類報告書、生成監査報告書、Coverage報告書にしない。
- 内部分析記法をHumanへ理解させる前提にしない。

## 9. 完了条件

次をすべて満たした場合だけ生成完了とする。

- 必須Viewがすべて存在するか、正当な理由付きでHuman Reviewへ送られている。
- SDMの主要設計意味に投影先がある。
- 投資方針・規則、保持・売却・損切、Watch、Allocationが理解できる。
- 投資判断からExecution Fact、Canonical State、監視、再評価までの閉ループが理解できる。
- 起動、継続実行、停止、復帰、再試行、復旧が理解できる。
- Human-in-the-loopと自動発注禁止が明確である。
- TEST / PAPER / LIVE分離とfail-closed境界が明確である。
- Single Writer + Atomic Commitが状態更新へ接続されている。
- ExternalServiceGateway / Provider Adapterと費用統制が理解できる。
- 詳細委譲先が正確である。
- 内部分析記法が本文へ露出していない。
- SourceまたはSDMにない確定設計事実を追加していない。
- Human Review項目が確定事項と区別されている。

## 10. 完了報告

生成物とは別に、次を簡潔に報告する。

1. 入力pathとSHA-256
2. 出力pathとSHA-256
3. 必須ViewのCoverage結果
4. 詳細を委譲した領域と参照先
5. 横断安全条件の記載位置
6. Reader Testの結果、または未実施理由
7. Human Review項目
8. 既存ファイルと正本を変更していないこと

成功時の最終行:

```text
HUMAN_SYSTEM_DESIGN_GENERATED_FROM_STRUCTURED_MODEL
```

生成を安全に完了できない場合の最終行:

```text
HUMAN_SYSTEM_DESIGN_GENERATION_BLOCKED
```
