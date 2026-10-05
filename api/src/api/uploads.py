"""The upload store: bytes on disk under UPLOAD_DIR, one directory per org,
one file per `files` row, and the type sniffer that decides what a file is
from its first bytes (never from the client's header or extension).

Phase 1 stores deposit receipts and payout proofs, phase 2 identity
documents and photos; the other purposes are in the database CHECK already
and are accepted here in their phase.
"""
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
# content_type -> extension used in the storage key and the download name.
ALLOWED = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "application/pdf": "pdf",
}
# What the upload route stores today. The files CHECK already lists every
# purpose of every phase; the rest are accepted in their phase.
ACCEPTED_PURPOSES = {"deposit_receipt", "payout_proof", "kyc_document", "kyc_photo"}
ALL_PURPOSES = ACCEPTED_PURPOSES | {"ticket_attachment", "avatar"}
UPLOADS_PER_HOUR = 30


def detect_type(head: bytes) -> tuple[str, str] | None:
    """(content_type, ext) from the magic bytes of the first 16 bytes of a
    file, or None when it is none of the four accepted types."""
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ALLOWED["image/jpeg"]
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ALLOWED["image/png"]
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp", ALLOWED["image/webp"]
    if head.startswith(b"%PDF-"):
        return "application/pdf", ALLOWED["application/pdf"]
    return None


class UploadStore:
    """Files under `root`, addressed by a relative key `<org_id>/<file_id>.<ext>`."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def key(self, org_id: int, file_id: int, ext: str) -> str:
        return f"{org_id}/{file_id}.{ext}"

    def path(self, key: str) -> Path:
        """The on-disk path for a key. Keys come from our own rows, but a
        row is still data: anything that could leave `root` is refused."""
        if not key or key.startswith(("/", "\\")) or "\\" in key or ":" in key:
            raise ValueError(f"refusing storage key {key!r}")
        parts = PurePosixPath(key).parts
        if PurePosixPath(key).is_absolute() or ".." in parts or any(p in ("", ".") for p in parts):
            raise ValueError(f"refusing storage key {key!r}")
        target = self.root.joinpath(*parts)
        try:
            target.resolve().relative_to(self.root.resolve())
        except ValueError:
            raise ValueError(f"refusing storage key {key!r}")
        return target

    def write(self, key: str, data: bytes) -> None:
        """Atomic: the bytes land in a .part file next to the target and are
        renamed into place, so a reader never sees a half-written file and
        a crash leaves at most a .part to sweep."""
        target = self.path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".part")
        with open(partial, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(partial, target)

    def read(self, key: str) -> bytes:
        return self.path(key).read_bytes()
