# ChatGPT Local Agent 本番設計 v0.2.2

``` text
Document Role: PRODUCTION_DESIGN
Version: 0.2.2
Status: IMPLEMENTATION_BASELINE
Supersedes: ChatGPT Local Agent 本番設計 v0.2.1
```

## 1. 目的

通常のChatGPTをControl Planeとして利用し、ローカルPC上のCodex / Claude
Codeを非同期実行する。

本システムは「Remoteから任意のローカルCLIを実行する仕組み」ではない。

> 事前登録されたWorkspaceに対して、許可されたSender / Channel / Actor /
> ModeのJOBだけを、状態管理されたLocal Runnerが実行する。

``` text
Human ↔ ChatGPT
          │
          ▼
        Slack
          │ Socket Mode
          ▼
 Local Agent Worker
   ├ Authorization
   ├ Job State Store
   ├ Workspace Lock
   └ Actor / Mode Policy
          │
     ┌────┴────┐
     ▼         ▼
   Codex    Claude Code
     └────┬────┘
          ▼
   Local Workspace
          │
          ▼
    Google Drive
          │
          ▼
       ChatGPT
```

## 2. 責務

  Component          Role
  ------------------ --------------------------------------------------
  ChatGPT            Control Plane。対話、JOB生成、結果解釈、後続判断
  Slack              Command / Event Bus
  Python Worker      認可、状態管理、排他制御、CLI起動、成果物収集
  SQLite             Job State Store / idempotency / recovery
  Codex              Implementation Actor
  Claude Code        Independent Review / Audit Actor
  Google Drive       Artifact Store
  Local Repo / Git   Source of Truth

Inbound HTTPは公開しない。Slack Socket Modeを利用する。

## 3. Slack Authorization

WorkerはChannel IDとSender IDの両方をallowlistで検証する。

``` python
ALLOWED_CHANNEL_IDS = {"C0XXXXXXXXX"}
ALLOWED_SENDER_IDS = {"U0XXXXXXXXX"}
```

``` text
Slack Event
  ↓
Channel allowed? ─ No → Ignore + Operational Log
  ↓ Yes
Sender allowed?  ─ No → Ignore + Operational Log
  ↓ Yes
JOB parse
```

認可不一致はSlackへ `BRIDGE_ERROR` を返さず、ローカルOperational
Logだけに記録する。

初期本番では、HumanがChatGPTへ実行依頼を出した時点をHuman
Authorizationとみなす。自動JOB連鎖や自動修正ループを将来導入する場合は別途Human
approval境界を設計する。

## 4. JOB Protocol

``` json
{
  "protocol_version": "1",
  "job_id": "ARGUS-P-0114-v1",
  "actor": "codex",
  "mode": "implementation",
  "workspace": "argus",
  "prompt": "ARGUS-P-0114-v1を実行してください。"
}
```

Claude review:

``` json
{
  "protocol_version": "1",
  "job_id": "ARGUS-DA-0014",
  "actor": "claude",
  "mode": "review",
  "workspace": "argus",
  "prompt": "DA-0014のRequest Bodyに従って独立レビューしてください。"
}
```

対応Versionは初期版では `"1"` のみ。未知VersionはFail
Closedで拒否し、Actorを起動しない。

ローカル絶対パスをJOBから指定することは禁止する。

## 5. Workspace Registry

``` python
WORKSPACES = {
    "sandbox": {
        "path": r"C:\dev\chatgpt-agent\sandbox",
        "git_required": False,
        "allow_skip_git_repo_check": True,
    },
    "argus": {
        "path": r"C:\dev\argus",
        "git_required": True,
        "allow_skip_git_repo_check": False,
        "artifact_roots": [
            "validation/reports",
            "validation/metrics",
        ],
    },
}
```

Actorの `cwd` は必ずRegistryのpathへ固定する。

`sandbox` のみ `--skip-git-repo-check` を許可する。`git_required=True`
のWorkspaceはActor起動前にgit repositoryであることを確認する。

## 6. Actor / Mode Policy

``` python
ALLOWED_ACTOR_MODES = {
    ("codex", "implementation"),
    ("claude", "review"),
}
```

未知の組合せは拒否する。

Claude reviewは以下のread-only構成とする。

