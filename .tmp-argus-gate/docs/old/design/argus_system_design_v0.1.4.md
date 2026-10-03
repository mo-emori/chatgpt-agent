# ARGUS Human System Design v0.1.4

**Document Role:** `HUMAN_SYSTEM_DESIGN`  
**Status:** `HUMAN_REVIEW_REQUIRED`  
**生成日:** 2026-09-23  
**Structured Design Data:** `docs/model/argus_structured_design_data_v0.1.md`  
**SDD SHA-256:** `0acc60f6bff9ae27fea86310b0160e2b344e9671a795798feb763334ddeebf40`  
**Design Source:** `docs/source/argus_design_source_v0.1.md`  
**Design Source SHA-256:** `feff970e4dc59c599c7fc5e48c5f77b87e7806aa156e87f8dd26bdae36609681`  
**Transformation Prompt:** `docs/development/prompts/argus_system_design_prompt_v0.1.4.md`  
**Prompt SHA-256:** `8751c7f733930306a495cf1b109bfad5d3e10721bc2b7e2a0092e34a0e8bc172`

本書はDesign SourceからStructured Design Data（SDD）を経由して生成した、人間による理解・レビュー用の派生文書である。設計内容の唯一の一次資料はDesign Sourceであり、本書は独立した設計正本ではない。不一致があればDesign Sourceを優先し、Human Reviewで解消する。

## 1. ARGUSが目指すもの

ARGUSは、AIへ投資判断を丸投げする自動売買システムではない。市場から候補を探し、異なる観点で分析し、根拠と反証を統合して具体的な売買案を人へ提示する。最終判断と証券会社での操作は人が行い、ARGUSは入力された約定事実を正本へ反映して、その後の監視と再評価を続ける。

初期版はユーザーPC上で動くLocal-firstシステムである。PCの停止、Sleep、通信断を例外ではなく通常条件として扱う。常時監視や即時通知を保証せず、復帰時に安全に再開できることを優先する。

初期版が行わないことは、Broker APIによる自動発注、完全自動投資、HFT、Scalping、秒単位監視、常時稼働保証、初期段階からのServer運用、分散Agent、複数WriterによるCanonical State更新である。

## 2. 文書とAuthority

表1　主要Artifactの役割

| Artifact | 役割 | Authority境界 |
|---|---|---|
| Design Source | 現在有効な設計事項、要求、制約、実現方式を保持 | 設計内容の唯一の一次資料 |
| Structured Design Data | Sourceの意味を型・関係・状態・処理として構造化 | 実装・Testが原則直接参照するが、Sourceを上書きしない |
| Human System Design | 人が全体設計を理解・レビューするための投影 | 本書。独立Authorityを持たない |
| ADR | Architecture判断と理由を保存 | 契約や現在仕様そのものを置換しない |
| Contracts | 限定Capabilityの詳細契約 | 上位設計の範囲内で実装可能な精度へ具体化 |
| Test Strategy | Evidence、環境、Gate、判定方法を規定 | 未実施を能力成立へ昇格しない |

## 3. システム全体像

図1　Human-in-the-loopの投資閉ループ

```text
Market / Disclosure Data
        ↓
候補探索 → 独立分析 → 矛盾抽出 → 投資Report
                                      ↓
                              Allocation / Risk検証
                                      ↓
                            Decision Queue / Human
                                      ↓ 人が最終判断
                          人がBroker画面で操作
                                      ↓
                              Execution Paste
                                      ↓
                  Canonical State Commit / Portfolio更新
                                      ↓
                    Position Watch / Event / 再評価
                                      └────────→ 次の提案
```

この閉ループでは、分析Agentは提案を作れても承認や発注はできない。Human ApprovalはProposalの特定revision、Trade内容、State、Policy等へ結び付く。Brokerで成立した結果は、提案が期限切れまたはPolicy逸脱であっても、確認済み事実として拒否しない。事実を反映したうえでCompliance RecordやCorrectionとして差を扱う。

## 4. 主体と責任境界

表2　主体の役割

| 主体 | 行うこと | 行わないこと |
|---|---|---|
| Human | 最終投資判断、Broker操作、Status変更、有料Service承認、重要変更承認 | Canonical Stateの通常時直接編集 |
| ARGUS | 探索、検証、Queue管理、状態更新、監視、再評価 | Human Approvalの代行、Broker API自動発注 |
| Agent / LLM | 独立分析、反証、Report、改善案の生成 | Hard Riskの最終判定、正本直接更新、外部API直接呼出し |
| 固定Validator / Writer | 決定論的制約判定、Single Writer Commit | 投資魅力度の自由判断 |
| Broker | 注文受付と約定という外部現実を生成 | ARGUSの内部Proposal状態を管理 |
| External Service | Market Data、Disclosure、Model推論等を提供 | ARGUSのHuman Authorityを置換 |

