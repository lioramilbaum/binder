"""Find duplicate attachments and make them share one stored copy."""

from __future__ import annotations

import argparse

from sqlalchemy.orm import Session, sessionmaker

from .database import create_session_factory, session_scope
from .folder_repository import find_duplicates
from .large_objects import hash_large_object
from .models import DocumentFile


def backfill_hashes(factory: sessionmaker[Session]) -> int:
    """Hash attachments stored before hashing existed, returning how many were updated.

    Rows sharing a large object are hashed once. One transaction per object, so
    an interrupted run keeps its progress and a retry resumes where it stopped.
    """

    with factory() as session:
        oids = [
            oid
            for (oid,) in session.query(DocumentFile.data_oid)
            .filter(DocumentFile.sha256.is_(None))
            .distinct()
        ]
    updated = 0
    for oid in oids:
        with session_scope(factory) as session:
            digest = hash_large_object(session, oid)
            updated += (
                session.query(DocumentFile)
                .filter(DocumentFile.data_oid == oid, DocumentFile.sha256.is_(None))
                .update({"sha256": digest})
            )
    return updated


def merge_duplicates(factory: sessionmaker[Session]) -> int:
    """Point identical attachments at one large object, returning the bytes reclaimed.

    The database trigger unlinks each replaced large object once nothing
    references it. Only hashed files are considered, so run ``backfill_hashes``
    first.
    """

    reclaimed = 0
    for group in find_duplicates(factory)["groups"]:
        if group["stored_copies"] < 2:
            continue
        with session_scope(factory) as session:
            files = (
                session.query(DocumentFile)
                .filter(
                    DocumentFile.sha256 == group["sha256"], DocumentFile.size == group["size"]
                )
                .order_by(DocumentFile.created_at)
                .all()
            )
            keep_oid = files[0].data_oid
            for file in files[1:]:
                file.data_oid = keep_oid
        reclaimed += group["reclaimable_bytes"]
    return reclaimed


def _format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _report(factory: sessionmaker[Session]) -> None:
    result = find_duplicates(factory)
    for group in result["groups"]:
        print(
            f"{group['sha256'][:12]}  {_format_size(group['size'])}  "
            f"{len(group['files'])} files, {group['stored_copies']} stored copies"
        )
        for file in group["files"]:
            print(f"    {file['folder_name']} / {file['document_name']} / {file['name']}")
    print(
        f"{len(result['groups'])} duplicate group(s); "
        f"{_format_size(result['reclaimable_bytes'])} reclaimable by merging."
    )
    if result["unhashed"]:
        print(f"{result['unhashed']} file(s) not hashed yet: run 'binder-dedupe backfill'.")


def main() -> None:
    """Command line entry point: backfill, report, or merge."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("backfill", "report", "merge"))
    parser.add_argument(
        "--apply", action="store_true", help="merge: actually share storage (default is a dry run)"
    )
    args = parser.parse_args()

    factory = create_session_factory()
    if args.command == "backfill":
        print(f"Hashed {backfill_hashes(factory)} file(s).")
    elif args.command == "report":
        _report(factory)
    elif args.apply:
        print(f"Merged duplicates; reclaimed {_format_size(merge_duplicates(factory))}.")
    else:
        result = find_duplicates(factory)
        print(
            f"Dry run: merging would reclaim {_format_size(result['reclaimable_bytes'])}. "
            "Back up first (make backup), then re-run with --apply."
        )


if __name__ == "__main__":
    main()
