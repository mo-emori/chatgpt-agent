# ChatGPT Local Agent 本番設計 v0.6

```text
Document Role: PRODUCTION_DESIGN
Version: 0.6
Status: IMPLEMENTED_BASELINE
Supersedes: ChatGPT Local Agent 本番設計 v0.5
```

## 1. 文書の目的・適用範囲・用語

### 1.1 目的と適用範囲

通常のChatGPTをControl Planeとして利用し、ローカルPC上のCodex / Claude
Codeを非同期実行する。

本システムは「Remoteから任意のローカルCLIを実行する仕組み」ではない。

> 事前登録されたWorkspaceに対して、許可されたSender / Channel / Actor /
> ModeのJOBだけを、状態管理されたLocal Runnerが実行する。

本書は、実装済みの本番baselineを構造とlifecycleに沿って記述する。明示的に
`PLANNED / NOT_IMPLEMENTED` と記した項目は設計方向であり、現在のruntime behavior、
security boundary、failure semanticsを変更しない。

### 1.2 用語

| 用語 | 定義 |
|---|---|
| Control Plane | Humanとの対話、Instruction登録、JOB生成、結果解釈、後続判断を担うChatGPT側の役割。 |
| Worker | 認可、snapshot、queue、排他、Actor起動、Evidence、Result deliveryを所有するPython Local Agent Worker。 |
| Actor | Workerから起動されるCodexまたはClaude Code。 |
| canonical workspace | Source of Truthである登録済みLocal Repo / Git workspace。 |
| Job Evidence | JOB単位でWorkerが保持するlocal execution evidence。 |
| Review Evidence | isolated reviewの実行・境界検証から正規化され、条件を満たす場合にcanonical repoへ採用されるevidence。 |
| Context Manifest | capabilityに必要なsourceとdependencyを宣言し、観測結果を決定的に表すmanifest。 |
| `SHADOW` | Context Harnessがcontextをbuild/index/scanして差分を観測するが、Actor prompt、読取り範囲、JOB成否を変更しない観測モード。Phase 1で実装済み。 |
| `LIVE` | 検証済みevidence packageをcanonical repoへ実際に採用する動作モード。現在はReview Evidence Adoptionで使用する名称であり、Context Harnessのpackage-first実行を意味しない。 |
| Full Review | critical、closure、authorityまたはboundary-sensitiveな場合に、deltaへ限定せず必要な全範囲を確認するreview。現時点の通常reviewもfull-repo inputを用いる。 |

`SHADOW` と `LIVE` は相互に切り替わる全システム共通stateではない。前者はContext Harness Phase 1の
non-enforcing observation、後者はReview Evidence Adoptionの実採用を表す各subsystem固有のmodeである。

## 2. 設計原則

- ChatGPTはControl Plane、Notionはv3 Instruction source / Control / Registry、SlackはCommand / Event Busとする。
- Python Workerは状態管理されたLocal Execution Bridge / Runnerであり、Codex / Claude Codeを境界の異なるActorとして扱う。
- Local Repo / GitをSource of Truthとし、Google Driveは条件付きArtifact Transport / Store、Browser Callbackはwake-up notificationとする。
- 認可、workspace containment、actor isolation、evidence provenanceをWorkerが所有し、Actor自己申告だけをauthorityにしない。
- actor、review boundary、artifact delivery、evidence adoption、callback、cleanupのfailure domainを分離する。
- Fail Closed、idempotency、same-workspace serialization、Human control boundaryを維持し、自動Actor chainや自動修正ループを行わない。

## 3. システム全体構成

### 3.1 Component topology

```text
Human ↔ ChatGPT
          │
          ├─ Notion Instruction page
          │       │ read-only resolve
          ▼       ▼
        Slack ── Local Agent Worker
        event      ├ Authorization
                   ├ Instruction snapshot / Job State Store
                   ├ Atomic Workspace Dispatch
                   └ Actor / Mode Policy
                            │
                       ┌────┴────┐
                       ▼         ▼
                     Codex    Claude Code
                       └────┬────┘
                            ▼
                     Local Workspace / Git
                            │ conditional artifacts
                            ▼
                       Google Drive
                            │
                            ▼
                         ChatGPT
```

### 3.2 Component responsibilities

| Component | Role |
|---|---|
| ChatGPT | Control Plane。対話、Instruction登録、JOB生成、結果解釈、後続判断 |
| Notion | v3 Instruction source / Control / Registry / Human-facing Knowledge |
| Slack | Command / Event Bus。v3ではInstruction本文ではなく `instruction_ref` を運ぶ |
| Python Worker | Local Execution Bridge / Runner。認可、Notion read-only resolve、snapshot、状態管理、排他制御、CLI起動、成果物収集 |
| SQLite | Job State Store / idempotency / snapshot / atomic dispatch / recovery |
| Codex | Implementation Actor |
| Claude Code | Independent Review / Audit Actor |
| Google Drive | 条件付きArtifact Transport / Store |
| Local Repo / Git | Source of Truth |

Inbound HTTPは公開しない。Slack Socket Modeを利用する。

### 3.3 Runtime contract summary（IMPLEMENTED_BASELINE）

運用原則は `Fail -> Diagnose -> Scope -> Fix -> Restart -> Smoke -> Retry` とする。Actor の loud failure は terminal Result の raw `error_summary` を authority として反応的に扱い、強く識別できるものだけを best-effort 分類する。曖昧な失敗は `UNKNOWN_RUNTIME_FAILURE` のまま残す。

自動 model fallback、自動 config 変更、Actor disable/brake、persistent availability、READY/DEGRADED state machine、runtime identity/cache/TTL、startup inference probe は導入しない。修復は scope と hint を確認した人が行い、Worker restart、smoke、元 JOB retry の順に進める。

Codex は shared `~/.codex` とその default model を意図的に利用し、専用 `CODEX_HOME` や model pin を持たない。一方、Windows sandbox backend は silent-corruption invariant である。Codex CLI 0.157.1 が `-c windows.sandbox="mxc"` を per-run override として受理し、`mxc` を同 build の正式な variant として検証することを確認したため、Local Agent launcher は `codex exec -c windows.sandbox="mxc" --sandbox workspace-write -` を使用する。これは shared config を変更せず、自動 fallback も行わない。

Terminal Result Manifest の `runtime` は evidence であり JOB 成否の追加 gate ではない。`configured_default_model` は shared config の観測値、`requested_model` は launcher の明示要求（現在は `null`）、`effective_model` は Codex execution header の `model:` または Claude stream-json `message.model` が存在する場合だけの実行証拠であり、推測しない。Codex CLI 0.157.1 の通常実行でauthoritativeなmodel出力が得られない場合、`effective_model=null` は正常である。`cli_version` と `sandbox_config` も best-effort evidence とする。`sandbox_config` は shared config の観測値であり、per-run MXC invariant 自体は launcher引数とmanual checkの `sandbox_invariant=windows.sandbox="mxc"` で確認する。Terminal `runtime` に別の `sandbox_invariant` keyは追加しない。

Manual diagnostic は Worker event loop、State Store write、Workspace Lock、canonical workspace を使用せず disposable temp directory で実行する。

```text
python agent_worker.py --check
python agent_worker.py --check all
python agent_worker.py --check codex
python agent_worker.py --check claude
```

無引数の `--check` と `--check all` は Codex、Claude の順に両方を診断し、`--check codex` / `--check claude` は指定Actorだけを診断する。出力は CLI path/version、configured default model、sandbox config/invariant、minimal inference PASS/FAIL、effective model（authoritativeに観測できる場合だけ）、impact scope を含む。check は manual/reactive、read-only/nonpersistentであり、disposable temp directoryで最小推論を行い、自動 compatibility gate、自動 startup probe、自動 repairには使用しない。ACL before/after、workspace-write mutation probe、Claude review-isolation canary、browser send E2E は将来の optional `--extended` の対象であり、本 baseline には含めない。

### 3.4 Slack ingress authorization

WorkerはChannel IDとSender IDの両方をallowlistで検証する。

```python
ALLOWED_CHANNEL_IDS = {"C0XXXXXXXXX"}
ALLOWED_SENDER_IDS = {"U0XXXXXXXXX"}
```

```text
Slack Event
  ↓
Channel allowed? ─ No → Ignore + Operational Log
  ↓ Yes
Sender allowed?  ─ No → Ignore + Operational Log
  ↓ Yes
Control message match? ─ Yes → Handle PING / PONG without Job State
  ↓ No
JOB parse / validate / accept
```

認可不一致はSlackへ `BRIDGE_ERROR` を返さず、ローカルOperational Logだけに記録する。
初期本番ではHumanがChatGPTへ実行依頼を出した時点をHuman Authorizationとみなす。
自動JOB連鎖や自動修正ループを導入する場合は、別途Human approval境界を設計する。

認可後の制御・liveness messageとして、次のplaintext PING / PONGを正式にサポートする。

```text
Request:  LOCAL-AGENT PING
Response: LOCAL-AGENT PONG — Worker ready
```

これはProtocol v1 / v2 / v3のJOB JSONではなく、Channel / Sender認可後にだけ処理する。
Job State rowを作成せず、JOB parser / dispatchには入らない。未認可のPINGにはPONGを返さず、
通常のnon-JOB textも従来どおりignoreする。目的はChatGPT → Slack → Worker → Slackの
軽量なliveness / preflightであり、Notion / Actors / Drive / Browserのdeep health checkではない。

## 4. Job Protocol / Job Lifecycle

### 4.1 Standard: Protocol v3（IMPLEMENTED_BASELINE）

v0.6の標準JOB Protocolは `protocol_version=3` とする。

```json
{
  "protocol_version": "3",
  "job_id": "ARGUS-P-0114-v3",
  "actor": "codex",
  "mode": "implementation",
  "workspace": "argus",
  "instruction_ref": {
    "type": "notion_page",
    "page_id": "01234567-89ab-cdef-0123-456789abcdef"
  },
  "callback": {
    "type": "chatgpt_browser",
    "url": "https://chatgpt.com/c/..."
  }
}
```

`callback` はoptionalである。v3 requestにはPrompt本文、Base64 Prompt、
`prompt_encoding`、request側の `prompt_sha256` / `instruction_sha256` を含めない。

```text
ChatGPT
  ↓ Instruction本文を登録
Notion Instruction page（exactly one code block）
  ↓ page_idを参照
Slack JOB（instruction_ref only）
  ↓
Worker
  ↓ read-only Notion API / acceptance-time resolve
resolved instruction UTF-8 bytes
  ├─ Worker-generated SHA-256
  ├─ SQLite accepted snapshot
  ├─ Local Execution Evidence
  └─ Actor stdin
```

authoritative identityは `page_id` 単独ではなく、受理時に解決したinstruction bytesと
Worker-generated SHA-256である。同一bytesをSHA、永続snapshot、Actor stdinの起点とする。

### 4.2 Compatibility: Protocol v1 / v2（IMPLEMENTED_BASELINE）

v1 / v2は既存producerと保存済みJOBのためのcompatibility protocolとして維持する。
新規JOBはv3を使用する。

- v1はlegacy plain Promptとrequest SHAの既存検証規則を維持する。
- v2はBase64 Promptをstrict decodeし、decoded Prompt bytesをauthorityとする。
- v2はrequest `prompt_sha256` を受け付けず、Workerがdecoded bytesから生成する。
- v1 / v2のinvalid inputは従来どおりFail Closedとする。

### 4.3 Notion Instruction Resolver / Acceptance Snapshot（IMPLEMENTED_BASELINE）

