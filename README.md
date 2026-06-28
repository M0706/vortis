# K/V Store

A Redis-compatible in-memory key-value store written in Python from scratch. It speaks the [RESP protocol](https://redis.io/docs/reference/protocol-spec/) (Redis Serialization Protocol), which means any standard Redis client — `redis-cli`, `redis-py`, `redis-benchmark`, etc. — can connect to it without modification.

---

## Architecture Overview

```
main.py
├── async_tcp.py       — Event-loop TCP server (selector-based, non-blocking I/O)
├── sync_tcp.py        — Synchronous TCP server (single-client, for reference)
├── sync_commands.py   — Command logic: PING, ECHO, SET, GET, DEL + TTL engine
└── resp.py            — RESP parser and encoder
```

The server runs in **async mode by default** (`main.py` calls `AsyncTCPServer().run()`). It uses Python's `selectors` module (`kqueue` on macOS, `epoll` on Linux) to multiplex many clients on a single thread — no threads, no async/await, just an event loop.

### Key Design Points

- **Single-threaded event loop** — no locks needed on the in-memory store.
- **Two-level expiry** — passive expiry on read (`_lookup`) + active expiry via a background cycle (`active_expire_cycle`) that mirrors Redis's `serverCron`. The active cycle samples from a separate TTL index (`expires` dict) so its cost scales with the number of volatile keys, not total keyspace size.
- **RESP pipelining** — the read buffer is drained in a loop, so multiple commands sent in one `recv()` are all handled before yielding back to the selector.
- **Inline command support** — plain text commands (e.g. `PING\r\n`) are accepted alongside full RESP arrays.

---

## Requirements

- Python 3.10+ (uses `X | Y` union type hints)
- No external dependencies for the server itself

For tests:
```
pip install pytest
```

---

## Running the Server

```bash
python main.py
```

The server listens on `127.0.0.1:65432` by default.

```
Listening on 127.0.0.1:65432
```

To switch to the synchronous single-client server (useful for debugging), edit `main.py`:

```python
# Change this:
AsyncTCPServer().run()

# To this:
run_sync_tcp_server()
```

---

## Connecting Clients

### Option 1 — redis-cli

The easiest way. Connect directly:

```bash
redis-cli -p 65432
```

You'll get an interactive shell:

```
127.0.0.1:65432> PING
PONG
127.0.0.1:65432> SET name "Manav"
OK
127.0.0.1:65432> GET name
"Manav"
127.0.0.1:65432> SET session_token "abc123" EX 60
OK
127.0.0.1:65432> GET session_token
"abc123"
127.0.0.1:65432> DEL name
(integer) 1
127.0.0.1:65432> GET name
(nil)
```

### Option 2 — netcat (raw RESP)

Send raw RESP frames directly to verify protocol correctness:

```bash
# PING
printf "*1\r\n\$4\r\nPING\r\n" | nc 127.0.0.1 65432

# SET foo bar
printf "*3\r\n\$3\r\nSET\r\n\$3\r\nfoo\r\n\$3\r\nbar\r\n" | nc 127.0.0.1 65432

# Inline command
printf "PING\r\n" | nc 127.0.0.1 65432
```

### Option 3 — Python (redis-py)

```bash
pip install redis
```

```python
import redis

r = redis.Redis(host="127.0.0.1", port=65432, decode_responses=True)

r.ping()                         # True
r.set("name", "Manav")          # True
r.get("name")                    # 'Manav'
r.set("token", "abc", ex=30)    # True  — expires in 30 seconds
r.get("token")                   # 'abc'
r.delete("name")                 # 1
r.get("name")                    # None
```

### Option 4 — redis-benchmark

The server handles the `CLIENT SETNAME` and `CONFIG GET` handshake that `redis-benchmark` sends, so you can run benchmarks directly:

```bash
redis-benchmark -p 65432 -t set,get -n 10000
```

---

## Supported Commands

| Command | Syntax | Description |
|---|---|---|
| `PING` | `PING [message]` | Returns `PONG`, or echoes the message if provided |
| `ECHO` | `ECHO message` | Returns the message as a bulk string |
| `SET` | `SET key value [EX seconds] [PX milliseconds]` | Set a key. Optional `EX`/`PX` sets a TTL |
| `GET` | `GET key` | Get the value of a key. Returns `nil` if missing or expired |
| `DEL` | `DEL key [key ...]` | Delete one or more keys. Returns count of keys actually deleted |

### TTL Behaviour

- `EX` — time-to-live in **seconds**
- `PX` — time-to-live in **milliseconds**
- A `SET` on an existing key with no TTL **clears** any previous expiry (matches Redis behaviour)
- Zero or negative TTL values are rejected with `-ERR`

```bash
SET counter 100 EX 10    # expires in 10 seconds
SET flag 1 PX 500        # expires in 500 milliseconds
SET key val              # no expiry — overwrites key and clears any prior TTL
```

### Expiry Implementation

Keys are expired via two mechanisms:

1. **Passive** — on every `GET` or `DEL`, the key's expiry is checked and the key is deleted if it has elapsed. No background work needed for keys that are regularly accessed.

2. **Active** — every 100ms, `active_expire_cycle()` runs. It randomly samples up to 20 keys from the TTL index and deletes the expired ones. If more than 25% of the sample is expired, it loops immediately to clean up aggressively. This prevents memory leaks from keys that are never read again.

---

## Running Tests

```bash
pytest test_sync_commands.py -v
```

The test suite covers:

- `PING` — bare, with message, too many args, case-insensitive
- `ECHO` — basic, empty string, wrong arg count
- `SET`/`GET` — basic, overwrite, missing key, spaces in values, multiple keys
- TTL — `EX`, `PX`, expiry via passive deletion, index sync, overwrite clears TTL
- `DEL` — single, multiple, missing, expired key not counted, idempotent
- Active expiry cycle — reclaims untouched keys, leaves live keys alone, ignores keys without TTL
- Protocol edge cases — inline commands, garbled input, unknown commands

```
pytest test_sync_commands.py -v --tb=short
```

---

## Contributing

`master` and `staging` are protected — **nobody pushes to them directly**. All changes go through pull requests, and the merge path is one-directional: feature branch → `staging` → `master`.

### Workflow

1. **Branch off `staging`:**
   ```bash
   git checkout staging
   git pull
   git checkout -b your-feature-branch
   ```
2. **Make your changes** and commit them.
3. **Run the test suite locally** before pushing — CI enforces the same gate:
   ```bash
   pytest
   ```
4. **Push your branch and open a PR into `staging`:**
   ```bash
   git push -u origin your-feature-branch
   ```
   Open the PR with **`staging`** as the base branch (never `master`).
5. **Wait for review and merge.** The repository owner reviews and merges the PR into `staging`.
6. **Promotion to `master`** is handled separately by the owner via a `staging` → `master` PR.

### Merge requirements

Every PR into `staging` and `master` must satisfy:

- ✅ **CI passes** — the `test` GitHub Actions check runs `pytest` on every PR.
- ✅ **Coverage ≥ 90%** — enforced by `--cov-fail-under=90` in `pytest.ini`. The build fails if application coverage drops below this.
- ✅ **Code-owner review** — see [`.github/CODEOWNERS`](.github/CODEOWNERS).

> Direct pushes, force-pushes, and branch deletions are blocked on `master` and `staging`. Only the repository owner can bypass these rules.

---

## Project Structure

```
.
├── main.py                  # Entry point — starts the async server
├── async_tcp.py             # Non-blocking selector-based TCP server
├── sync_tcp.py              # Blocking single-client TCP server (reference)
├── sync_commands.py         # All command handlers + TTL engine
├── resp.py                  # RESP protocol parser and encoder
└── test_sync_commands.py    # pytest test suite
```

---

## Configuration

The host and port are hardcoded at the top of `async_tcp.py` and `sync_tcp.py`:

```python
HOST = "127.0.0.1"
PORT = 65432
```

Change these directly if you need the server to bind on a different interface or port (e.g. `HOST = "0.0.0.0"` to accept connections from other machines).

The active expiry interval is controlled in `async_tcp.py`:

```python
CRON_INTERVAL = 0.1  # seconds — how often the active-expire cycle runs
```

And the sampling parameters in `sync_commands.py`:

```python
KEYS_PER_LOOP = 20       # keys sampled per cycle
ACCEPTABLE_STALE = 0.25  # re-run if more than 25% of the sample was expired
```
