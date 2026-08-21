"""Optional tqdm progress wrapper for merge LOD scripts."""

from __future__ import annotations

from typing import Iterable, Iterator, Optional, TypeVar

T = TypeVar("T")


def iter_progress(
    iterable: Iterable[T],
    *,
    total: Optional[int] = None,
    desc: str = "",
    enabled: bool = True,
    unit: str = "it",
    leave: bool = True,
) -> Iterator[T]:
    if not enabled:
        yield from iterable
        return
    try:
        from tqdm import tqdm
    except ImportError:
        yield from iterable
        return
    yield from tqdm(iterable, total=total, desc=desc, unit=unit, leave=leave)
