# argus System Design v0.1.3

**Document Role:** `HUMAN_SYSTEM_DESIGN`  
**Status:** `HUMAN_REVIEW_REQUIRED`  
**Structured Design Model:** `docs/model/argus_structured_design_model_v0.1.3.md`  
**Design Source:** `docs/source/argus_design_source_v0.1.md`

## 1. 目的と全体像

argus は、人間の投資判断を支援する local-first の投資システムです。AI は分析を補助しますが、最終的な判断と証券会社アプリケーションの操作は Human が行います。Broker API による自動発注は行いません。

**図1　投資判断から状態反映まで**

```text
市場・開示情報 → 分析・報告 → リスク確認 → Humanの判断
→ Humanによる証券会社アプリ操作 → 実行事実の確認 → 状態反映
```

## 2. 投資処理と方針

候補選定、独立分析、矛盾の保持、報告、リスク確認、判断待ち、人間の判断、実行事実の確認、Portfolioへの反映を一連の流れとして扱います。保有期間、Portfolio方針、高配当Core、Allocation、売却・損切、再評価は投資方針と規則として保持します。停止判断では thesis、risk、price、time/opportunity cost を考慮します。

## 3. Watchと運用

Position Watch、Candidate Watch、Market Watch により、保有・候補・市場の変化を観察します。変化や前提崩れは再評価又はBreakへつなげます。User Status、Decision Queue、通知、incident、優先度は、人間が対応する運用上の境界です。Human correction は追跡可能な形で扱います。

## 4. 起動と継続実行

起動時には、正しい入口、runtime identity、実行環境、データ保存領域、設定、前回状態を確認してから通常運転へ進みます。Runner は継続実行を管理し、Job は再試行、途中成果の保存、復旧、状態反映を安全に扱います。停止、復帰、recovery は断続的な local runtime を前提にします。

## 5. 状態、データ、保存先

Canonical State は Single Writer と Atomic Commit により更新します。Raw data は書き換えずに保存し、normalized data、report、decision record、execution fact、configuration、archive、backup を用途に応じて管理します。TEST、PAPER、LIVEの混在は防止します。Data Root は path や drive letter だけで判断せず、marker identity を確認します。正確な仕様は Environment Binding Contract を参照します。

## 6. 外部接続と費用統制

J-Quants、EDINET、Model Provider は外部サービスです。ExternalServiceGateway と Provider Adapter により、外部仕様や障害の影響を業務ロジックから隔離します。新しい有料サービス、料金増額、有料fallbackは、明示的なHuman承認なしに導入しません。

## 7. 検証と改善

Historical validation、Paper validation、Regression、provider確認、security/secret確認、baseline、evidence、reviewを通じて設計と実装を検証します。Test StrategyはCV/RV、oracle、evidence、静的解析、Critical Path Reviewを定めます。検証結果とHuman Reviewは、改善ループへ追跡可能な形で戻します。

## 8. 参照先と未決事項

正確なruntime identity、environment binding、bootstrapは `docs/contracts/`、検証運用は `docs/test/argus_test_strategy_v0.1.3.md`、Providerおよび費用に関する決定は `docs/adr/argus_architecture_decision_records_v0.1.md` を参照します。authority artifactで固定されない運用値、schema、artifact間の実質的衝突はHuman Reviewへ戻します。

