from champion_follow.domain.integrity import evaluate_issue


INTEGRITY_VERSION = "issue-integrity-v1"


class IssueBuilder:
    def __init__(self, repository):
        self.repository = repository

    async def build_issue(self, namespace_id, issue):
        events = await self.repository.load_issue_events(namespace_id, issue)
        unresolved_gap = await self.repository.has_unresolved_gap(namespace_id, issue)
        evaluation = evaluate_issue(
            issue,
            events,
            unresolved_gap=unresolved_gap,
        )
        await self.repository.save_evaluation(
            namespace_id,
            evaluation,
            INTEGRITY_VERSION,
        )
        return evaluation

    async def build_pending(self, namespace_id):
        return tuple(
            [
                await self.build_issue(namespace_id, issue)
                for issue in await self.repository.pending_issues(namespace_id)
            ]
        )
