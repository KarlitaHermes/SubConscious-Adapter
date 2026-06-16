# SubConscious upgrade & test — instructions for Hermes

**You are in the SubConscious-Adapter repo.** This plugin runs inside **Hermes Gateway** on port **8769**. It must be upgraded **together with** [SubConscious-Engine](https://github.com/KarlitaHermes/SubConscious-Engine) — see that repo's `UPGRADE-HERMES-version.md` for the full engine + systemd guide (same procedure, engine-centric paths).

**Goal:** Deploy both repos to current `main`:

1. **Queue delivery** (this repo, `759219e+`) — `POST /inject` accepts `delivery: "queue"`; busy sessions are not interrupted.
2. **Notify gate** (engine repo, `96a4670+`) — engine suppresses redundant nudges (cooldown, `in_progress` ack, routing rules).

**Target commits:**

| Repo | Commit | This repo? |
|------|--------|------------|
| **SubConscious-Adapter** | `772edef` or later | **Yes — you are here** |
| SubConscious-Engine | `96a4670` or later | [Separate repo](https://github.com/KarlitaHermes/SubConscious-Engine) |

**Do not modify** `hermes-agent` / gateway source code. **Do not** run a second engine on **8770** while `subconscious-engine.service` is running.

**Upgrade order:** **this adapter first** → engine systemd service second → integration tests last.

---

## 1. SubConscious-Adapter — upgrade (this repo)

### 1.1 Check what is running now (old version)

```bash
cd /path/to/subconscious-adapter   # typical: ~/workspace/subconscious-adapter or clone below
git log -1 --oneline                 # note for rollback
readlink -f ~/.hermes/plugins/subconscious-adapter   # which checkout gateway loads

curl -s http://127.0.0.1:8769/sessions | head || echo "adapter not responding — gateway down?"
```

**Signs you are on an old build:**

| Check | Old (`139af67` and earlier) | New (`759219e+`) |
|-------|----------------------------|------------------|
| `git log -1` | before `759219e` | `772edef` or newer |
| `POST /inject` with `"delivery":"queue"` | field ignored; always interrupts when busy | `"delivery": "queue"` in response |
| Inject while Hermes mid-task | **Interrupts** current turn | **Enqueues** for next turn |

### 1.2 Pull and link plugin

```bash
cd /path/to/subconscious-adapter
# first time:
# git clone https://github.com/KarlitaHermes/SubConscious-Adapter.git .

git rev-parse HEAD > /tmp/subconscious-adapter-pre-upgrade.commit

git fetch origin
git checkout main
git pull origin main
git log -1 --oneline   # expect 772edef or newer

# Gateway must load THIS checkout (not a stale copy)
ln -sfn "$(pwd)" ~/.hermes/plugins/subconscious-adapter
hermes plugins enable subconscious-adapter   # if not already enabled
```

Confirm `~/.hermes/config.yaml` includes:

```yaml
platforms:
  subconscious:
    enabled: true
    port: 8769
```

### 1.3 Restart gateway

```bash
sudo systemctl restart hermes-gateway
sleep 2
systemctl status hermes-gateway
```

### 1.4 Verify adapter

```bash
curl -s http://127.0.0.1:8769/sessions | head
```

Pick a live `session_id` from the response.

**Queue inject test** (replace `SESSION_ID`):

```bash
curl -s -X POST http://127.0.0.1:8769/inject \
  -H "Content-Type: application/json" \
  -d '{"session_id":"SESSION_ID","text":"[SUBCONSCIOUS ENGINE TEST] queue delivery","delivery":"queue"}'
```

Expect:

```json
{"ok": true, "session_id": "...", "platform": "...", "delivery": "queue"}
```

While Hermes is **busy**, the message must **not** interrupt — it runs on the next turn. When **idle**, it runs immediately.

**Interrupt test** (optional, confirms legacy path still works):

```bash
curl -s -X POST http://127.0.0.1:8769/inject \
  -H "Content-Type: application/json" \
  -d '{"session_id":"SESSION_ID","text":"[SUBCONSCIOUS ENGINE TEST] interrupt","delivery":"interrupt"}'
```

### 1.5 If gateway fails after upgrade

```bash
journalctl -u hermes-gateway -n 80 --no-pager
ls -la ~/.hermes/plugins/subconscious-adapter
hermes plugins list
```

### 1.6 Rollback adapter only

```bash
cd /path/to/subconscious-adapter
git checkout "$(cat /tmp/subconscious-adapter-pre-upgrade.commit)"
# or: git checkout 139af67
ln -sfn "$(pwd)" ~/.hermes/plugins/subconscious-adapter
sudo systemctl restart hermes-gateway
```

---

## 2. SubConscious-Engine — upgrade (other repo)

The engine is **not** in this repository. After adapter is verified on `8769`, upgrade the engine.

**Quick summary** (full steps in [SubConscious-Engine `UPGRADE-HERMES-version.md`](https://github.com/KarlitaHermes/SubConscious-Engine/blob/main/UPGRADE-HERMES-version.md)):

```bash
# Test checkout (pytest only — do not bind 8770)
cd /path/to/subconscious-engine-test
git pull origin main   # 96a4670+
python -m pytest -q

# Production systemd service
cd /home/hermes/workspace/subconscious-engine
git pull origin main   # 96a4670+
source .venv/bin/activate && pip install aiohttp pyyaml && deactivate
sudo systemctl restart subconscious-engine

curl -s http://127.0.0.1:8770/health
curl -s -X POST http://127.0.0.1:8770/ack \
  -H "Content-Type: application/json" \
  -d '{"cooldown_key":"upgrade_probe","status":"done","cooldown_minutes":1}'
```

Engine sends `"delivery": "queue"` on every subconscious inject — **old engine + new adapter** still works (adapter defaults to queue). **New engine + old adapter** does not queue when busy — **both repos must be current**.

---

## 3. Hermes ack protocol (engine `8770`)

Notify gate requires Hermes to ack nudges. Use the script from the **engine** repo:

```bash
chmod +x /home/hermes/workspace/subconscious-engine/hermes/subconscious-engine-nudges/scripts/ack-engine.sh
export SUBCONSCIOUS_ENGINE_URL=http://127.0.0.1:8770

# Start work on a nudge (KEY from inject footer [engine-ack:KEY|in_progress,done])
ack-engine.sh COOLDOWN_KEY in_progress

# Finished
ack-engine.sh COOLDOWN_KEY done --minutes 60 --reset-idle
```

This adapter repo does **not** implement `/ack` — that is engine `8770` only.

---

## 4. Integration test plan (both repos upgraded)

### A. Endpoints up

```bash
curl -s http://127.0.0.1:8769/sessions | head    # adapter (this repo)
curl -s http://127.0.0.1:8770/health              # engine
```

### B. Queue path (adapter + engine)

1. Start a long Hermes task.
2. Trigger an engine nudge (idle, file drop, weather poll, etc.).
3. Confirm: **no interrupt**; inject arrives after current turn.
4. `ack-engine.sh KEY in_progress` then `done`.

### C. Notify gate (engine only)

| Scenario | Expected |
|----------|----------|
| Same `cooldown_key` in cooldown | No second inject |
| `in_progress` ack for key | Same key blocked until `done` |
| Idle nudge while `idle_engine` in progress | No duplicate idle inject |

Engine logs: `journalctl -u subconscious-engine -f`

### D. Adapter-only smoke test

```bash
SESSION_ID="$(curl -s http://127.0.0.1:8769/sessions | python3 -c "import sys,json; s=json.load(sys.stdin).get('sessions',[]); print(s[0]['id'] if s else '')")"
curl -s -X POST http://127.0.0.1:8769/inject \
  -H "Content-Type: application/json" \
  -d "{\"session_id\":\"$SESSION_ID\",\"text\":\"[SUBCONSCIOUS ENGINE TEST] adapter post-upgrade\",\"delivery\":\"queue\"}"
```

---

## 5. Full rollback (both repos)

**Adapter (this repo):**

```bash
cd /path/to/subconscious-adapter
git checkout 139af67
ln -sfn "$(pwd)" ~/.hermes/plugins/subconscious-adapter
sudo systemctl restart hermes-gateway
```

**Engine:**

```bash
cd /home/hermes/workspace/subconscious-engine
git checkout e84119f
sudo systemctl restart subconscious-engine
```

---

## 6. Constraints

- **No changes** to `hermes-agent` — queue uses existing gateway FIFO via this plugin.
- **Adapter port 8769** — engine talks to `adapter.url` in config (default `http://127.0.0.1:8769`).
- **Engine port 8770** — one instance per host (`subconscious-engine.service`).
- Test with throwaway `cooldown_key` values so real idle/weather nudges are not blocked.

**Docs:**

| Repo | File |
|------|------|
| This repo | `VERSION-NOTES.md`, `README.md` |
| Engine | `UPGRADE-HERMES-version.md`, `VERSION-NOTES.md`, `hermes/subconscious-engine-nudges/SKILL.md` |