v3のInstruction sourceはNotion page内のcode block **ちょうど1個** とする。
WorkerのNotion integrationはread-onlyであり、Instruction pageをREADできるが、Notionを更新してはならない。

| Resolver result | Outcome |
|---|---|
| pageがinaccessible / nonexistent | `INSTRUCTION_NOT_FOUND` |
| code blockが0個 | `INSTRUCTION_BLOCK_MISSING` |
| code blockが1個 | bytesへ解決して受理・実行 |
| code blockが2個以上 | `INSTRUCTION_BLOCK_AMBIGUOUS` |

resolverはFail Closedである。対象code blockのrich textを連結し、UTF-8 bytesとして解決する。
空Instruction、decode不能、想定外schemaを推測補完しない。

v3はJOB acceptance時にNotionをresolveし、以下をSQLiteへ永続化してからqueueへ進める。

- resolved prompt / instruction snapshot
- `prompt_sha256` / `instruction_sha256`
- `instruction_ref`
- callback metadata

実装上のcanonical hash保存列は `prompt_sha256` であり、v3のResult ManifestとEvidenceでは
`prompt_sha256 == instruction_sha256` とする。両者は受理時に解決した同一bytesを識別する。

受理後の規則:

- Notionの変更はaccepted executionを変更しない。
- queue待機後もpersisted snapshotを実行する。
- restart recoveryでNotionを再resolveしない。
- v3 persisted queueでsnapshot、SHA、`instruction_ref` が欠落・不正、またはSHA不一致なら
  `FAILED / INCOMPLETE_QUEUED_STATE` としてFail Closedにし、Notionから再構築しない。

### 4.4 Slack Message Parse and pre-dispatch rejection

ChatGPT Slack Pluginは本文末尾にattributionを付加し得るため、先頭JSON objectだけをdecodeする。
Markdown code fenceもtransport表現として除去する。

```python
decoder = json.JSONDecoder()
job, end = decoder.raw_decode(text)
```

Parse前にChannel / Sender authorizationを行う。v3 parserはPrompt本文やrequest hashを許容せず、
`instruction_ref.type == "notion_page"` と `page_id` を検証する。
認可後、requestがjob_id / actor / workspaceとtrustworthyな `chatgpt_browser` callback destinationを安全に確立できるまで
parseされた後のterminal validation failureは、shared rejection pathでSlack failure Resultを公開してから
`LOCAL_AGENT_JOB_FAILED` を送る。これはvalidation成功またはJOB acceptanceを意味せず、Actorを起動しない。
unauthorized、callback destinationを安全に確立できないmalformed / unparseable request、invalid / untrusted callback metadataは
Fail Closedかつno-callbackとする。previous / global conversation URLを推定または再利用しない。

PINGはJSON decodeより前の、認可済みplaintext control messageの完全一致として扱う。
ChatGPT Slack transportが付加する既知のattribution付き形式、例えば
`LOCAL-AGENT PING *使用して送信されました* <@...>` は受理する。ただし、
`LOCAL-AGENT PING SOMETHING` のような任意のlookalikeはPINGとして受理しない。

## 5. Actor Execution

### 5.1 Workspace Registry and launch root

Workspace / Browser等のnon-secret installation固有情報は `config.json` に外出しする。

```json
{
  "workspaces": {
    "sandbox": {
      "path": "C:\\dev\\chatgpt-agent\\sandbox",
      "git_required": false,
      "allow_skip_git_repo_check": true,
      "artifact_roots": []
    },
    "argus": {
      "path": "C:\\dev\\argus",
      "git_required": true,
      "allow_skip_git_repo_check": false,
      "artifact_roots": ["validation/reports", "validation/metrics", "tests"]
    }
  },
  "browser": {
    "enabled": true,
    "cdp_url": "http://127.0.0.1:9222"
  }
}
```

`config.py` はload / validate / Path変換 / environment bindingを担当し、Credentialは
`.env` / environment variables等の既存secret管理を維持する。Actorの `cwd` は必ずRegistryのpathへ固定する。
`sandbox` のみ `--skip-git-repo-check` を許可し、`git_required=True` のWorkspaceはActor起動前にgit repositoryであることを確認する。

### 5.2 Actor / Mode Policy

```python
ALLOWED_ACTOR_MODES = {
    ("codex", "implementation"),
    ("claude", "review"),
}
```

未知の組合せはActor起動前のterminal validation failureとして拒否する。現在の外部failure contractは
`status=BRIDGE_ERROR`、`failure_class=INVALID_JOB` であり、例えば `codex + review` の
`error_summary` は `Actor/mode not allowed: ('codex', 'review')` となる。認可済みrequestが安全なrouting metadataと
trustworthyなcallback destinationを確立できるところまでparseされている場合、この拒否はSlack failure Resultを先に公開し、
続いて `LOCAL_AGENT_JOB_FAILED` を通知する。通知はJOB acceptanceではなく、許可されていないActor / Modeを実行する権限を
作らない。Actorは起動しない。Claude review isolation v0.1では、reviewは実行可能な独立clone上で行う。canonical workspaceへの実行権限は与えず、Workerが入力provenance、canonical保護、実行証跡、採用可否を所有する。

```text
claude -p <prompt>
--restricted --tools Read,Glob,Grep,Bash
--permission-mode dontAsk --permission-prompts none
--safe-mode --strict-mcp-config --no-session-persistence
```

`Bash` はWorker-managed settingsで明示許可するreview capabilityであり、security boundaryではない。
完全なlauncher contractと制約は13.3で定義する。Edit、Write、PowerShellは公開しない。

CodexはWorkspace Registryに応じて実行する。

```text
sandbox:       codex exec --skip-git-repo-check -c windows.sandbox="mxc" --sandbox workspace-write -
git workspace: codex exec -c windows.sandbox="mxc" --sandbox workspace-write -
```

Codex implementationとClaude independent reviewの境界を維持し、CodexからClaude Codeを直接起動させない。

### 5.3 Codex: Windows sandbox backend decision

Windows上の本Local Agent環境では、Codex設定のsandbox backendを次のとおり固定する。

```toml
[windows]
sandbox = "mxc"
```

Local Agentの実行contractは `codex exec -c windows.sandbox="mxc" --sandbox workspace-write -` とし、subprocessの`cwd`はWorkspace Registryに登録されたworkspace pathとする。`--sandbox workspace-write`はCodex sandboxの境界を指定し、`-c windows.sandbox="mxc"` はWindows上でその境界を実装するbackendを一回の実行に限定して固定する。approval behaviorは変更しない。shared configやdefault modelは引き続き利用する。

本環境では`elevated` backendを承認しない。また、MXCを利用できない場合に`elevated`へsilent fallbackしてはならない。ARGUS（`C:\dev\argus`）で`elevated`を使用した際、workspace rootへの持続的なsandbox ALLOW ACE（`ares\CodexSandboxUsers`および未解決sandbox SID）、`.git`へのsandbox SID明示DENY ACE、tree内の不整合なACL、書込み可能fileだけに付加されたsandbox SID ACE、およびcanonical docsへの実際のwrite failureを観測した。既存の影響pathはARGUS作業で別途修復済みである。

これらのローカル観測と後述のcontrolled A/Bは、同世代Windows Codex CLIの`elevated` / `workspace-write`周辺で知られているACL mutation defectと整合し、backendが原因であることを強く支持する。ただし、観測範囲を超えてCLI全体または全Windows環境へ一般化しない。

### 5.4 Codex: workspace validation and filesystem integrity scope

本incidentのprimary remediationはWindows sandbox backendをMXCに固定することである。広範なFilesystem Preflight frameworkはprimary remediationとして要求せず、Planner / Operation-Preflight architectureをv0.6へ導入しない。軽量なworkspace integrity checkは、必要性を別途評価したうえで将来追加してよい。

### 5.5 Codex / Claude Code CLI Version Policy

確認済みCLI versionを設定に保持し、Worker起動時に完全一致で照合する。

```python
EXPECTED_CLI = {
    "codex": "codex-cli 0.157.1",
    "claude": "2.1.280",
}
```

Version不一致時は該当ActorをUnavailableとする。WindowsでCodex CLIを更新する場合、`EXPECTED_CLI`を更新する前に次のsandbox-backend regressionを完了しなければならない。

- backendがMXCのままであることを確認する
- fresh scratch workspaceを用意する
- `--sandbox workspace-write`でfile createおよび同一fileのrewriteを確認する
- workspace root ACLのbefore / afterを比較する
- `.git` ACLのbefore / afterを比較する
- new file ACLにsandbox ACE pollutionがないことを確認する
- 実行可能な場合はLocal Agent E2Eを行う

regressionがpassした後にのみ`EXPECTED_CLI`を更新する。現在の`codex-cli 0.157.1`を普遍的に安全とみなしてはならない。本環境で確認された安全性は、テスト済みのMXC backend configurationと`workspace-write` execution contractの組合せに依存する。

## 6. Workspace / FIFO / Lock / State Store

### 6.1 Job State Store / State Machine

SQLiteをJob State Storeとして使用する。

```text
jobs
------------------------------------------------
job_id              PRIMARY KEY
protocol_version
actor / mode / workspace
prompt              accepted instruction snapshot
prompt_sha256        accepted bytes SHA-256
instruction_ref     serialized reference
callback_type / callback_url
status / pid / host
received_at / queued_at / started_at
heartbeat_at / completed_at
exit_code / failure_class
result_drive_file_id
```

State:

```text
RECEIVED → VALIDATED → QUEUED → DISPATCHING → RUNNING
                                              ├─ DONE
                                              ├─ FAILED
                                              ├─ INTERRUPTED
                                              └─ RECOVERY_REQUIRED
```

`DISPATCHING` はSQLite transactionによるatomic claim stateである。
`claim_next_queued(workspace)` は `queued_at` FIFOで1件をclaimし、同じtransaction内で
同一Workspaceに `DISPATCHING` / `RUNNING` がないことを確認する。これにより並行する新規受理、
完了後dispatch、再起動queue recoveryが同じWorkspaceで複数Actorを起動することを防ぐ。

新規JOBとpersistent `QUEUED` JOBは、どちらも同一のatomic dispatch pathを通る。
`job_id` をidempotency keyとし、重複JOBを再実行しない。

### 6.2 Heartbeat / Crash Recovery

RUNNING中はWorkerが60秒ごとに `heartbeat_at` を更新する。HeartbeatだけでActorの生死を断定せず、
PID / host / process stateと組み合わせる。

```text
RUNNING
  ↓ host / pid / process確認
  ├─ Process alive → heartbeat復旧・RUNNING維持
  ├─ Process dead  → INTERRUPTED
  └─ 判定不能      → RECOVERY_REQUIRED
```

同じRUNNING JOBを無条件に自動再実行しない。判定不能はHumanへ戻す。
RUNNING recoveryはqueued dispatchより先に完了しなければならない。

### 6.3 Workspace FIFO / Persistent Queue Recovery

同一Workspaceに複数Actorを同時実行しない。Lock単位はActorではなくWorkspaceであり、異なるWorkspaceは並行実行可能である。
同一Workspaceは `queued_at` 昇順のFIFOとする。

Worker startup sequence:

```text
1. initialize state / schema
2. CLI version gate
3. recover RUNNING jobs
4. restore abandoned DISPATCHING claims → QUEUED
5. scan persistent QUEUED jobs
6. workspace FIFO atomic dispatch
7. start Slack listener
```

RUNNING recovery完了前にqueued JOBをdispatchしない。起動時はWorkspaceごとのheadをatomic claimし、
完了時に次のoldest `QUEUED` を同じ経路でclaimする。

