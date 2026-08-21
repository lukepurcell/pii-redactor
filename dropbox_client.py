"""
Dropbox integration.

Thin wrapper around the official `dropbox` Python SDK covering exactly what the
MVP needs: list PDFs in a folder, download a file to a local path, and upload a
redacted copy back. Auth is via an access token (env var DROPBOX_TOKEN) for the
demo; a production build would use the full OAuth 2 + refresh-token flow and,
for the admin auto-redact-on-share scenario, a Dropbox webhook + team folders.

The web app degrades gracefully: if no token is configured, the Dropbox tab is
disabled and the local-upload path still works for the demo.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class DropboxFile:
    name: str
    path: str            # path_lower / path_display
    size: int


class DropboxClient:
    def __init__(self, token: str | None = None):
        self.token = token or os.environ.get("DROPBOX_TOKEN")
        self._dbx = None

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    def _client(self):
        if self._dbx is None:
            import dropbox  # imported lazily so the app runs without the SDK
            self._dbx = dropbox.Dropbox(self.token)
        return self._dbx

    def list_pdfs(self, folder: str = "") -> list[DropboxFile]:
        """List PDF files in a folder ('' means the app/root folder)."""
        import dropbox
        dbx = self._client()
        out: list[DropboxFile] = []
        res = dbx.files_list_folder(folder, recursive=False)
        while True:
            for entry in res.entries:
                if isinstance(entry, dropbox.files.FileMetadata) and \
                        entry.name.lower().endswith(".pdf"):
                    out.append(DropboxFile(entry.name, entry.path_lower, entry.size))
            if not res.has_more:
                break
            res = dbx.files_list_folder_continue(res.cursor)
        return out

    def download(self, dropbox_path: str, local_path: str) -> str:
        dbx = self._client()
        dbx.files_download_to_file(local_path, dropbox_path)
        return local_path

    def upload(self, local_path: str, dropbox_path: str, overwrite: bool = False) -> str:
        """Upload a (redacted) file. Returns the resulting Dropbox path."""
        import dropbox
        dbx = self._client()
        mode = (dropbox.files.WriteMode.overwrite if overwrite
                else dropbox.files.WriteMode.add)
        with open(local_path, "rb") as f:
            md = dbx.files_upload(f.read(), dropbox_path, mode=mode, mute=True)
        return md.path_display

    @staticmethod
    def redacted_path(original_path: str) -> str:
        """`/folder/report.pdf` -> `/folder/report (redacted).pdf`."""
        base, ext = os.path.splitext(original_path)
        return f"{base} (redacted){ext}"
