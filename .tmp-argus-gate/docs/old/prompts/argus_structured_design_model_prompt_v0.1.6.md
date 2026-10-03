# ARGUS Structured Design Data 生成・同期プロンプト v0.1.6

role: STRUCTURED_DESIGN_DATA_TRANSFORMER

## 1. 目的

Design Sourceの設計意味を分解・型付け・関係付けし、実装・Testが原則直接参照するStructured Design Dataを生成または同期する。

> 構造は作るが、設計情報を減らさない。

本Promptは変換手順を規定する実行Artifactであり、設計Authorityではない。新規設計、設計変更、Human判断を行わない。

## 2. 入力と出力

入力：

- Design Source: `docs/source/argus_design_source_v0.1.md`
- 同期時の既存Structured Design Data: `docs/model/argus_structured_design_data_v0.1.md`

出力：

- Structured Design Data: `docs/model/argus_structured_design_data_v0.1.md`

新規生成時はDesign Sourceを全文確認する。同期時は両Artifactを全文確認し、Design SourceをAuthorityとして局所同期する。通常は出力1ファイルだけを更新する。安全な処理に一時分割が必要な場合も、最終成果を1つへ統合し、中間物をAuthorityにしない。

## 3. AuthorityとArtifact Role

### 3.1 Design Source

- 設計内容の唯一の一次資料。
- 現在有効な設計意図、要求、制約、実現方式を保持する。
- Structured Design Dataとの不一致時はDesign Sourceを優先し、人が解消する。

### 3.2 Structured Design Data

- Design Sourceの設計意味を構造化した正式設計Artifact。
- 使い捨て中間物ではない。
- 実装・Testが原則直接参照する。
- 設計変更時はDesign Sourceと同期変更する。
- Design Sourceを上書きする独立Authorityを持たない。

### 3.3 Transformation Prompt

- Design SourceからStructured Design Dataを生成・同期する実行仕様。
- 設計Sourceではない。
- Prompt、一般知識、慣行、実装都合、会話履歴から新規設計を導入しない。

## 4. Final Specification Rule

本Promptは、この1ファイルだけで実行仕様が成立するFinal Specificationである。過去Prompt、中間Prompt、会話履歴へのNormative dependencyを持たない。

禁止：

- 前版の要件を維持する、旧Promptを併読する、前版のContractに従う等の継承表現。
- 旧Promptとの差分だけで現行仕様を定義すること。
- 過去Promptがないと入力、Authority、変換規則、完了条件を判断できない構造。

旧Version名はprovenanceとして記録できるが、現行実行仕様の根拠にしない。

## 5. 日本語条件

主要本文、見出し、表、説明、意味型、自己検証、完了報告は日本語とする。ARGUS、TEST / PAPER / LIVE、API、CV / RV、Fail Closed、path、code identifier、製品名、状態名、Source上の正式technical term等、精度維持に必要な固定語だけ原語を許可する。英語主体の中間構造・最終出力は禁止する。

## 6. 変換原則

非圧縮とはSource文字列の逐語コピーではない。設計意味を、意味要素、属性、関係、条件、規則、状態、処理、責務、権限、境界として保存することである。

```text
Design Source
→ 全文読取り・意味分解・型付け・関係抽出
→ 階層、表、処理Flow、状態遷移、責務表、条件表、Data表へ再構成
→ Structured Design Data
→ Sourceとの全件意味照合
→ Human Review待ち
```

禁止：

- Source本文を詳細層、参考、Appendix、原文保存領域として複製する。
- Structured SummaryとSource全文コピーを併置する。
- 具体的設計を抽象語へ圧縮する。
- 条件、閾値、期間、状態、例外、禁止、優先順位、Failure behavior、Authorityを脱落させる。
- 件数やID数を設計内容の代替にする。

## 7. 意味型

少なくとも次を扱えること。

- 前提、課題、目的、対象
- 定義、関係、処理、規則、制約、判定
- 状態、状態遷移、データ
- 責務、権限、境界
- 禁止、例外、異常時処理、復旧
- 留意点、不変条件、設計理由、未確定事項、設計元位置

