# ChatGPT Local Agent 本番設計 v0.6

```text
Document Role: PRODUCTION_DESIGN
Version: 0.6
Status: IMPLEMENTED_BASELINE
Supersedes: ChatGPT Local Agent 本番設計 v0.5
```

## 1. 目的

通常のChatGPTをControl Planeとして利用し、ローカルPC上のCodex / Claude
Codeを非同期実行する。

本システムは「Remoteから任意のローカルCLIを実行する仕組み」ではない。

> 事前登録されたWorkspaceに対して、許可されたSender / Channel / Actor /
> ModeのJOBだけを、状態管理されたLocal Runnerが実行する。

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

## 2. 責務

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

## 3. Slack Authorization

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

## 4. JOB Protocol

### 4.1 Standard: Protocol v3

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

### 4.2 Compatibility: Protocol v1 / v2

v1 / v2は既存producerと保存済みJOBのためのcompatibility protocolとして維持する。
新規JOBはv3を使用する。

- v1はlegacy plain Promptとrequest SHAの既存検証規則を維持する。
- v2はBase64 Promptをstrict decodeし、decoded Prompt bytesをauthorityとする。
- v2はrequest `prompt_sha256` を受け付けず、Workerがdecoded bytesから生成する。
- v1 / v2のinvalid inputは従来どおりFail Closedとする。

## 5. Notion Instruction Resolver / Acceptance Snapshot

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

## 6. Workspace Registry

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

## 7. Actor / Mode Policy

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
作らない。Actorは起動しない。Claude reviewはread-only構成とする。

```text
claude -p <prompt>
--permission-mode dontAsk
--permission-prompts none
--allowedTools Read,Glob,Grep
```

CodexはWorkspace Registryに応じて実行する。

```text
sandbox:       codex exec --skip-git-repo-check --sandbox workspace-write -
git workspace: codex exec --sandbox workspace-write -
```

Codex implementationとClaude independent reviewの境界を維持し、CodexからClaude Codeを直接起動させない。

### 7.1 Windows Codex sandbox backend decision

Windows上の本Local Agent環境では、Codex設定のsandbox backendを次のとおり固定する。

```toml
[windows]
sandbox = "mxc"
```

Local Agentの実行contractは変更しない。Codexは引き続き `codex exec --sandbox workspace-write -` で起動し、subprocessの`cwd`はWorkspace Registryに登録されたworkspace pathとする。`--sandbox workspace-write`はCodex sandboxの境界を指定し、Windows上でその境界を実装するbackendはCodex configのMXCである。approval behaviorも変更しない。sandboxを削除する判断ではなく、`workspace-write`をMXCで実装する判断である。

本環境では`elevated` backendを承認しない。また、MXCを利用できない場合に`elevated`へsilent fallbackしてはならない。ARGUS（`C:\dev\argus`）で`elevated`を使用した際、workspace rootへの持続的なsandbox ALLOW ACE（`ares\CodexSandboxUsers`および未解決sandbox SID）、`.git`へのsandbox SID明示DENY ACE、tree内の不整合なACL、書込み可能fileだけに付加されたsandbox SID ACE、およびcanonical docsへの実際のwrite failureを観測した。既存の影響pathはARGUS作業で別途修復済みである。

これらのローカル観測と後述のcontrolled A/Bは、同世代Windows Codex CLIの`elevated` / `workspace-write`周辺で知られているACL mutation defectと整合し、backendが原因であることを強く支持する。ただし、観測範囲を超えてCLI全体または全Windows環境へ一般化しない。

### 7.2 Workspace validation and filesystem integrity scope

本incidentのprimary remediationはWindows sandbox backendをMXCに固定することである。広範なFilesystem Preflight frameworkはprimary remediationとして要求せず、Planner / Operation-Preflight architectureをv0.6へ導入しない。軽量なworkspace integrity checkは、必要性を別途評価したうえで将来追加してよい。

## 8. CLI Version Policy

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

## 9. Slack Message Parse

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

## 10. Job State Store / State Machine

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

## 11. Heartbeat / Crash Recovery

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

## 12. Workspace FIFO / Persistent Queue Recovery

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

## 13. Result Manifest

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

## 14. Artifact Manifest

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

## 15. Artifact Path Validation / Upload Policy

