import sqlite3
import json
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
    instruction_ref TEXT,
    review_mode TEXT,
    review_package_ref TEXT,
    measurement_mode INTEGER NOT NULL DEFAULT 0,
    callback_type TEXT,
    callback_url TEXT,
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

WORKSPACE_CLAIM_SCHEMA = """
CREATE TABLE IF NOT EXISTS workspace_claims (
    workspace TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    acquired_at TEXT NOT NULL
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
        db.execute(WORKSPACE_CLAIM_SCHEMA)

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
        
        if "callback_type" not in columns:
            db.execute(
                """
                ALTER TABLE jobs
                ADD COLUMN callback_type TEXT
                """
            )

        if "callback_url" not in columns:
            db.execute(
                """
                ALTER TABLE jobs
                ADD COLUMN callback_url TEXT
                """
            )
        
        if "instruction_ref" not in columns:
            db.execute(
                """
                ALTER TABLE jobs
                ADD COLUMN instruction_ref TEXT
                """
            )
        if "review_mode" not in columns:
            db.execute("ALTER TABLE jobs ADD COLUMN review_mode TEXT")
        if "review_package_ref" not in columns:
            db.execute("ALTER TABLE jobs ADD COLUMN review_package_ref TEXT")
        if "measurement_mode" not in columns:
            db.execute("ALTER TABLE jobs ADD COLUMN measurement_mode INTEGER NOT NULL DEFAULT 0")


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
                instruction_ref,
                review_mode,
                review_package_ref,
                measurement_mode,
                callback_type,
                callback_url,
                status,
                received_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'RECEIVED', ?)
            """,
            (
                job.job_id,
                job.protocol_version,
                job.actor,
                job.mode,
                job.workspace,
                job.prompt,
                job.prompt_sha256,
                (
                    json.dumps(
                        job.instruction_ref,
                        ensure_ascii=False,
                    )
                    if job.instruction_ref
                    is not None
                    else None
                ),
                job.review_mode,
                json.dumps(job.review_package_ref, ensure_ascii=False) if job.review_package_ref else None,
                int(job.measurement_mode),
                job.callback_type,
                job.callback_url,
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
                started_at = COALESCE(started_at, ?),
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
        db.execute("DELETE FROM workspace_claims WHERE owner = ?", (f"job:{job_id}",))


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


def list_queued_workspaces():
    with connect() as db:
        rows = db.execute(
            """
            SELECT workspace, MIN(queued_at) AS first_queued_at
            FROM jobs
            WHERE status = 'QUEUED'
            GROUP BY workspace
            ORDER BY first_queued_at ASC
            """
        ).fetchall()

    return [row["workspace"] for row in rows]


def restore_dispatching_jobs():
    """Return startup-abandoned claims to the persisted FIFO queue."""
    with connect() as db:
        db.execute(
            """
            UPDATE jobs
            SET status = 'QUEUED'
            WHERE status = 'DISPATCHING'
            """
        )
        db.execute("DELETE FROM workspace_claims WHERE owner LIKE 'job:%'")


def acquire_manual_workspace_claim(workspace, owner):
    """Acquire the cross-process workspace lease used by normal dispatch."""
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        busy = db.execute(
            "SELECT 1 FROM jobs WHERE workspace=? AND status IN ('DISPATCHING','RUNNING')",
            (workspace,),
        ).fetchone()
        leased = db.execute(
            "SELECT 1 FROM workspace_claims WHERE workspace=?", (workspace,)
        ).fetchone()
        if busy is not None or leased is not None:
            return False
        db.execute("INSERT INTO workspace_claims VALUES (?, ?, ?)",
                   (workspace, owner, now_iso()))
        return True


def release_manual_workspace_claim(workspace, owner):
    with connect() as db:
        result = db.execute(
            "DELETE FROM workspace_claims WHERE workspace=? AND owner=?",
            (workspace, owner),
        )
    return result.rowcount == 1


def is_workspace_busy(workspace):
    with connect() as db:
        row = db.execute(
            """
            SELECT 1
            FROM jobs
            WHERE workspace = ?
              AND status IN ('DISPATCHING', 'RUNNING')
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


def claim_next_queued(workspace):
    """Atomically claim the FIFO queue head if the workspace is idle."""
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")

        busy = db.execute(
            """
            SELECT 1
            FROM jobs
            WHERE workspace = ?
              AND status IN ('DISPATCHING', 'RUNNING')
            LIMIT 1
            """,
            (workspace,),
        ).fetchone()
        leased = db.execute(
            "SELECT 1 FROM workspace_claims WHERE workspace = ?", (workspace,)
        ).fetchone()
        if busy is not None or leased is not None:
            return None

        row = db.execute(
            """
            SELECT *
            FROM jobs
            WHERE workspace = ?
              AND status = 'QUEUED'
            ORDER BY queued_at ASC, rowid ASC
            LIMIT 1
            """,
            (workspace,),
        ).fetchone()
        if row is None:
            return None

        updated = db.execute(
            """
            UPDATE jobs
            SET status = 'DISPATCHING'
            WHERE job_id = ?
              AND status = 'QUEUED'
            """,
            (row["job_id"],),
        )
        if updated.rowcount != 1:
            return None

        db.execute("INSERT INTO workspace_claims VALUES (?, ?, ?)",
                   (workspace, f"job:{row['job_id']}", now_iso()))

        claimed = dict(row)
        claimed["status"] = "DISPATCHING"
        return claimed


def restore_claim(job_id):
    """Safely put an unstarted claim back without overwriting later state."""
    with connect() as db:
        result = db.execute(
            """
            UPDATE jobs
            SET status = 'QUEUED'
            WHERE job_id = ?
              AND status = 'DISPATCHING'
            """,
            (job_id,),
        )
        if result.rowcount == 1:
            db.execute("DELETE FROM workspace_claims WHERE owner=?", (f"job:{job_id}",))
    return result.rowcount == 1


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
