import asyncio
from uuid import UUID


class TaskHub:
    def __init__(self) -> None:
        self._queues: dict[UUID, asyncio.Queue[UUID]] = {}

    def connect(self, device_id: UUID) -> asyncio.Queue[UUID]:
        queue: asyncio.Queue[UUID] = asyncio.Queue(maxsize=1)
        self._queues[device_id] = queue
        return queue

    def disconnect(self, device_id: UUID, queue: asyncio.Queue[UUID]) -> None:
        if self._queues.get(device_id) is queue:
            self._queues.pop(device_id, None)

    def publish(self, device_id: UUID, task_id: UUID) -> None:
        queue = self._queues.get(device_id)
        if queue is None:
            return
        if queue.full():
            queue.get_nowait()
            queue.task_done()
        queue.put_nowait(task_id)
