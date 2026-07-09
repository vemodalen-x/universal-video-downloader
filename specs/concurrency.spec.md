# concurrency.spec — Multi-session safety

> Several agents or sessions editing one repo must not silently stomp each other. VEMO keeps the ownership state
> machine-readable, and binds each harness session to *its own* task.

## 1. Chat identity & session binding
- Each session gets `chat_id = chat-<YYYYMMDD>-<HHMM>-<3char>`, generated at first task-state write.
- Read-only sessions need no id.
- Stored authoritatively in the task front-matter `owning_chat` (machine-readable).
- **Bind the session** when you create/continue a task:
  `task_state.py bind --session <harness session_id> --task <task-id>`. The hooks pass the harness
  `session_id` to every scope/gate check, so a bound session is checked against **its own task** — not
  against whichever live task happens to have the freshest heartbeat. Unbound sessions fall back to
  freshest-heartbeat (single-task repos never notice).

## 2. Heartbeat & staleness
- `heartbeat` (ISO-8601) updates on every meaningful state write (phase change, acceptance change, exec note).
- A task is **stale** when `now - heartbeat > concurrency.stale_threshold_hours` (default 4).
- Staleness is evaluated during the session-start continuity check, and `vemo doctor` flags stale live
  tasks mechanically (takeover candidates).

## 3. Takeover protocol (ADVISORY protocol + audit trail)
On resume, scan live task front-matter:
1. **Same `owning_chat`** → continue.
2. **Different + stale** → ask: `[STALE] Task <id> last held by <chat> at <ts>. Take over? (Y/N)`. On yes: reassign `owning_chat`, reset heartbeat, log takeover in the task file.
3. **Different + active** → ask with a stronger prompt (force-takeover needs explicit approval).
4. **Silent takeover is forbidden** — every `owning_chat` change must appear in the task's Execution Log
   (auditable in the diff; the judge flags an unlogged reassignment on R2).

*Honest limit:* git has no notion of "the committing session", so ownership at commit time is **not**
mechanically verifiable — this protocol is convention plus an audit trail, and the spec does not claim
otherwise. What IS mechanical: session-bound scope checks (§1), stale detection (`doctor`), and the
front-matter diff that makes every takeover visible in review.

## 4. Thin index (optional)
A `tasks/_index.md` may snapshot live tasks (id, state, owning_chat, heartbeat) for fast discovery,
but the **task file front-matter is authoritative** — the index is a convenience, never the source of truth.

## 5. Why keep it machine-readable
Even where enforcement is impossible (§3 limit), machine-readable ownership means collisions are *detected
and attributed* — a forgetful session is caught by the session-bound scope check, and a takeover leaves a
reviewable trace instead of a mystery.
