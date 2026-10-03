# ARGUS Human System Design Transformation Prompt v0.1.6

## 1. 目的

Structured Design Data（SDD）から、HumanがARGUSのシステム設計を理解し、レビューできるHuman System Design（HSD）を生成する。

SDDではDesign Sourceに含まれる設計情報の意味分解、分類、関係整理、構造化が完了している。本変換の主処理は、SDDに保持された設計情報をHumanが理解しやすい章、節、図、表、文章へ組み換えることである。

HSDはSDDの要約でも、semantic typeやFormal Nameの一覧でもない。

**構造は組み換えるが、情報量を減らさない。**

生成は一回で完走する。途中のHuman Review GateやPlanning Artifactを生成工程へ置かない。

```text
SDD
  ↓
HSD基本構成の確認
  ↓
セクション単位の生成
  ├─ 説明すべき設計事項を確認
  ├─ 必要な図・表を作成
  ├─ 図表で表現できない設計意味を本文化
  ├─ 柱書・説明文を最後に整える
  └─ 必要な場合だけサブセクションを追加
  ↓
全セクション完成
  ↓
Coverage Mapによる意味保存検査
  ↓
HSDとCoverage Mapの全体調整
  ↓
STOP Human Review
```

CoverageはHSDを作る方法ではなく、完成後に設計意味の保存を確認する検査手段である。

---

## 2. 入力とAuthority

### 2.1 主入力

HSD生成の直接入力は次のStructured Design Dataとする。

`docs/model/argus_structured_design_data_v0.1.md`

SDDがPromptへの貼付・添付として与えられた場合は、その提供されたSDDを直接入力として扱う。

実際に参照できないrepo file、Artifact、会話履歴等を参照したものとして扱ってはならない。

### 2.2 Design Authority

設計内容の一次Authorityは次のDesign Sourceとする。

`docs/source/argus_design_source_v0.1.md`

Design Sourceは通常のHSD生成時に再度全文を意味分解する入力ではない。次の場合に限って確認する。

- SDDの意味に疑義がある
- SDD内部に矛盾がある
- SDDとDesign Sourceとの不一致が疑われる
- HSDへの組み換えだけでは意味を確定できない
- Source由来の設計理由を確認する必要がある

Design SourceとSDDが矛盾する場合はDesign Sourceを優先する。ただし、実行者が新しい設計判断で矛盾を解決せず、Human Review事項として提示する。

### 2.3 補助Artifact

ADR、Contract、Test Strategy、AI Development Operating Rules、その他のcanonical Artifactは、実際に利用可能であり、SDDの意味確認またはexact detailへの接続に必要な場合だけ参照してよい。

これらはHSDの生成構造を決定する入力ではない。

既存のPlanning Artifact、旧HSD、旧Coverage Mapを生成入力、修正ベース、Authority、固定構造として使用してはならない。

---

## 3. 変換の基本原則

次を行ってはならない。

- SDDを再度意味分解または再設計する
- SDDを別のFormal Modelへ再構築する
- 新しいsemantic classificationを作る
- semantic type、Formal Name、Coverage UnitをHSD構造へ機械変換する
- Design Sourceに存在しない設計、理由、因果関係を補完する
- 一般的なベストプラクティスをARGUSの設計として追加する
- SDD生成・検証用メタ情報をARGUSのSystem Designとして展開する
- Coverage Unit順にHSD本文を作る
- 全セクションの図表一覧やサブセクション一覧を事前確定する
- 既存Planning Artifactのサブセクション案や図表候補を継承する
- 旧HSDを編集・増補して新HSDとする

各セクションを完成させてから次のセクションへ進む。

---

## 4. セクション単位の生成手順

各セクションで次の順序を基本とする。

### 4.1 設計事項を確認する

SDDから、そのセクションでHumanへ説明すべき設計事項を確認する。少なくとも該当する範囲で次を確認する。

- 中心的な設計事項
- 目的、課題、設計理由
- 主要な機能または処理
- Actor、Authority、Boundary
- 入力、出力、状態、データ
- 条件、数値、閾値、時刻、期間、回数、優先順位
- 規則、禁止、例外
- 異常時処理、復旧
- 他の章・節との関係
- 未確定事項

これはCoverage Unit一覧を作る工程ではない。

### 4.2 必要な図・表を先に作る

処理順、状態遷移、Actor間相互作用、責務比較、条件比較、Data flow等について、図表の方がHuman理解を明確に改善する場合だけ図表を作る。

- 処理順、条件分岐: flowchart
- Actor / Component間の時間的相互作用: sequence diagram
- 状態と遷移条件: state diagram
- Component / Boundary / dependency: architecture / component diagram
- Data flow: data flow diagram
- 条件、責務、Authority、具体値、対応関係: table

