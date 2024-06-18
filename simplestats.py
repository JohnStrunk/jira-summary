"""Collect some simple performance statistics"""

import atexit
import time
from dataclasses import dataclass
from sys import stderr
from typing import Callable, ParamSpec, TextIO, TypeVar


class Timer:
    """
    Collect performance statistics for a named block of code.

    - Auto start/stop via `with`:

        ```python
        with Timer("my code"):
            # code to time
        ```

    - Manual start/stop:

        ```python
        t = Timer("my code")
        t.start()
        # code to time
        t.stop()
        ```
    """

    @dataclass
    class Stats:
        """Statistics tracked for each timer"""

        name: str
        count: int = 0
        elapsed_ns: int = 0

    # Database of all timer statistics
    _db: dict[str, Stats] = {}

    @classmethod
    def clear(cls) -> None:
        """Clear the timer statistics"""
        cls._db.clear()

    @classmethod
    def dump(cls, out: TextIO = stderr) -> None:
        """Dump the timer statistics"""
        if not cls._db:
            return  # No timers have been used
        name_size = max(len(t.name) for t in cls._db.values()) + 1
        header = f"{'Name':<{name_size}} {'Count':>8} {'Avg':>8}  {'Total':>10}"
        print(header, file=out)
        print("-" * len(header), file=out)
        for t in cls._db.values():
            elapsed_s = t.elapsed_ns / 1000000000
            print(
                f"{t.name+":":<{name_size}} {t.count:>8}"
                + f" {elapsed_s/t.count:>8.3f}s {elapsed_s:>10.3f}s",
                file=out,
            )

    @classmethod
    def stats(cls, name: str) -> Stats:
        """Get the statistics for a named timer"""
        return cls._db.get(name) or cls.Stats(name)

    def _save(self) -> None:
        stats = Timer._db.get(self.name) or Timer.Stats(self.name)
        stats.count += 1
        stats.elapsed_ns += self.elapsed_ns
        Timer._db[self.name] = stats

    def __init__(self, name: str, autostart: bool = False):
        self.name = name
        self._start = None
        self.elapsed_ns = 0
        if autostart:
            self.start()

    def start(self):
        """Start the timer"""
        self._start = time.perf_counter_ns()

    def stop(self):
        """Stop the timer"""
        stop = time.perf_counter_ns()
        if self._start is not None:
            self.elapsed_ns += stop - self._start
            self._start = None
            self._save()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()


_P = ParamSpec("_P")
_T = TypeVar("_T")


def measure_function(func: Callable[_P, _T]) -> Callable[_P, _T]:
    """
    Decorator to measure the time spent in a function.

    The timer will be automatically named after the decorated function.

    ```python
    @measure_function
    def my_function():
        # code to time
    ```
    """

    def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _T:
        with Timer(func.__qualname__):
            return func(*args, **kwargs)

    return wrapper


# Automatically dump the timer statistics to stderr when the program exits
atexit.register(Timer.dump)
