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
JOB parse / validate / accept
```

認可不一致はSlackへ `BRIDGE_ERROR` を返さず、ローカルOperational Logだけに記録する。
初期本番ではHumanがChatGPTへ実行依頼を出した時点をHuman Authorizationとみなす。
自動JOB連鎖や自動修正ループを導入する場合は、別途Human approval境界を設計する。

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

未知の組合せは拒否する。Claude reviewはread-only構成とする。

```text
claude -p <prompt>
--permission-mode dontAsk
--permission-prompts none
--allowedTools Read,Glob,Grep
```

CodexはWorkspace Registryに応じて実行する。

```text
sandbox:       codex exec --skip-git-repo-check --sandbox workspace-write <prompt>
git workspace: codex exec --sandbox workspace-write <prompt>
```

Codex implementationとClaude independent reviewの境界を維持し、CodexからClaude Codeを直接起動させない。

## 8. CLI Version Policy

確認済みCLI versionを設定に保持し、Worker起動時に完全一致で照合する。

```python
EXPECTED_CLI = {
    "codex": "codex-cli 0.157.1",
    "claude": "2.1.280",
}
```

Version不一致時は該当ActorをUnavailableとする。CLI更新時はsandboxで回帰テストを実施し、確認後に設定を更新する。

## 9. Slack Message Parse

ChatGPT Slack Pluginは本文末尾にattributionを付加し得るため、先頭JSON objectだけをdecodeする。
Markdown code fenceもtransport表現として除去する。

```python
decoder = json.JSONDecoder()
job, end = decoder.raw_decode(text)
```

Parse前にChannel / Sender authorizationを行う。v3 parserはPrompt本文やrequest hashを許容せず、
`instruction_ref.type == "notion_page"` と `page_id` を検証する。

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

Slack Result ManifestはBrowser Callbackより先に送信する。Browser CallbackはResult authorityではない。
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

```text
Actor execution
  ↓
Git Evidence / Artifact processing
  ↓
Slack Result Manifest
  ↓
Browser Callback
  ↓
Local result.jsonへcallback結果追記
  ↓
JOB END
```

CallbackではResult全文やArtifactを配送せず、job_id / actor / workspace / execution status /
artifact statusとSlack Result確認要求だけを送る。callback失敗はActor / Artifact結果を変更しない。
callbackでwake-upしたChatGPTがHumanの確認なしに次のJOBを発行してはならない。

Agent専用Browser profile、CDP endpoint、selector一意性のFail Closed規則を維持する。
正常系E2EとBrowser停止時 `ECONNREFUSED` のfailure isolation E2Eを確認済みである。

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
- Codex implementation / Claude Code read-only independent review
- JOB単位Local Execution Evidence / Git before-after Evidence
- Execution Status / Artifact Status分離
- Artifact Manifest / Windows Path Validation / conditional Drive upload
- centralized rotating Operational Log
- Result Manifest before Browser Callback / callback failure isolation

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

既存E2Eとして、ChatGPT → Slack → Worker → Codex / Claude、Local Workspace書込み、
条件付きDrive upload、Slack Result、Browser callback正常系、およびBrowser停止時failure isolationを維持する。

## 26. 設計原則

> ChatGPTはControl Plane、Notionはv3 Instruction source / Control / Registry、SlackはCommand /
> Event Bus、Python Workerは状態管理されたLocal Execution Bridge / Runner、Codex / Claude Codeは
> 境界を分けたExecution Actor、Google Driveは条件付きArtifact Transport / Store、Local Repo / Gitは
> Source of Truth、Browser Callbackはwake-up notificationとする。

> v3の実行identityはpage_idではなく、受理時に解決・snapshotしたInstruction bytesとWorker-generated SHAである。

> 同一WorkspaceはSQLiteのatomic `DISPATCHING` claimで直列化し、新規JOBと復旧JOBを同じdispatch pathへ通す。

> Result ManifestをBrowser Callbackより先に確定し、no inbound HTTP、no automatic actor chain without human boundaryを維持する。

> セキュリティは個人ローカル運用に見合う単純な境界を維持し、停止・Credentialローテーション・復旧容易性を優先する。