Formal構造が存在すること、同種要素が複数あること、名称を列挙できること自体を図表作成理由にしない。

図表作成後、同じ内容を散文で重複列挙しない。本文では図表だけでは伝わらない意味、理由、例外、注意点を説明する。

### 4.3 本文を書く

図表だけでは伝わらない設計意味、目的、課題、設計理由、制約、例外、異常時処理、具体条件を本文として書く。

目的、課題、設計理由はHumanが読める自然な日本語にする。口語化の対象は文体であり、内容ではない。

一文に4件以上の独立した設計事項を並べる必要がある場合は、箇条書きまたは表を優先する。単純な属性列挙はこの限りではない。

### 4.4 柱書を最後に整える

図表と本文を配置した後、そのセクションが何を扱い、なぜ必要で、何を理解すればよいかを自然な日本語の柱書として整える。

柱書を先に作って内容を合わせてはならない。単なる「本節ではXを説明する」にしない。

### 4.5 必要な場合だけサブセクションを追加する

次の場合に限ってサブセクションを検討する。

- Humanが別の設計問題として理解する必要がある
- 責務主体が異なる
- 独立した処理または状態遷移がある
- 独立した安全境界がある
- 異常時処理または設計理由が異なる

semantic type、Formal Name、Coverage Unitの違い、設計事項が多いこと、列挙を避けたいことだけを分割理由にしない。Humanが一つの設計事項として理解する内容を細かく分割しすぎない。

---

## 5. HSD基本構成

- 目次
- 0. 設計環境
- 1. システム全体構成
  - 1.1 システムの目的
  - 1.2 全体構成
  - 1.3 HumanとARGUSの役割分担
  - 1.4 全体処理
- 2. 投資機能
  - 2.1 候補探索
  - 2.2 分析・反証・判断材料
  - 2.3 資金配分とリスク検証
  - 2.4 Humanの判断
  - 2.5 売買と売買結果
  - 2.6 ポートフォリオ管理
  - 2.7 監視と再評価
- 3. 実行基盤
  - 3.1 起動
  - 3.2 継続実行
  - 3.3 中断・再開
  - 3.4 状態とデータ
  - 3.5 保存・バックアップ・復旧
  - 3.6 Deployment
- 4. 外部接続
  - 4.1 外部サービスとの境界
  - 4.2 Provider
  - 4.3 費用・利用量
  - 4.4 外部障害
- 5. Human Interfaceと運用
  - 5.1 CLI / Human Interface
  - 5.2 Humanの対応状態
  - 5.3 判断待ち
  - 5.4 通知
  - 5.5 Incidentと復旧
- 6. 安全設計
  - 6.1 Fail Closed
  - 6.2 TEST / PAPER / LIVE
  - 6.3 正本状態の保護
  - 6.4 投資リスク
  - 6.5 Secret・データ保護
- 7. 検証・評価・改善
  - 7.1 検証
  - 7.2 Historical / PAPER / LIVE
  - 7.3 判断結果の評価
  - 7.4 変更管理
- 8. 機能一覧
- 9. 用語・詳細仕様への参照
- 10. 継続検討事項

この構成はHuman向けの基本骨格であり、固定schemaでもSDD semantic typeの分類表でもない。Human理解のために明らかに必要な場合だけ、主要構成を変えずにサブセクションを追加、統合、削除できる。

---

## 6. 情報保存規則

構造は組み換えるが、情報量を減らさない。

特に次を一般化または省略してはならない。

- 具体数値、時刻、期間、閾値、回数、上限、下限、容量、preset
- 状態名、遷移条件、入力、出力
- 条件、例外、禁止、異常時処理、復旧条件
- Authority、Boundary、責務主体
- 未確定値とその確定性
- Design Source / SDDに存在する課題、目的、設計理由

数値、状態名等を「詳細」としてSDDへ委譲して消してはならない。SDD自体を詳細仕様への参照先としてHSDから設計意味を省略する理由にしてはならない。

複数箇所に関係する情報はSemantic Ownerとなる節で詳述し、他節から参照する。横断規則は代表節で説明し、各適用先では固有の影響だけを示す。

---

## 7. 詳細仕様と範囲

HSDはContract、Test Strategy、ADR等の複製ではない。HSDにはHuman理解に必要な目的、役割、設計理由、Boundary、主要制約、関係、基本的な異常時挙動を残す。

exact JSON Schema、parser error ordering、test fixture、exact oracle、RED / GREEN手順、Evidence path、implementation command等は、Human理解に不要なら詳細Artifactへ委譲できる。

Current、Deferred、Futureを混同しない。設計記載、実装完了、検証完了、運用承認を同一状態として扱わない。

その版で有効な最終仕様だけをNormativeな現在仕様として記述する。旧版への言及が必要な場合は非NormativeなHistory、Provenance、Migrationとして扱う。