persisted v3 queued JOBは保存済みInstruction snapshotを実行し、現在のNotion contentsを参照しない。
不完全なlegacy v3 queued stateはNotionから再構築せずFail Closedにする。

## 7. Context Management

### 7.1 Current / legacy behavior

現在のActorは、taskに関係するrepo、docs、evidenceを広く調査できる。Context Harness Phase 1は
この読取り範囲を制限せず、Actor promptの削減やpackage注入も行わない。このbaselineを変更せずに、
将来のcontext omissionを測定できる観測基盤を先に導入している。

### 7.2 Context Manifest and Phase 1 observation（SHADOW / IMPLEMENTED_BASELINE）

workspaceは `.agent/context.json`（`schema_version: 1`, `mode: SHADOW`）でopt inできる。
declarationはcapabilityをrepo-relative source、source kind、authority class、明示的なcontext-item dependency edge、
actor/mode selector、generated evidence rootへ対応付ける。YAML dependencyを避けるためJSONを採用し、raw bytesをhashする。
Actorが編集可能なdeclarationに `approved_semantics` を含めることは禁止する。

deterministic builderはHEAD、declaration raw SHA-256、declared file raw SHA-256、type/existence、
関係pathごとのtracked/untracked status、configured dependency edge、optional normalized-text hashを記録する。
raw bytesがauthorityである。normalized-text hashが一致してもraw-byte changeは `EOL_ONLY` とlabelするだけで、
`NO_IMPACT` へ変更しない。関連しないdirty fileを観測外にできるのは、configured edgeが選択しない場合だけである。

canonical manifestはUTF-8 JSON、sorted keys、compact separators、LF suffixで生成する。content hashは
`observed` とPhase 1では空の `approved_semantics` を対象とし、同じinputから同じhashを得るためtimestampと
`previous_context_hash` を除外する。lifecycle metadataはcontext ID、schema/hash、prior hash、HEAD、
authoritative hash、builder provenanceを保持する。

mandatory terminal scanはactor executionとevidence adoptionの後、SQLite workspace claimを保持したまま、
`mark_completed` の前に実行する。Result Manifestのoptional `context` objectはmode、capability、current/previous hash、
delta status、`would_block`、evidence path、changed source、unverifiable reasonを持つ。Phase 1はJOB statusもActor promptも変更しない。

### 7.3 Delta Scan state definitions（IMPLEMENTED_BASELINE）

| State | 意味 |
|---|---|
| `NO_IMPACT` | 宣言されたcontextのauthoritative raw inputに影響する差分がない。 |
| `CONTEXT_UPDATE` | context inputに追随可能な変更があり、再buildされたmanifestで表現できる。 |
| `POTENTIAL_AUTHORITY_CHANGE` | 承認済みsemantic authorityに影響し得るため、機械的な追随だけでは確定できない。 |
| `UNVERIFIABLE` | 必要なsource/hash/状態を決定的に検証できない。将来enforcementする場合はFail Closedとなる。Phase 1では `would_block` として観測する。 |

### 7.4 `approved_semantics` authority reconciliation

`approved_semantics` は単なるfile hashではなく、承認された意味・authorityを表す。Phase 1では空であり、
Actor-editableな `.agent/context.json` から設定または変更できない。

`POTENTIAL_AUTHORITY_CHANGE` のsemantic reconciliationはLLMが分析し、差分、影響、更新案を提案してよい。
通常、contextの受入れ・更新を決定するauthorityはChatGPTである。ただしcritical、ambiguous、または
authorityを変更するdecisionはHumanへescalateする。LLM outputだけでapproved semanticsをmutationしてはならず、
Human/ChatGPTのdecisionとWorkerが検証できる更新経路を経ない自動変更は認めない。

### 7.5 Evidence Indexer（PHASE 2A / IMPLEMENTED）

Phase 2Aはcanonical normalized evidenceを検索可能な決定的Evidence Indexへ投影する。入力はconfigured
Review Evidence Adoption package（`worker-review-evidence`）、Cross-JOB Historical Job Evidence Adoption package
（`worker-job-evidence`）、およびworkspace declarationが `canonical_evidence: true` と明示したcanonical
machine-readable Result Manifestだけである。raw Local Agent transcript/log、Slack単体、任意のprose fileはsource discovery
対象にしない。Slackは既存historical package内の `CORROBORATIVE_ONLY` provenanceとしてのみ保持する。

index schemaは `context-harness-evidence-index` version 1であり、entry identity/type、JOB/actor/mode/workspace/capability、
JOB/failure/artifact status、package path/hash、git head、instruction provenance、review boundary/actor execution、
structured finding/verdict/relationship、trust class、source file raw SHA-256をnullable fieldとして保持する。構造化されていない
値をproseから推測しない。qualityは `STRUCTURED`（semantic fieldsを含むmachine-readable evidence）、`PARTIAL`
（optional relationship/finding等が未提供）、`UNSTRUCTURED`（canonical packageは存在するがreview semanticsがproseのみ）である。

canonical indexはUTF-8 canonical JSON（sorted keys、compact separators、LF suffix）で、timestamp、cache hit count、生成時刻を
hash materialに含めない。同一source bytes/setは同一index SHA-256となる。workspace外のpath/traversal/ADS、symlink/reparse、
malformed JSON、package reference hash mismatchは明示的 `FAILED` diagnosticとし、`NO_IMPACT` と呼ばない。cacheはWorkerの
`logs/evidence-index/` にindexとは分離して置き、path/source hash/entryを保持する。unchanged manifestはreferenced bytesを
再検証した上でparseをreuseし、addition/removal/replacementをreportする。

generated canonical index/reportは `.agent/context.json` の `generated_root` 配下
`<capability>/evidence-index.json` / `evidence-index-report.json` に置く。Result Manifestへはadditive optional
`evidence_index` diagnostic（schema/status/quality counts/entry/source/reuse/change count/index hash/report path）を出す。
これはcomparison-onlyであり、qualityやbuild failureだけでJOB status、review verdict、Actor prompt、context visibilityを変更しない。
failed actor execution（429/session limitを含む）はreview verdictではなくexecution failureとしてindexし、入力に古い
`review_verdict` があってもfailed executionでは公開しない。generated indexはContract/ADR authorityではない。

### 7.6 Differential context / reviewへの段階移行（PHASE 3A COMPARISON IMPLEMENTED / PHASE 3B NOT_IMPLEMENTED）

移行は一度にActorの読取りを狭めず、次の段階で行う。

1. **Current / legacy:** Actorは関係するrepo、docs、evidenceを広く調査する。
2. **Observation / comparison:** Context Harnessがbuild、index、slice候補を作る一方、Actor behaviorは広いまま維持する。準備packageと実際に必要だったcontextを比較し、omissionを測定する。
3. **Package-first:** ActorはWorkerが準備したbounded packageを最初に読み、不足時はbounded additional referenceをrequestできる。
4. **Differential-review:** 通常reviewは主にdelta packageを使い、必要時にboundaryまたはfull scopeへexpandする。
5. **Full Review:** critical、closure、authority-changing、security/boundary-sensitiveなcaseでは常に選択可能とし、必要な全範囲を確認する。

Phase 2A Evidence Indexer、JOB・capability・actor/modeに応じてcandidateを組み立てるcomparison-only
**Job Context Slicer**、およびPhase 3A **Review Delta Package Builder** は実装済みである。次段階はactor package consumptionである。設計authorityはcompleted design job
`LOCAL-AGENT-CONTEXT-HARNESS-PHASE23-DESIGN-20261003-001` とし、その範囲を越えて本書で発明しない。

**NOT IMPLEMENTED:** prompt reduction/injection、Actorによる `context_ref` / `job_context_ref` /
`review_package_ref` consumption、package-first Claude invocation、`DELTA_REVIEW` /
`BOUNDARY_REVIEW` actor execution/routing、LLM reconciliationのruntime接続、
automatic `approved_semantics` mutation、Actorのsupplemental repository read禁止。

### 7.7 Job Context Slicer（PHASE 2B / IMPLEMENTED_COMPARISON_ONLY）

Phase 2Bは、terminal Delta ScanとPhase 2A Evidence Index生成後、workspace leaseを保持したまま
deterministic `job-context.json` candidateを生成する。これは観測・比較専用であり、Codex/Claudeへ送るinstruction、
actor prompt、context visibility、Claude review cloneのfull-repo内容、jobの成功/失敗条件を変更しない。
Review Delta Package BuilderはPhase 3Aとしてcomparison-onlyで実装済みである。actorによるpackage-first consumptionは
Phase 3Bであり **NOT IMPLEMENTED** である。

入力はcurrent Capability Context Manifest、mandatory Delta Report、Evidence Index、workspace/capability declaration、
protocol-v3 job identity/instruction hash、および `instruction_ref.context_request` に明示された構造化metadataのみである。
`phase`、`target_files`、`finding_ids`、`previous_finding_ids`、`context_items`、`evidence_ids`、`max_text_bytes`
を構造化入力として扱い、instruction proseからcapability/finding/dependencyを推論しない。LLMをslicer内部で使用しない。

schemaは `context-harness-job-context` version 1であり、workspace/capability/phase、source manifest hash、delta hash/status、
Evidence Index hash、job identity/instruction hash、selection status、authority refs、provenanced approved semantics、dependency
state、evidence refs、structured finding IDs、target/changed files、protected/forbidden boundaries、unknowns、reconciliation/
expansion requirements、included file/excerpt hashes、categoryごとのselection reason、size counts、deterministic package hashを持つ。
serializationはUTF-8、key sort、compact separator、末尾LFでcanonical化する。whole documentは複製せずref/hashを優先し、
declarationにmechanically stable selectorがない場合はsemantic excerptを作らずexpansion requirementを記録する。

selection statusは次の3値である。

| Status | Meaning |
|---|---|
| `READY_BOUNDED` | 検証済み入力からbounded candidateを決定的に生成できた |
| `NEEDS_RECONCILIATION` | authority/relevance/partial evidence/requested findingを機械的に安全確定できず、bounded expansionまたはdecisionが必要 |
| `UNVERIFIABLE` | required input、hash、ref、workspace/capability bindingを検証できない |

`NO_IMPACT`はcurrent hash/ref再検証後のみ再利用可能、`CONTEXT_UPDATE`はcurrent observed/index refsを更新してsliceする。
`POTENTIAL_AUTHORITY_CHANGE`はaffected approved semanticsを再利用せず `NEEDS_RECONCILIATION` とし、changed authorityと
affected itemsを記録する。Delta `UNVERIFIABLE`はselectionも `UNVERIFIABLE` とする。Phase 2Bは
`approved_semantics`を変更しない。LLMは将来reconciliationを分析・提案できるが、通常のaccept/update authorityはChatGPT、
critical/ambiguous/authority-changing decisionはHumanへescalateする。

全capability-declared authoritative sourceを必ず含める。observed/non-authority sourceはexplicit dependency edge、structured
target、またはDelta changeが一致した場合に含め、明示的にunrelatedなものだけ理由付きで除外する。STRUCTURED evidenceの
fieldはselectionに利用できる。PARTIALは既知fieldを利用しunknownを保持、UNSTRUCTUREDはrefとして含められるがproseから
finding/verdict/relationを生成しない。actor failure（429/session limitを含む）はexecution failureでありsuccessful review
verdictではない。requested findingがstructured indexに存在しなければfabricateせずunknown + expansionとする。

