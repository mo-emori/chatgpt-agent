# ChatGPT Local Agent 本番設計 v0.5

``` text
Document Role: PRODUCTION_DESIGN
Version: 0.5
Status: IMPLEMENTED_BASELINE
Supersedes: ChatGPT Local Agent 本番設計 v0.4.1
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

v0.5標準JOB Protocolは `protocol_version=2` とする。

PromptはSlackによる本文変形を回避するためBase64でtransportする。
ChatGPTはPrompt SHA-256を生成しない。

```json
{
  "protocol_version": "2",
  "job_id": "ARGUS-P-0114-v2",
  "actor": "codex",
  "mode": "implementation",
  "workspace": "argus",
  "callback": {
    "type": "chatgpt_browser",
    "url": "https://chatgpt.com/c/..."
  },
  "prompt_encoding": "base64",
  "prompt": "<base64 encoded UTF-8 prompt>"
}
```

WorkerはBase64をstrict decodeし、decoded Prompt bytesを
authoritative execution Promptとする。

同一decoded Prompt bytesを起点として、

- SHA-256
- Actor stdin
- Local Execution Evidence

を生成する。

```text
Slack Base64 Prompt
        ↓
strict Base64 decode
        ↓
decoded_prompt_bytes
   ├─ SHA-256
   ├─ Local Execution Evidence
   └─ Actor stdin