権限主体、実行主体、処理責務は別概念である。CLIやTrayは操作入口、WriterやAdapterは実行Componentであり、それ自体をHumanやARGUSと同じAuthority主体として扱わない。

## 5. 候補探索から投資Reportまで

対象市場とUniverseは明示的に固定する。まず流動性不足、必要Data欠損、上場直後、対象外商品等を機械的に除外し、その後にValue、Growth、Change、Quality、Event、Contrarianなどの独立した探索枝を使う。枝同士が結論を共有して見かけ上の合意を作らないようにする。

分析でもFundamental、Valuation、Bull、Bear、Macro、Risk、Technical、Portfolio Fit等の観点を分ける。Data不足を0点や否定評価へ変換せず、不足理由を保持する。Contradiction Engineは矛盾を消して単一Scoreに畳み込むのではなく、どのEvidenceと仮定が衝突するかを明示する。

Human-facing Reportには、銘柄と状態、分析要約、Bull / Bear、Valuation、Risk、矛盾、Investment Thesis、Invalidator、BUY / ADD / HOLD / REDUCE / SELL / WATCH等の提案、具体的Trade、Confidence、Counterargument、Humanの選択肢を含める。Declared Reason Weightは判断時点の寄与説明であり、因果効果や将来収益率を意味しない。

## 6. Allocation、Risk、承認、実行

「魅力があるか」「いくら取引するか」「制約を満たすか」は別処理である。AllocationはCash、既存Position、Role Bucket、集中度等を踏まえて具体的Tradeを作る。Deterministic Risk Validatorは最新State、Policy、Evidence、評価時刻とTradeを入力し、`PASS / REJECT / ADJUST_REQUIRED / DATA_INSUFFICIENT`を返す。

Hard RiskはFail Closedであり、Policyや必要Dataが未設定・不足ならBUY / ADDを進めない。一方、Risk縮小のSELL / REDUCEを、BUY向け制約の機械適用によって妨げない。承認後も提出前に価格、Cash、数量、Policy、Data freshness等を再検証し、実質変更があれば古いApprovalを流用しない。

初回運用は未解決発注一件に直列化する。Approvalとexecution slot・資源予約を同一Commitで確保し、OrderがBroker上で解決するまでTTLや停止だけを理由に予約を解放しない。

Broker操作は必ず人が行う。結果はCopy & Pasteで受け取り、原文をDurable保存してからParse、Validation、重複判定、Canonical Commitを行う。部分約定、取消待ち、結果不明を隠さない。

## 7. Portfolio、保持、売却

Portfolioは単なる現在残高ではなく、Cash、Position、lot / tranche、Decision、Thesis、Order、reservation、Execution Fact、履歴を関連付ける。集計Positionは`account_id + instrument_id`単位とし、内訳はDecisionとThesisへ追跡可能にする。

Portfolio PolicyはTarget、Tolerance、Constraint、Modeを分ける。高配当Core等のRole Bucket、集中度、流動性、Cash reserve、追加投資条件を扱うが、候補値や例示比率を未承認の確定Policyにしない。

保持期間はSHORT、MEDIUM、LONG / COREを区別する。売却判断は次の観点を分離する。

- Thesis Stop：購入理由・投資仮説が崩れた。
- Risk Stop：Portfolioまたは銘柄Riskが許容範囲を外れた。
- Price Stop：価格条件に到達した。ただし価格だけで自動売却しない。
- Time / Opportunity Cost Stop：時間経過や他機会との比較で再配分を検討する。

## 8. Watch、Event、再評価

Position Watch、Candidate Watch、Market Watchは目的と優先度を分ける。保有PositionのRisk監視をCandidate探索より優先し、保有数量が0になったときだけPosition Watchを終了する。必要ならCandidate Watchへ戻す。

Earnings、Disclosure、price move、news、Dividend cut、Guidance revision等のEventはEvidence更新として扱い、元Thesisとの差を評価する。Event-drivenは即時Push保証ではない。Data取得、分析、Queue、通知可能時間、PC稼働状態によるLatencyを記録する。

Proposalは配送直前やBUSY復帰時に`EXPIRED / REANALYZE / RERANK / MERGE / KEEP SILENT`へ再評価する。Proposalの失効でIncidentを削除せず、解決Evidenceが揃うまで独立管理する。