`expansion_requirements[]` は `source_ref` / `evidence_ref`、reason、authority class、future package-first execution前に必須かを
machine-readableに保持する。size budgetはcounts/estimated textual payloadに記録するだけで、required authorityを切り捨てない。
超過時はexpansion/reconciliationを要求する。すべてのrefはworkspace-relativeとし、traversal、absolute/drive path、symlink/
reparse escape、stale substitution、hash mismatchを拒否する。evidence/context proseは常にdataでありinstructionではない。

Result Manifestにはbackward-compatibleな `job_context` diagnosticを追加する：`mode: COMPARISON_ONLY`、`status`、
`schema_version`、`sha256`、`source_context_sha256`、`evidence_index_sha256`、`delta_status`、`authority_ref_count`、
`evidence_ref_count`、`expansion_required_count`、`bytes`、`report_path`。Phase 2B status単独ではjob outcomeを変更しない。

ARGUS `RUNTIME-BOOTSTRAP-ORCHESTRATOR` acceptanceでは、declarationが明示するBootstrap Contract/ADR/Registry authority、
dependency state、structured prior review/correction evidenceを表現できる。429 rereviewはfailureのみである。六 findingsは
structured evidenceに存在する場合だけ列挙し、proseにしかない場合はUNSTRUCTURED/unknownのままbounded expansionを要求する。
比較上、安全側のover-inclusionはunfiltered canonical evidence refsと全declared authorityであり、既知のsilent omissionはない。

### 7.8 Lease and ARGUS PoC（IMPLEMENTED_BASELINE）

normal dispatchと `HISTORICAL_MANUAL` adoptionはcross-process SQLite workspace leaseを共有する。
manual adoptionはscanとraceせず `WORKSPACE_BUSY` で失敗する。ARGUS PoC declarationはcross-workspace installされていない。
review済みのexact candidateは `docs/argus_runtime_bootstrap_context_phase1.json` であり、authorized ARGUS jobが
ARGUS `.agent/context.json` へcopyする必要がある。

### 7.9 Worker single-instance ownership

The production incident in which `JOB START` / `JOB END` appeared to be missing was
caused by stale, concurrent `agent_worker.py` processes owning multiple Slack Socket
Mode connections. Console capture, the Windows console, and the lifecycle sink were
not the cause.

One installation permits exactly one daemon Worker. `WorkerInstanceGuard` holds an
OS byte-range lock on `logs/worker/agent_worker.lock` for the entire daemon lifetime.
On Windows it uses the standard-library `msvcrt.locking` primitive; POSIX uses
`fcntl.flock`. The JSON PID/host text in the file is diagnostic metadata only: lock
ownership is decided by the OS, not by trusting a PID file. Normal exit and Ctrl+C
close/unlock the handle. After abnormal process death, the kernel releases the lock;
the next Worker atomically acquires it and replaces stale metadata. The lock file is
intentionally retained to avoid unlink/recreate races. No process is killed or
replaced automatically.

Daemon startup order is:

1. `configure_logging`
2. `state_store.initialize`
3. CLI availability checks
4. acquire the single-instance guard
5. recover RUNNING jobs
6. construct `SlackBridge`
7. recover queued jobs
8. open Slack Socket Mode via `SlackBridge.start`

The guard precedes both recovery paths so two daemons cannot concurrently recover
queues or own Socket Mode. A contender exits non-zero with
`SINGLE_INSTANCE_ALREADY_RUNNING` before constructing `SlackBridge`. Maintenance and
adoption CLI paths return before daemon guard acquisition and remain usable while the
daemon runs.

The canonical liveness contract remains exactly:

```text
Request:  LOCAL-AGENT PING
Response: LOCAL-AGENT PONG — Worker ready
```

Normal lifecycle console output is limited to `JOB START` and `JOB END` blocks;
temporary forensic `DIAG_*` probes are not part of the production path.

## 8. Result Manifest and terminal status domains

Actor executionとArtifact deliveryを独立状態として扱う。

```json
{
  "protocol_version": "3",
  "job_id": "ARGUS-P-0114-v3",
  "actor": "codex",
  "mode": "implementation",
  "workspace": "argus",
  "instruction_ref": {
    "type": "notion_page",
    "page_id": "01234567-89ab-cdef-0123-456789abcdef"
  },
  "prompt_sha256": "...",
  "instruction_sha256": "...",
  "status": "DONE",
  "exit_code": 0,
  "summary": "completed",
  "git": {
    "baseline_commit": "...",
    "head_after": "...",
    "changed_paths": ["tests/unit/test_x.py"]
  },
  "artifact_status": "DONE",
  "artifacts": [],
  "rejected_artifacts": []
}
```

v3では `prompt_sha256 == instruction_sha256` であり、Workerがacceptance-time resolved bytesから生成する。
`status=DONE / exit_code=0 / artifact_status=PARTIAL_FAILURE` は有効である。
Drive失敗を理由に成功したActor executionをFAILEDへ変更しない。

Slack Result Manifestは、成功・失敗のどちらでもBrowser Callbackより先に送信する。
Browser CallbackはResult authorityではない。
callback結果はLocal `result.json` に記録し、callback失敗によってexecution / artifact statusを変更しない。
Claude reviewのResultは共通fieldに加え、input manifest provenance、raw / normalized stream evidence、
`review_boundary_status`、`adoptable`、`partial_evidence_available`、`cleanup_status`を保持する。
これらはactor、boundary、artifact、cleanupの各status domainを相互に上書きせず表現する。

## 9. Artifact Delivery

### 9.1 Actor Result Artifact Contract（IMPLEMENTED_BASELINE）

`AGENT_RESULT.artifacts` はexternal deliveryを意図したfileだけのlistである。`.agent` declaration、documentation、
validation evidence、context、baseline等のrepo-canonical outputを含めてはならない。canonical changeは
Worker-observed `git.changed_paths` と該当するcanonical evidence mechanismで表す。

WorkerはActor申告candidateを `EXTERNAL_DELIVERABLE`、`REPO_CANONICAL_REFERENCE`、`INVALID` に分類する。
external deliveryには通常のartifact-root validationがauthorityを持つ。configured external artifact root外という理由だけで
rejectされたcandidateだけがcanonical-reference normalizationの対象になり得る。その場合も同じworkspace-relative safetyと
file-existence validationを通り、normalized pathがcurrent Worker-observed `git.changed_paths` に存在し、job before/after snapshotで
status/content fingerprintが変化していなければならない。

有効なcanonical referenceはuploadせず、`artifact_status` を失敗させず、path、disposition、reasonとともにResult Manifest
`canonical_references` へ加算的に記録する。job前からdirtyだったfileを含む任意のunchanged repo fileは証明にならない。
missing、absolute、drive-qualified、UNC、traversal/out-of-workspace、unsafe等のcandidateは `rejected_artifacts` に残る。
genuine external deliverableは引き続きconfigured `artifact_roots` matchを必要とし、delivery failureはFail Closedとする。
このruntime normalizationはActor prompt guidanceを補完し、自己申告だけには依存しない。

### 9.2 Artifact Manifest

Agentの自然文Markdownリンク抽出には依存せず、Workerがprompt末尾へmachine-readable出力指示を追加する。

```text
<AGENT_RESULT>
{
  "summary": "...",
  "artifacts": ["relative/path/to/result.md"]
}
</AGENT_RESULT>
```

成果物がない場合は `artifacts: []` を許容する。

### 9.3 Artifact Path Validation / Upload Policy

Artifact pathは信頼しない。absolute path、drive-qualified path、UNC、`\\?\`、root escape、
Alternate Data Streams、Windows reserved device names、trailing dot / space、case-insensitive境界逸脱を拒否する。

検証はRaw syntax → Workspaceと結合 → normalize / resolve → root配下再確認 →
junction/symlink escape確認 → Workspace別Artifact Root allowlist確認、の順で行う。

Password、API key、OAuth token、Worker `.env`、`credentials.json`、`token.json`、Git credential、
外部同期禁止のCanonical State / log / secret領域、`browser-profile/` はuploadしない。

### 9.4 Google Drive transport

Google Driveは必須実行経路ではなく、条件付きArtifact Transport / Storeである。
source codeと変更事実のSource of TruthはLocal Repo / Gitに留まる。

- `artifacts=[]` の場合、Drive uploadは行わない。
- Agentが宣言し、path validationとWorkspace allowlistを通過したArtifactだけをuploadする。
- rejected ArtifactはpathとreasonをResult Manifestへ記録する。
- Drive failureはActor execution statusと分離し、`artifact_status` で表現する。
- uploadした場合はDrive File IDをResult Manifestへ記録する。

```text
chatgpt/jobs/<job_id>/
  ├─ result.json
  ├─ agent-output.md
  └─ artifacts/       # validated allowlisted artifacts only
```

## 10. Security Boundaries

### 10.1 Credential and local runtime state

個人ローカル運用を前提とし、専用Vault等は初期版では導入しない。

- `.env` / `credentials.json` / `token.json` はGitへ入れない。
- CredentialをJOB / Result Manifest / Artifactへ意図的に含めない。
- Worker資格情報領域とAgent Workspaceを分離する。
- 流出時はWorker停止 → Credential失効 → 再発行 → 必要な外部ログ/Artifact削除 → 原因修正、で復旧する。

`browser-profile/` はCredential相当のLocal Runtime Stateとして扱い、Git / Drive / Agent Workspaceへ含めない。

## 11. Evidence / Provenance

### 11.1 Logging Policy

#### 11.1.1 Operational Log

Python standard loggingを用い、設定を `operational_logging.py` に集中する。

| Item | Implemented baseline |
|---|---|
| File | `logs/worker/worker.log` |
| Handler | `RotatingFileHandler` |
| Rotation | `maxBytes=10 MiB`, `backupCount=5` |
| Encoding | UTF-8 |
| Console | INFO以上 |
| Operational file | DEBUG以上 |
| Slack SDK / Bolt | DEBUGを抑止しINFO以上を保持 |

認可済みSlack eventのraw textはDEBUGにだけ記録する。INFOにはjob_id、protocol、actor、mode、workspace、
status、queue / dispatch / recovery、exit code等の簡潔なmetadataを記録する。
Credentialとdecoded Instruction bodyをINFOへ記録してはならない。

### 11.2 Job Evidence: Local Execution Evidence

Operational LogとJOB単位Local Execution Evidenceは別物である。
`logs/<job_id>/` に以下のcontractを維持する。

- `request.json`
- `stdout.txt`
- `stderr.txt`
- Claude reviewではraw `stream-json` transcriptと、そこから導出したnormalized event evidence
- Git before/afterのHEAD / status / diff / cached diff
- `result.json`

EvidenceはWorkspace repo外・Git管理外・通常のDrive Artifact対象外とする。
Worker取得Git Evidenceを変更事実のauthorityとする。Slackへstdout/stderrのraw全文を流さない。

Windowsでは、WorkerによるGit before / after evidenceはCodex actor sandboxの外側から収集する。一方、`elevated` backendで`.git`へsandbox SIDの明示DENY ACEが付与されたことは、許容できないworkspace side effectである。したがってGit content evidenceとは別に、`.git` ACLのbefore / afterをWindows sandbox regressionの必須確認対象とする。

### 11.3 Cross-JOB Historical Job Evidence Adoption（IMPLEMENTED_BASELINE）

terminal `result.json` は意図的にcompactであり、後続JOBのreconciliationには情報が不足し得る。
過去のCodex `BLOCKED` decisionがlocal `stdout.txt` にしか存在しない場合の限定的なrecovery pathが
`HISTORICAL_MANUAL` である。automatic normal-JOB adoption、replay/event sourcing、safety gateではない。

authority modelは次のとおりである。

- `WORKER_OBSERVED`: Workerが永続化したidentity、instruction hash/reference、terminal state/exit code/failure class、runtime/artifact facts、Git snapshot、changed path、利用可能なstate-store timestamp。
- `ACTOR_REPORTED`: Codex summary、judgment、元のBLOCKED decision/detail、bounded exact final actor message。Codexの報告内容を証明するが、その正しさを証明しない。
- `RAW_LOCAL_ONLY`: `stdout.txt`、`stderr.txt`、その他execution log。adoption-time SHA-256は記録できるがraw fileはcopyしない。
- `CORROBORATIVE_ONLY`: read-only state storeとHuman/ChatGPT提供のSlack Result Manifest。一致はprovenanceを補強するがActor judgmentのauthorityを格上げしない。

確認済みのhistorical/current plain `codex exec` formatでは、stdoutがterminal actor-message専用channelで、
progress/tool traceはstderrにある。extraction method `codex-exec-plain-stdout-terminal-message-v1` は、non-empty strict UTF-8
plain textである完全なstdoutだけを受理し、CRLFとbare CRをLFへnormalizeする。invalid UTF-8、NUL、ANSI control sequence、
missing messageはFail Closedとする。method/version、raw stdout SHA-256、extracted-message SHA-256を記録し、messageを要約・rewriteしない。

ARGUSのadoption先は `validation/evidence/job-results/<job_id>/` である。package fileは
`job-evidence-manifest.json`、`normalized-result.json`、`actor-reported.json` だけとする。manifestはdeterministic canonical JSON hash、
authority label、local source provenance、raw-local-only hash、corroboration、Human approval、trust limitationを記録する。
installationはunsafe Windows pathとsymlink/reparse escapeを拒否し、atomicにstageする。同一bytesには `NOOP`、異なる既存packageには
上書きせずfailureを返す。exact destination外のdirty canonical changeは変更しない。Workerはcommitしない。

manual adoptionには `--historical-manual`、`--human-approved`、supplied Slack Result Manifestが必要である。
WorkerはSlackをcallしない。supplied JSONは `job_id`、`actor`、`mode`、`workspace`、`instruction_sha256`、`status`、
`exit_code`、`git.baseline_commit`、`git.head_after` を含まなければならない。request/result/state-store/Slack間で重なる
stable valueを機械的に比較し、不一致はFail Closedとする。

```text
python agent_worker.py --adopt-job-evidence <job_id> --workspace argus \
  --historical-manual --human-approved \
  --slack-result-manifest C:\\path\\to\\slack-result-manifest.json
