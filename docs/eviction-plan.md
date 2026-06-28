# Plan: Bounded `Store` — max-size + Random eviction (Strategy pattern, phase 1)

## Context

The library has **no size bound** — a documented footgun (cache use without TTLs
leaks until OOM). We add a configurable limit + eviction, like Redis `maxmemory` +
policies, via a **Strategy pattern** (SOLID) so more policies can be added later
without touching `Store`.

**Scope of THIS change: Random eviction only.** Other policies (LRU, FIFO, LFU,
Volatile-TTL) are deliberately deferred — we prove the framework with the
cheapest, simplest policy first, then add others incrementally.

### Why Random first (and the LRU memory concern)

For an in-memory KV store, **memory per key** and **per-operation latency** dominate.
Random eviction is the ideal first policy because it costs **zero extra bytes per key**
and **zero per-`get` overhead** — no ordering structure, no counters, no access
tracking. It still exercises the entire eviction machinery (sizer, policy interface,
evict-before-insert hook), so it validates the design end to end.

LRU is deferred on purpose: a hand-built linked list would cost ~2 pointer objects
per key (real memory). Python's `OrderedDict` *does* maintain an access-order list
in C for free-ish (no extra Python objects, O(1) `move_to_end`), but it carries a
larger per-entry footprint and nuances we'd rather evaluate deliberately later — not
bundle into the first cut.

## How Redis does Random eviction

Redis's `allkeys-random` / `volatile-random` policies pick a key **at random** from
the keyspace (or the TTL set) and evict it — no recency or frequency tracking. Redis
already samples keys for its LRU/LFU approximations; Random is the degenerate, cheapest
case of that sampling. We mirror this exactly: pick a random key and drop it.

## Design decisions (locked)

- **Pluggable sizer**: limit measured in "units" via a `Sizer` strategy.
  `KeyCountSizer` ships now (`current()` = `len(data)` — O(1), exact, nothing to
  maintain; `cost()` = 1). A `BytesSizer` can be added later with **no Store/API
  change**.
- **Policy selection**: string name — `Store(max_size=N, eviction="random")` via a
  factory. `"noeviction"` also supported (the null-object / off case).
- **At-limit behavior**: evict-then-write (Redis default).
- **Victim selection**: reuse the existing random-sampling helper already in
  `store.py` (`_sample_ttl_keys`) — generalized to sample any mapping.
- **Zero overhead when off**: `max_size is None` (the default, and what the server
  uses) → no policy, no hooks, no size checks. Current hot path byte-for-byte
  unchanged; the server stays exactly as fast.

## Pattern choice & SOLID

- **Strategy** (eviction + sizer): interchangeable algorithms behind `EvictionPolicy`
  / `Sizer` ABCs → **Open/Closed** (adding LRU/LFU/etc. later = new class, never edit
  `Store`), **Dependency Inversion** (`Store` depends on the abstraction).
- **Null Object** (`NoEvictionPolicy`) for the off case so there's no `if policy:`
  branching — though when `max_size is None` we skip the policy path entirely.
- **Single Responsibility**: `Store` owns the keyspace + lock; the policy owns only
  victim selection.
- **No metadata drift**: every removal funnels through the single `_del_key`
  chokepoint, which notifies the policy — so future stateful policies can't desync.
  (Random keeps no state, so this is a no-op now, but the seam is built in correctly
  from the start.)

## Architecture — new module `eviction.py`

```python
EVICTION_SAMPLES = 5            # mirrors Redis maxmemory-samples (used by sampling policies)

class Sizer(ABC):
    @abstractmethod
    def current(self, data) -> int: ...
    @abstractmethod
    def cost(self, key, value) -> int: ...

class KeyCountSizer(Sizer):
    def current(self, data): return len(data)
    def cost(self, key, value): return 1
# BytesSizer(Sizer) -> future, no other change

class EvictionPolicy(ABC):
    tracks_access: bool = False          # if False, Store skips note_access entirely
    def note_write(self, key): ...        # no-op default
    def note_access(self, key): ...       # no-op default
    def note_remove(self, key): ...       # no-op default
    @abstractmethod
    def evict(self, sample: list[str]) -> str | None: ...  # choose victim from sample

class NoEvictionPolicy(EvictionPolicy):
    def evict(self, sample): return None

class RandomPolicy(EvictionPolicy):
    def evict(self, sample): return sample[0] if sample else None
    # sample is already randomly drawn by Store, so sample[0] is a random victim

def make_policy(name: str) -> EvictionPolicy   # 'random' | 'noeviction'; raises on unknown
```