``` text
claude -p <prompt>
--permission-mode dontAsk
--permission-prompts none
--allowedTools Read,Glob,Grep
```

CodexはWorkspace Registryに応じて実行する。

``` text
sandbox:
  codex exec --skip-git-repo-check --sandbox workspace-write <prompt>

git workspace:
  codex exec --sandbox workspace-write <prompt>
```

## 7. CLI Version Policy

PoCで確認したCLI versionを設定に保持し、Worker起動時に照合する。

初期本番では完全一致で照合する。

``` python
EXPECTED_CLI = {
    "codex": "codex-cli 0.157.1",
    "claude": "2.1.280",
}
```

Version不一致時は該当ActorをUnavailableとする。CLI更新時はsandboxで回帰テストを実施し、確認完了後に
`EXPECTED_CLI` を更新する。

## 8. Slack Message Parse

ChatGPT Slack Pluginは本文末尾にattributionを付加する。

``` text
{JSON} *使用して送信されました* <@ChatGPT>
```

そのため先頭JSON objectだけをdecodeする。

``` python
decoder = json.JSONDecoder()
job, end = decoder.raw_decode(text)
```

Parse前にChannel / Sender authorizationを行う。

## 9. Job State Store

SQLiteをJob State Storeとして使用する。

``` text
jobs
------------------------------------------------
job_id              PRIMARY KEY
protocol_version
actor
mode
workspace
status
pid
host
received_at
queued_at
started_at
heartbeat_at
completed_at
exit_code
failure_class
result_drive_file_id
```

State:

``` text
RECEIVED
   ↓
VALIDATED
   ↓
QUEUED
   ↓
RUNNING
   ├─ DONE
   ├─ FAILED
   ├─ INTERRUPTED
   └─ RECOVERY_REQUIRED
```

`job_id` をidempotency keyとし、RUNNING / QUEUED /
DONEの重複JOBは再実行しない。

## 10. Heartbeat / Crash Recovery

RUNNING中はWorkerが60秒ごとに `heartbeat_at`
を更新する。HeartbeatはActorの生死を単独判定する根拠にはせず、PID / host
/ process stateと組み合わせて使用する。

Worker起動時にRUNNING JOBを検査する。

``` text
RUNNING
  ↓
heartbeat stale?
  ├─ No → 通常確認
  └─ Yes
       ↓
host / pid / process確認
       ├─ Process alive → heartbeat復旧・RUNNING維持
       ├─ Process dead  → INTERRUPTED
       └─ 判定不能      → RECOVERY_REQUIRED
```

HeartbeatがActor
timeoutより古い場合でも、それだけを理由にActorを死亡扱いしない。

同じJOBを自動再実行しない。判定不能な場合はHumanへ戻す。

## 11. Workspace Lock / QUEUED Dispatch

同一Workspaceに複数Actorを同時実行しない。Lock単位はActorではなくWorkspace。

``` text
ARGUS-P-0114 / codex → RUNNING
workspace=argus      → LOCKED
ARGUS-DA-0014 / claude → QUEUED
```

異なるWorkspaceは並行実行可能。

同一Workspaceで待機するJOBは `queued_at` の昇順によるFIFOを既定とする。

``` text
先行JOB完了
   ↓
Workspace Lock release
   ↓
oldest QUEUED(workspace)
   ↓
VALIDATE CURRENT STATE
   ↓
RUNNING
```

Worker起動時にも `QUEUED`
JOBを再scanし、Workspaceが空いていればFIFOでdispatchする。したがってWorker再起動をまたいだQUEUED
JOBも放置しない。

RUNNING孤児状態のRecovery判定が完了するまでは、同一WorkspaceのQUEUED
JOBを起動しない。

## 12. Result Manifest

DONE:

``` json
{
  "protocol_version": "1",
  "job_id": "ARGUS-P-0114-v1",
  "actor": "codex",
  "mode": "implementation",
  "workspace": "argus",
  "status": "DONE",
  "exit_code": 0,
  "summary": "ARGUS-P-0114-v1 completed.",
  "artifacts": [
    {
      "name": "full-hsd-candidate.md",
      "source_path": "validation/reports/argus-p-0114-v1/full-hsd-candidate.md",
      "drive_file_id": "..."
    }
  ]
}
```