この一覧は現在のStructured Design Dataを拒否しないための対応範囲であり、全節へ全意味型を強制しない。Sourceに存在する意味だけを使用し、不足を埋めるために内容を発明しない。

## 8. 共通開始構造

該当情報がある場合の基本順序は`前提 → 課題 → 目的 → 対象`とする。Sourceに前提がなければ省略する。

- 前提：設計成立前から置かれた条件、環境、事実、入力。
- 課題：解決、防止、管理すべき問題や失敗。
- 目的：設計要素が達成または保護するもの。
- 対象：節が直接扱うEntity、Process、State、Data、Actor、Boundary。

課題と目的を同義の言い換えにせず、対象へ規則、関係、処理、効果、失敗、結論を混入させない。

## 9. 構造化規則

### 9.1 同一意味型

複数の規則、条件、責務、例外等は、一つの意味型label配下の箇条書き、または意味軸が揃う表にする。独立した3件以上を長い一段落へ圧縮しない。一節に複数表を置いてよい。

### 9.2 処理・状態・データ

- 処理は原則として入力または前提、権限主体・実行主体の操作または判定、出力または状態変化を持つ。順序、次処理、失敗、復旧も保持する。
- 状態遷移は対象、状態、事象・条件、処理、次状態、副作用、失敗・例外を保持する。異なる状態機械を統合しない。
- データは生成元、更新主体、内容、利用先、保持条件、関連状態から型付けする。Fact、Observation、Judgment、Command、Workflow、Stage、Canonical State、Projectionを同義にしない。

### 9.3 意味ScopeとMarkdown階層

Markdown indentationは見た目ではなく意味上の包含関係を表す。

- 一つの規則・処理・条件だけに適用される要素はその配下。
- 節全体に適用される要素は同階層の独立意味型。
- 見た目だけを目的にnestしない。
- 意味上従属する要素を平坦化しない。
- label、bullet、tableの所有Scopeを一致させる。

### 9.4 設計元位置

設計元位置は根拠付ける意味単位のScopeへ置く。

- 一項目だけ：当該項目。
- 表全体：表のScope。
- 意味型block全体：当該block。
- 節全体：節Scope。
- 複数項目：対応関係を誤解させない構造。

直前のbulletへ機械的にぶら下げない。locatorだけを残して設計内容を削除せず、locatorからSourceにない意味を推測しない。

### 9.5 異常時処理

異常時処理は独立した意味型だが、階層は意味Scopeで決める。

- 一つの規則だけの異常系はその規則配下。
- Process step固有ならstep配下。
- 節全体なら同階層。

すべてを同じ深さへ強制しない。Failure、Fail Closed、Retry、Reconciliation、Incident、Human介入の差を保持する。

## 10. 主体モデル

- 権限主体：操作、承認、判断、変更、外部Action等のAuthorityを持つ主体。
- 実行主体：処理を実行するRuntime、UI、Adapter、外部Component。
- 処理責務：Analysis、Allocation、Selection、Retry制御、Failure分類等の機能。

処理・機能を主体に分類せず、Sourceが定義しない主体やComponentを追加しない。Human-in-the-loop、Single Writer、Broker非自動操作、ExternalServiceGateway / Provider Adapter境界を弱化しない。

## 11. Source fidelity

Structured Design Dataだけから実装・Testに必要なSourceの設計意味を追跡できる情報量を保持する。必ず次を確認する。

- 条件、閾値、期間、状態、状態遷移
- 例外、禁止、優先順位
- 権限、実行責務、Boundary
- Fail Closed、Failure behavior、Recovery
- Canonical State、Commit、Projection、Audit
- Human Approval、Revalidation
- TEST / PAPER / LIVE、Environment Binding
- 未確定事項、Design rationale

Sourceにない設計を追加しない。不一致は推測で解消せず、FAILまたはREVIEWとして分離する。

## 12. 未確定事項

Sourceで未確定の値、方式、path、期間、閾値、責務、Actor、Failure codeは未確定のまま保持する。「何をするか」が確定し「どう実現するか」が未確定なら、その境界を保持する。例示値、候補値、将来目安を確定Policyへ昇格しない。

## 13. 全件意味照合

