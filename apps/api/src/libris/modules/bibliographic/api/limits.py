"""Limites efetivos antes da desserialização FastAPI dos comandos M05.04."""

import json
from collections.abc import Callable, Coroutine

from fastapi import Request, Response
from fastapi.routing import APIRoute
from starlette.types import Message

from libris.core.config import Settings
from libris.modules.bibliographic.domain.errors import CatalogCode, CatalogError


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Propriedade JSON duplicada.")
        result[key] = value
    return result


class BoundedCatalogRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[object, object, Response]]:
        handler = super().get_route_handler()

        async def bounded(request: Request) -> Response:
            if request.method not in {"POST", "PATCH"}:
                return await handler(request)
            settings: Settings = request.app.state.settings
            total = 0

            async def receive() -> Message:
                nonlocal total
                message = await request.receive()
                total += len(message.get("body", b""))
                if total > settings.rdf_max_document_bytes:
                    raise CatalogError(CatalogCode.MALFORMED_REQUEST, "Limite de entrada excedido.")
                return message

            limited = Request(request.scope, receive)
            body = await limited.body()
            # Bodyless commands have no JSON document to pre-parse. Routes that
            # declare a body still let FastAPI report the missing required body,
            # while every non-empty body remains subject to the checks below.
            if not body:
                return await handler(limited)
            try:
                value: object = json.loads(body, object_pairs_hook=unique_object)
            except (ValueError, RecursionError):
                raise CatalogError(CatalogCode.MALFORMED_REQUEST, "JSON inválido.") from None
            pending = [(value, 0)]
            while pending:
                current, depth = pending.pop()
                if depth > 64:
                    raise CatalogError(CatalogCode.MALFORMED_REQUEST, "Profundidade excedida.")
                if isinstance(current, dict):
                    pending.extend((item, depth + 1) for item in current.values())
                elif isinstance(current, list):
                    pending.extend((item, depth + 1) for item in current)
            return await handler(limited)

        return bounded