FAILED:

``` json
{
  "protocol_version": "1",
  "job_id": "ARGUS-P-0114-v1",
  "actor": "codex",
  "mode": "implementation",
  "workspace": "argus",
  "status": "FAILED",
  "exit_code": 1,
  "failure_class": "ACTOR_FAILED",
  "error_summary": "Last relevant error lines..."
}
```

`error_summary` は最大4000文字程度とし、明らかなCredential
patternを簡易redactionしてSlackへ返す。Raw stderr全文はSlackへ送らない。

## 13. Artifact Manifest

Agentの自然文Markdownリンク抽出には依存しない。Workerがprompt末尾へmachine-readable出力指示を追加する。

``` text
<AGENT_RESULT>
{
  "summary": "...",
  "artifacts": [
    "relative/path/to/result.md"
  ]
}
</AGENT_RESULT>
```

成果物がない場合は `artifacts: []` を許容する。

## 14. Artifact Path Validation

Artifact pathは信頼しない。最低限以下を拒否する。

-   absolute path
-   drive-qualified path (`C:foo`)
-   UNC
-   `\\?\`
-   normalization後の `..` root escape
-   Alternate Data Streams (`file:stream`)
-   Windows reserved device names
-   trailing dot / trailing space
-   case-insensitive境界逸脱

検証はRaw syntax確認 → Workspaceと結合 → normalize / resolve →
root配下再確認 → junction/symlink escape確認 → Artifact Root
allowlist確認、の順で行う。

ARGUSでは既存の `ARGUS-DA-0002` のWindows path
escape検証規則を再利用する。

## 15. Artifact Upload Policy

WorkspaceごとにDrive upload可能範囲をallowlist化する。

ARGUS例:

``` text
validation/reports
validation/metrics
```

以下はuploadしない。

-   Password / API Key / OAuth token
-   Worker `.env`
-   `credentials.json`
-   `token.json`
-   Git credential
-   Projectが外部同期禁止として定義するCanonical State
-   Projectが外部同期禁止として定義するlog / secret領域

ARGUSでは既存の同期allowlist / exclusion ruleを尊重する。

## 16. Google Drive

``` text
chatgpt/
└─ jobs/
   ├─ ARGUS-P-0114-v1/
   │   ├─ result.json
   │   ├─ agent-output.md
   │   └─ artifacts/
   └─ ARGUS-DA-0014/
       ├─ result.json
       ├─ agent-output.md
       └─ artifacts/
```

Drive File IDをResult
Manifestへ記録し、ChatGPTは検索だけに依存せずID指定で取得できるようにする。

## 17. Credential

個人ローカル運用を前提とし、専用Vault等は初期版では導入しない。

-   `.env` / `credentials.json` / `token.json` はGitへ入れない。
-   CredentialをJOB / Result Manifest / Artifactへ意図的に含めない。
-   Worker資格情報領域とAgent Workspaceを分離する。
-   流出時はWorker停止 → Credential失効 → 再発行 →
    必要な外部ログ/Artifact削除 → 原因修正、で復旧する。

過度な複雑化より停止・失効・再発行の容易さを優先する。

## 18. Logging Policy

### Operational Log

job_id、actor、mode、workspace、timestamps、status、exit_code、failure_class、artifact
IDs、authorization rejection、recovery eventを記録する。

### Agent Output

最終stdout。必要に応じDriveへ保存する。Slackには全文を流さない。

### Debug Log

stderr、CLI diagnostic、Worker exception、command
details。原則ローカル保存・短期利用。

Credential混入を発見した場合は外部共有停止、該当ログ削除、Credentialローテーションで対応する。初期版では複雑なDLP
/ SIEMは導入しない。

## 19. Timeout

``` python
ACTOR_TIMEOUT = {
    "codex": 7200,
    "claude": 7200,
}
```

Timeoutは `FAILED / TIMEOUT` とする。

## 20. Codex → Claude Independent Review

``` text
Human
 ↓
ChatGPT
 ↓
Codex implementation
 ↓
DONE / Evidence
 ↓
ChatGPT / Human control boundary
 ↓
Claude independent review
 ↓
DONE / Findings
 ↓
ChatGPT
 ↓
