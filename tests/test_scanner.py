from __future__ import annotations

import time

from dexscreener_cli.scanner import HotScanner


def test_prune_histories_drops_stale_and_excess_entries() -> None:
    scanner = HotScanner(object())  # type: ignore[arg-type]
    now_s = time.time()
    stale_ts = now_s - scanner._history_ttl_seconds - 10

    scanner._boost_history = {
        ("solana", "stale"): (stale_ts, 1.0),
        ("solana", "fresh"): (now_s, 2.0),
    }
    scanner._momentum_history = {
        ("solana", "stale"): [(stale_ts, 1.0)],
        ("solana", "fresh"): [(now_s, 2.0)],
    }
    scanner._max_history_keys = 1

    scanner._prune_histories(now_s)

    assert ("solana", "stale") not in scanner._boost_history
    assert ("solana", "stale") not in scanner._momentum_history
    assert list(scanner._boost_history) == [("solana", "fresh")]
    assert list(scanner._momentum_history) == [("solana", "fresh")]
