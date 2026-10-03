# ARGUS Human System Design変換プロンプト v0.1.4

```yaml
role: HUMAN_SYSTEM_DESIGN_TRANSFORMATION_INSTRUCTION
version: 0.1.4
status: APPROVED
required_structured_design_data: docs/model/argus_structured_design_data_v0.1.md
canonical_design_source: docs/source/argus_design_source_v0.1.md
default_output: docs/design/argus_system_design_v0.1.4.md
```

## 1. 目的

Structured Design Data（SDD）が保持する設計意味を、人間がARGUSの全体像、責務、境界、安全条件、未確定事項を理解・レビューできるHuman System Design（HSD）へ投影する。本処理は要約ではなく意味保存変換であり、内部構造の逐語コピーでもない。

本Promptは変換実行仕様であってDesign Authorityではない。設計の創作、矛盾の黙示修正、Human判断の代行を行わない。

## 2. 入力とAuthority

必須入力：

- Design Source：`docs/source/argus_design_source_v0.1.md`
- Structured Design Data：`docs/model/argus_structured_design_data_v0.1.md`
- ADR：`docs/adr/argus_architecture_decision_records_v0.1.md`
- applicable Contracts：`docs/contracts/`
- Test Strategy：`docs/test/argus_test_strategy_v0.1.3.md`

補助入力：

- Development Operating Rules：`docs/development/argus_ai_development_operating_rules_v0.1.md`

Authorityは次のとおりとする。

1. Design Sourceは設計内容の唯一の一次資料である。
2. SDDはDesign Sourceの意味を構造化し、実装・Testが原則直接参照する正式設計Artifactである。
3. HSDはHumanが設計を理解・レビューするためのHuman-facing projectionであり、独立した設計Authorityではない。
4. 本Promptは変換方法を規定するだけで、設計Authorityを持たない。
5. SourceとSDDが実質的に衝突する場合はSourceを優先し、本文で推測解消せずHuman Reviewへ送る。
6. ADRは判断理由、Contractは限定Capabilityの詳細、Test Strategyは検証方法を示す。これらでSourceの設計意味を上書きしない。

## 3. Final Specification Rule

本Promptだけで現在の変換仕様が成立する。過去Prompt、過去HSD、会話履歴をNormative dependencyにしない。現在要件を直接記述し、過去版を読まなければ入力、Authority、変換規則、完了条件を判断できない構造を禁止する。

HSDも現在成立している世界だけをNormativeに記述する。履歴が必要ならNon-Normativeとして分離し、旧版を現在仕様の根拠にしない。

## 4. 生成前Gate

次を確認し、一つでも不成立ならHSDを生成せず`HUMAN_SYSTEM_DESIGN_GENERATION_BLOCKED`として停止する。

- 必須入力が実在し、pathとSHA-256を固定できる。
- SDDにDesign SourceのIdentityが記録され、実ファイルと一致する。
- SourceとSDDに安全な投影を妨げる未解決のAuthority衝突がない。
- 未確定事項と確定事項を区別できる。
- 出力先が存在せず、既存Artifactを上書きしない。
- 本Prompt単体で変換を実行できる。

## 5. 日本語絶対条件

HSDの主要本文、見出し、表、図ラベル、説明は日本語とする。ARGUS、TEST / PAPER / LIVE、API、CV / RV、Fail Closed、正式な識別子、path、code identifier、製品名、状態名、Formal Nameなど精度維持に必要な固定語だけ原語を許可する。英語主体の章・表・図を生成しない。

## 6. 意味保存とHuman-facing projection

- SDDの設計意味を理由なく削らない。
- SDDの章立て、意味型label、Source locator、Coverage・生成監査情報を機械的にコピーしない。
- 人間が理解しやすい章、説明、表、必要最小限の図へ再構成する。
- Capability、目的、責務、Authority、境界、安全条件、Failure behavior、他領域との接続を本文に残す。
- 詳細を別Artifactへ委譲しても、そのCapabilityが存在する理由と作用点を本文から消さない。
- Current、Deferred、Future、TBD、UNCONFIGUREDを混同しない。
- closed setが安全挙動を決める場合は値集合を省略しない。
- Sourceにない設計、推奨値、例外、Actor、状態を追加しない。
- SDDの分類がHuman文書上で不自然でも勝手に再分類せず、Human Review項目へ分離する。

## 7. 人間向け記述規則

各主要領域は必要に応じて次を追跡可能にする。

```text
何が問題か
→ 何を実現・保護するか
→ 誰が判断し、誰が実行するか
→ どの仕組みが作用するか
→ 失敗時にどう止まり、どう復旧するか
→ 詳細をどのArtifactで確認するか
```

- ARGUS固有語は初出で日本語説明とFormal Nameを対応付ける。
- 抽象的な機構名だけでなく、守る対象、禁止動作、失敗時挙動を説明する。
- 実装者専用の分類報告書や生成監査報告書にしない。
- Test ID、byte-level仕様、Error一覧等は理解に必要な範囲だけ説明し、正確な参照先へ委譲できる。

## 8. 必須View

章構成は固定しないが、少なくとも次を本文または明示的参照で扱う。