Human Decision
```

CodexからClaude Codeを直接起動させない。

## 21. Notion Integration / Control & Registry Plane

NotionはJOB配送やArtifact転送には使用せず、人間向けのControl / Knowledge
/ Registry Planeとして扱う。

主な用途:

-   Prompt Registry
-   Design Advisory Registry
-   Human Decision / Approval状態
-   実行対象・レビュー対象・Actor指定等の管理情報
-   完了結果・Finding・判断結果の要約記録

役割分担:

``` text
Notion       = Control / Registry / Human-facing Knowledge
Slack        = Command / Event Bus
Python       = Local Execution Bridge
Drive        = Artifact Transport / Store
Local Repo   = Source of Truth
```

Python WorkerからNotionを直接更新しない。Workerの責務はJOB実行とResult
Manifest / Artifactの返却までとする。

Notion更新が必要な場合は、原則として以下の境界を維持する。

``` text
Agent Result
    ↓
Slack / Drive
    ↓
ChatGPT
    ↓
必要に応じてHuman Decision
    ↓
Notion Registry更新
```

これにより、JOB RunnerがPrompt Registry、Design Advisory、Human
Decision等の意味状態を直接変更することを避ける。

Evidence本体はLocal Repo /
Driveに保持し、Notionには人間が参照する状態・結論・要約を記録する。大量の実行ログやArtifact全文をNotionへ複製することは原則としない。

ARGUS等でNotion上のRegistryを実行起点として参照する場合も、Notion自体をCommand
Busにはせず、ChatGPTがRegistry内容を確認・解釈した上でSlack
JOBへ変換する。

## 22. 初期本番で導入しないもの

-   Cloudflare
-   Redis
-   外部Queue service
-   自作Chat UI
-   Web server / Inbound HTTP
-   自動Actor選択AI
-   自動修正無限ループ
-   Claude implementation mode
-   複数PC Worker
-   分散JOB scheduler
-   専用Secret Vault
-   複雑なDLP / SIEM

## 23. 実装順序

1.  PoC版 `integrated_worker.py` をFreeze
2.  本番コードをモジュール分割
3.  Credential配置 / Logging Policy
4.  JOB Protocol / protocol version validation
5.  Slack Channel / Sender Authorization
6.  Workspace Registry
7.  Actor / Mode Policy
8.  CLI Version Check
9.  Job State Store
10. Workspace Lock / QUEUED Dispatch
11. Heartbeat / Crash Recovery
12. CodexActor / ClaudeActor
13. Artifact Path Validator
14. Artifact Upload Policy
15. Artifact Manifest
16. Drive `jobs/<job_id>` 構造
17. Result Manifest / error_summary
18. sandbox E2E回帰テスト
19. `workspace=argus` 開放
20. ARGUS小規模Codex JOB
21. Claude Independent Review JOB
22. 実運用開始

`workspace=argus` を開放する前に、少なくともSlack Authorization、git
requirement、Workspace Lock、Crash Recovery、Artifact Path
Validation、Artifact Upload Policyが実装済みであることを条件とする。

## 24. PoC確認済み

-   ChatGPT → Slack
-   Slack → Python Socket Mode
-   Python → Slack
-   Python → Codex CLI
-   Codex → Local Workspace書込み
-   Python ↔ Google Drive
-   Python → Drive複数ファイルupload
-   Google Drive → ChatGPT
-   ChatGPT → Slack → Python → Codex → Drive / Slack → ChatGPT E2E
-   Claude Code非対話Read-only
-   Python → Claude Code
-   ChatGPT → Slack → Python → Claude Code → Drive / Slack → ChatGPT E2E

## 25. 設計原則

> ChatGPTはControl Plane、SlackはCommand
> Bus、Pythonは状態管理されたLocal Runner、Codex / Claude
> CodeはExecution Actor、Google DriveはArtifact Store、Local Repo /
> GitはSource of Truthとする。

> Remoteから自由にCLIを叩くのではなく、認可されたSender /
> Channelから、登録Workspace・許可Actor / Modeに限定して実行する。

> 同一Workspaceは直列化し、Worker障害後のRUNNING状態を無条件に再実行しない。

> セキュリティは個人ローカル運用に見合う単純な境界を維持し、過剰な複雑化より停止・Credentialローテーション・復旧容易性を優先する。