```

JSON resultは `ADOPTED`、`NOOP`、`FAILED` のいずれかで、mode、job/workspace、destination、manifest SHA-256、
corroboration、trust limitation、errorを含む。adoptionはhistorical state-store rowを変更しない。hashはadoption時に作成するため、
original terminal-time hash continuityは得られない。将来のall-JOB terminal Slack hash anchorは明示的に `DEFERRED` である。

## 12. Failure Domains / Recovery

### 12.1 Timeout

```python
ACTOR_TIMEOUT = {
    "codex": 7200,
    "claude": 7200,
}
```

Timeoutはactor domainの `TIMEOUT` とする。Claude reviewではtimeoutまでにreaderが取得したpartial stdout / stderr / raw
`stream-json` とnormalized eventを失わずEvidenceへ永続化し、Workerはその後もreview boundary verificationとterminal
closureを実行する。partial evidenceが存在してもactorをDONEへ昇格せず、reviewはadoptableにならない。

## 13. Review System

### 13.1 Current full-repo independent review

現在のnormal reviewはReview Input Manifest v1でcanonical workspaceの対象file集合を独立cloneへ再現する
full-repo input behaviorである。Context Harness Phase 1はこのbehaviorを変更せず、delta packageへ限定しない。

### 13.2 Claude Review Isolation v0.1（IMPLEMENTED_BASELINE）

Claude reviewはcanonical workspace単位のqueue lockをactor timeoutまで保持する。このためreviewは同一workspaceの他jobを最大actor timeoutまで直列化し得る。actorのcwdはWorker所有のjob固有disposable directoryに作る独立local cloneであり、worktreeではない。作成は `git clone --no-hardlinks --no-checkout <canonical> <review_dir>`、続いてcanonical HEADのdetached checkoutを行う。

### 13.3 Review Isolation → Worker Evidence Adoption（IMPLEMENTED_BASELINE）

Claudeは引き続き独立cloneだけを読み、canonical workspaceへ一切writeしない。既存のcanonical/review boundary verificationが完了し、`review_boundary=CLEAN`、`actor_status=DONE`、`evidence_persisted=true`により`review_execution.adoptable=true`となった後だけ、同じworkspace queue lockを保持したWorkerがnormalized review evidenceをcanonicalへ採用する。設定はworkspace単位のoptional `review_evidence_root`であり、ARGUSでは `validation/evidence/external-review` とする。このrootはactor申告の通常の`artifact_roots`には含めない。

採用先は `<canonical>/<review_evidence_root>/<job_id>/` で、Worker固定allowlistの `review-input.json`、`review-execution.json`、canonical/reviewのbefore/after `diff HEAD --stat` 4ファイル、およびWorker生成のdeterministic `review-manifest.json`だけを置く。Claudeの`AGENT_RESULT`は採用対象を選択できない。Operating Rules v0.3 §19の制約により、raw Local Execution Logsである`claude-stream.jsonl`と`stderr.txt`は常に`LOCAL_ONLY`でありARGUS Gitへ入れてはならない。manifestとResultにはraw内容ではなくSHA-256と`storage=LOCAL_ONLY`だけを記録する。

Workerはconfigured rootとcanonicalへのstrict containment、absolute/drive/UNC/traversal/ADS/Windows reserved-name拒否、既存parent/destinationのsymlink/reparse拒否を行う。完全なpackageはconfigured root直下のWorker作成temporary directoryへstageし、同一filesystemのatomic renameでjob destinationへ設置する。同じjob_idにbyte-equivalent packageが既にあれば`NOOP`、異なるpackageまたは余分なentryがあれば`FAILED`として既存packageを上書き・mergeしない。採用前後のcanonical snapshotを比較し、configured evidence root外の変化があれば採用を`FAILED`にして新規packageをbest-effort rollbackする。既存のdirty source変更はbefore/afterが同一なら許容し、変更しない。

Terminal Resultの`review_evidence`は`status: ADOPTED | NOOP | FAILED | NOT_RUN | NOT_CONFIGURED`、`mode: LIVE`、canonical-relative destination、manifest SHA-256、normalized file hash、raw local-only hash/storage、optional errorを持つ。review qualificationとevidence transportは別failure domainであり、adoption failureは`status=DONE`、`review_boundary=CLEAN`、`review_execution.adoptable=true`を変更しない。Slack Resultは採用attempt後に公開されるためfinal adoption status/hashを含む。Workerはevidenceをcommitせず、commit authorityはHumanに残る。

provenance chainは `Worker-owned local job log → normalized allowlist → Worker manifest → Result.manifest_sha256 → downstream Codex` である。downstream Codexはcanonicalの`review-manifest.json`をSHA-256で再計算し、Resultの`manifest_sha256`と一致した場合だけ採用evidenceをtrustする。ここでdeferredなのは、過去のisolated **review evidence package**をこの経路へ採用する `HISTORICAL_MANUAL` modeであり、このbaselineでは実装しない。一方、18節の過去JOBに対する **job evidence package** の `HISTORICAL_MANUAL` adoptionは別機能として実装済みである。両者を同一のadoption pathとして扱ってはならない。

Review Input Manifest v1は `git ls-files --cached --others --exclude-standard` が列挙し、現在のfilesystemに通常fileとして存在するpathを対象とする。pathはworkspace-relative slash-normalized、byte順で決定的に整列し、各fileのraw bytesのSHA-256とsize、決定的JSON serialization全体の `input_manifest_sha256` を記録する。staged/unstaged区別はreview cloneへ再現しない。staged deletionを含むcanonicalで現存しないfileは入力集合に含めず、clone側から削除する。Git LFS、submoduleの完全なsnapshot semanticsはv0.1の保証外である。

Workerはclone内を入力集合と完全一致させ、Claude開始前にmanifestを再計算する。不一致はpreparation domainの `REVIEW_SNAPSHOT_MISMATCH` としactorを起動しない。作成・snapshot取得失敗はそれぞれ `REVIEW_WORKSPACE_CREATE_FAILED` / `REVIEW_SNAPSHOT_FAILED` とする。

Claude Code 2.1.280は `-p --restricted --tools Read,Glob,Grep,Bash --settings <Worker-managed settings> --safe-mode --strict-mcp-config --permission-mode dontAsk --permission-prompts none --no-session-persistence --output-format stream-json --verbose` で起動する。`--tools` はBashを公開するだけで実行承認を意味しないため、Worker-managed settingsはhooksを空にしたうえで `permissions.allow: ["Bash"]` を明示する。これにより `restricted + dontAsk` / promptなしでもBashを利用でき、実E2EではBash経由のPython、git、focused unittest実行を確認した。repo `.venv` にはpytest自体が存在しなかったため、pytest実行を確認済みとはしない。command allowlistをsecurity boundaryとはしない。safe modeによりCLAUDE.md、skills、plugins、project hooks等を無効化する。restricted modeはproject/local settingsを無視し、strict MCPはproject `.mcp.json` を無視する。Bashはreview capabilityであり、native Windows上のfilesystem security boundaryではない。Edit、Write、PowerShellは公開しない。repo外absolute writeを完全には防止できず、別Windows user+ACLは将来の強化案である。

Actor環境はactor別explicit allowlistから構築する。Slack、Notion、Worker用Google/Drive credentialはClaudeにもCodexにも継承しない。Claudeには認証providerに必要な限定envに加え `PYTHONDONTWRITEBYTECODE=1`、`PYTEST_ADDOPTS=-p no:cacheprovider`、`GIT_OPTIONAL_LOCKS=0` を設定する。Codexの `workspace-write` / Windows MXC decisionは変更しない。

stream-json raw transcriptをprimary execution evidenceとして保存し、tool_use/command、tool_result、result/exit、順序、final result textをnormalized evidenceへ派生する。final result textだけを既存の `<AGENT_RESULT>` extraction/artifact pipelineへ渡す。secret値はResultへ転記しない。timeout時もreaderが既に収集したstdout/stderr/raw stream-jsonを例外に保持し、raw transcript、stderr、取得できたnormalized eventを永続化する。final result eventがなくても `partial_evidence_available=true` により証跡の存在をResultへ公開するが、actor statusはTIMEOUTのままであり採用可能にはならない。

Actor終了（timeout/errorを含む）後、Workerは所有するprocess treeをsettleし、全original input fileの最終hashを検証する。`Popen`成功をprocess ownership境界とし、Job attach、`mark_running`、reader起動、stdin write/closeを含む以後の全例外経路で、当該actor treeだけをterminateしてJob handleをcloseする。Windowsではjob固有のJob ObjectへClaudeを割り当て、normal completionとtimeoutの双方で残存childを終了する。reader joinより先にowned descendantsをsettleし、継承されたpipe handleによる不要な待ちを避ける。PID/image-name横断killは行わず、無関係processを対象にしない。標準 `subprocess.Popen` はprimary thread handleを公開しないため、CREATE_SUSPENDEDから安全にresumeする実装へは置換していない。このためprocess作成からJob Object割当までにchildをspawnし得る小さいraceをv0.1のaccepted limitationとする。

一時変更が復元されていれば許容し、未復元ならboundary `INPUT_MODIFIED` とする。新規fileは自動違反ではなく、`.git`を除くrecursive traversalでignored fileも含め `review_workspace.outputs` に列挙する。canonicalはlock下でHEAD、index/staged state、tracked current contents、non-ignored untracked contentsをbefore/after比較する。変化時は原因を帰属せず `CANONICAL_STATE_CHANGED` とする。canonical ignored filesはv0.1では監視しない。disposable review cloneのGit HEAD/index/stateの移動自体は証跡であってboundary判定条件ではない。`head_before` / `head_after` は常に記録するが、boundaryはcanonical stateとoriginal input fileの最終content hashだけで決める。したがってcanonicalが不変でoriginal input contentsが最終的に復元されていれば、cloneのGit stateが移動していてもreviewはCLEANになり得る。これは意図したsemanticsであり実装欠陥ではない。

状態domainはpreparation、actor（DONE/AGENT_ERROR/TIMEOUT）、review boundary（CLEAN/INPUT_MODIFIED/CANONICAL_STATE_CHANGED）、artifact、cleanup（DONE/FAILED/PENDING）に分離する。採用可能条件はactor DONEかつboundary CLEANかつrequired execution evidence永続化済みである。reviewer proseが `CHANGES REQUESTED` でもこの条件を満たせばadoptableであり、adoptableはreview賛否ではなくexecution/provenanceの信頼性を表す。actor DONEでboundaryがnon-CLEANの場合もactor statusをFAILEDへ変換せず、`adoptable=false` とし、v0.1ではnon-authoritativeなreview cloneからartifact uploadを行わず理由をResultへ記録する。

accepted/start後のlog directory作成を含むpreparation、actor、evidence parse、boundary verification、Result assembly/persistence例外は、単一のterminal closureへ収束する。terminal DB state、Slack Result、Browser Callback attempt、final local Resultを相互に分離し、あるsinkの失敗が後続sinkを抑止しない。Slack terminal Resultは1回だけ公開する。cleanupは正しさ・採用条件に含めず、Slack Result、Browser Callback後にbounded cleanupし、その最終状態をlocal operational stateへ記録する。公開済みResultの `cleanup_status=PENDING` と、後続のlocal operational Resultに記録されるDONE/FAILEDの差は意図的である。cleanup完了をResult/callbackのcritical pathへ入れず、review correctnessと分離するためである。Windows cleanupはdisposable clone配下のread-only属性を必要時だけ解除し、canonical ACLには触れない。cleanup failure/PENDINGはDONE+CLEAN+evidenceを無効化しない。

```text
Human → ChatGPT → Codex implementation → DONE / Evidence
      → ChatGPT / Human control boundary
      → Claude independent review → DONE / Findings
      → ChatGPT → Human Decision
