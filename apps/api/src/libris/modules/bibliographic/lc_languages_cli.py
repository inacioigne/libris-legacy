"""Explicit operator CLI for LC Languages; never runs during API startup."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from libris.core.config import Settings
from libris.infrastructure.database import Database
from libris.modules.bibliographic.application.lc_languages import (
    IMPORTER_VERSION,
    LC_LANGUAGES_VOCABULARY,
    LC_LANGUAGES_XML,
    LC_PROVIDER,
    parse_lc_languages,
)
from libris.modules.bibliographic.application.vocabulary_catalog import (
    VocabularyCatalogService,
    VocabularyImport,
    VocabularyInput,
    VocabularyReleaseInput,
    VocabularySourceInput,
)
from libris.modules.bibliographic.infrastructure.vocabulary_catalog import (
    VocabularyReleaseRecord,
)

MAX_ARTIFACT_BYTES = 10 * 1024 * 1024


def _aware_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("O instante deve incluir timezone.")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Opera releases locais de LC Languages.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare", help="valida e persiste release candidata")
    prepare.add_argument("artifact", type=Path)
    prepare.add_argument("--release-key", required=True)
    prepare.add_argument("--retrieved-at", required=True, type=_aware_datetime)
    prepare.add_argument("--source-version")
    prepare.add_argument("--identifiers", type=Path)
    prepare.add_argument("--translations", type=Path)
    prepare.add_argument("--partial-scope")

    activate = subparsers.add_parser("activate", help="ativa candidata com CAS")
    activate.add_argument("--release-key", required=True)
    activate.add_argument("--expected-active-release-id")

    search = subparsers.add_parser("search", help="consulta somente a release ativa local")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=20)
    return parser


async def _run(args: argparse.Namespace) -> dict[str, object]:
    database = Database(Settings())
    service = VocabularyCatalogService(database.sessions)
    try:
        vocabulary = await service.register(
            VocabularySourceInput(
                key="lc-languages",
                provider_uri=LC_PROVIDER,
                source_uri=LC_LANGUAGES_XML,
                provenance={
                    "maintainer": "Library of Congress",
                    "format": "MARC Code List XML",
                    "importer_version": IMPORTER_VERSION,
                    "rights_status": "pending-institutional-review",
                },
            ),
            VocabularyInput(uri=LC_LANGUAGES_VOCABULARY, status="active"),
            now=getattr(args, "retrieved_at", None) or datetime.now().astimezone(),
        )
        if args.command == "prepare":
            size = args.artifact.stat().st_size
            if size <= 0 or size > MAX_ARTIFACT_BYTES:
                raise ValueError("Artefato vazio ou acima do limite de 10 MiB.")
            artifact = args.artifact.read_bytes()
            complete = args.partial_scope is None
            parsed = parse_lc_languages(
                artifact,
                identifier_sidecar=args.identifiers,
                translation_sidecar=args.translations,
                complete=complete,
            )
            release = await service.import_release(
                vocabulary.id,
                VocabularyImport(
                    idempotency_key=f"lc-languages:{args.release_key}:{parsed.digest}",
                    release=VocabularyReleaseInput(
                        release_key=args.release_key,
                        source_version=args.source_version,
                        source_uri=LC_LANGUAGES_XML,
                        digest=parsed.digest,
                        completeness="complete" if complete else "partial",
                        scope=args.partial_scope,
                        retrieved_at=args.retrieved_at,
                    ),
                    concepts=parsed.concepts,
                ),
                now=args.retrieved_at,
            )
            async with database.sessions() as session:
                active_id = await session.scalar(
                    select(VocabularyReleaseRecord.id).where(
                        VocabularyReleaseRecord.vocabulary_id == vocabulary.id,
                        VocabularyReleaseRecord.status == "active",
                    )
                )
            report = await service.compare_releases(release.id, active_id)
            return {
                "release_id": str(release.id),
                "status": release.status,
                "digest": parsed.digest,
                "concepts": len(parsed.concepts),
                "source_identifier": parsed.source_identifier,
                "changes": report.model_dump(mode="json"),
            }
        if args.command == "activate":
            async with database.sessions() as session:
                candidate_release = await session.scalar(
                    select(VocabularyReleaseRecord).where(
                        VocabularyReleaseRecord.vocabulary_id == vocabulary.id,
                        VocabularyReleaseRecord.release_key == args.release_key,
                    )
                )
            if candidate_release is None:
                raise LookupError("Release candidata não encontrada.")
            from uuid import UUID

            expected = (
                UUID(args.expected_active_release_id) if args.expected_active_release_id else None
            )
            activated = await service.activate_release(
                vocabulary.id, candidate_release.id, expected_active_release_id=expected
            )
            return {"release_id": str(activated.id), "status": activated.status}
        rows = await service.search_active(vocabulary.id, args.query, limit=args.limit)
        return {
            "query": args.query,
            "results": [
                {"uri": uri, "marc": notation, "label": label, "language": language}
                for uri, notation, label, language in rows
            ],
        }
    finally:
        await database.close()


def main() -> None:
    args = _parser().parse_args()
    print(json.dumps(asyncio.run(_run(args)), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
