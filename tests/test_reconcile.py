from types import SimpleNamespace
from uuid import uuid4

from fli.queue import MemoryQueue
from fli.reconcile import blob_scope_ids, enqueue_scopes


async def test_discovery_includes_deleted_orphan_scopes_without_cross_library_queries():
    existing, orphan = str(uuid4()), str(uuid4())

    class Container:
        async def walk_blobs(self, **kwargs):
            assert kwargs == {"delimiter": "/"}
            for name in [existing + "/", orphan + "/", "other/", "../", existing, orphan + "/nested/"]:
                yield SimpleNamespace(name=name)

    queue = MemoryQueue()
    await enqueue_scopes(queue, [existing, existing], blob_scope_ids(Container()))
    assert queue.sent == [(key, {"type": "reconcileLibrary", "libraryId": key}) for key in [existing, orphan]]


async def test_failed_enqueue_can_be_retried_without_losing_discovered_scope():
    import pytest

    orphan = str(uuid4())

    class Container:
        async def walk_blobs(self, **kwargs):
            yield SimpleNamespace(name=orphan + "/")

    class Queue(MemoryQueue):
        async def send(self, message, *, session_id):
            if not self.sent:
                self.sent.append((session_id, message))
                raise RuntimeError("ambiguous send")
            await super().send(message, session_id=session_id)

    queue = Queue()
    with pytest.raises(RuntimeError, match="ambiguous send"):
        await enqueue_scopes(queue, [], blob_scope_ids(Container()))
    await enqueue_scopes(queue, [], blob_scope_ids(Container()))
    assert len(queue.sent) == 2
    assert queue.sent[0] == queue.sent[1]  # at-least-once delivery; existing reconcile handler is idempotent
