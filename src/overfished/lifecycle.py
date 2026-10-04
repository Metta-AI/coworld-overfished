"""One absolute cleanup budget for work owned by an episode process."""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Coroutine
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, TypeVar

CLEANUP_SECONDS = 2.0
T = TypeVar("T")


@dataclass
class Owner:
    tasks: set[asyncio.Task] = field(default_factory=set)
    deadline: float | None = None


current_owner: ContextVar[Owner | None] = ContextVar("current_owner", default=None)


class OwnershipUnsettled(RuntimeError):
    """An acquired reader or writer did not finish within its owner's budget."""


def owned_task(work: Coroutine[Any, Any, T]) -> asyncio.Task[T]:
    task = asyncio.create_task(work)
    owner = current_owner.get()
    if owner is not None:
        owner.tasks.add(task)
        task.add_done_callback(owner.tasks.discard)
    return task


async def settle(tasks: set[asyncio.Task], deadline: float, *, cancel: bool) -> bool:
    """Cancellation is a request; only an observed terminal task establishes a join."""
    pending = {task for task in tasks if not task.done()}
    if cancel:
        for task in pending:
            if not task.cancelling():
                task.cancel()
    if pending:
        _, pending = await asyncio.wait(pending, timeout=max(0, deadline - asyncio.get_running_loop().time()))
    for task in tasks - pending:
        if not task.cancelled():
            task.exception()
    return not pending


def cleanup_deadline() -> float:
    deadline = asyncio.get_running_loop().time() + CLEANUP_SECONDS
    owner = current_owner.get()
    return min(deadline, owner.deadline) if owner is not None and owner.deadline is not None else deadline


async def run_owned(work: Coroutine[Any, Any, T]) -> T | None:
    owner = Owner()
    token = current_owner.set(owner)
    loop = asyncio.get_running_loop()
    stopping = asyncio.Event()
    task = asyncio.create_task(work)

    stop_requested = False

    def receive_signal(signum: int, frame: Any) -> None:
        nonlocal stop_requested
        if not stop_requested:
            stop_requested = True
            owner.deadline = loop.time() + CLEANUP_SECONDS
            loop.call_soon_threadsafe(stopping.set)

    previous = {sig: signal.signal(sig, receive_signal) for sig in (signal.SIGTERM, signal.SIGINT)}
    signal_task = asyncio.create_task(stopping.wait())
    try:
        done, _ = await asyncio.wait({task, signal_task}, return_when=asyncio.FIRST_COMPLETED)
        if task not in done:
            assert owner.deadline is not None
            if not await settle(owner.tasks | {task}, owner.deadline, cancel=True):
                raise OwnershipUnsettled("signal shutdown left owned work unresolved")
        if task.cancelled() and stopping.is_set():
            return None
        return task.result()
    finally:
        if stopping.is_set():
            for sig in previous:
                signal.signal(sig, signal.SIG_IGN)
        else:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
        joined = await settle(owner.tasks | {signal_task}, cleanup_deadline(), cancel=True)
        current_owner.reset(token)
        if not joined:
            raise OwnershipUnsettled("process cleanup left owned work unresolved")


def main_owned(work: Coroutine[Any, Any, T]) -> T | None:
    """Avoid asyncio.run's unbounded cancellation sweep after an unresolved join."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(run_owned(work))
    finally:
        loop.close()