`evict` receives a pre-drawn random `sample` (not the whole keyspace), so no policy
ever scans or copies the full key set — faithful to Redis sampling and cheap.

## Changes to `store.py`

1. **Constructor**: add `max_size: int | None = None`, `eviction: str = "random"`.
   - off: `self._bounded = False` (no policy/sizer created).
   - on: `self._policy = make_policy(eviction)`, `self._sizer = KeyCountSizer()`,
     `self._bounded = True`.
2. **`_sample(pool, n)`**: generalize the existing `_sample_ttl_keys` (islice/random-
   window helper) to sample from any mapping — reused by eviction *and* the expiry
   cycle (DRY).
3. **`_del_key`** (the one removal chokepoint — delete, passive expiry, active sweep,
   eviction all route through it): if bounded, call `self._policy.note_remove(key)`.
4. **`set`** (under the lock): if bounded, evict-before-insert —
   ```
   while self._sizer.current(self.data) + self._sizer.cost(key, value) > max_size:
       if self._evict_one() is None:
           break          # nothing left to evict
   ```
   then insert as today. (Overwrite of an existing key is naturally handled: cost is
   1 and the key already counts toward the limit, so worst case it evicts one extra —
   acceptable for the count sizer; revisited with BytesSizer.)
5. **`_get_nolock`** on hit: `if self._bounded and self._policy.tracks_access:
   self._policy.note_access(key)`. Random has `tracks_access = False`, so the hot
   path adds nothing today.
6. **`_evict_one`**: `sample = self._sample(self.data, EVICTION_SAMPLES)`;
   `victim = self._policy.evict(sample)`; `if victim is None: return None`;
   `self._del_key(victim)`; `return victim`.

All hook calls are inside already-lock-guarded methods → no new locks, no reentrancy
(`_del_key` is the `_nolock` internal).

## Server impact

None. `AsyncTCPServer` / `sync_tcp` build `Store(thread_safe=False)` with no
`max_size` → eviction disabled → identical behavior and latency.

## Tests (`tests/test_eviction.py` + a few in `test_store.py`)

- `KeyCountSizer.current/cost` correctness.
- `make_policy("random")` / `make_policy("noeviction")`; unknown name raises.
- `Store(max_size=N, eviction="random")`: after inserting > N keys, `len(store) == N`
  (limit strictly enforced).
- Eviction removes from **both** `data` and `expires` (invariant `expires ⊆ data`
  holds after eviction).
- `noeviction`: store grows past the soft limit / writes still land (documents the
  off-policy behavior) — or, if we treat noeviction as "reject", assert accordingly
  (decide in impl; default is grow, matching null-object).
- **Zero-overhead-when-off**: default `Store()` has `_bounded is False` and never
  constructs a policy.
- Concurrency: bounded store under many threads stays `<= max_size` and never raises.

## Docs

Replace the README "⚠️ unbounded growth" warning with a **"Bounding memory:
`max_size` + eviction"** section: show `Store(max_size=10_000, eviction="random")`,
explain it's evict-then-write, note Random is the first policy (others coming),
and explain the Redis sampling parallel. Keep it honest that the limit is **key
count** for now (bytes-based limiting is a planned `Sizer`).

## `pytest.ini`

Add `--cov=eviction` so the new module is under the 90% gate.

## Verification

- `pytest` — existing 102 tests pass; new eviction tests pass; 90% gate holds.
- Manual: `s = Store(max_size=3, eviction="random")`; insert k1..k10; assert
  `len(s) == 3` throughout and the invariant `set(s.expires) <= set(s.data)` holds.
- `python scripts/stress_test.py` (optional add): a bounded-store scenario asserting
  `len <= max_size` under a concurrent writer storm.
- Confirm default `Store()` and the server path construct **no** policy/sizer.

## Deferred (explicitly out of scope here)

LRU, FIFO, LFU, Volatile-TTL; bytes-based `Sizer`; `noeviction`-as-reject mode;
exposing `maxmemory` over RESP. The Strategy seam makes each an additive change.