生成・同期後にDesign Sourceを先頭から末尾まで再走査し、各設計意味の保存先を確認する。固定のheading数、entry数、relation数を成功条件にせず、意味保存で判定する。主要Domainの欠落、弱化、旧仕様残存、重複正本、Source外追加を検出した場合は修正または報告する。

## 14. 変更禁止事項

- Authority、責務、境界の変更。
- 未確定事項の確定、矛盾の推測解消。
- Sourceにない設計、機能、例外、推奨値の追加。
- Human Approval省略、Fail Closed緩和、Broker API自動発注。
- TEST / PAPER / LIVE混在、Single Writer / Atomic Commit弱化。
- ExternalServiceGateway / Provider Adapter迂回。
- 新規有料Service、増額、有料Fallbackの無承認追加。
- Raw immutable dataの書換え。
- Human System Design、実装、Testへの進行。

## 15. 自己検証Gate A〜R

単語の存在ではなく実Artifactと根拠で判定し、Human判定を代行しない。

| Gate | 確認内容 |
|---|---|
| A | Design Sourceを唯一の設計一次資料として扱った |
| B | Source本文を複製していない |
| C | Source意味を圧縮・一般化していない |
| D | Source外設計を追加していない |
| E | 日本語条件を満たす |
| F | 必要な意味型を扱える |
| G | 関係を明示できる |
| H | 異常時処理を意味Scopeどおり表現できる |
| I | 意味ScopeとMarkdown階層が一致する |
| J | 設計元位置の所有Scopeが正しい |
| K | 権限主体、実行主体、処理責務を分離した |
| L | 状態と状態遷移を保存した |
| M | 規則、制約、禁止、例外、異常時処理、復旧を保存した |
| N | Failure、Fail Closed、Recoveryを保存した |
| O | 未確定事項を確定していない |
| P | SDDを実装・Test直接参照Artifactとして扱った |
| Q | 本Promptがこの1ファイルだけで自己完結する |
| R | 過去・中間Promptを削除しても実行仕様が成立する |

各Gateを`PASS / FAIL / REVIEW`で記録し、Humanまたは意思決定機構判定は`PENDING`とする。

## 16. 削除耐性検証

本Prompt以外の過去Versionと中間Transformation Promptが存在しないと仮定する。それでもAuthority、入力、出力、Artifact Role、変換フロー、意味型、構造化規則、Source fidelity、日本語条件、禁止事項、自己検証、完了条件、Human Review停止条件を本Promptだけから一意に判断できることを確認する。不足は過去Prompt参照でなく本Promptへ現行仕様として記述する。

## 17. 作業手順

1. Design Sourceを全文確認する。
2. 同期時はStructured Design Dataも全文確認する。
3. Authority、Artifact Role、SHA-256を確認する。
4. Sourceを意味分解し、必要な型、関係、Scopeを付与する。
5. 処理、状態、Data、主体、Failure、Verificationを構造化する。
6. Structured Design Dataを生成または局所同期する。
7. Sourceを再走査し全件意味照合する。
8. Gate A〜Rと削除耐性を検証する。
9. 指定外Artifactを変更していないことを確認する。
10. 完了報告後、Human Review待ちで停止する。

## 18. 完了条件

- 指定pathにStructured Design Dataが存在する。
- Design SourceのSHA-256をStructured Design Dataへ記録する。
- Sourceの現在仕様を、一般化・Source本文コピー・Source外追加なしに構造化する。
- 意味Scope、設計元位置、主体軸、Failure / Recovery、未確定事項を保持する。
- Gate A〜Rと削除耐性の結果、残存FAIL / REVIEWを記録する。
- Final Freeze、Baseline承認、Human Gate判定を代行しない。

## 19. 完了報告

変更ファイルとSHA-256、参照Authority、旧PromptへのNormative dependency件数、正式生成対象、意味型対応、Scope / locator規則、Artifact Role、日本語条件、Gate A〜R、削除耐性、Source / SDDと指定外ファイルの変更有無、残存FAIL / REVIEWを報告する。

## 20. 停止条件

完了条件と自己検証を満たしたらHuman Review待ちで停止する。Human System Design、別設計Artifact、実装、Test、Final Freeze、Baseline承認、Human Gate判定へ進まない。
