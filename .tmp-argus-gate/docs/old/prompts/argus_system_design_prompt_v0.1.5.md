# ARGUS Human System Design Transformation Prompt v0.1.5

**Status:** ACTIVE  
**Purpose:** Design Source と Structured Design Data（SDD）から、Human が通常の設計書として読める Human System Design（HSD）を新規投影する。  
**Output:** `docs/design/argus_system_design_v0.1.5.md`

## 1. Authority と入力

設計内容の唯一の一次資料は `docs/source/argus_design_source_v0.1.md` である。`docs/model/argus_structured_design_data_v0.1.md` は、その意味、関係、状態機械、制約を構造化した正式 Artifact であり、HSD の Coverage を組み立てる入力とする。両者が衝突する場合は Design Source を優先し、衝突を明示して Human Review へ送る。

次も入力とし、現在有効な決定、契約、検証境界を反映する。

- `docs/adr/argus_architecture_decision_records_v0.1.md`
- `docs/contracts/` 配下の applicable Contracts
- `docs/test/argus_test_strategy_v0.1.3.md`
- `docs/development/argus_ai_development_operating_rules_v0.1.md`

Transformation Prompt は変換方法の実行仕様であって Design Authority ではない。入力にない機能、既定値、状態、例外、依存関係を創作してはならない。未確定事項を一般論や推奨値で埋めず、未確定のまま表示する。

## 2. 中心原則

> 構造は作るが、情報量を減らさない。

HSD 変換は要約ではない。SDD の機械的 label や全 Source locator をそのまま列挙する必要はないが、それらが保持する設計意味、関係、粒度を失ってはならない。「Human-facing」は「短くする」という意味ではない。

Coverage は名称の出現ではなく、Human が目的、責務、境界、処理、状態、異常時挙動、他領域との関係を追跡できるかで判定する。詳細参照先や Capability 名だけを示して Coverage 済みとしてはならない。

## 3. Final Specification Rule

出力は現在有効な最終仕様だけで自己完結させる。過去版 Prompt、過去版 HSD、変更履歴を Normative dependency にしない。過去情報が必要なら、本文とは分離した Non-Normative provenance とする。

Current、Deferred、Future を混同しない。Deferred / Future を現在実装済みまたは現在要求される動作として書かない。実装状態と設計状態も分離する。

## 4. 文書階層

HSD は次の三層を持つ。

1. **全体設計:** 目的、Scope、Architecture、Actor / Authority、全体処理、横断安全原則。
2. **領域設計:** Candidate / Analysis、Decision / Approval、Execution、Portfolio、Watch、Runtime、State、Data、Human Operation / Notification、External Service、Verification / Change。
3. **個別設計要素:** 各領域の責務、入出力、処理、規則、状態、異常系、関係を追跡できる粒度。

Level 1 だけを書き、Level 2 / 3 を数段落へ圧縮してはならない。

## 5. 文書構造 Contract

各主要章は原則として次の構造を持つ。

```text
## N. 章名

その章の設計領域、明らかにする内容、必要な前後関係を述べる柱書。

### N.1 節

本文。必要に応じて図表。

### N.2 節

本文。必要に応じて図表。
```

### 5.1 柱書

全主要章の見出し直後に、自然な文章の柱書を置く。柱書は、その章が扱う設計領域と明らかにする内容を示し、必要なら前後章との関係を示す。表、図、箇条書きだけで代替しない。全章へ同一の定型文を付けるだけでは足りない。

### 5.2 節分割

独立した責務、入出力、規則、状態を持つ設計事項は節へ分ける。Allocation、Risk Validation、Human Approval、Broker Operation、Execution Fact などを一段落へ詰め込まない。

### 5.3 設計要素の記述

Source / SDD に存在する範囲で、各要素について必要な次の意味を明示する。

- 目的、責務、Authority / Actor
- 入力、前提、処理、出力
- 状態、遷移条件、規則
- 制約、禁止、異常時処理
- 他領域との関係、未確定事項、詳細参照先

すべての節に同じ小見出しを機械的に並べる必要はない。ただし、存在する意味を散文一段落へ圧縮しない。

## 6. 図表設計 Contract

図表は装飾ではなく、設計関係を正確に理解させるために使う。

- 処理分岐・処理順序: フローチャート
- Actor / Component 間の時系列 Interaction: シーケンス図
- State と遷移条件: 状態遷移図
- Component / Boundary / dependency: 構成図
- Data の流れ: Data Flow 図
- 単純な階層または短い補助説明: text diagram

