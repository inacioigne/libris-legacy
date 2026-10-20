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
    LCLanguagesError,
    parse_lc_languages,
)
from libris.modules.bibliographic.application.lc_languages_rdf import (
    parse_lc_languages_rdf,
)
from libris.modules.bibliographic.application.vocabulary_catalog import (
    VocabularyCatalogService,
    VocabularyImport,
    VocabularyInput,
    VocabularyReleaseInput,
    VocabularySourceInput,
)
from libris.modules.bibliographic.infrastructure.vocabulary_catalog import (
    VocabularyRecord,
    VocabularyReleaseRecord,
    VocabularySourceRecord,
)

MAX_ARTIFACT_BYTES = 10 * 1024 * 1024
LC_LANGUAGES_LANDING_PAGE = "https://www.loc.gov/marc/languages/"
SUPPORTED_FORMATS = ("xml", "turtle")


def _aware_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("O instante deve incluir timezone.")
    return parsed


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("O limite/contagem deve ser um inteiro positivo.")
    return parsed


def _artifact_format(path: Path, requested: str | None) -> str:
    if requested is not None:
        return requested
    suffix = path.suffix.lower()
    if suffix == ".xml":
        return "xml"
    if suffix in {".ttl", ".turtle"}:
        return "turtle"
    raise LCLanguagesError("Formato não reconhecido; informe --format xml ou --format turtle.")


def _read_artifact(path: Path, max_bytes: int) -> bytes:
    try:
        with path.open("rb") as artifact_file:
            artifact = artifact_file.read(max_bytes + 1)
    except OSError as error:
        raise LCLanguagesError(f"Não foi possível ler o artefato local: {path}") from error
    if not artifact:
        raise LCLanguagesError("Artefato vazio.")
    if len(artifact) > max_bytes:
        raise LCLanguagesError(f"Artefato excede o limite configurado de {max_bytes} bytes.")
    return artifact


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Opera releases locais de LC Languages.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare", help="valida e persiste release candidata")
    prepare.add_argument("artifact", type=Path)
    prepare.add_argument("--format", choices=SUPPORTED_FORMATS)
    prepare.add_argument("--release-key", required=True)
    prepare.add_argument("--retrieved-at", required=True, type=_aware_datetime)
    prepare.add_argument("--source-version")
    prepare.add_argument("--expected-concepts", type=_positive_int)
    prepare.add_argument("--max-bytes", type=_positive_int, default=MAX_ARTIFACT_BYTES)
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
        async with database.sessions() as session:
            existing_vocabulary = await session.scalar(
                select(VocabularyRecord).where(
                    VocabularyRecord.uri == LC_LANGUAGES_VOCABULARY
                )
            )
            existing_source = (
                await session.get(VocabularySourceRecord, existing_vocabulary.source_id)
                if existing_vocabulary is not None
                else None
            )
        if existing_vocabulary is not None:
            if (
                existing_source is None
                or existing_source.key != "lc-languages"
                or existing_source.provider_uri != LC_PROVIDER
            ):
                raise LCLanguagesError(
                    "Registro LC Languages existente não corresponde à fonte governada."
                )
            source_registration = VocabularySourceInput(
                key=existing_source.key,
                provider_uri=existing_source.provider_uri,
                source_uri=existing_source.source_uri,
                provenance=existing_source.provenance,
            )
        else:
            source_registration = VocabularySourceInput(
                key="lc-languages",
                provider_uri=LC_PROVIDER,
                source_uri=LC_LANGUAGES_LANDING_PAGE,
                provenance={
                    "maintainer": "Library of Congress",
                    "canonical_vocabulary_uri": LC_LANGUAGES_VOCABULARY,
                    "formats": ["MARC Code List XML", "SKOS/RDF Turtle"],
                    "importer_version": IMPORTER_VERSION,
                    "rights_status": "pending-institutional-review",
                },
            )
        vocabulary = await service.register(
            source_registration,
            VocabularyInput(uri=LC_LANGUAGES_VOCABULARY, status="active"),
            now=getattr(args, "retrieved_at", None) or datetime.now().astimezone(),
        )
        if args.command == "prepare":
            artifact_format = _artifact_format(args.artifact, args.format)
            artifact = _read_artifact(args.artifact, args.max_bytes)
            complete = args.partial_scope is None
            if artifact_format == "turtle":
                parsed = parse_lc_languages_rdf(
                    artifact,
                    identifier_sidecar=args.identifiers,
                    translation_sidecar=args.translations,
                    complete=complete,
                    expected_concepts=args.expected_concepts,
                    max_bytes=args.max_bytes,
                )
            else:
                parsed = parse_lc_languages(
                    artifact,
                    identifier_sidecar=args.identifiers,
                    translation_sidecar=args.translations,
                    complete=complete,
                    expected_concepts=args.expected_concepts,
                )
            release_provenance: dict[str, object] = {
                "provider": "Library of Congress",
                "source_uri": parsed.source_uri,
                "format": "MARC Code List XML" if parsed.format == "xml" else "SKOS/RDF Turtle",
                "artifact_filename": args.artifact.name,
                "sha256": parsed.digest,
                "retrieved_at": args.retrieved_at.isoformat(),
                "importer_version": IMPORTER_VERSION,
                "concept_count": len(parsed.concepts),
                "rights_status": "pending-institutional-review",
                "unmapped_rdf_properties": dict(parsed.unmapped_properties),
            }
            if parsed.format == "xml":
                release_provenance["artifact_uri"] = LC_LANGUAGES_XML
            release = await service.import_release(
                vocabulary.id,
                VocabularyImport(
                    idempotency_key=f"lc-languages:{args.release_key}:{parsed.digest}",
                    release=VocabularyReleaseInput(
                        release_key=args.release_key,
                        source_version=args.source_version,
                        source_uri=parsed.source_uri,
                        digest=parsed.digest,
                        provenance=release_provenance,
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
                "source_uri": parsed.source_uri,
                "format": parsed.format,
                "importer_version": IMPORTER_VERSION,
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
