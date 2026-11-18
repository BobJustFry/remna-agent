"""Serialize a monitoring transition with a metrics cycle in this API process."""
import asyncio

monitoring_lock = asyncio.Lock()
generation = 0


def invalidate() -> None:
    global generation
    generation += 1
