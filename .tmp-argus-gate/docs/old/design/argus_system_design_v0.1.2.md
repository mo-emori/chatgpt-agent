# argus System Design v0.1.2

**Document Role:** `HUMAN_SYSTEM_DESIGN`  
**Base:** v0.1  
**Status:** `HUMAN_REVIEW_REQUIRED`

## 目次

1. 目的と設計原則  
2. システム全体像  
3. 投資判断とポートフォリオ運用  
4. 監視、運用、Human連携  
5. 起動と継続実行  
6. 状態、データ、保存先  
7. 外部サービスと費用統制  
8. 検証と改善  
9. 用語と参照先  
10. 継続検討事項

## 1. 目的と設計原則

argus は、人間の投資判断を支援するローカル優先のシステムです。AI は分析を補助しますが、投資判断と証券会社アプリケーションの操作は Human が担います。自動発注は行いません。安全上重要な確認ができない場合は処理を進めず、TEST、PAPER、LIVE の状態とデータを混在させません。

## 2. システム全体像

**図1　argusの全体像**

```text
市場・開示・モデルなどの外部情報
             ↓
  収集・分析・報告・リスク確認
             ↓
       Humanによる投資判断
             ↓
 Humanによる証券会社アプリの操作
             ↓
  実行事実の確認と状態への安全な反映
```

**表1　主な責務**

| No. | 担当 | 主な責務 |
|---:|---|---|
| 1 | Human | 投資判断、証券会社アプリの操作、実行事実の確認 |
| 2 | argus | 分析支援、判断待ち管理、状態の安全な更新 |
| 3 | AI | 独立分析と矛盾の可視化の補助 |
| 4 | 外部サービス | 市場、開示、モデル等の情報提供 |

## 3. 投資判断とポートフォリオ運用

候補を選び、複数の観点で分析し、矛盾も含めて報告します。リスク確認後の提案は判断待ちとして保持され、Human が判断します。Human が証券会社アプリを手動操作した後、確認済みの実行事実だけをポートフォリオ状態へ反映します。

**表2　投資方針と規則の扱い**

| No. | 領域 | 設計上の扱い |
|---:|---|---|
| 1 | 保有期間 | SHORT、MEDIUM、LONG/CORE の方針を区別する |
| 2 | 高配当Core | ポートフォリオ方針として扱う |
| 3 | 売却・損切 | Thesis、Risk、Price、Time/Opportunity Cost の観点を持つ |
| 4 | Allocation | リスクとポートフォリオ方針に従う |
| 5 | Risk | 判断前に決定論的な確認を行う |

## 4. 監視、運用、Human連携

Position Watch、Candidate Watch、Market Watch は、それぞれ保有、候補、市場の変化を継続的に確認します。変化により前提が崩れた場合は、再評価又は判断待ちへ戻します。User Status、Decision Queue、incident、通知は、人間が対応可能なタイミングと優先度を扱うための運用基盤です。Human correction は既存の判断・事実を黙って置換せず、追跡可能な形で扱います。

## 5. 起動と継続実行

起動時には、正しい入口、実行環境、データ保存領域、設定、前回状態を確認してから通常運転へ進みます。継続実行では Runner が処理を管理し、各 Job は処理条件を固定して必要な途中成果を保存します。障害、再試行、停止、復旧は local-first の断続的な実行環境を前提に扱います。

## 6. 状態、データ、保存先

Canonical State は単一の書込み担当が一貫して更新します。Raw data は書き換えずに保持し、共通形式へ変換したデータ、報告、実行事実、設定、ログ、archive、backup を目的に応じて管理します。データ保存領域は path や drive letter だけで信用せず、環境と論理的な識別情報を確認します。詳細な環境結合の仕様は Environment Binding Contract を参照します。

## 7. 外部サービスと費用統制

J-Quants は市場データ、EDINET は開示情報、Model Provider は分析補助の候補です。外部接続は共通窓口と提供元ごとの接続部に分け、提供元の変更や障害の影響を局所化します。新しい有料サービス、料金増額、追加機能、有料の代替手段は明示的な Human 承認なしに導入しません。

## 8. 検証と改善

過去データ、Paper運用、回帰確認、provider確認、evidence、review を通じて設計と実装の妥当性を確かめます。Test Strategy は baseline、oracle、evidence、CV/RV、静的解析、Critical Path Review の運用を定めます。検証結果、Human correction、障害・改善提案は、次の改善へ追跡可能な形で戻します。

## 9. 用語と参照先

実行環境（Environment）は TEST/PAPER/LIVE の安全境界です。データ保存領域（Data Root）は保存先を表し、論理 identity そのものではありません。正確な runtime identity、environment binding、bootstrap、検証運用は `docs/contracts/` と `docs/test/argus_test_strategy_v0.1.3.md` を参照します。アーキテクチャ判断は `docs/adr/argus_architecture_decision_records_v0.1.md` を参照します。

## 10. 継続検討事項

データ時刻の正確な意味、Historical Provider/Local Stub の実運用方針、startup manifest の正確な形式は、根拠を確認した上で Human Review により決定します。