```

## 5. Workspace Registry

Workspace / Browser等のnon-secret installation固有情報は `config.json`
に外出しする。

``` json
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
      "artifact_roots": [
        "validation/reports",
        "validation/metrics",
        "tests"
      ]
    }
  },
  "browser": {
    "enabled": true,
    "cdp_url": "http://127.0.0.1:9222"
  }
}
```

`config.py` はload / validate / Path変換 / environment bindingを担当し、
Credentialは `.env` / environment
variables等の既存secret管理を維持する。

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

そのため先頭JSON objectだけをdecodeする。ChatGPT / SlackがJSONをMarkdown
code fenceで 囲む場合（```` ```json ... ``` ```` /
```` ```{...}``` ````）もtransport表現として除去する。

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
prompt
prompt_sha256
callback_type
callback_url
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

Actor executionとArtifact deliveryを独立状態として扱う。

``` json
{
  "protocol_version": "2",
  "job_id": "ARGUS-P-0114-v1",
  "actor": "codex",
  "mode": "implementation",
  "workspace": "argus",
  "prompt_sha256": "...",
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

v2の `prompt_sha256` は、WorkerがBase64 decode後のauthoritative
Prompt bytesから生成したSHA-256である。
request由来のhashではない。

`status=DONE / exit_code=0 / artifact_status=PARTIAL_FAILURE`
は有効な状態である。 Artifact validation / Drive upload
failureを理由に成功したActor executionをFAILEDへ変更しない。
拒否Artifactはpathとreasonを `rejected_artifacts` へ記録する。

Slack Result ManifestはBrowser Callbackより先に送信する。Browser
CallbackはResultのauthorityではない。 callback結果はLocal `result.json`
に記録し、callback失敗によってexecution / artifact statusを変更しない。

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
tests
```

以下はuploadしない。

-   Password / API Key / OAuth token
-   Worker `.env`
-   `credentials.json`
-   `token.json`
-   Git credential
-   Projectが外部同期禁止として定義するCanonical State
-   Projectが外部同期禁止として定義するlog / secret領域
-   Agent専用Browser Profile (`browser-profile/`)

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

Agent専用 `browser-profile/`
はChatGPT等のログイン済みSessionを保持し得るため、 Credential相当のLocal
Runtime Stateとして扱う。Git / Google Drive Artifact / Agent
Workspaceへ含めない。

過度な複雑化より停止・失効・再発行の容易さを優先する。

## 18. Logging Policy

### Operational Log

job_id、actor、mode、workspace、timestamps、status、exit_code、failure_class、artifact
IDs、authorization rejection、recovery eventを記録する。

### Local Execution Evidence

`C:\dev\chatgpt-agent\logs\<job_id>\` にJOB単位で
`request.json`、`stdout.txt`、 `stderr.txt`、Git before/afterのHEAD /
status / diff / cached diff、`result.json` を保存する。 Local Execution
LogはWorkspace repo外・Git管理外・通常のDrive Artifact対象外とする。
Worker取得Git Evidenceを変更事実のauthorityとする。

### Agent Output

最終stdout/stderrはLocal Execution
Evidenceへ保存する。必要なArtifactだけをDriveへ配送し、
Slackにはraw全文を流さない。

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

Python WorkerからNotionを直接更新しない。Workerの責務はJOB実行、Result
Manifest / Artifact返却、Browser wake-upまでとする。

ChatGPTがv2 Agent JOBを発行する場合、実行前に以下をNotionへ登録する。

- Submitted Instruction
- Job ID
- Actor
- Mode
- Workspace
- その他Human-facing metadata

ChatGPTは実行前Prompt SHA-256を生成しない。
Workerは実際にActorへ渡すdecoded Prompt bytesからSHA-256を生成し、
Result Manifestへ記録する。
JOB完了後、ChatGPTはResult ManifestのWorker-generated
Prompt SHA-256をExecution FactとしてNotionへ反映する。
v2におけるPrompt SHA-256はInstruction submission時の属性ではなく、
実際に実行されたPrompt bytesの識別子である。

Python WorkerからNotionを直接read/writeしない。

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

## 22. Browser Callback / ChatGPT Wake-up

Browser CallbackはJOBごとのoptional routing metadataで、固定conversation
URLをWorker設定に持たない。 ChatGPTがJOB発行時に自分のconversation
URLを渡す。

Agent専用OperaをCDP付きで特殊起動する。

``` text
%LOCALAPPDATA%\Programs\Opera\opera.exe
  --remote-debugging-port=9222
  --user-data-dir="C:\dev\chatgpt-agent\browser-profile"
```

通常起動Operaはremote debuggingを有効化せずWorker操作対象外とする。

CDP
endpointへ接続可能な同一PC上のProcessは、Agent専用Browserのログイン済みSessionを
利用してPageを操作できる。そのためremote
debuggingはAgent専用Profileで起動したBrowserだけに
限定し、通常利用Browserでは有効化しない。`browser-profile/`
はCredential相当として扱う。

ChatGPT DOM selectorは `browser/chatgpt_dom.json`
に隔離する。確認済みselectorは
composer=`form [contenteditable='true'][role='textbox']`、
send=`form button[aria-label='送信']`。visibleで一意でなければFail
Closedとする。

Browser CallbackではResult全文やArtifactを配送せず、job_id / actor /
workspace / execution status / artifact statusとSlack
Result確認要求だけを送る。

Browser Callbackはwake-up notificationであり、実行承認ではない。
callbackで起動したChatGPTが、Humanの確認を経ずに次のJOBを発行しない。

自動JOB連鎖 / 自動修正Loopを導入する場合は、
別途Human Approval境界を設計する。

``` text
Actor execution
  ↓
Git Evidence
  ↓
Artifact processing
  ↓
Slack Result Manifest
  ↓
Browser Callback
  ↓
Local result.jsonへcallback結果追記
  ↓
JOB END
```

Agent専用Opera起動時の正常系E2Eと、Opera停止時の `ECONNREFUSED`
障害系E2Eを確認済み。 障害系でも
`status=DONE / artifact_status=DONE / callback.status=FAILED`
を維持する。

## 23. 初期本番で導入しないもの

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

## 24. 実装・Acceptance状態

本書時点で以下を実装・Acceptance済みとする。

-   Slack Channel / Sender Authorization
-   JSON / Markdown code-fence parse
-   Protocol v1/v2 coexistence
-   v2 Base64 Prompt transport
-   Worker-generated Prompt SHA-256
-   v2 invalid Base64 / empty Prompt / invalid UTF-8 Fail Closed
-   v2 request prompt_sha256 rejection
-   decoded Prompt byte identity regression test
-   SQLite Job State / idempotency / Workspace Lock / persistent FIFO
-   PID / host / heartbeat / Crash Recovery
-   CLI Version Gate / Git Workspace Validation
-   Codex implementation / Claude Code read-only review
-   JOB単位Local Execution Evidence / Git before-after Evidence
-   Execution Status / Artifact Status分離
-   Artifact Manifest / Windows Path Validation / multiple upload /
    rejected_artifacts
-   config.jsonによるWorkspace / Browser設定外出し
-   per-JOB Browser Callback URL / Slack mrkdwn URL normalization
-   Agent専用Opera / CDP callback正常系E2E
-   Browser停止時callback failure isolation E2E

## 25. 実装・E2E確認済み

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
-   v2 decoded Prompt bytes
    = Worker SHA-256 input bytes
    = Actor stdin source bytes
-   Worker-generated Prompt SHA-256
    = Result Manifest prompt_sha256
- v1 SHA mismatch時Actor未起動 / Fail Closed
- v2 invalid Base64 / empty Prompt / invalid UTF-8時Actor未起動 /
  Fail Closed
-   Slack Result → Agent専用Opera/CDP → 発行元ChatGPT
    conversation自動wake-up
-   Opera停止時もActor/Artifact成功を維持しcallbackだけFAILED

## 26. 設計原則

> ChatGPTはControl Plane、SlackはCommand / Result
> Bus、Pythonは状態管理されたLocal Runner、 Codex / Claude
> CodeはExecution Actor、Google DriveはArtifact Store、Local Repo /
> Gitは Source of Truth、Browser Callbackはwake-up notification
> channelとする。

> Remoteから自由にCLIを叩くのではなく、認可されたSender /
> Channelから、登録Workspace・許可Actor / Modeに限定して実行する。

> 同一Workspaceは直列化し、Worker障害後のRUNNING状態を無条件に再実行しない。

> セキュリティは個人ローカル運用に見合う単純な境界を維持し、過剰な複雑化より停止・Credentialローテーション・復旧容易性を優先する。