## 9. Human操作、Status、Queue、通知

Human Commandの正規入口はLocal CLIであり、Windows Task Trayは同じCommand Dispatcherを使う軽量UI候補である。Status、Help、System StatusはRunnerが非RUNNINGでも利用できるが、Execution Pasteは原則RUNNING時だけ受け付ける。

User Statusは`FREE / NORMAL / BUSY`、内部capacityは`LARGE / MEDIUM / SMALL`である。Statusは人だけが明示変更する。BUSY Lease終了後はNORMALへ戻り、FREEへ自動遷移しない。人の再設定とLease Jobが競合した場合はHumanの最新操作を優先する。

通知はSeverityとPriorityを別軸にする。PriorityはP0からP3、SeverityはINFO / WARNING / CRITICALを使う。Alert Queueを正本とし、作成、配送、認知、解決を分ける。Popup成功はHuman acknowledgementではない。

通知可能時間はAsia/Tokyoで評価する。Investment Emergency Overrideは初期disabledであり、BUSY中の重大Investment Riskも記録・分析は続けるが、自動通知は許可Windowまで保持する。System Critical Overrideは初期UNCONFIGUREDで、Humanが設定する。Override対象は次のclosed classだけである。

- `CANONICAL_STATE_CORRUPTION`
- `DATA_STORAGE_IDENTITY_MISMATCH`
- `ENVIRONMENT_BINDING_MISMATCH`
- `MIGRATION_REQUIRED`
- `RUNNER_LOCK_RECOVERY_REQUIRED`

Investment EventをSystem Criticalへ偽装して通知制約を迂回しない。

## 10. Runtime lifecycleとRetry

Runner状態は`INIT / RUNNING / RESUMING / END`である。通常起動はINITから検証後にRUNNINGへ進む。前回の異常終了を検出した場合はRESUMINGでinbox、outbox、Stage、Commit、lock等を照合する。CrashやOS killは独立状態ではなく、次回INITで検出する終了事象である。

図2　Runner状態

```text
INIT ──正常検証────────→ RUNNING ──安全な終了──→ END
  └──前回異常終了を検出→ RESUMING ──復旧成功──→ RUNNING
```

JobはDurable Stageを持つ。外部API成功後にLocal Commitだけが失敗した場合、同じinput hashのStageから再開して再課金・重複副作用を避ける。Retryable failureはburst retry、backoffとjitter、次回時刻を記録する。Schema、Policy、認証、Budget等のNon-Retryable failureを無限Retryしない。

Schedule判定はtimezone付きWall Clock、timeoutやelapsed timeはmonotonic clockを使う。SleepやOfflineから復帰したら、last success、cursor、freshness、relevanceに基づいてCatch-upする。

## 11. Runtime Identity、Environment Binding、Bootstrap

Runtime Identity、Configuration、User Status、Domain State、Runtime State、Secretは混在させない。Identityはenvironment、state identity、data root identity等の論理的な組合せを表し、drive letterや配置pathだけから推測しない。

TEST / PAPER / LIVEは別environmentとして分離し、Canonical State、Data Root、Credential、Queue、Evidenceを混在させない。起動時にはRuntime Identity、Configuration、Secret、Environment Bindingの順序と依存を検証し、不一致なら通常処理を開始しない。

大容量Data Rootは`L:\emori\InvestmentAgentData`である。ただしdrive letterだけを信用せず、root markerの`state_id / data_root_id`を期待値と照合する。marker不明、欠落、破損、identity不一致はFail Closedとし、書込みや外部処理へ進まない。

詳細なJSON field、deterministic serialization、failure code、初期化・検証順序は次を参照する。

- `docs/contracts/argus_runtime_identity_document_contract_v0.1.md`
- `docs/contracts/argus_environment_binding_contract_v0.1.md`
- `docs/contracts/argus_runtime_foundation_bootstrap_contract_v0.1.md`

Bootstrap Contractには、manifest、config、secret、sequence接続にDesign Authority判断が必要な未確定事項が残る。既存実装があることを契約完成とみなさない。

## 12. Canonical Stateと永続化

Canonical Stateの更新はSingle WriterとAtomic Commitを必須とする。Writerはlock取得、最新State再読込、version確認、Command / Fact検証、新Envelope生成、temp書込み、flush / fsync、atomic replace、Commit記録、Projection更新の境界を守る。途中失敗時に旧正本と新正本を混在させない。

