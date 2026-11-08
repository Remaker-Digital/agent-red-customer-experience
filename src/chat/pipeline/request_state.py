# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""Per-request attributes for the shared ChatPipeline (architecture review S7)."""

from __future__ import annotations

import contextvars
from typing import Any


class RequestScoped:
    """An attribute whose value belongs to the current request, not the instance.

    One ChatPipeline serves every request. Values stored on ``self`` were
    shared, so a request that resumed after an ``await`` could read another
    request's tenant, tenant document or preferences. Here each value lives in
    a ContextVar, keyed by instance. Starlette runs each request in its own
    task, and each task has its own copy of the context, so concurrent
    requests see only their own values. A write replaces the mapping instead
    of changing it in place, so no other context ever sees it.

    Reading an unset value raises AttributeError, so
    ``getattr(obj, name, default)`` keeps returning its default.
    """

    def __set_name__(self, owner: type, name: str) -> None:
        self._name = name
        self._var: contextvars.ContextVar[dict[int, Any]] = contextvars.ContextVar(f"{owner.__qualname__}.{name}")

    def __get__(self, obj: Any, objtype: type | None = None) -> Any:
        if obj is None:
            return self
        try:
            return self._var.get({})[id(obj)]
        except KeyError:
            raise AttributeError(self._name) from None

    def __set__(self, obj: Any, value: Any) -> None:
        values = dict(self._var.get({}))
        values[id(obj)] = value
        self._var.set(values)
