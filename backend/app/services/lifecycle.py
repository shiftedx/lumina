"""Cancellation must leave blocking lifecycle work owned until it finishes."""
from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable, Coroutine
from concurrent.futures import Executor, Future, wait
from typing import Any, ParamSpec, TypeVar

P = ParamSpec("P")
T = TypeVar("T")


async def finish_on_cancel(work: Coroutine[Any, Any, T]) -> T:
    task = asyncio.create_task(work)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        if not task.cancelled():
            task.exception()  # retrieve a late failure, preserving the original cancellation
        raise


# Detached work may create another writer before its own last callback returns.
# Register at submission, then drain after the service producers have stopped.
_writers_lock = threading.Lock()
_writer_threads: set[threading.Thread] = set()
_writer_futures: set[Future] = set()


async def run_blocking(work: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    # A server can cancel a request and begin shutdown before awaiting it.
    # Track completion from inside the thread, independent of the request task.
    done: Future[None] = Future()

    def invoke() -> T:
        try:
            return work(*args, **kwargs)
        finally:
            done.set_result(None)

    with _writers_lock:
        _writer_futures.intersection_update(future for future in _writer_futures if not future.done())
        _writer_futures.add(done)
    return await finish_on_cancel(asyncio.to_thread(invoke))


def start_writer(thread: threading.Thread) -> None:
    with _writers_lock:
        _writer_threads.intersection_update(worker for worker in _writer_threads if worker.is_alive())
        thread.start()
        _writer_threads.add(thread)


def submit_writer(executor: Executor, work: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> Future[T]:
    with _writers_lock:
        _writer_futures.intersection_update(future for future in _writer_futures if not future.done())
        future = executor.submit(work, *args, **kwargs)
        _writer_futures.add(future)
        return future


def join_detached_writers() -> bool:
    deadline = time.monotonic() + 10
    while True:
        with _writers_lock:
            _writer_threads.intersection_update(thread for thread in _writer_threads if thread.is_alive())
            _writer_futures.intersection_update(future for future in _writer_futures if not future.done())
            threads, futures = tuple(_writer_threads), tuple(_writer_futures)
        if not threads and not futures:
            return True
        if time.monotonic() >= deadline:
            return False
        for thread in threads:
            thread.join(max(0, deadline - time.monotonic()))
        wait(futures, timeout=max(0, deadline - time.monotonic()))