Current Envelopeは現在値、Append-only Archiveは履歴、Projectionは再生成可能な表示物である。ProjectionやMarkdown Logを正本へ昇格しない。Fact、Observation、Judgment、Command、Workflow Stateも同義に扱わない。

Backup復元後はBroker現実との差をReconciliationする。古いsnapshotをそのまま正しい現在Stateとみなさず、差分を確認済みFactまたはCorrection Eventとして再Commitする。照合完了までBUY / ADDを停止する。

## 13. Data、Storage、Application Log

外部取得DataはRaw immutableとProvider-independentなNormalized Dataに分ける。Rawを上書きせず、取得時刻、Provider、request、hash等のprovenanceを保持する。Historical Dataはpublication time、revision / vintage、当時Universeを持ち、未来Data混入を防ぐ。

Storage枯渇時は低重要度のNews、Historical expansion、Discovery bulk取得、非重要Reportから停止する。その後Application LogやCandidate向けDataを縮退し、Canonical State、Human Decision / Correction / Execution Fact、Audit / Incident、Queue / reservation、recent backup、Position Watch Minimum Dataを最後まで保護する。

Application LogはRuntime、Job、Adapter、Error等の運用解析用であり、Canonical State、Fact、Event、Audit Recordの代替ではない。単独の書込み失敗でCanonical Commitを失敗させないが、同一Storage障害が正本や復旧可能性を危険にさらす場合はStorage Fail Closedを適用する。TEST / PAPER / LIVE間およびDevelopment / Runtime間で混在させない。具体的partition、path、保持期間、cleanup方法は未確定である。

## 14. 外部Serviceと費用統制

Business LogicやAgentは外部APIを直接呼ばない。Domain Service InterfaceからExternalServiceGatewayを経由し、Provider固有処理はProvider Adapterへ閉じ込める。

図3　外部Service境界

```text
Application / Agent
        ↓
Domain Service Interface
        ↓
ExternalServiceGateway
        ├─ MarketDataProvider
        ├─ DisclosureProvider
        ├─ ModelProvider
        └─ NewsProvider
```

Gatewayはenabled / configured、Human approval、Secret、Budget、rate limit、Retry、Circuit Breaker、usage、provenanceを共通管理する。Adapterはendpoint、pagination、response validation、Raw保存、Normalizeを担当する。

J-Quantsは日本株Structured Dataの第一Providerで、開発初期はFreeを利用できる。Paper開始前にはHuman承認付きでLight以上を導入し、Capabilityを実測する。EDINET APIは法定開示の第一Providerである。TDnet有料APIは費用要件不一致のため対象外である。OpenAI API等のModel APIもModelProvider境界を通す。

新しい有料Service、FreeからPaidへの変更、Add-on、上限増額、有料FallbackはHumanの明示承認なしに有効化しない。Budgetはper-call / job、run、daily、monthlyの各階層で事前Hard Gateとし、見積不能や超過時はCallを開始しない。障害やRate Limitを理由に未承認の有料Planへ自動移行しない。

Architecture判断の理由は`docs/adr/argus_architecture_decision_records_v0.1.md`を参照する。

## 15. 検証と段階移行

設計の存在はCapabilityの成立を意味しない。Capability Verification（CV）は実環境で能力とFailure behaviorを確かめ、Regression Verification（RV）は変更後も成立済みの不変条件が維持されるかを確認する。未実施は`NOT_RUN`でありPASSではない。

TestはEvidence Firstとし、Requirement、Test、Implementationを分離する。baselineとoracleはRun前に固定し、失敗後に同じRunの合格条件を書き換えない。TEST専用の本番迂回路を作らず、Environment Binding、Writer、Validator等の同じ契約を通す。

Historical ValidationではQuant BacktestとModel Historical Replayを分ける。LLM内部の未来知識を完全遮断できないため、Replayを真のout-of-sampleと称さない。最終的な前向き評価はPaper Tradingで行う。

Paper Tradingは事前固定した仮想Budget、fee、tax、slippage、settlementを使い、実取引と同じFact ingress / Writer経路を通す。1か月Paperは運用性能を確認する期間であり、長期投資成果や稀Eventを十分証明するものではない。Live移行は自動ではなくHuman Gateで判断する。

検証環境、CV / RV、Failure Injection、Evidence形式、静的解析、Completion Gateの詳細は`docs/test/argus_test_strategy_v0.1.3.md`を参照する。

## 16. 評価、Break、変更統制

評価軸は投資成績だけでなく、Agent / branch性能、System Health、Cost、Human Attentionを含む。結果が悪いときに評価軸を都合よく変更しない。

