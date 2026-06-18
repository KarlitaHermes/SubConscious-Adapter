# Version notes — queue delivery for subconscious injects

**Date:** 2026-06-15  
**Adapter commit:** `759219e` (requires SubConscious-Engine `62e0c9c` or later)

## Summary

`POST /inject` now supports `delivery: "queue"` (default). When the target Hermes session is already running an agent turn, subconscious messages are **enqueued for the next turn** instead of interrupting.

## What changed

| File | Change |
|------|--------|
| `__init__.py` | `delivery` field on `/inject`; `_try_queue_when_busy()` calls gateway `runner._enqueue_fifo()` when session is in `_running_agents` |
| `README.md` | API documentation for `delivery` |

## Why

SubConscious Engine fires nudges (idle, weather, inbox, etc.) while Hermes may be mid-task. Without queue delivery, injects behaved like a new User message and **interrupted** the current turn (`busy_input_mode: interrupt`).

This adapter change uses the gateway's **existing** FIFO queue (same mechanism as `/queue`). No Hermes gateway source code changes required.

## API

```json
POST /inject
{
  "session_id": "...",
  "text": "[SUBCONSCIOUS] ...",
  "delivery": "queue"
}
```

| `delivery` | Behavior when session busy | Behavior when idle |
|------------|---------------------------|-------------------|
| `queue` (default) | Enqueue — next turn | Dispatch immediately |
| `interrupt` | Interrupt current turn | Dispatch immediately |

## Install / upgrade

```bash
cd /path/to/subconscious-adapter
git pull origin main
ln -sfn "$(pwd)" ~/.hermes/plugins/subconscious-adapter
sudo systemctl restart hermes-gateway
```

Also upgrade [SubConscious-Engine](https://github.com/KarlitaHermes/SubConscious-Engine) to `62e0c9c+` and restart the engine so injects send `delivery: "queue"`.

```bash
cd /path/to/subconscious-engine
git pull origin main
sudo systemctl restart subconscious-engine.service
```

## Verify

```bash
curl -s http://127.0.0.1:8769/sessions
curl -s -X POST http://127.0.0.1:8769/inject \
  -H "Content-Type: application/json" \
  -d '{"session_id":"SESSION_ID","text":"test","delivery":"queue"}'
# Expect: {"ok": true, ..., "delivery": "queue"}
```

## Rollback

```bash
git checkout 139af67
sudo systemctl restart hermes-gateway
```

Old adapter ignores the `delivery` field on inject payloads and always interrupts when busy.
