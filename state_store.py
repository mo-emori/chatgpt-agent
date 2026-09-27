import sqlite3
from datetime import datetime, timezone

from config import STATE_DB


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    protocol_version TEXT NOT NULL,
    actor TEXT NOT NULL,
    mode TEXT NOT NULL,
    workspace TEXT NOT NULL,
    prompt TEXT NOT NULL,
    prompt_sha256 TEXT,
    status TEXT NOT NULL,
    pid INTEGER,
    host TEXT,
    received_at TEXT NOT NULL,
    queued_at TEXT,
    started_at TEXT,
    heartbeat_at TEXT,
    completed_at TEXT,
    exit_code INTEGER,
    failure_class TEXT,
    result_drive_file_id TEXT
);
"""


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def connect():
    db = sqlite3.connect(STATE_DB)
    db.row_factory = sqlite3.Row
    return db


def initialize():
    with connect() as db:
        db.execute(SCHEMA)

        columns = {
            row["name"]
            for row in db.execute(
                "PRAGMA table_info(jobs)"
            ).fetchall()
        }

        if "prompt_sha256" not in columns:
            db.execute(
                """
                ALTER TABLE jobs
                ADD COLUMN prompt_sha256 TEXT
                """
            )


def create_job(job):
    with connect() as db:
        db.execute(
            """
            INSERT INTO jobs (
                job_id,
                protocol_version,
                actor,
                mode,
                workspace,
                prompt,
                prompt_sha256,
                status,
                received_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'RECEIVED', ?)
            """,
            (
                job.job_id,
                job.protocol_version,
                job.actor,
                job.mode,
                job.workspace,
                job.prompt,
                job.prompt_sha256,
                now_iso(),
            ),
        )


def get_job(job_id):
    with connect() as db:
        row = db.execute(
            "SELECT * FROM jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()

    return dict(row) if row else None


def set_status(job_id, status):
    with connect() as db:
        db.execute(
            """
            UPDATE jobs
            SET status = ?
            WHERE job_id = ?
            """,
            (status, job_id),
        )


def mark_queued(job_id):
    with connect() as db:
        db.execute(
            """
            UPDATE jobs
            SET status = 'QUEUED',
                queued_at = ?
            WHERE job_id = ?
            """,
            (now_iso(), job_id),
        )


def mark_running(job_id, pid, host):
    now = now_iso()

    with connect() as db:
        db.execute(
            """
            UPDATE jobs
            SET status = 'RUNNING',
                pid = ?,
                host = ?,
                started_at = ?,
                heartbeat_at = ?
            WHERE job_id = ?
            """,
            (
                pid,
                host,
                now,
                now,
                job_id,
            ),
        )


def heartbeat(job_id):
    with connect() as db:
        db.execute(
            """
            UPDATE jobs
            SET heartbeat_at = ?
            WHERE job_id = ?
              AND status = 'RUNNING'
            """,
            (now_iso(), job_id),
        )


def mark_completed(
    job_id,
    *,
    status,
    exit_code=None,
    failure_class=None,
    result_drive_file_id=None,
):
    with connect() as db:
        db.execute(
            """
            UPDATE jobs
            SET status = ?,
                completed_at = ?,
                exit_code = ?,
                failure_class = ?,
                result_drive_file_id = ?
            WHERE job_id = ?
            """,
            (
                status,
                now_iso(),
                exit_code,
                failure_class,
                result_drive_file_id,
                job_id,
            ),
        )


def list_running():
    with connect() as db:
        rows = db.execute(
            """
            SELECT *
            FROM jobs
            WHERE status = 'RUNNING'
            ORDER BY started_at
            """
        ).fetchall()

    return [dict(row) for row in rows]


def list_queued(workspace=None):
    with connect() as db:
        if workspace is None:
            rows = db.execute(
                """
                SELECT *
                FROM jobs
                WHERE status = 'QUEUED'
                ORDER BY queued_at
                """
            ).fetchall()
        else:
            rows = db.execute(
                """
                SELECT *
                FROM jobs
                WHERE status = 'QUEUED'
                  AND workspace = ?
                ORDER BY queued_at
                """,
                (workspace,),
            ).fetchall()

    return [dict(row) for row in rows]


def is_workspace_busy(workspace):
    with connect() as db:
        row = db.execute(
            """
            SELECT 1
            FROM jobs
            WHERE workspace = ?
              AND status = 'RUNNING'
            LIMIT 1
            """,
            (workspace,),
        ).fetchone()

    return row is not None


def get_next_queued(workspace):
    with connect() as db:
        row = db.execute(
            """
            SELECT *
            FROM jobs
            WHERE workspace = ?
              AND status = 'QUEUED'
            ORDER BY queued_at ASC
            LIMIT 1
            """,
            (workspace,),
        ).fetchone()

    return dict(row) if row else None


def mark_interrupted(job_id):
    mark_completed(
        job_id,
        status="INTERRUPTED",
        failure_class="PROCESS_NOT_FOUND",
    )


def mark_recovery_required(job_id):
    with connect() as db:
        db.execute(
            """
            UPDATE jobs
            SET status = 'RECOVERY_REQUIRED',
                failure_class = 'RECOVERY_REQUIRED'
            WHERE job_id = ?
            """,
            (job_id,),
        )