Breakは既存構造を前提としない改善提案で、B1 Parameter、B2 Component、B3 Architecture、B4 Objectiveを区別する。Objective変更は旧Objectiveを凍結して新旧を並行評価し、強いHuman Approvalを必要とする。Breakは過去のFact、Cash、Execution、Auditを書き換えない。

変更は「AI提案→Human承認→System修正→再検証」の順で行う。開発時のPrompt実行、Review分離、Fail Closedな運用規則は`docs/development/argus_ai_development_operating_rules_v0.1.md`を参照する。

## 17. 現在範囲、Deferred、未確定事項

初期Minimum Vertical Sliceは、最小の探索・分析からHuman Approval、Virtual BUY、Position Watch、SELL、全売却、再起動復元までを一つの閉ループとして確認する。SELL Proposal生成だけでは完了ではない。

追加分析枝、B3 / B4自動化、統計的Reason Weight較正、DRIP、SQLite、Dashboard、Server移行等は後段候補であり、現在成立済みとして扱わない。

主な未確定事項は次のとおりである。

- Runtime Foundationのmanifest、config、secret、完全なsequence接続
- 内蔵SSD上のrecent backup世代数
- Point-in-time Historical Dataの十分性
- News / Web Provider
- Strategyごとのrequired latency
- Policy、Budget、通知、Retention等の具体値
- SQLite / ParquetおよびServer移行条件
- Declared Reason Weightの正式較正方法
- System Critical OverrideのHuman設定
- Application Logの保持期間、cleanup、環境別分離方法

未確定値を0、無制限、推奨値へ暗黙変換しない。

## 18. Human Review項目

本投影で確認された、Human判断を要する事項は次のとおりである。

1. SDD §20.2の`データ`という意味型labelは、Event例の分類として自然か疑義がある。本書ではEventの例として説明し、SDD自体は変更していない。
2. SDD内部のGate Qには旧Transformation Promptを前提とした`REVIEW`記録が残る一方、現行Structured Design Data生成Promptは自己完結している。Human / Design Authorityによる正式なGate反映が必要である。
3. Runtime Foundation Bootstrap Contractが列挙するmanifest、config、secret、sequenceの未決事項は、実装開始前にDesign Authority判断が必要である。

これらは本書で新しい結論を与えず、確定設計と区別して扱う。

## 19. 用語と参照先

表3　主要用語

| 用語 | 本書での意味 |
|---|---|
| Canonical State | 現在の運用判断と復旧の基準になる正本State |
| Fact | Broker約定等、確認された外部現実。提案妥当性と分離して受領する |
| Proposal | Human判断を求める提案。ApprovalやOrderとは別Entity |
| Incident | 解決まで保持する問題状態。Proposal失効では消えない |
| Projection | Canonical Stateから再生成できる表示・参照用Artifact |
| Durable Stage | 外部結果を再利用し、再Callや再課金を避ける中間永続化 |
| Fail Closed | 必須条件が不明・不成立なら危険側へ進まず停止する挙動 |
| Environment Binding | Runtime Identity、Environment、State、Data Rootの対応を検証する境界 |

Canonicalな詳細参照先：

- 設計一次資料：`docs/source/argus_design_source_v0.1.md`
- 構造化設計：`docs/model/argus_structured_design_data_v0.1.md`
- Architecture判断：`docs/adr/argus_architecture_decision_records_v0.1.md`
- Environment Binding：`docs/contracts/argus_environment_binding_contract_v0.1.md`
- Runtime Identity：`docs/contracts/argus_runtime_identity_document_contract_v0.1.md`
- Bootstrap：`docs/contracts/argus_runtime_foundation_bootstrap_contract_v0.1.md`
- Critical Path Review：`docs/contracts/argus_critical_path_review_contract_v0.1.md`
- 検証戦略：`docs/test/argus_test_strategy_v0.1.3.md`

## 20. Human Reviewの観点

Human Reviewでは、特に次を確認する。

- ARGUSが行うことと、人だけが行うことが意図どおりか。
- Broker API自動発注禁止とHuman Approval境界が明確か。
- 投資候補からExecution Fact、Portfolio更新、Watchまでの閉ループに欠落がないか。
- Risk、Environment Binding、Cost、Canonical StateのFail Closed条件が理解できるか。
- 通知抑制と重大Risk監視の関係が意図どおりか。
- Deferredと未確定事項が、現在成立済みの仕様に見えていないか。
- §18のReview項目をどのAuthorityで解消するか。

本書の承認、Final Freeze、Baseline確定はHumanが別途行う。
