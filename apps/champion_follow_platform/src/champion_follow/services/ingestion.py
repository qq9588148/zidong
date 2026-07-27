from champion_follow.contracts.events import CollectorBatch
from champion_follow.repositories.ingestion import GapDetected, SequenceGap


class IngestionService:
    def __init__(self, repository):
        self.repository = repository

    async def accept(self, batch: CollectorBatch):
        result = await self.repository.ingest(batch)
        if isinstance(result, GapDetected):
            raise SequenceGap(result.highest_contiguous_sequence)
        return result
