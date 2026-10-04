# Context Harness artifact lifecycle / cleanup v0.1

cleanup は Worker の job 終了処理とは独立した保守コマンドで、既定は dry-run です。対象は
workspace の `.agent/context.json` が明示する `generated_root` 配下だけです。v0.1 では capability
直下の再生成可能な latest snapshot（`evidence-index*.json`、`job-context*.json`、
`materialized-context*.json`）と、同じ階層の `*.tmp` / `*.temp` だけを削除候補にします。

job ID のディレクトリは監査証跡として既定で保持します。特に 2026-10-04 P0-4 external smoke
の job-scoped proof は pinned production evidence であり削除しません。未知のファイル、source / user
data、trusted baseline、acceptance receipt、baseline archive、candidate / provenance、および trust
validation に必要な状態は対象外です。trust cache を repo 外に置く既存動作も変更しません。
symlink、path traversal、allowlist 外へ解決されるパスは拒否します。

```powershell
# 計画のみ（既定、変更なし）
python context_artifacts.py cleanup --workspace C:\path\to\FooProject

# 同一 invocation で計画を作成・検証してから、その計画内の項目だけを削除
python context_artifacts.py cleanup --apply --workspace C:\path\to\FooProject
```

出力は JSON で、各項目に `path`、`category`、`action`、`reason`、取得可能なら `bytes` を含みます。
apply はさらに `deleted` / `skipped` / `error` を報告します。生成 latest / temp のみを選択的に
`.gitignore` へ登録し、immutable な job 監査証跡は blanket-ignore しません。
