import json
import subprocess
from pathlib import Path


LOG_ROOT = Path(__file__).parent / "logs"


def _run_git(workdir, *args):
    result = subprocess.run(
        ["git", "-C", str(workdir), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )

    return result.stdout


def get_git_snapshot(workdir):
    workdir = Path(workdir)

    return {
        "head": _run_git(
            workdir,
            "rev-parse",
            "HEAD",
        ).strip(),
        "status": _run_git(
            workdir,
            "status",
            "--porcelain",
        ),
        "diff": _run_git(
            workdir,
            "diff",
            "--no-ext-diff",
        ),
        "diff_cached": _run_git(
            workdir,
            "diff",
            "--cached",
            "--no-ext-diff",
        ),
    }


def get_changed_paths(before, after):
    def parse_status(text):
        paths = set()

        for line in text.splitlines():
            if len(line) < 4:
                continue

            path = line[3:].strip()

            # rename: old -> new
            if " -> " in path:
                path = path.split(
                    " -> ",
                    1,
                )[1]

            paths.add(path)

        return paths

    before_paths = parse_status(
        before["status"]
    )
    after_paths = parse_status(
        after["status"]
    )

    # statusだけでは、JOB前からdirtyだった
    # 同一pathへの追加変更を検出できない。
    # Slack表示用の概略として使用する。
    return sorted(
        before_paths | after_paths
    )


def create_job_log(job_id):
    path = LOG_ROOT / job_id
    path.mkdir(
        parents=True,
        exist_ok=True,
    )

    return path


def save_text(
    log_dir,
    filename,
    content,
):
    (log_dir / filename).write_text(
        content or "",
        encoding="utf-8",
    )


def save_json(
    log_dir,
    filename,
    data,
):
    (log_dir / filename).write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def save_git_snapshot(
    log_dir,
    prefix,
    snapshot,
):
    save_text(
        log_dir,
        f"git-head-{prefix}.txt",
        snapshot["head"],
    )

    save_text(
        log_dir,
        f"git-status-{prefix}.txt",
        snapshot["status"],
    )

    save_text(
        log_dir,
        f"git-diff-{prefix}.patch",
        snapshot["diff"],
    )

    save_text(
        log_dir,
        f"git-diff-cached-{prefix}.patch",
        snapshot["diff_cached"],
    )