Mermaid を使用してよい。処理順序や Interaction を理由なく ASCII / text diagram に簡略化しない。図の前に何を示すか説明し、必要なら図の後に読み方と制約を書く。表は比較、対応、責務分担、条件整理に使用し、章見出し直後へ説明なしに置かない。図表に入れたために本文から設計意図を消してはならない。

## 7. 必須 Coverage

最低限、次を相互関係とともに展開する。

- 目的、非目的、Human-in-the-loop、自動発注禁止
- Canonical authority、Actor、責務分離
- Local-first、intermittent runtime、persistent loop、retryable job、transaction boundary、clock / catch-up
- Configuration / Identity / Status / State / Artifact の分離、Runtime Foundation
- Canonical State、Single Writer、Atomic Commit、Raw immutable data、Data Root identity
- Candidate、複数探索枝、Analysis、Contradiction、Report、Declared Reason Weight
- Allocation、Hard Risk Validator、Proposal、Approval、Order、Execution Paste
- Portfolio Policy、役割、保持期間、売却、追加投資、Watch
- User Status、BUSY lease、Decision Queue、Incident、Alert、Notification
- External Service Gateway、Provider Adapter、paid service governance、model budget
- Secret、backup / restore、storage pressure、safe degradation
- Audit、Decision Log、evaluation、Break、version
- Historical validation、Paper / Live gate、time gate
- Test architecture、CV / RV、Evidence、Critical Path Review、MVS、implementation order
- Current / Deferred / Future と未確定事項

複数の独立要素を一段落へまとめること、名称を一度記載して Coverage 済みとすること、参照先だけを示して HSD で理解すべき境界を削ることを禁止する。

## 8. 日本語絶対条件

主要本文、章節見出し、表、図ラベル、説明は日本語とする。ARGUS、TEST / PAPER / LIVE、API、CV / RV、Fail Closed、正式 identifier、path、code identifier、製品名など、精度維持に必要な固定語だけ原語を許容する。

## 9. Source fidelity と矛盾処理

- Source にない設計を創作しない。
- SDD の分類を独断で修正しない。
- Design Source と派生文書が衝突したら Design Source を優先し、衝突を報告する。
- Contract はその Capability の exact boundary として反映するが、Design Source を上書きする根拠にしない。
- 不明点、hash mismatch、Canonical ambiguity があれば Fail Closed とする。

## 10. Human Review を可能にする品質

HSD 単体を通常の設計書として読み、Human が次を評価できることを完成条件とする。

- 設計として自然で意図どおりか
- 抜けがなく、責務と Authority が正しいか
- 処理、状態遷移、異常時挙動が成立するか
- 不自然な分類や矛盾がないか
- Current / Deferred / Future、設計済み / 実装済みを識別できるか

チェックリストは補助であり本文の代替ではない。

## 11. 生成手順

1. 全必須入力の path と SHA-256 を固定する。
2. Design Source の current normative 部分と SDD の設計領域を対応付ける。
3. ADR、Contracts、Test Strategy、Operating Rules から applicable boundary を統合する。
4. 文書の章構造と Coverage map を作る。
5. 各章を、柱書、節、本文、必要な図表の順に新規生成する。
6. Source fidelity、情報量、状態、異常系、横断関係を照合する。
7. Gate S1〜S5を実行し、失敗時は成功と報告しない。

既存 HSD を編集・増補したり、その文章、章構成、図表をテンプレートとして使ったりしてはならない。

## 12. 生成後 Gate

### Gate S1: 柱書

全主要章の見出し直後に、その章の設計領域と目的を理解できる柱書がある。見出し直後が表、図、箇条書きだけなら FAIL。

### Gate S2: 節構造

複数の独立設計要素を持つ章が適切に節分割され、数段落だけへ圧縮されていない。

### Gate S3: 図法

処理順序、Interaction、状態遷移、構造関係に適した図法を使う。特にシステム全体の投資閉ループを単純 text diagram だけで済ませない。

### Gate S4: 情報量

主要設計意味について、名称だけでなく目的、責務、境界、処理、規則、状態、異常系、関係を Source / SDD に存在する範囲で追跡できる。

### Gate S5: 設計書としての可読性

最初の4章だけを独立して読み、各章の役割、図表の目的、前後章との接続が分かり、図表を推測で解釈する必要がない。

## 13. 出力と停止条件

出力先に同名ファイルが存在する場合は上書きせず停止する。全 Gate PASS の場合だけ生成成功とする。一つでも FAIL の場合は局所補修で隠さず、原因と該当箇所を報告して Human Review を待つ。Final Freeze は実行しない。
