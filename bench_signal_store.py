"""Benchmark: đo lường hiệu năng bulk_update vs update tuần tự trên SignalStore.

So sánh thời gian cập nhật N signal khi:
  - update(): acquire lock N lần  → O(N) lock overhead
  - bulk_update(): acquire lock 1 lần → O(1) lock overhead
"""

from __future__ import annotations

import asyncio
import time

from src.core.signal_store import SignalStore


async def bench_individual(store: SignalStore, signals: dict[str, float], rounds: int) -> float:
    """Benchmark: gọi update() riêng lẻ cho từng signal."""
    start = time.perf_counter()
    for _ in range(rounds):
        ts = time.time()
        for name, value in signals.items():
            await store.update(name, value, timestamp=ts)
    return time.perf_counter() - start


async def bench_bulk(store: SignalStore, signals: dict[str, float], rounds: int) -> float:
    """Benchmark: gọi bulk_update() cho tất cả signal cùng lúc."""
    start = time.perf_counter()
    for _ in range(rounds):
        ts = time.time()
        await store.bulk_update(signals, timestamp=ts)
    return time.perf_counter() - start


async def main() -> None:
    sizes = [10, 50, 200]
    rounds = 500

    for n in sizes:
        signals = {f"Signal_{i}": float(i) for i in range(n)}
        print(f"\n--- {n} signals x {rounds} rounds ---")

        store_a = SignalStore()
        t_individual = await bench_individual(store_a, signals, rounds)

        store_b = SignalStore()
        t_bulk = await bench_bulk(store_b, signals, rounds)

        speedup = t_individual / t_bulk if t_bulk > 0 else float("inf")
        saved_pct = (1 - t_bulk / t_individual) * 100 if t_individual > 0 else 0

        print(f"  update()      : {t_individual:.4f}s")
        print(f"  bulk_update() : {t_bulk:.4f}s")
        print(f"  Tăng tốc      : {speedup:.2f}x (giảm {saved_pct:.1f}%)")


if __name__ == "__main__":
    asyncio.run(main())
