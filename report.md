# PDC Assignment Report
## Building Resilient Distributed Systems — StudySync Case Study
**Name:** Anas | **Student ID:** Bscs23190

---

# Part 1: Analysing the Mess

StudySync's MVP is a naive distributed system where every component communicates synchronously over HTTP with no concurrency controls, no idempotency guarantees, and no fault isolation. Three distinct failure modes emerge directly from these omissions.

---

### Problem 1 — Lost Update (Synchronization Bug)

**Root cause: no version predicate on concurrent writes.**

When two users simultaneously open the same shared document, both send a `GET /document/:id` request. The database returns the same snapshot to both. Each user edits independently and then clicks Save. Both clients POST their changes back to the server.

The backend executes a plain:

```sql
UPDATE documents SET content = ? WHERE id = ?
```

There is no version check. Whichever HTTP request arrives *last* silently overwrites the first. This is the **Lost Update anomaly** — a classic race condition arising from an unprotected read-modify-write cycle. The database cannot detect this because it only ever sees one writer at a time; it has no knowledge of the concurrent read that happened before.

---

### Problem 2 — Dropped Webhook (Coordination Bug)

**Root cause: stateless, fire-and-forget webhook handler with no idempotency or retry.**

When a user cancels their Clerk subscription, Clerk fires a single HTTP POST to the StudySync webhook endpoint. The current handler processes the event synchronously in memory and returns `200 OK`. There is no:
- persistence of the raw event before processing
- idempotency key check to handle duplicate deliveries
- retry configuration on Clerk's side

If the server is restarting, the network drops the packet, or the handler throws an exception before completing the database update, the delivery attempt fails silently. Clerk marks the webhook as failed but — without retry configuration — never resends it. The `is_premium` flag in the database remains `true` indefinitely. The two systems are permanently **out of sync**, a failure of distributed coordination.

---

### Problem 3 — Synchronous LLM Call (Fault Tolerance Bug)

**Root cause: a blocking external call with no timeout, no fallback, and no isolation.**

The `/api/ask` endpoint calls the LLM API using a synchronous HTTP client with no timeout configured. When the LLM provider is degraded, each request holds a Uvicorn worker thread open for the full OS socket timeout — potentially 60+ seconds.

Uvicorn's worker pool is finite (typically 4–8 workers by default). A handful of concurrent slow LLM requests **exhausts all available workers**, so every other request in the application — including health checks, document saves, and authentication — begins queuing and eventually timing out too. One upstream service's instability cascades into a **full application outage**. This is a textbook single point of failure with no circuit isolation.

---
---

# Part 2: Designing a Better System

---

## 2.1 Sync Fix — Optimistic Locking with Version Numbers

**Approach:** Add an integer `version` column to the `documents` table. Every read returns the current version alongside the content. Every write becomes a **conditional update** using that version as a predicate.

```sql
-- Read
SELECT id, content, version FROM documents WHERE id = ?;

-- Write (only succeeds if nobody else saved since you read)
UPDATE documents
SET    content = ?, version = version + 1
WHERE  id = ? AND version = ?;
-- If 0 rows affected → 409 Conflict
```

If the `WHERE version = ?` predicate matches zero rows (because another writer incremented the version first), the backend returns **HTTP 409 Conflict**. The client reloads the latest version, re-applies their change on top, and retries. No locks are held between the read and write, so throughput remains high.

---

### UML Sequence Diagram — Two Concurrent Writers

```
  User A              Backend                Database            User B
    |                    |                       |                  |
    |── GET /doc ────────>|                       |                  |
    |                    |── SELECT (ver=5) ─────>|                  |
    |                    |<──── doc, ver=5 ───────|                  |
    |<── doc v5 ─────────|                       |                  |
    |                    |                       |<── GET /doc ──────|
    |                    |                       |── SELECT ────────>|
    |                    |                       |<── doc, ver=5 ────|
    |                    |                       |── doc v5 ────────>|
    |                    |                       |                  |
    | [User A saves first]                       |                  |
    |── POST /doc ────────>                      |                  |
    |   {ver=5, data}    |── UPDATE WHERE ver=5 ->|                  |
    |                    |<── 1 row (ver → 6) ───|                  |
    |<── 200 OK, ver=6 ──|                       |                  |
    |                    |                       |                  |
    | [User B saves with stale ver=5]            |                  |
    |                    |<── POST {ver=5, data} ─────────────────── |
    |                    |── UPDATE WHERE ver=5 ->|                  |
    |                    |<── 0 rows (mismatch!) ─|                  |
    |                    |── 409 Conflict ─────────────────────────> |
    |                    |   "Please reload"      |                  |
```

