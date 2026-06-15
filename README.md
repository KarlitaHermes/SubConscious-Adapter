# SubConscious Adapter for Hermes Gateway

A custom platform adapter plugin that injects messages into existing gateway sessions via REST API. Enables out-of-band event injection that routes through the gateway's normal platform delivery pipeline.

## What It Does

The SubConscious adapter creates a new "subconscious" platform in the Hermes gateway. External services (like an orchestrator) can inject messages into any active session via REST API. The message goes through the gateway's normal processing pipeline, so the agent responds and the response is delivered to the correct platform (Telegram, CLI, Discord, etc.).

## Installation

1. Copy this directory to `~/.hermes/plugins/subconscious-adapter/`
2. Enable the plugin: `hermes plugins enable subconscious-adapter`
3. Add to `~/.hermes/config.yaml`:
   ```yaml
   platforms:
     subconscious:
       enabled: true
       port: 8769
   ```
4. Restart the gateway: `sudo systemctl restart hermes-gateway`

## REST API

### GET /sessions

List active (non-ended, non-cron) sessions.

**Response:**
```json
{
  "sessions": [
    {
      "id": "20260615_075357_b969b9f4",
      "source": "telegram",
      "user_id": "112072229",
      "title": "Home",
      "started_at": 1781406420.08,
      "message_count": 5
    }
  ]
}
```

### POST /inject

Inject a message into an existing session.

**Request:**
```json
{
  "session_id": "20260615_075357_b969b9f4",
  "text": "Hello from the orchestrator!",
  "delivery": "queue"
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `session_id` | yes | Target session id from `GET /sessions` |
| `text` | yes | Message body to inject |
| `delivery` | no | `queue` (default) or `interrupt` |

**`delivery` behavior:**

- **`queue`** (default) — if Hermes is already working on that session, the inject is enqueued for the **next turn** (same FIFO as `/queue`). If the session is idle, it runs immediately. SubConscious Engine nudges use this so subconscious work never interrupts Rev's in-flight tasks.
- **`interrupt`** — always dispatch immediately; interrupts a busy session (legacy behavior).

**Response:**
```json
{
  "ok": true,
  "session_id": "20260615_075357_b969b9f4",
  "platform": "telegram",
  "delivery": "queue"
}
```

## Configuration

| Option | Default | Description |
|--------|---------|-------------|
| `port` | `8769` | Localhost port for the REST API |
| `enabled` | `false` | Enable/disable the adapter |

## Architecture

```
Client (orchestrator)
    │
    ▼ POST /inject
SubConscious Adapter (port 8769)
    │
    ▼ MessageEvent(source=original_platform)
Gateway _handle_message()
    │
    ▼ Agent processes message
    │
    ▼ Response via original platform adapter
Telegram / CLI / Discord / etc.
```

## License

MIT
