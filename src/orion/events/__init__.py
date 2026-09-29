"""ERP-neutral event intake and bounded continuous-learning primitives."""

from .contracts import (
    EnterpriseEvent,
    EnterpriseEventKind,
    LearningTrigger,
    TriggerKind,
    route_enterprise_event,
)
from .queue import (
    EnqueueDisposition,
    EnqueueResult,
    EventClaim,
    EventQueueConflictError,
    EventQueueIntegrityError,
    SQLiteEventQueue,
)
from .worker import (
    EventWorkDisposition,
    EventWorkerBounds,
    EventWorkerReport,
    EventWorkResult,
    run_event_worker,
)

__all__ = [
    "EnqueueDisposition",
    "EnqueueResult",
    "EnterpriseEvent",
    "EnterpriseEventKind",
    "EventClaim",
    "EventQueueConflictError",
    "EventQueueIntegrityError",
    "EventWorkDisposition",
    "EventWorkResult",
    "EventWorkerBounds",
    "EventWorkerReport",
    "LearningTrigger",
    "SQLiteEventQueue",
    "TriggerKind",
    "route_enterprise_event",
    "run_event_worker",
]
