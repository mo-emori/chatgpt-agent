# argus System Design生成プロンプト v0.1

```text
role: HUMAN_SYSTEM_DESIGN_GENERATION_INSTRUCTION
version: 0.1
status: CURRENT
source_lineage: old/argus_design_prompt_v0.1.11.md
required_input: docs/model/argus_structured_design_model_v0.1.md
default_output: docs/design/argus_system_design_v0.1.md
```

## 1. 生成経路

`Design Source → Structured Design Model → Human Review → System Design`を維持する。System DesignでDesign Sourceを再分類しない。Modelが存在しない、または入力実体と対応しない場合は直接生成せず、Model再生成を要求する。

## 2. 役割

System Designは普通の人間向け設計書として、課題、目的、設計、効果、機能構造、処理、責務境界を説明する。個別Capabilityのexact仕様はContracts、選択理由はADR、検証規則はTest Strategyへ委ねる。

## 3. 表現

- 本文は理解・課題・目的、図は構造・関係・順序、表は分類・比較・対応を担う。
- 一般的なIT用語は使用できるが、意味不明な英語を本文へ漏らさない。
- Formal Nameは日本語表示名に併記する。
- 課題→目的→要求/制約→設計原則→機能→実現方式→効果を追えるようにする。
- 実現方式は交換可能なDesign Choiceであり、課題や目的と同一視しない。

## 4. 必須View

- environmentのCurrent/Future
- 責務・包含構造
- 投資機能の包含と処理循環（別々に表示）
- アクターとロール。Human stepは文言と視覚表現で区別
- 起動処理の意味上の順序と正式mechanism対応
- state/dataの機能構造
- 外部serviceの課題→目的→原則→方式→効果
- 各verificationの目的、実施内容、効果
- capability stateを含まない機能一覧
- terminologyとsource案内
- Human Review継続事項

## 5. 境界

開発のCURRENT/COMPLETE等を複製しない。Capability Registry/Development Progressへ案内する。不明な分類、ownership、shared service、data時刻、Historical Providerへのoperational policy適用を確定しない。新しいCanonical assertionを追加しない。

## 6. 完了条件

Structured Design Modelの分類・関係・確度を保ち、Humanが環境、課題、目的、機能、実現方式、効果を追跡できること。投資処理・起動・state/data・external service・verificationが、それぞれ適した図表で説明されること。