```

Codex implementation / Claude independent review境界を維持する。自動Actor chainを行わず、
後続JOB発行前にChatGPT / Human control boundaryを置く。

### 13.4 Review Delta Package and review modes（PHASE 3A / IMPLEMENTED_COMPARISON_ONLY）

Review Delta Package Builderはterminal Job Context生成後、同じworkspace lease内で決定的packageを
`<generated_root>/<capability>/<job_id>/review-package/` に生成する。現在のfull-repo clone、actor prompt、review qualification、
job statusは変更せず、package coverageを比較するだけである。mode schemaは次の意味とする。

| Planned mode | 定義 |
|---|---|
| `DELTA_REVIEW` | 通常caseでdelta packageをprimary inputとし、必要ならbounded referenceを追加要求する。 |
| `BOUNDARY_REVIEW` | security、authority、interface、dependency等の境界へ影響が及ぶため、deltaから関係boundaryまで範囲を拡張する。 |
| `FULL_REVIEW` | critical、closure、authority-changing、またはboundary-sensitiveなcaseで、deltaに限定せず必要な全範囲を確認する。 |

packageは `package-manifest.json`、`job-context.json`、`delta.json`、`diff.patch`、`findings.json`、
`authority-refs.json`、`validation-results.json`、`evidence-refs.json`、`expansion-plan.json`、`comparison.json` からなる。
canonical JSONはsorted keys/compact separators/LFであり、manifest hash materialに時刻を含めない。manifestは各package fileと
included authority/evidence refのworkspace-relative path、raw-byte SHA-256、size、source Context/Delta/Evidence Index/Job Context hash、
job/instruction identity、Git base/head、Worker changed paths、provenance、previous context linkage、count/bytesを保持する。

diff attributionはWorker before/after snapshotをauthorityとする。HEADが同一で、対象pathがjob開始時にcleanであり、path stateが
job中に変わった場合だけ `git diff --binary HEAD -- <bounded paths>` をexact deltaとする。pre-existing dirty target、HEAD transition、
snapshot欠落ではpatchを空にして `ATTRIBUTION_UNCERTAIN` / `NEEDS_RECONCILIATION` とし、unrelated dirty pathをtargetへ割り当てない。
STRUCTURED findingsだけを選択し、PARTIAL/UNSTRUCTURED proseからfindingを作らない。429/session limitはexecution failureであり
verdictではない。validationではWorker-observed execution factとactor-reported verdict/findingを別authority labelで保持する。

`DELTA_REVIEW` はattributed hunks、selected authority、structured prior findings/evidence、direct declared dependency surfaceを含む。

Evidence の選択と expansion は、prose の内容や job 名ではなく index に符号化された構造 edge だけで決定する。relevance は
`REQUIRED_RELEVANT`（`review_of` / target、selected finding/evidence、declared evidence source、明示 dependency 等）、
`BOUNDED_CANDIDATE`（relevance は不明だが current review への required edge がない）、`IRRELEVANT`（capability mismatch 等の
決定的 exclusion）の三値とする。expansion requirement は `REQUIRED_BEFORE_REVIEW`、`OPTIONAL_BOUNDED`、`NONE` の三値とし、
`NEEDS_RECONCILIATION` と pre-launch rejection に寄与するのは unresolved `REQUIRED_BEFORE_REVIEW` だけである。

`PARTIAL` / `UNSTRUCTURED` であることは内容を構造的に判定できないという quality fact であって、current review に必須という
relationship fact ではない。したがって unknown relevance は mandatory full read と同義ではない。required authority/dependency edge 上の
unknown/missing evidence は従来どおり fail closed とする一方、edge のない unknown evidence は `OPTIONAL_BOUNDED` として将来の bounded
expansion に利用可能にし、package-first launch を阻害しない。capability match 単独も、宣言が evidence class を required としていない限り
required 化しない。Job Context 由来 requirement は Review Package で provenance を保持して継承し、同じ ref を package-native requirement
として二重生成しない。`expansion_required_count` は後方互換 field として `REQUIRED_BEFORE_REVIEW` 件数だけを表す。
`BOUNDARY_REVIEW` はJob Contextのdeclared dependency boundaryを追加する。`FULL_REVIEW` はbroad repo visibilityが必要というmarkerと
bounded expansionを記録し、packageがrepo全体を代替すると主張しない。expansion planはmissing ref、reason、authority/quality、
required-before-review、deterministic bounded scopeを保持し、automatic unrestricted fallbackを行わない。

statusは `READY_PACKAGE` / `NEEDS_RECONCILIATION` / `UNVERIFIABLE` である。safe resolverはworkspace/capability/schema/hash、
source Context/Job Context freshness、path containment、traversal、symlink/reparse、全included file/ref bytesを再検証する。
Result Manifestへadditive `review_package` comparison diagnosticを出すがstatus単独でcurrent executionをblockしない。

明示的なpackage reuseでは、**Package Source Context** と **Review Execution Context** を分離する。Package Source
Contextはpackage生成時のContext Manifest SHA、Evidence Index SHA、Job Context SHA、implementation/target job provenance、
diff attribution/snapshot、authority refs/findingsからなる不変のsource chainである。Review Execution Contextは後続reviewの
job_id、review instruction SHA、callback/runtime、review mode/package refであり、package source identityを書き換えない。
したがってreview jobのjob_id、instruction、callback、runtimeだけが異なってもstaleではない。package内の
`job-context.json` はmanifestのsource hashと自己hashを再検証し、そのContext/Evidence linkもsource chainと一致しなければ
ならない。後続jobが共有diagnostic `job-context.json` を生成しても、そのhashをpackage source Job Context hashと比較しない。

current-state freshnessは別のgateで判定する。現在のcapability Context Manifestのsemantic hash（context declaration、
authority source hashes、dependency edgesを含む）、現在のEvidence Index SHA、packageに記録されたtarget HEADとEXACT attribution、
対象pathの現在のbinary diff、およびmandatory Delta Scanをsource baselineと比較する。authority/context/dependency/evidence、
または対象working-tree deltaが変化した場合はfail closedとし、package再生成を要求する。Contextがsemanticに同一で
Deltaが`NO_IMPACT`、Evidence Indexが同一なら、review execution identityが異なってもpackageはfreshである。

**Phase 3B-1 / IMPLEMENTED_EXPERIMENTAL_MEASUREMENT_ONLY:** protocol v3 の明示的な
`review_mode=DELTA_REVIEW` と `review_package_ref` を、workspace/capability
allowlist と組み合わせた場合に限り、Claude package-first invocation を行う。package manifest、Context
Manifest、Delta Report、Evidence Index、Job Context、target implementation job provenance、全hash、path containment、
symlink/reparse point をactor起動前に再検証する。`READY_PACKAGE` かつ current context が検証可能な場合のみ起動し、
`NEEDS_RECONCILIATION`、`UNVERIFIABLE`、stale/tamper、identity mismatch はfail closedとする。
明示refのpackageがreview inputであり、actor launch前にreview job用candidate packageを再生成・置換しない。source packageの
attributionが`EXACT`でhash-boundされ、現在のHEAD/target diffが一致する場合、review execution job自身のWorker snapshotは
不要である。これはattribution safetyの一般的緩和ではなく、検証済みsource attributionの再利用に限定する。

accepted envelope の `review_package_ref` は `path`、64文字 lowercase hex の `sha256`、
`target_job_id` の3 fieldだけからなる。これは `actor=claude`、`mode=review`、
`review_mode=DELTA_REVIEW` の protocol v3 job でのみ許可する。この組合せ自体が measurement activation を表し、
内部 Job は `measurement_mode=true` に正規化する。移行互換のため明示的な `measurement_mode=true` も受理するが、
省略時と同じ意味であり、`false`、ref欠落、余分なref field、malformed path/hash/target identity はfail closedとする。

Claudeにはpackageをprimary inputとして先に読むこと、repo全体を既定で再探索しないこと、最小のbounded expansion
だけを要求すること、各path/refとreasonを記録すること、範囲外を `OUT_OF_PACKAGE_SCOPE` とすることを指示する。
confidenceを確立できなければ `PACKAGE_INSUFFICIENT` / `NEEDS_FULL_REVIEW` を返す。これは別の明示的
FULL_REVIEW jobを要求する結果であり、同一invocation内でのsilent broad fallbackを許可しない。Claudeは
`approved_semantics` を変更せず、reconciliationを提案できるだけである。通常のaccept authorityはChatGPT、
critical/ambiguous/authority-changing caseはHumanに残る。

Result Manifestのadditive `review_context` はmode、package identity/status/bytes/files、expansion、観測可能な
bytes、escalation、context/evidence/job-context hash、Claude streamに構造化されたtoken/cache usageだけを記録する。
明示reuse時はさらに `supplied_package_ref/hash`、`package_source_context_hash`、`current_context_hash`、
`semantic_freshness_status`、`execution_job_context_hash`、`source_chain_valid`、`package_reused=true`、
`package_regenerated=false`、stale reasonsを記録する。pre-actor failureでも要求された`DELTA_REVIEW`とrefを保持し、
`FULL_REVIEW`へ偽装しない。
Prompt ownershipは分離する。受理・永続化されたfrozen `Job`はcanonical instructionと
`prompt_sha256 == instruction_sha256`を保持し、変更しない。package-first preamble、bounded expansion、
decision contractを加えたactor入力はReview Invocationが`effective_prompt`として所有し、別の
`effective_prompt_sha256`を記録する。Claude runnerにはこのeffective promptを明示的に渡す。
Bash等でread pathを完全に観測できない場合は `measurement_complete=false` とし、metricを推測しない。
normalized Review Evidenceにも同metadataを保存する。independent clone、canonical boundary、cleanup、Evidence
Adoption、callback、JOB ENDの意味は変更しない。

ローカルconsole lifecycleはclaimed worker threadが所有する。stateを`RUNNING`にした直後に
flush済みの`JOB START`をstdoutへ一度だけ出し、setup、actor、callbackの成否にかかわらず
terminal state確定後のfinally pathで`JOB END`を一度だけ出す。同じblockはworker logにも残し、
actorのbulk stdout/stderr captureとは分離する。出力先は`configure_logging`時にstartup/Bolt diagnosticsを
表示したworker-owned console sinkへ固定し、event thread実行中の一時的な`sys.stdout`置換には追従しない。

`FULL_REVIEW` は現在の安全なreference/fallbackであり既定動作は不変である。`BOUNDARY_REVIEW` のpackage
consumptionは未実装であり、package refを受理しない。universal differential reviewはまだ有効化されていない。

**NOT IMPLEMENTED:** universal package-first default、`BOUNDARY_REVIEW` package consumption、Codex implementation slicing、
review clone visibility制限、automatic expansion/reconciliation。通常reviewはfull-repo input behaviorを維持する。

## 14. Notion Integration / Control & Registry Plane

Notionはv3 Instruction sourceであり、同時にControl / Registry / Human-facing Knowledge Planeである。
SlackはCommand / Event Busであり、Notion自体をCommand Busにはしない。

```text
Notion       = v3 Instruction source / Control / Registry / Knowledge
Slack        = Command / Event Bus
Python       = Local Execution Bridge / Runner（Notion READ only）
Drive        = conditional Artifact Transport / Store
Local Repo   = Source of Truth
```

Workerはv3受理時にInstruction pageをread-only APIで解決するが、Notionを更新してはならない。
Result、Finding、Human Decision等をNotionへ反映する場合は次の境界を維持する。

```text
Agent Result → Slack / Drive → ChatGPT → optional Human Decision → Notion update
```

Evidence本体はLocal Repo / Local Execution Evidence / 必要に応じたDriveに保持し、Notionへ大量ログを複製しない。

## 15. Browser Callback / Delivery Acknowledgement

Browser CallbackはJOBごとのoptional routing metadataであり、Result authorityではなくwake-up notificationである。
固定conversation URLをWorker設定に持たず、ChatGPTがJOB発行時に自分のconversation URLを渡す。
Workerは発行元conversationを推定または自動発見しない。新しいLocal Agent利用conversationでは、
Humanがそのconversation URLをChatGPTに一度渡し、ChatGPTはそのconversationから発行するJOBの
`callback.url` に使う。別conversationのcallback URLを流用しない。

```text
Accepted execution terminal path:
Actor execution → Git Evidence / Artifact processing → Slack Result Manifest → Browser Callback
                → Local result.jsonへcallback結果追記 → JOB END

