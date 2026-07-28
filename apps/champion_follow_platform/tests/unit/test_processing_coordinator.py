import asyncio

import pytest

from champion_follow.services.processing import ProcessingCoordinator


@pytest.mark.asyncio
async def test_processing_coordinator_builds_only_finalized_issues_before_causal_update():
    calls = []

    class Issues:
        async def finalized_pending_issues(self, namespace_id):
            calls.append(("ready", namespace_id))
            return ("2607290001", "2607290002")

    class Builder:
        async def build_issue(self, namespace_id, issue):
            calls.append(("build", namespace_id, issue))

    class Causal:
        async def process_ready(self, *, namespace_version):
            calls.append(("causal", namespace_version))
            return ("processed", "excluded")

    coordinator = ProcessingCoordinator(
        pool=object(),
        issues=Issues(),
        builder=Builder(),
        causal=Causal(),
    )

    result = await coordinator.process(
        namespace_id="namespace-id",
        namespace_version="actor-hmac-v1",
    )

    assert result == ("processed", "excluded")
    assert calls == [
        ("ready", "namespace-id"),
        ("build", "namespace-id", "2607290001"),
        ("build", "namespace-id", "2607290002"),
        ("causal", "actor-hmac-v1"),
    ]


@pytest.mark.asyncio
async def test_processing_coordinator_serializes_one_namespace():
    active = 0
    maximum = 0

    class Issues:
        async def finalized_pending_issues(self, _namespace_id):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0)
            active -= 1
            return ()

    class Causal:
        async def process_ready(self, *, namespace_version):
            return (namespace_version,)

    coordinator = ProcessingCoordinator(
        pool=object(),
        issues=Issues(),
        builder=object(),
        causal=Causal(),
    )

    await asyncio.gather(
        coordinator.process(
            namespace_id="same-namespace",
            namespace_version="actor-hmac-v1",
        ),
        coordinator.process(
            namespace_id="same-namespace",
            namespace_version="actor-hmac-v1",
        ),
    )

    assert maximum == 1
