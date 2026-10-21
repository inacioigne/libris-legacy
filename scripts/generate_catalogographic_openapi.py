"""Baseline OpenAPI real: geração inicial exclusiva e --check somente leitura."""

import argparse
import difflib
import json
import os
import sys
from pathlib import Path
from typing import cast
from unittest.mock import patch

from libris.core.config import Settings

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "docs/architecture/catalogographic-openapi-v2-workspace.json"
type Json = dict[str, Json] | list[Json] | str | int | float | bool | None


def openapi_document() -> dict[str, Json]:
    # main also constructs its module-level ASGI app. Isolate both factories from
    # process settings and .env, without starting lifespan or replacing any router.
    with patch.dict(os.environ, {}, clear=True), patch.dict(Settings.model_config, env_file=None):
        from libris.main import create_app

        app = create_app(Settings(_env_file=None, app_env="test"))
        document = cast(dict[str, Json], app.openapi())
    validate_document(document)
    return document


def validate_document(document: object) -> None:
    if not isinstance(document, dict) or not all(
        key in document for key in ("openapi", "info", "paths", "components")
    ):
        raise ValueError("Documento OpenAPI inválido: objeto/estrutura obrigatória ausente.")
    if (
        not isinstance(document["openapi"], str)
        or not document["openapi"].startswith("3.")
        or not all(isinstance(document[key], dict) for key in ("info", "paths", "components"))
    ):
        raise ValueError("Documento OpenAPI inválido: versão ou objetos obrigatórios inválidos.")

    def visit(value: object) -> None:
        if isinstance(value, dict):
            reference = value.get("$ref")
            if reference is not None:
                if not isinstance(reference, str) or not reference.startswith("#/"):
                    raise ValueError(f"Referência não local: {reference}")
                target: object = document
                for token in reference[2:].split("/"):
                    token = token.replace("~1", "/").replace("~0", "~")
                    if not isinstance(target, dict) or token not in target:
                        raise ValueError(f"Referência ausente: {reference}")
                    target = target[token]
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(document)


def serialize(document: dict[str, Json]) -> str:
    return json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def verify(path: Path, current: str) -> bool:
    try:
        expected = path.read_bytes().decode("utf-8")
        validate_document(json.loads(expected))
    except (OSError, UnicodeError, ValueError) as error:
        print(f"Baseline ausente ou inválida: {path.name}: {error}", file=sys.stderr)
        return False
    if expected == current:
        return True
    print(f"Drift OpenAPI: {path.name}; revisão contratual obrigatória.", file=sys.stderr)
    sys.stderr.writelines(difflib.unified_diff(
        expected.splitlines(keepends=True), current.splitlines(keepends=True),
        fromfile=f"baseline/{path.name}", tofile="runtime/openapi.json",
    ))
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Compara sem escrever.")
    mode.add_argument("--generate", action="store_true", help="Cria somente arquivo ausente.")
    parser.add_argument("--output", type=Path, default=BASELINE)
    args = parser.parse_args()
    current = serialize(openapi_document())
    if args.check:
        if not verify(args.output, current):
            return 1
        print("Baseline OpenAPI verificada; nenhum arquivo alterado.")
        return 0
    try:
        with args.output.open("x", encoding="utf-8", newline="\n") as output:
            output.write(current)
    except OSError as error:
        print(f"Geração recusada: {args.output.name}: {error}. "
              "Preservar histórico; criar novo artefato após revisão explícita.", file=sys.stderr)
        return 1
    print(f"Baseline OpenAPI criada: {args.output.name}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