User B reloads (fetches ver=6), merges their edit, and retries with `ver=6` — which then succeeds.

---

## 2.2 Coordination Fix — Idempotent Webhook Handler + Dead-Letter Queue

**Three layers of protection:**

**Layer 1 — Idempotency keys.**
Every Clerk event carries a unique `svix-id` header. Before any processing, the handler queries a `processed_events` table:

```python
if db.exists("SELECT 1 FROM processed_events WHERE event_id = ?", svix_id):
    return Response(status_code=200)  # duplicate — safe to ignore

# Process event + insert event_id in one transaction
with db.transaction():
    cancel_subscription(user_id)
    db.execute("INSERT INTO processed_events (event_id) VALUES (?)", svix_id)
```

This guarantees **exactly-once semantics** even if Clerk retries the delivery.

**Layer 2 — Durable event log.**
Persist the raw webhook payload to the database *before* processing. If the handler crashes mid-execution, the event can be replayed from the log.

**Layer 3 — Dead-Letter Queue (DLQ).**
Configure Clerk (or an AWS SQS queue in front of the handler) to retry failed deliveries with exponential back-off — e.g., 3 attempts over 1 hour. Events that exhaust all retries land in a DLQ, triggering an ops alert for manual review and replay. No cancellation can ever be silently lost.

---

## 2.3 Fault Tolerance Fix — Circuit Breaker Pattern

Wrap every LLM call in a **Circuit Breaker** that tracks consecutive failures and short-circuits requests when the upstream is clearly down.

| State      | Behaviour |
|------------|-----------|
| **CLOSED** | Normal — requests pass through to the LLM. Failures are counted. |
| **OPEN**   | Fail-fast — requests return a static fallback instantly (< 1 ms). No worker thread is blocked. |
| **HALF-OPEN** | After the recovery timeout, one probe request is allowed through. Success → CLOSED. Failure → OPEN again. |

**Fallback response** (served when OPEN):
> *"Our AI assistant is temporarily unavailable. Please try again in a few moments."*

**LLM call timeout** is set to 5 seconds. A single slow response is recorded as a failure rather than hanging a worker for 60 seconds.

```
Failure threshold : 3 consecutive failures → OPEN
Recovery timeout  : 20 seconds → HALF-OPEN probe
```

---

## 2.4 CAP Theorem Trade-offs

The CAP theorem states that under a **network partition (P)**, a distributed system must choose between **Consistency (C)** and **Availability (A)**.

| Fix | C | A | Trade-off |
|-----|---|---|-----------|
| **Optimistic Locking** | ✅ Strong | ⚠️ Reduced | Consistency is prioritised. Conflicting writers receive a 409 and must retry, briefly reducing availability for that writer. Data is never silently lost. |
| **Webhook + DLQ** | ⚠️ Eventual | ✅ High | The system stays fully available during outages. Consistency is eventual — the subscription state may lag by seconds to minutes during retries — but no event is ever dropped. |
| **Circuit Breaker** | ⚠️ Stale fallback | ✅ High | Availability is maximised. While the circuit is OPEN, clients receive a static fallback instead of a live LLM answer — a deliberate sacrifice of consistency to keep the server responsive. |

**Summary:** The optimistic locking fix is **CP** (trades availability for consistency); the webhook and circuit-breaker fixes are **AP** (trade consistency for availability). StudySync's domain — educational content — tolerates brief staleness far better than data loss or server-wide outages, making the AP bias in fixes 2 and 3 the correct engineering call.

---

*End of Report*