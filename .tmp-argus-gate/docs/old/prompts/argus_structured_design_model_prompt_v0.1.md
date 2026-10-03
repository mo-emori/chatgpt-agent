# argus Structured Design Model生成プロンプト v0.1

```text
role: STRUCTURED_DESIGN_MODEL_GENERATION_INSTRUCTION
version: 0.1
status: CURRENT
source_lineage: ARGUS-P-0033-v1 structure-analysis prompt artifact
default_output: docs/model/argus_structured_design_model_v0.1.md
```

## 1. 目的

Design Source、Contracts、ADR、Test Strategy等を、System Designより前のStructured Design Modelへ変換する。Modelは環境、因果、機能、関係、用語、実現方式を構造化し、将来のgraph表現へ移行可能にする。

## 2. 生成順序

```text
Design Source上の位置
→ 情報要素抽出
→ 固有名詞・設計用語抽出
→ 用語対応表作成
→ 意味分類
→ 関係型付け
→ 課題・目的・方針・対策
→ 因果・論理構造候補
```

正本の章を並べ替えただけの成果物を禁止する。Human Viewの章立てや表現を先に決めない。

## 3. 用語対応表

Human Viewへ露出し得る設計概念について、次を記録する。

| Formal Name / English | 日本語表示名 | 意味 | Human Viewでの用例 | 使用上の注意 | Canonical Source |
|---|---|---|---|---|---|

単語ではなく設計概念単位で登録する。同じ英語でも意味が異なる場合は別entryにする。例えば`Atomic Commit`は「状態の一括確定」、`Portfolio commit`は「ポートフォリオへの確定反映」、`cost commitment`は「費用発生の確定」とする。

### 3.1 日本語表示名

- 本文で自然に読める日本語を優先する。
- Formal Nameが検索・同定に必要なら`日本語表示名（Formal Name）`とする。
- API/type/enum/identifierは変更しない。
- カタカナ化で意味が伝わらない場合は意味訳する。
- 日本語化でCanonicalな意味を変えない。
- 訳語が一意でなければ候補と注意を記録し、Human Review対象にする。

## 4. 情報要素・分類・関係

目的、課題、要求、制約、機能、責務、actor、state、lifecycle、process、decision、validation、安全/runtime/storage mechanism、external boundary、cost、environment、test stage、deferred scope、development dependencyを抽出する。

意味で分類し、主分類と横断関係を分ける。包含、構成、処理順序、開発依存、実行時依存、入出力、状態遷移、制約、検証、参照、横断を型付けし、包含・順序・依存を混同しない。

## 5. 課題の口語化

背景、課題、目的、現在の方針、現在の対策、Canonical Sourceを分離する。課題は人間の言葉で「何が起きると困るのか？」へ答える。機構名から説明を始めず、現在の対策から課題を発明しない。

## 6. 根拠確度

`EXPLICIT`、`SYNTHESIZED`、`INFERRED`、`UNKNOWN`を区別する。INFERREDをHuman Review前に確定せず、複数案は代替案として残す。

## 7. 重点分析

- 起動・実行基盤: 概念包含、起動順、実行依存、開発依存を別々に示す。
- 投資領域: 機能、process、state、decision、横断関心事を分ける。
- 安全・運用: 独立機能とcross-cutting作用を分け、何をどこで守るかを示す。

## 8. 出力Contract

`NONCANONICAL_ANALYSIS`として、入力/hash、用語対応表、情報要素一覧、分類案、型付き関係、課題・目的・方針・対策、論理階層、重点分析、未確定事項、Human Review Pointsを含める。分析用IDはCanonical IDではない。

## 9. 禁止事項

Design Source、Contracts、ADR、Test Strategy、Capability stateを変更しない。System Designの分類を正解として逆輸入しない。不明点を推測で埋めない。

## 10. 完了条件

用語が設計概念単位で日本語表示と対応し、その語彙を使って情報要素・課題・論理構造を読めること。分類・関係型・確度はv0.1から理由なく変更せず、Humanが次段のViewをレビューできること。
