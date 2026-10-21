"""Security observation never substitutes for the M03 transactional audit."""

import asyncio

import pytest
from libris.modules.bibliographic.application.history import HistoryService
from libris.modules.bibliographic.domain.models import Provenance, ResourceIdentity
from libris.modules.identity.application.authorization import AuthorizationGuard
from libris.modules.identity.domain.exceptions import PermissionDenied
from libris.modules.security_audit.domain.events import Outcome, SecurityEvent
from test_audit_postgres import actor, context, graph_for

pytestmark = pytest.mark.integration


class Capture:
    def __init__(self, *, fail: bool = False) -> None:
        self.events: list[SecurityEvent] = []
        self.fail = fail

    def emit(self, event: SecurityEvent) -> None:
        self.events.append(event)
        if self.fail:
            raise OSError("SECRET-SINK-FAILURE")


@pytest.mark.parametrize("sink_fails", [True, False])
def test_security_sink_preserves_m03_commits_and_denials(sink_fails: bool) -> None:
    async def scenario() -> None:
        async with context() as (sessions, service):
            capture = Capture(fail=sink_fails)
            service.authorization = AuthorizationGuard(capture)
            identity = ResourceIdentity.create("https://security41.example.invalid/resources")
            graph, provenance = graph_for(identity), Provenance("fixture", "audit41")
            human = actor()
            revision = await service.create(identity, graph, provenance, actor=human)
            assert capture.events[0].outcome == Outcome.SUCCESS
            assert capture.events[0].resource_id == identity.internal_id
            history = HistoryService(sessions)
            before = await history.read_editorial(identity.internal_id, actor=human)
            with pytest.raises(PermissionDenied):
                await service.revise(
                    identity.internal_id, 1, graph, provenance, actor=actor("libris-reader")
                )
            assert capture.events[-1].outcome == Outcome.DENIED
            after = await history.read_editorial(identity.internal_id, actor=human)
            assert before == after
            assert len(await history.list_revisions(identity.internal_id, actor=human)) == 1
            events = await history.list_events(identity.internal_id, actor=human)
            assert len(events) == 1
            assert events[0].revision_number == revision.number == 1
            assert events[0].event_type.value == "record.created"

    asyncio.run(scenario())
