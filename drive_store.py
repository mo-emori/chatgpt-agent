import mimetypes
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


SCOPES = [
    "https://www.googleapis.com/auth/drive"
]

CREDENTIALS_FILE = Path("credentials.json")
TOKEN_FILE = Path("token.json")

ROOT_FOLDER_NAME = "chatgpt"


def get_credentials():
    creds = None

    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(
            TOKEN_FILE,
            SCOPES,
        )

    if not creds or not creds.valid:
        if (
            creds
            and creds.expired
            and creds.refresh_token
        ):
            creds.refresh(Request())

        else:
            flow = (
                InstalledAppFlow
                .from_client_secrets_file(
                    CREDENTIALS_FILE,
                    SCOPES,
                )
            )

            creds = flow.run_local_server(
                port=0
            )

        TOKEN_FILE.write_text(
            creds.to_json(),
            encoding="utf-8",
        )

    return creds


def get_service():
    return build(
        "drive",
        "v3",
        credentials=get_credentials(),
    )


def find_folder(
    service,
    name,
    parent_id=None,
):
    parts = [
        f"name = '{name}'",
        (
            "mimeType = "
            "'application/vnd.google-apps.folder'"
        ),
        "trashed = false",
    ]

    if parent_id:
        parts.append(
            f"'{parent_id}' in parents"
        )

    result = service.files().list(
        q=" and ".join(parts),
        spaces="drive",
        fields="files(id,name)",
    ).execute()

    files = result.get(
        "files",
        []
    )

    if not files:
        return None

    return files[0]["id"]


def create_folder(
    service,
    name,
    parent_id=None,
):
    body = {
        "name": name,
        "mimeType":
            "application/vnd.google-apps.folder",
    }

    if parent_id:
        body["parents"] = [
            parent_id
        ]

    result = service.files().create(
        body=body,
        fields="id,name",
    ).execute()

    return result["id"]


def ensure_folder(
    service,
    name,
    parent_id=None,
):
    folder_id = find_folder(
        service,
        name,
        parent_id,
    )

    if folder_id:
        return folder_id

    return create_folder(
        service,
        name,
        parent_id,
    )


def ensure_job_artifact_folder(
    service,
    job_id,
):
    root_id = ensure_folder(
        service,
        ROOT_FOLDER_NAME,
    )

    jobs_id = ensure_folder(
        service,
        "jobs",
        root_id,
    )

    job_id_folder = ensure_folder(
        service,
        job_id,
        jobs_id,
    )

    artifacts_id = ensure_folder(
        service,
        "artifacts",
        job_id_folder,
    )

    return artifacts_id


def upload_file(
    service,
    local_path,
    parent_id,
):
    local_path = Path(
        local_path
    )

    mime_type, _ = (
        mimetypes.guess_type(
            local_path.name
        )
    )

    if not mime_type:
        mime_type = (
            "application/octet-stream"
        )

    media = MediaFileUpload(
        str(local_path),
        mimetype=mime_type,
        resumable=False,
    )

    result = service.files().create(
        body={
            "name": local_path.name,
            "parents": [parent_id],
        },
        media_body=media,
        fields=(
            "id,name,mimeType,size"
        ),
    ).execute()

    return result


def upload_artifacts(
    job_id,
    paths,
):
    service = get_service()

    folder_id = (
        ensure_job_artifact_folder(
            service,
            job_id,
        )
    )

    uploaded = []

    for path in paths:
        result = upload_file(
            service,
            path,
            folder_id,
        )

        uploaded.append(
            {
                "name":
                    result["name"],
                "drive_file_id":
                    result["id"],
                "mime_type":
                    result.get(
                        "mimeType"
                    ),
                "size":
                    result.get(
                        "size"
                    ),
            }
        )

    return uploaded