Callback-eligible pre-dispatch rejection path:
terminal validation failure → Slack failure Result → Browser Callback LOCAL_AGENT_JOB_FAILED
                            → Local result.jsonへcallback結果追記 → JOB END
                              （Actorは起動しない）
```

terminal successでは `LOCAL_AGENT_JOB_COMPLETED`、trustworthyなcallback metadataを持つ
supported terminal failureでは `LOCAL_AGENT_JOB_FAILED` を送る。supported failureには、通常のJOB acceptance / execution後の
failureだけでなく、認可済みrequestがcallback destinationと安全なrouting metadataを確立できるところまでparseされた後の
terminal pre-dispatch validation rejectionを含む。この場合もshared rejection pathはfailure determination →
Slack failure Result → Browser Callbackの順で処理し、Actorを起動しない。失敗callbackには
job_id / actor / workspace / status / 取得できる場合のfailure_class / artifact_statusと、
Slack Result Manifestを確認する指示のみを含める。Prompt / Instruction本文、stdout / stderr dump、
secrets、Artifactは含めない。

Failure notification boundaryは次のとおりとする。

- callback eligible: authorizedで、job_id / actor / workspace等のsafe routing metadataまで十分にparseでき、
  request自身からtrustworthyなcallback destinationを確立済みであるterminal validation failure
- callback ineligible: unauthorized channel / sender、callback destinationを安全に確立できないmalformed / unparseable request、
  invalid / untrusted callback metadata

callback-ineligible failureはFail Closedかつno-callbackとする。Workerはcallback URLを推定せず、previous / global conversation URLや
別conversationのURLを再利用しない。したがって、すべてのmalformed requestがcallback可能であるとは限らない。

Slack Result Manifestを必ずBrowser Callbackより先に公開し、成功・失敗のどちらでも
この順序を守る。Slack Result Manifestがauthorityであり、callback失敗はActor execution / Artifact statusを変更しない。
callbackでwake-upしたChatGPTがHumanの確認なしに次のJOBを発行してはならない。

Browser UIはlive DOMを毎回検査し、次の観測済みstateを使う。

| State | Observed action control |
|---|---|
| IDLE / empty | 右側action `aria-label="音声会話を開始"` |
| SEND-ready / text present | `button type="submit"`, `aria-label="送信"` |
| GENERATING | `button type="button"`, `aria-label="停止"` |

`aria-label="停止"` がvisibleな間はChatGPTが生成中である。WorkerはSTOPをクリックせず、
callback textも書き込まず、約120秒を上限として約500 ms間隔で終了を待つ。
生成終了後にuniqueなvisible composerを再取得・再検証する。composerが非空ならHuman draftとして
Fail Closedにし、overwrite / clear / append / sendのいずれも行わない。観測したliveな空contenteditableは
`innerText="\n"`, `textContent=""`, placeholder paragraph HTMLであり、whitespace-onlyの `innerText` は空と扱う。

空composerの確認後にのみcallback textをfillし、uniqueなvisible SEND buttonを約5秒まで待つ。
SENDがenabledであることを必須としてからclickする。callback text挿入後、send前に失敗した場合のcleanupは
best-effortであり、現在のcomposer textがそのinvocationが挿入したcallback messageと完全一致する場合に限ってclearする。
Humanが変更した、またはそれ以外のcomposer textは決してclearしない。unknown / ambiguous DOM stateは
Fail Closedとし、Agent専用Browser profile、CDP endpoint、selectorのuniqueness / visibility safetyを維持する。

click正常終了は `CLICK_SUCCEEDED` であり、callback成功ではない。Workerはclick前に
現行DOMの `article[data-testid^="conversation-turn-"][data-turn="user"]` を優先し、旧DOMの
`[data-message-author-role="user"]` をfallbackとして、可視user-turnのtext snapshotを取得する。click後は約10秒を上限として
約500 ms間隔で同じ優先順を再検査し、使用selectorと前後件数をdiagnostic logへ残す。
事前node数より後に追加された単一の新規user-message node内に、送信callbackの
`LOCAL_AGENT_JOB_COMPLETED` または `LOCAL_AGENT_JOB_FAILED` markerと、exactなunique `job_id` の両方が存在する場合だけ
`DELIVERY_CONFIRMED` とし、`notify_chatgpt` の成功returnおよびWorkerの `Browser callback succeeded` を許可する。
DOM renderingによる改行・通常whitespaceの正規化は許容し、full rendered textの完全一致は要求しない。markerのみ、`job_id` のみ、
または別々の新規nodeに分かれた一致はconfirmed ACKにならない。これにより、同一callbackが過去に存在しても新規deliveryのACKにはならない。

selectorが利用できない場合を含め、click前にこのinvocationがcomposerを空からcallback本文へ遷移させ、click後にcomposerが空へ戻り、かつgeneration開始（STOP表示）を観測した場合は、submissionは成立したがrender/server identity ACKを直接確認できない `SUBMITTED_ACK_UNVERIFIED` とする。composer-emptyまたはSTOPの片方だけではこの状態にしない。これはcallback execution failureではなく、Local `result.json` の `callback.status` に同名で記録し、`BROWSER CALLBACK FAILED` を出力しない。

identity一致も上記submission transitionも得られない場合は `DELIVERY_UNKNOWN` としてcallback failureを記録する。この状態では既にsendが受理された可能性があるため、自動retry / resendを
行わず、composer cleanupも行わない。これによりduplicate callbackと、send受理後にHumanが入力したreplacementの破壊を防ぐ。
Slack Result ManifestのauthorityとCallbackより先に公開する順序は変えない。

`tests/manual/inspect_chatgpt_buttons.py` は現在のChatGPT composer / action button stateを調べる
手動DOM diagnostic helperであり、通常のWorker executionの一部ではない。

## 16. Failure exclusions / 未導入範囲

### 16.1 初期本番で導入しないもの

- Cloudflare / Web server / Inbound HTTP
- Redis / 外部Queue service / 分散JOB scheduler
- 自作Chat UI / 自動Actor選択AI
- 自動Actor chain / 自動修正無限ループ
- Claude implementation mode
- 複数PC Worker
- 専用Secret Vault / 複雑なDLP / SIEM

## 17. Implementation Status / Roadmap

### 17.1 IMPLEMENTED_BASELINE

本書時点で以下を実装・Acceptance済みとする。

- Slack Channel / Sender Authorization、JSON / Markdown code-fence parse
- Protocol v3 standardとv1 / v2 compatibility
- v3 `instruction_ref` only request、Notion read-only resolver、0 / 1 / >1 code block Fail Closed
- acceptance-time Instruction snapshot、UTF-8 byte identity、Worker-generated SHA
- Notion mutation / TOCTOUに影響されないaccepted identity
- SQLite Job State / idempotency / workspace FIFO
- `DISPATCHING` atomic claimとsame-workspace concurrent new-job serialization
- persistent QUEUED restart recoveryとincomplete legacy v3 state Fail Closed
- PID / host / heartbeat / RUNNING Crash Recovery
- CLI Version Gate / Git Workspace Validation
- Windows Codex sandbox backend MXC requirement / no silent fallback to elevated
- Windows sandbox regression（workspace root / `.git` / new file ACL、create / rewrite）
- Codex implementation / Claude Code isolated independent review（job固有disposable clone）
- Review Input Manifest v1、actor別environment allowlist、Worker-managed Bash permission、raw stream evidence
- owned process tree settlement、timeout partial evidence、review boundary / adoptability、cleanup domain分離
- JOB単位Local Execution Evidence / Git before-after Evidence
- Execution Status / Artifact Status分離
- Artifact Manifest / Windows Path Validation / conditional Drive upload
- centralized rotating Operational Log
- 認可後のformal plaintext PING / PONG（attribution付き許容、lookalike拒否）
- success / supported terminal failure Browser Callback（trustworthy metadataを持つpre-dispatch validation rejectionを含む）、
  Result Manifest before Callback、callback failure isolation
- pre-dispatch invalid actor / modeのshared rejection path、Slack `BRIDGE_ERROR / INVALID_JOB`、Actor非起動
- Browser Callbackのgeneration wait / STOP非操作 / composer draft protection / DOM Fail Closed
- Actor Result Artifact Contractと `EXTERNAL_DELIVERABLE` / `REPO_CANONICAL_REFERENCE` / `INVALID` classification
- Review IsolationからのWorker-owned normalized Review Evidence Adoption（`mode: LIVE`）
- Cross-JOB Historical Job Evidence Adoption（`HISTORICAL_MANUAL`）
- Phase 1 Capability Context Harness（`mode: SHADOW`）、deterministic Context Manifest、terminal Delta Scan、workspace lease
- Phase 2A Evidence IndexerとPhase 2B Job Context Slicer（`mode: COMPARISON_ONLY`）
- Phase 3A Review Delta Package Builder（`mode: COMPARISON_ONLY`、actor consumptionなし）

### 17.2 PLANNED / NOT_IMPLEMENTED

Context Harness Phase 3Bは未実装である。対象はpackage-first prompt/context供給、actor-consumed `review_package_ref`、
Claude lightweight invocation、`DELTA_REVIEW` / `BOUNDARY_REVIEW` / `FULL_REVIEW` actor routing、
LLM reconciliationのruntime接続である。automatic `approved_semantics` mutationと、
Actorのsupplemental repository read禁止も未実装である。Phase 1の `SHADOW` 観測はこれらを実装済みとみなす根拠にならない。

### 17.3 実装・E2E確認済み

v1 / v2の既存baselineに加え、v3について以下を確認済みとする。Claude review isolationの最終correction cycleは
`local-agent-review-isolation-final-fixes-20261002-001` と、その後のuser local `.venv` regressionで確認した。

- Notion page resolve
- Worker integration access boundary（Notion READのみ、update不可）
- code block 0 / 1 / >1 behavior
- resolved UTF-8 byte identity
- Worker-generated SHA
- Instruction mutation identity
- acceptance-time TOCTOU snapshot
- queue-time mutation snapshot
- duplicate job idempotency
- normal FIFO dispatch
- RUNNING crash recovery
- persistent QUEUE restart recovery
- `V3-PERSIST-QUEUE-002` がrestart後にrecoveryされ、`PERSISTENT-QUEUE-VERSION-C` を実行
- Result Manifest `prompt_sha256 == instruction_sha256`
- atomic `DISPATCHING` claim
- concurrent new-job same-workspace serialization
- formal plaintext PING / PONGのChatGPT → Slack → Worker → Slack E2E
- Slack attribution付きPINGの受理
- valid callback metadataを持つterminal failure callback
- `INSTRUCTION_NOT_FOUND` → Slack failure Result Manifest → `LOCAL_AGENT_JOB_FAILED` によるChatGPT wake-up
- failure pathのResult-before-Callback ordering
- pre-dispatch invalid actor / mode failure callback automated regression
- `V3-PREDISPATCH-INVALID-ACTOR-MODE-E2E-001`: authorized / parsed `codex + review`、workspace `sandbox`、
  valid callbackをActor起動前に拒否
- 同E2EでSlack Resultを先に送信: `status=BRIDGE_ERROR`、`failure_class=INVALID_JOB`、
  `error_summary="Actor/mode not allowed: ('codex', 'review')"`
- 同E2EでActor非起動を確認後、`LOCAL_AGENT_JOB_FAILED` がjob_id / actor / workspace / `BRIDGE_ERROR` /
  `INVALID_JOB` / `artifact_status=NOT_RUN` を通知し、ChatGPT conversationのwake-up成功を確認
- busy ChatGPT callback: GENERATING / `aria-label="停止"` → Worker wait → generation end →
  empty composer認識 → callback fill → `aria-label="送信"` → send → `LOCAL_AGENT_JOB_FAILED` がChatGPTに到達
- generation wait中にSTOPをclickしないこと
- 既存composer draftの保護
- Claude review isolation real E2E: managed Bash permission under `restricted + dontAsk`、Bash / Python / git / focused unittestを実行し、focused review-isolation unittest 16 / 16 PASS。repo `.venv` にpytest自体は未導入
- `mark_running` failureおよびstdin write / close failure後に、owned process treeだけをsettleしてhandleをclose
- timeout時のpartial stdout / stderr / raw stream evidence保持、Workerによるpartial transcript永続化、actor `TIMEOUT`維持、boundary verification継続
- disposable cloneのHEAD / index / state移動はevidenceのみとし、canonical state不変かつoriginal input content hash復元時はCLEANとするsemantics
- boundary verification exception、evidence parse exception、log directory creation failure、transient terminal DB-state failureがterminal closureへ収束し、Slack terminal Resultを重複publishしない
- `INPUT_MODIFIED` / `CANONICAL_STATE_CHANGED`でもactor `DONE`を保持し、`adoptable=false`としてauthoritative review artifactをskip
- Windows read-only fileを含むdisposable clone cleanup
- managed settings欠落時のlauncher fail-fastと、Worker settingsによるBash明示承認
- repository `.venv` full unittest regression: `Ran 67 tests in 4.173s`, `OK`, skip 0

#### 17.3.1 Windows sandbox controlled A/B and Local Agent E2E

fresh scratch repo `C:\dev\codex-sandbox-test`をcontrolled validation専用に使用した。初期ACLは通常の継承Windows ACLのみであり、Codex sandbox SID ALLOW ACE、`ares\CodexSandboxUsers` ACE、`.git` sandbox DENY ACEはいずれも存在しなかった。

MXCでのinteractive Codex testでは`mxc-test.txt`の作成に成功した。実行後もworkspace root、`.git`、`baseline.txt`のACLは不変で、新規fileは通常の継承ACLのみを持った。

Local-Agent-equivalent direct CLI testでは、workdirを`C:\dev\codex-sandbox-test`として、次のcontractを実行した。

```text
"Create local-agent-mxc-test.txt ..." | codex exec --sandbox workspace-write -
approval: never
sandbox: workspace-write
```

file createに成功し、同一fileを再open / rewriteして内容を検証した。workspace root、`.git`、`baseline.txt`のACLは不変で、新規fileにもsandbox ACE pollutionはなかった。

full Local Agent E2E `LOCAL-AGENT-MXC-E2E-20261001-001`は次の実経路を検証した。

```text
actor=codex
mode=implementation
workspace=codex-sandbox-test