Artifact pathは信頼しない。absolute path、drive-qualified path、UNC、`\\?\`、root escape、
Alternate Data Streams、Windows reserved device names、trailing dot / space、case-insensitive境界逸脱を拒否する。

検証はRaw syntax → Workspaceと結合 → normalize / resolve → root配下再確認 →
junction/symlink escape確認 → Workspace別Artifact Root allowlist確認、の順で行う。

Password、API key、OAuth token、Worker `.env`、`credentials.json`、`token.json`、Git credential、
外部同期禁止のCanonical State / log / secret領域、`browser-profile/` はuploadしない。

## 16. Google Drive

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

## 17. Credential

個人ローカル運用を前提とし、専用Vault等は初期版では導入しない。

- `.env` / `credentials.json` / `token.json` はGitへ入れない。
- CredentialをJOB / Result Manifest / Artifactへ意図的に含めない。
- Worker資格情報領域とAgent Workspaceを分離する。
- 流出時はWorker停止 → Credential失効 → 再発行 → 必要な外部ログ/Artifact削除 → 原因修正、で復旧する。

`browser-profile/` はCredential相当のLocal Runtime Stateとして扱い、Git / Drive / Agent Workspaceへ含めない。

## 18. Logging Policy

### 18.1 Operational Log

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

### 18.2 Local Execution Evidence

Operational LogとJOB単位Local Execution Evidenceは別物である。
`logs/<job_id>/` に以下のcontractを維持する。

- `request.json`
- `stdout.txt`
- `stderr.txt`
- Git before/afterのHEAD / status / diff / cached diff
- `result.json`

EvidenceはWorkspace repo外・Git管理外・通常のDrive Artifact対象外とする。
Worker取得Git Evidenceを変更事実のauthorityとする。Slackへstdout/stderrのraw全文を流さない。

Windowsでは、WorkerによるGit before / after evidenceはCodex actor sandboxの外側から収集する。一方、`elevated` backendで`.git`へsandbox SIDの明示DENY ACEが付与されたことは、許容できないworkspace side effectである。したがってGit content evidenceとは別に、`.git` ACLのbefore / afterをWindows sandbox regressionの必須確認対象とする。

## 19. Timeout

```python
ACTOR_TIMEOUT = {
    "codex": 7200,
    "claude": 7200,
}
```

Timeoutは `FAILED / TIMEOUT` とする。

## 20. Codex → Claude Independent Review

```text
Human → ChatGPT → Codex implementation → DONE / Evidence
      → ChatGPT / Human control boundary
      → Claude independent review → DONE / Findings
      → ChatGPT → Human Decision
```

Codex implementation / Claude independent review境界を維持する。自動Actor chainを行わず、
後続JOB発行前にChatGPT / Human control boundaryを置く。

## 21. Notion Integration / Control & Registry Plane

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

## 22. Browser Callback / ChatGPT Wake-up

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

`tests/manual/inspect_chatgpt_buttons.py` は現在のChatGPT composer / action button stateを調べる
手動DOM diagnostic helperであり、通常のWorker executionの一部ではない。

## 23. 初期本番で導入しないもの

- Cloudflare / Web server / Inbound HTTP
- Redis / 外部Queue service / 分散JOB scheduler
- 自作Chat UI / 自動Actor選択AI
- 自動Actor chain / 自動修正無限ループ
- Claude implementation mode
- 複数PC Worker
- 専用Secret Vault / 複雑なDLP / SIEM

## 24. 実装・Acceptance状態

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
- Codex implementation / Claude Code read-only independent review
- JOB単位Local Execution Evidence / Git before-after Evidence
- Execution Status / Artifact Status分離
- Artifact Manifest / Windows Path Validation / conditional Drive upload
- centralized rotating Operational Log
- 認可後のformal plaintext PING / PONG（attribution付き許容、lookalike拒否）
- success / supported terminal failure Browser Callback（trustworthy metadataを持つpre-dispatch validation rejectionを含む）、
  Result Manifest before Callback、callback failure isolation
- pre-dispatch invalid actor / modeのshared rejection path、Slack `BRIDGE_ERROR / INVALID_JOB`、Actor非起動
- Browser Callbackのgeneration wait / STOP非操作 / composer draft protection / DOM Fail Closed

## 25. 実装・E2E確認済み

v1 / v2の既存baselineに加え、v3について以下を確認済みとする。

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
- full local regression suite: 45 tests PASS

### 25.1 Windows sandbox controlled A/B and Local Agent E2E

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

## 26. 設計原則

> ChatGPTはControl Plane、Notionはv3 Instruction source / Control / Registry、SlackはCommand /
> Event Bus、Python Workerは状態管理されたLocal Execution Bridge / Runner、Codex / Claude Codeは
> 境界を分けたExecution Actor、Google Driveは条件付きArtifact Transport / Store、Local Repo / Gitは
> Source of Truth、Browser Callbackはwake-up notificationとする。

> v3の実行identityはpage_idではなく、受理時に解決・snapshotしたInstruction bytesとWorker-generated SHAである。

> 同一WorkspaceはSQLiteのatomic `DISPATCHING` claimで直列化し、新規JOBと復旧JOBを同じdispatch pathへ通す。

> Result ManifestをBrowser Callbackより先に確定し、no inbound HTTP、no automatic actor chain without human boundaryを維持する。

> セキュリティは個人ローカル運用に見合う単純な境界を維持し、停止・Credentialローテーション・復旧容易性を優先する。
