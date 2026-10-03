# argus Structured Design Model v0.1.1

**Document Role:** `STRUCTURED_DESIGN_MODEL`  
**Status:** `HUMAN_REVIEW_REQUIRED`  
**Purpose:** argus の設計対象に関する知識を、System Design を生成するための内部入力として整理する。  
**Version policy:** v0.1 と v0.2 は履歴として変更しない。本書は人向け設計書へ内部分析情報を漏出させないための修正版である。

## 1. 入力

- `docs/source/argus_design_source_v0.1.md`
- `docs/adr/argus_architecture_decision_records_v0.1.md`
- `docs/contracts/` 配下の関連契約
- `docs/test/argus_test_strategy_v0.1.3.md`

入力にない運用値や未決の方針は補完しない。

## 2. 設計対象

argus の設計対象には、実行環境、課題、目的、要求と制約、設計原則、機能、アクター、ロール、状態、データ、設定、処理、実現方式、外部主体、検証、成果物がある。

Human、argus、AI、外部サービスはアクターであり、同じアクターが複数のロールを持ち得る。Human は投資判断者、Broker 操作者、実行事実記録者となる。argus は判断待ち管理と Canonical State の単一書込みを担う。AI は分析を補助するが、最終投資判断は行わない。外部サービスはデータ又はモデル結果を提供し、argus の状態を更新しない。

設計の理解では、処理の所属、利用する設定・サービス、入力と出力、処理順序、状態遷移、実行主体、実現方式、制約、検証根拠、開発依存と実行時依存を区別して保持する。この区別は内部で用い、Human System Design にその分析記法を出力しない。

## 3. 投資判断と状態反映

投資判断の処理は、候補収集、独立分析と矛盾保持、リスク検証、判断待ち登録、Human による投資判断、Human による Broker application の手動操作、実行事実の記録と検証、Portfolio state の反映から成る。

Risk Validator は判断前の検証機能、Decision Queue は判断待ちを扱う機能と耐久情報、Human Decision は判断の記録、Execution Fact は外部で発生した事実、Portfolio Commit は状態を反映する処理である。状態反映は Single Writer と Atomic Commit により実現する。

## 4. Runtime

起動は、実行入口の解決、identity と manifest の検証、Data Root marker の検証、有効 Configuration の固定、復旧状態の確認、通常運転への移行から成る。

Runner は継続実行を管理し、Persistent Loop が Job を開始する。Job は開始時点の Config Snapshot と Business Service を利用し、必要な途中成果を durable に保存する。Business Service は ExternalServiceGateway と Provider Adapter を通じて外部サービスを利用する。Portfolio state の更新は、確認済みの実行事実を基に行う。

## 5. 状態、データ、保存

Canonical State と Runtime State は現在の状況を表す。Raw data、Normalized data、Report、Execution Fact、Config Snapshot は処理対象又は根拠となるデータである。更新は Portfolio Commit により行われ、Raw data は immutable として保存する。archive、log、backup は追跡と復旧のための保存方針であり、現在状態とは区別する。

## 6. 実行環境とデータ保存領域

実行環境（Environment）は `TEST`、`PAPER`、`LIVE` を隔離する安全境界である。データ保存領域（Data Root）は runtime がデータを保存・参照する領域である。path や drive letter は保存先を見つける locator であり、論理 identity ではない。marker の `state_id`、`data_root_id`、`environment` を expected binding と照合し、不一致時には開始又は書込みを拒否する。

## 7. 外部接続

外部サービスは変更、終了、API 仕様差分、料金・利用上限、障害の影響を受ける。この影響を業務ロジックへ直接伝播させないため、ExternalServiceGateway と Provider Adapter を境界として用いる。

J-Quants は market data、EDINET は disclosure、OpenAI は model provider の候補である。Broker application は Human が手動操作する対象である。新しい有料サービス、料金増額、有料 fallback は Human の明示承認なしに導入しない。

Historical Provider と Local Stub の実運用方針は入力から一意に決定できないため、Human Review の対象とする。

## 8. 検証

最上位の目的は「投資システムとして、投資判断の妥当性を高めること。」である。過去データでのロジック検証、Paper data による将来の振る舞いの検証、変更による既存能力の破壊防止、検証結果の信頼性維持を行う。検証の信頼性には、未来情報の混入防止、evidence と対象の不一致防止、必要な独立レビューが含まれる。

## 9. 未決事項

- 利用データ時刻が as-of、取得時刻、処理時刻のどれを指すか。
- Historical Provider / Local Stub の実運用方針。
- startup manifest の正確な schema。

これらは Design Authority（ChatGPT + Human）が判断するまで確定事項として扱わない。