ChatGPT -> Slack -> Local Agent Worker -> Workspace Registry
-> C:\dev\codex-sandbox-test
-> codex exec --sandbox workspace-write -> MXC
```

結果は`status=DONE`、`exit_code=0`、`artifact_status=DONE`であった。Codexは`local-agent-e2e-mxc.txt`を作成して内容を検証し、同一fileを再open / rewriteできた。callbackも正常に返却された。

callback後のhost-side ACL inspectionでは、workspace root、`.git`、`baseline.txt`はいずれも不変で、`local-agent-e2e-mxc.txt`は通常の継承ACLのみを持った。sandbox SID ALLOW ACE、`CodexSandboxUsers` ACE、`.git` sandbox DENY ACEは再現しなかった。したがって、このcontrolled testではMXCを使用するLocal Agentの実execution pathが`elevated`で観測したACL pollutionを再現しないことを確認した。

`codex-sandbox-test`はcontrolled validationの一時workspaceであり、恒久的なproduction workspace要件またはWorkspace Registry entryではない。

既存E2Eとして、ChatGPT → Slack → Worker → Codex / Claude、Local Workspace書込み、
条件付きDrive upload、Slack Result、Browser callback成功・失敗終端系、busy state synchronization、
およびBrowser停止時failure isolationを維持する。

## 23. Structured Review Decision / Finding（IMPLEMENTED）

Claude reviewはhuman-readable summaryに加え、末尾の単一`<REVIEW_DECISION>` JSON blockで
`normalized-review-decision` schema version 1を返す。recordはreview JOB identity、actor、workspace、
optional capability、review mode、review target/baseline/head/instruction/package hash、actor verdict、package
sufficiency、findings、expansion result、actor usage、provenanceを保持する。Findingはactorが明示した場合だけ
stable ID、severity/category、status、summary/title、workspace-relative affected/authority/evidence refs、
predecessor、disposition/recommendationを保持する。欠落値をproseから推論しない。

judgment、verdict、finding、dispositionのtrust classは常に`ACTOR_REPORTED`である。Workerが観測するのは
process exit、boundary、bytes、hash、source JOB identity、schema validity、persistenceだけであり、structurally
validであることはsubstantive correctnessを意味しない。malformed、duplicate ID、invalid enum、identity mismatch、
unsafe refはdecisionをreject/quarantineするが、review execution自体が完了した場合はJOBを失敗させない。
missing/rejected decisionはEvidence Indexで`PARTIAL`/`UNSTRUCTURED`のままとしfindingを作らない。429等の
failed executionはvalid completed decisionがなければverdictにならない。

LIVE Review Evidence Adoptionはvalidな`review-decision.json`だけをhash付きnormalized fileとして採用し、
raw transcriptはlocal-onlyのままとする。Evidence Indexはexecution status（`WORKER_OBSERVED`）とdecision
（`ACTOR_REPORTED`）を分離して投影する。Job ContextとReview Packageは`STRUCTURED` findingだけをexact IDで
搬送し、自動resolveやprose extractionを行わない。

historical reviewは一般的prose scraperを持たない。Human/ChatGPTがsource JOBとmapping JSONを明示し、
`--historical-manual --human-approved`を与えた一回限りのadoptionだけを許可する。Workerはexact local
`review-execution.json` hash、successful completion、mapped IDのliteral presence、schema/pathを検証し、
source transcriptをcanonical repoへcopyしない。結果は`HISTORICAL_MANUAL`かつ`ACTOR_REPORTED`であり、
`WORKER_OBSERVED`へ昇格しない。このhistorical manifestはhash検証対象の`review-decision.json`だけを
`normalized_files`に持ち、source `review-execution.json`はhash/provenance付き`LOCAL_ONLY_NOT_COPIED`参照に
留める。この明示的subtypeだけはcanonical execution fileを要求せず、Evidence Index上のexecution statusと
boundaryをunknown (`null`) とする。LIVE manifestは引き続きhash検証可能な`review-execution.json`を必須とする。

package-first移行はmeasurement限定である。`DELTA_REVIEW`はworkspace configの明示allowlistにあるcapability
だけが使用でき、ARGUSでは`bootstrap-contract`と`RUNTIME-BOOTSTRAP-ORCHESTRATOR`だけを許可する。
universal enableは行わず、通常/default/reference pathは引き続き`FULL_REVIEW`である。

### 17.4 Status summary

> ChatGPTはControl Plane、Notionはv3 Instruction source / Control / Registry、SlackはCommand /
> Event Bus、Python Workerは状態管理されたLocal Execution Bridge / Runner、Codex / Claude Codeは
> 境界を分けたExecution Actor、Google Driveは条件付きArtifact Transport / Store、Local Repo / Gitは
> Source of Truth、Browser Callbackはwake-up notificationとする。

> v3の実行identityはpage_idではなく、受理時に解決・snapshotしたInstruction bytesとWorker-generated SHAである。

> 同一WorkspaceはSQLiteのatomic `DISPATCHING` claimで直列化し、新規JOBと復旧JOBを同じdispatch pathへ通す。

> Result ManifestをBrowser Callbackより先に確定し、no inbound HTTP、no automatic actor chain without human boundaryを維持する。

> セキュリティは個人ローカル運用に見合う単純な境界を維持し、停止・Credentialローテーション・復旧容易性を優先する。
