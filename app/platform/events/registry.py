"""Event handler registry — maps event types to their handler functions.

Handlers must be idempotent: at-least-once delivery will produce duplicates.
Each handler records processed event IDs in a dedupe table.
"""
from collections import defaultdict
from typing import Any, Callable, Type

from app.platform.observability import get_logger

logger = get_logger(__name__)

# event_type_name -> list of handler coroutines
_registry: dict[str, list[Callable[..., Any]]] = defaultdict(list)


def subscribe(event_type: type) -> Callable:
    """Decorator to register a handler for an event type."""
    def decorator(fn: Callable) -> Callable:
        _registry[event_type.__name__].append(fn)
        logger.debug("event_handler_registered", evt=event_type.__name__, handler=fn.__name__)
        return fn
    return decorator


def get_handlers(event_type_name: str) -> list[Callable]:
    return _registry.get(event_type_name, [])
