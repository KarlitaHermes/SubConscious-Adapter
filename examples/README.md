# SubConscious-Adapter — working deployment examples

The adapter is small (`__init__.py`, `plugin.yaml`). **Full integration examples** (engine config, state shape, systemd, file map) live in the engine repo:

**[SubConscious-Engine → examples/WORKING-DEPLOYMENT.md](https://github.com/KarlitaHermes/SubConscious-Engine/blob/main/examples/WORKING-DEPLOYMENT.md)**

## What this repo needs on a live host

| Artifact | Repo path | Installed path |
|----------|-----------|----------------|
| Plugin code | `__init__.py` | `~/.hermes/plugins/subconscious-adapter/` (symlink) |
| Plugin manifest | `plugin.yaml` | same directory |
| Hermes config excerpt | `examples/hermes-gateway.snippet.yaml` | merge into `~/.hermes/config.yaml` |

## Install (from adapter checkout)

```bash
cd ~/workspace/subconscious-adapter
git pull origin main
ln -sfn "$(pwd)" ~/.hermes/plugins/subconscious-adapter
```

Merge `examples/hermes-gateway.snippet.yaml` into `~/.hermes/config.yaml`:

```yaml
platforms:
  subconscious:
    enabled: true
    port: 8769
plugins:
  enabled:
    - subconscious-adapter
```

```bash
sudo systemctl restart hermes-gateway
curl -s http://127.0.0.1:8769/sessions | head
```

## `plugin.yaml` (reference)

Shipped at repo root — describes the platform adapter to Hermes:

```yaml
name: subconscious-adapter
version: 1.0.0
kind: platform
```

See root `plugin.yaml` for the full file. No secrets in this repo.

## Pair with engine

Adapter alone is not enough for end-to-end nudges. Engine must point at `adapter.url: http://127.0.0.1:8769` and send `delivery: queue` on injects (`96a4670+`). See `UPGRADE-HERMES-version.md` in both repos.