開発Prompt、Prompt Registry、AI review手順等は、それ自体がARGUS Runtime / Investment Systemの設計要素でない限りHSDへ展開しない。

---

## 8. Coverage Map

Coverage Mapは、完成HSDに対してSDDからの設計意味保存を検査する独立Artifactである。HSD本文へ埋め込まず、HSDの生成構造にも使用しない。

Coverage Unitは、SDDにおいて独立した設計意味を持つ最小確認単位とする。

- 標準意味型ラベル配下の独立記述
- 表の各データ行
- 独立した条件、規則、禁止、例外、異常時処理、復旧、状態遷移、Authority、Boundary、未確定事項

一つの意味を単語、文、列へ機械的に細分化しない。

Coverage Mapは少なくとも次の列を持つ。

| 列 | 内容 |
|---|---|
| Coverage ID | 一意な追跡ID |
| SDD位置 | 元の節、表、行、ラベル |
| 設計意味 | 保存すべき意味の短い識別 |
| HSD配置先 | 対応する章、節、図表 |
| 表現 | prose / table / diagram / reference |
| Coverage判定 | PRESERVED / REFERENCED / REVIEW |
| 備考 | 必要な場合のみ |

### 8.1 PRESERVED

設計意味が、主語と述語を持つ説明、意味を説明する表の行、または関係・条件・遷移を保持する図表要素で表現されている場合だけ使用する。

名称が出現しただけではPRESERVEDにしない。数値、閾値、時刻、期間、状態名を含むUnitは、実値または実名称がHSDに存在しなければPRESERVEDにしない。

### 8.2 REFERENCED

HSDにHuman理解に必要な意味を残した上で、Contract、Test Strategy、ADR等に存在するexact detailだけを外部Artifactへ委譲する場合に使用する。

SDD自体をREFERENCED先にしない。

### 8.3 REVIEW

未配置、判定不能、意味欠落、または除外のHuman判断が必要なUnitに使用する。

HSDから除外候補としたexact detailは、実行者だけで除外を確定せずREVIEWとし、理由を記録する。

---

## 9. 全体調整

Coverage Map作成後、HSD全体を通読し、次を確認する。

- ARGUSというシステム全体と投資処理の一周が見える
- HumanとARGUSの役割分担が分かる
- 章間で詳細説明を不要に重複していない
- 図表と本文が同じ内容を重複列挙していない
- 不自然または過剰なサブセクションがない
- 柱書が内容、必要性、読み方を説明している
- Current / Deferred / Futureが混ざっていない
- Formal分類や名称列挙中心の文書になっていない
- 具体値、条件、状態、異常時処理、課題、設計理由が不当に消えていない
- 一文へ4件以上の独立事項を詰め込んでいない
- 日本語として自然に読める

不足をHSDへ反映し、Coverage Mapも同期する。

---

## 10. 日本語

日本語は絶対条件とする。主要本文、見出し、表、図ラベルは日本語で記述する。

ARGUS、TEST / PAPER / LIVE、API、CV / RV、Fail Closed、identifier、path、code identifier、製品名、正式なSource用語等は精度維持に必要な範囲で原語を使用してよい。

英語中心の説明、英語中心の見出し、Formal Name列挙中心の本文にしない。

---

## 11. 未確定事項とHuman Review

SDDで未確定の事項を推測で埋めない。

次を発見した場合はHuman Review事項とする。

- SDD内部の意味矛盾
- Design Sourceとの不一致
- 配置または統合で設計意味が変わる事項
- CurrentとFutureの境界が不明な事項
- 詳細Artifact間のAuthorityが不明な事項
- 必要なAuthority Artifactを参照できない事項
- Coverage MapでREVIEWとなる事項
- Humanによる設計判断が必要な事項

問題が存在しない項目を形式的に作らない。

---

## 12. 出力と停止条件

次の3 Artifactを生成する。

1. `docs/development/prompts/argus_system_design_prompt_v0.1.6.md`
2. `docs/design/argus_system_design_v0.1.6.md`
3. `docs/review/argus_system_design_coverage_map_v0.1.6.md`

Planning Artifactは生成・更新しない。

HSD全体、Coverage Map、Evidence、必要な全体調整まで一回で完了する。途中でHuman Reviewを要求しない。

完了時に次を報告する。

- 変更ファイル一覧とSHA-256
- HSD総行数
- 図表数と種類
- Coverage Unit総数
- PRESERVED / REFERENCED / REVIEW件数
- HSDに保持した具体数値、時刻、期間、閾値の代表例
- 4件以上の独立事項を詰め込んだ長文の確認結果
- Human Review事項

Human Review前にFreezeしない。

全成果物の生成と調整が完了した後、**STOP Human Review**。