1. 文書の位置づけ、Authority、読者
2. 目的、前提、対象範囲、対象外
3. 全体像と主要責務
4. Human、ARGUS、Agent / LLM、Broker、外部Serviceの役割
5. 候補形成、独立分析、Contradiction、Thesis、Report
6. Allocation、Hard Risk、Human Decision、Broker操作、Execution Fact
7. Portfolio、保持期間、売却・損切、Policy
8. Position / Candidate / Market Watch、Event、再評価
9. 投資処理の閉ループ
10. Runtime Identity、Environment Binding、Bootstrap、起動
11. Runner、Job、停止・再開、Retry、Recovery
12. Canonical State、Single Writer、Atomic Commit、Data、Config、Backup
13. Human操作、User Status、Queue、通知、Incident、Correction
14. ExternalServiceGateway、Provider Adapter、Service health、Cost governance
15. Historical Validation、TEST / PAPER / LIVE、Live Readiness
16. Capability Verification、Regression、Evidence、Review、Approval
17. 評価、Break、改善、変更統制
18. Current / Deferred / Futureと未確定事項
19. 用語とCanonical Artifact案内

## 9. 横断安全条件

次を削除・弱化せず、独立章だけでなく実際の作用点へ接続する。

- Human-in-the-loopとBroker API自動発注禁止
- TEST / PAPER / LIVE分離
- Fail Closed、Environment Binding、Risk Control、Human Approval、Cost Control
- Canonical State、Single Writer、Atomic Commit
- ExternalServiceGateway / Provider Adapter境界とBusiness Logicからの外部API直接呼出し禁止
- 有料Service、増額、有料Fallbackの無断追加禁止
- Raw immutable dataとmarker identity検証
- Runtime lifecycle、Retry、Recovery
- Application LogとCanonical State / Auditの責務分離

## 10. 図表規則

- 図は構造、処理順序、状態遷移など、文章より関係を理解しやすくする場合だけ使う。
- 一つの図へ異なる関係型を過剰に混在させない。
- Humanの判断・操作を色だけでなく文言で識別可能にする。
- 表は同種要素の比較、分類、対応関係に使用し、単なる箇条書きを表へ変換しない。
- 図表には日本語の番号と題名を付ける。

## 11. 生成手順

1. 入力を全文確認し、pathとSHA-256を固定する。
2. SDDの主要意味領域ごとに、本文、図、表、詳細委譲、Human Reviewの投影先を決める。
3. Design SourceでAuthorityとSource fidelityを確認する。
4. ADR、Contract、Test Strategyから、理由、限定Capability、検証境界を確認する。
5. 日本語のHuman-facing HSDを生成する。
6. 参照pathが実在し、内容と参照先が一致することを検証する。
7. 必須Viewと横断安全条件を全件照合する。
8. SDDの主要Purpose、Policy、Rule、Process、State、Data、Boundary、Failure、Verificationに投影先があることを確認する。
9. Reader Testを行う。別Actorを利用できない場合は生成Actor自身による独立再読で代替し、その事実を報告する。
10. 疑義をHuman Review項目へ分離し、Human Review待ちで停止する。

## 12. Reader Test

HSDだけを読んだ読者が次を説明できることを確認する。

- ARGUSの目的と対象外
- HumanとARGUSの責任境界
- 投資処理の閉ループと自動発注しない境界
- 状態更新、環境分離、停止・復帰の安全条件
- 外部Serviceと費用の統制
- 検証、段階移行、改善の流れ
- 未確定事項と詳細参照先

回答不能な項目は一般知識で補完せず、文書不足またはHuman Review候補として扱う。

## 13. Human Review項目

次を発見した場合は本文を勝手に補正しない。

- SourceとSDDの実質的不一致
- Human文書へ投影すると不自然な意味型
- 同一概念の多義的使用
- 一意でない責務またはAuthority
- 未確定事項が確定事項に見える箇所
- 因果関係または実装判断に必要な意味の不足

## 14. 出力Contract

出力先：`docs/design/argus_system_design_v0.1.4.md`

冒頭に最低限、Document Role、Status、入力SDDとDesign Sourceのpath / SHA-256、使用Promptのpath / SHA-256、生成日、派生文書であり正本ではない旨を記録する。Statusは`HUMAN_REVIEW_REQUIRED`とする。

## 15. 禁止事項

- 入力Artifact、既存Prompt、既存HSDを変更または上書きする。
- Source本文またはSDD内部構造を大量コピーする。
- 設計意味、安全条件、Failure behavior、未確定事項を削る。
- Source外設計を追加する。
- 不自然さや矛盾を黙示修正する。
- Final Freeze、Baseline承認、Human判断を代行する。
- Human System Designから実装またはTest変更へ進む。

## 16. 完了条件と報告

必須Viewと横断安全条件が追跡可能で、Source外設計がなく、Human Review項目が確定本文から分離され、Reader Testを完了した場合だけ生成完了とする。

完了報告には、入力と出力のpath / SHA-256、Phase Gate、必須View、横断安全条件、詳細委譲、Reader Test、Human Review項目、変更範囲、Final Freeze未実行を含める。

成功時の最終行：

```text
HUMAN_SYSTEM_DESIGN_PROMPT_UPDATED_AND_DESIGN_GENERATED
```
