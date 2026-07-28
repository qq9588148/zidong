import asyncio

from champion_follow.repositories.issues import IssueRepository
from champion_follow.services.causal import CausalProcessor
from champion_follow.services.issue_builder import IssueBuilder


class ProcessingCoordinator:
    def __init__(
        self,
        pool,
        *,
        issues=None,
        builder=None,
        causal=None,
    ):
        self.pool = pool
        self.issues = issues or IssueRepository(pool)
        self.builder = builder or IssueBuilder(self.issues)
        self.causal = causal or CausalProcessor(pool)
        self._locks = {}

    async def process(self, *, namespace_id, namespace_version):
        lock = self._locks.setdefault(namespace_id, asyncio.Lock())
        async with lock:
            for issue in await self.issues.finalized_pending_issues(namespace_id):
                await self.builder.build_issue(namespace_id, issue)
            return await self.causal.process_ready(
                namespace_version=namespace_version
            )
