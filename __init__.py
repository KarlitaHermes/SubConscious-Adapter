"""
SubConscious Adapter Plugin for Hermes Gateway

A custom platform adapter that injects messages into existing gateway sessions
via REST API. Enables out-of-band event injection that routes through the
gateway's normal platform delivery pipeline.

Usage:
    1. Place this directory in ~/.hermes/plugins/subconscious-adapter/
    2. Enable with: hermes plugins enable subconscious-adapter
    3. Add to config.yaml platforms section:
       platforms:
         subconscious:
           enabled: true
           port: 8769

REST API:
    GET /sessions - List active sessions with their source platform
    POST /inject  - Inject a message into a specific session

The adapter:
1. Starts an aiohttp server on localhost:8769
2. Accepts injection requests via REST API
3. Creates a MessageEvent with the target session's original platform
4. Dispatches through the gateway's handle_message for normal processing
5. Delivers the agent's response to the correct platform (Telegram, CLI, etc.)
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Optional

from hermes_constants import get_hermes_home

logger = logging.getLogger("plugin.subconscious")

DEFAULT_PORT = 8769


def _state_db_path() -> Path:
    return get_hermes_home() / "state.db"


def _sessions_json_path() -> Path:
    return get_hermes_home() / "sessions" / "sessions.json"


def register(ctx):
    """Register the SubConscious platform adapter."""

    def adapter_factory(config, base_cls=None):
        from gateway.config import Platform
        from gateway.platforms.base import (
            BasePlatformAdapter,
            MessageEvent,
            MessageType,
        )
        from gateway.session import SessionSource

        _base_cls = base_cls or BasePlatformAdapter

        class SubConsciousAdapter(_base_cls):
            """Gateway platform adapter for out-of-band session injection."""

            gateway_runner = None  # injected by gateway/run.py after creation

            def __init__(self, config):
                super().__init__(config, Platform("subconscious"))
                self._runner = None
                self._site = None
                extra = getattr(config, "extra", None) or {}
                self._port = int(extra.get("port", DEFAULT_PORT))

            async def connect(self, *, is_reconnect: bool = False) -> bool:
                from aiohttp import web

                app = web.Application()
                app.router.add_get("/sessions", self._handle_list_sessions)
                app.router.add_post("/inject", self._handle_inject)

                self._runner = web.AppRunner(app)
                await self._runner.setup()
                self._site = web.TCPSite(self._runner, "127.0.0.1", self._port)
                await self._site.start()
                logger.info("SubConscious adapter listening on 127.0.0.1:%d", self._port)
                self._mark_connected()
                return True

            async def disconnect(self):
                if self._runner:
                    await self._runner.cleanup()
                    self._runner = None
                    self._site = None
                    logger.info("SubConscious adapter stopped")

            async def get_chat_info(self, chat_id: str) -> dict:
                return {"name": chat_id, "type": "dm"}

            async def send(self, chat_id, content, reply_to=None, metadata=None):
                return {"success": True, "message_id": None}

            def _lookup_session_origin(self, session_id: str) -> Optional[SessionSource]:
                """Resolve the original SessionSource for a persisted session ID."""
                # Try gateway's session store first (most accurate)
                runner = getattr(self, "gateway_runner", None)
                if runner is not None:
                    store = getattr(runner, "session_store", None)
                    lookup = getattr(store, "lookup_by_session_id", None)
                    if callable(lookup):
                        entry = lookup(session_id)
                        origin = getattr(entry, "origin", None) if entry else None
                        if origin is not None:
                            if origin.user_id and origin.user_id.startswith("system:"):
                                origin.user_id = origin.chat_id
                                origin.user_name = "SubConscious"
                            return origin

                # Fallback: sessions.json
                sessions_file = _sessions_json_path()
                if sessions_file.exists():
                    try:
                        data = json.loads(sessions_file.read_text(encoding="utf-8"))
                        for entry in data.values():
                            if entry.get("session_id") == session_id:
                                origin = entry.get("origin")
                                if origin:
                                    ss = SessionSource.from_dict(origin)
                                    if ss.user_id and ss.user_id.startswith("system:"):
                                        ss.user_id = ss.chat_id
                                        ss.user_name = "SubConscious"
                                    return ss
                    except Exception as exc:
                        logger.debug("Could not read sessions.json: %s", exc)

                # Fallback: state.db
                db_path = _state_db_path()
                if not db_path.exists():
                    return None
                try:
                    conn = sqlite3.connect(str(db_path))
                    row = conn.execute(
                        "SELECT source, user_id FROM sessions WHERE id = ?",
                        (session_id,),
                    ).fetchone()
                    conn.close()
                except Exception as exc:
                    logger.debug("Session DB lookup failed: %s", exc)
                    return None

                if not row:
                    return None

                original_source, original_user_id = row
                # chat_id must be the platform peer id (telegram user), not session_id
                chat_id = original_user_id or session_id
                return SessionSource(
                    platform=Platform(original_source),
                    chat_id=chat_id,
                    chat_name=chat_id,
                    chat_type="dm",
                    user_id=original_user_id or "subconscious",
                    user_name="SubConscious",
                )

            def _active_route_ids(self) -> set[str]:
                """Session IDs currently bound in the gateway routing index."""
                ids: set[str] = set()
                runner = getattr(self, "gateway_runner", None)
                store = getattr(runner, "session_store", None) if runner else None
                if store is not None:
                    try:
                        for entry in store.list_sessions():
                            sid = getattr(entry, "session_id", None)
                            if sid:
                                ids.add(str(sid))
                        if ids:
                            return ids
                    except Exception as exc:
                        logger.debug("session_store list_sessions failed: %s", exc)

                sessions_file = _sessions_json_path()
                if sessions_file.exists():
                    try:
                        data = json.loads(sessions_file.read_text(encoding="utf-8"))
                        for key, entry in data.items():
                            if key == "_README" or not isinstance(entry, dict):
                                continue
                            sid = entry.get("session_id")
                            if sid:
                                ids.add(str(sid))
                        if ids:
                            return ids
                    except Exception as exc:
                        logger.debug("Could not read sessions.json routes: %s", exc)

                db_path = _state_db_path()
                if db_path.exists():
                    try:
                        conn = sqlite3.connect(str(db_path))
                        rows = conn.execute(
                            "SELECT entry_json FROM gateway_routing"
                        ).fetchall()
                        conn.close()
                        for (entry_json,) in rows:
                            try:
                                sid = json.loads(entry_json).get("session_id")
                                if sid:
                                    ids.add(str(sid))
                            except Exception:
                                continue
                    except Exception as exc:
                        logger.debug("gateway_routing read failed: %s", exc)
                return ids

            def _canonicalize_inject_target(
                self, session_id: str
            ) -> tuple[str, Optional[SessionSource]]:
                """Map a stale session_id to the live route for that chat."""
                source = self._lookup_session_origin(session_id)
                runner = getattr(self, "gateway_runner", None)
                store = getattr(runner, "session_store", None) if runner else None
                if source is None or runner is None or store is None:
                    return session_id, source

                if store.lookup_by_session_id(session_id) is not None:
                    return session_id, source

                try:
                    session_key = runner._session_key_for_source(source)
                except Exception as exc:
                    logger.debug("session key resolve failed: %s", exc)
                    return session_id, source

                current = store.peek_session_id(session_key)
                if not current or current == session_id:
                    return session_id, source

                active_origin = self._lookup_session_origin(current) or source
                logger.info(
                    "Inject target %s is not active for %s; redirecting to %s",
                    session_id,
                    session_key,
                    current,
                )
                return str(current), active_origin

            def _get_platform_adapter(self, source: SessionSource):
                """Return the gateway adapter for the session's original platform."""
                runner = getattr(self, "gateway_runner", None)
                if runner is None:
                    return None
                adapters = getattr(runner, "adapters", {})
                platform = source.platform
                return adapters.get(platform) or adapters.get(platform.value)

            def _try_queue_when_busy(
                self,
                event: "MessageEvent",
                source: SessionSource,
                *,
                delivery: str,
            ) -> bool:
                """Enqueue inject when delivery=queue and the target session is busy."""
                if delivery != "queue":
                    return False
                runner = getattr(self, "gateway_runner", None)
                if runner is None:
                    return False
                session_key = runner._session_key_for_source(source)
                running = getattr(runner, "_running_agents", {})
                if session_key not in running:
                    return False
                platform_adapter = self._get_platform_adapter(source)
                enqueue = getattr(runner, "_enqueue_fifo", None)
                if platform_adapter is None or not callable(enqueue):
                    return False
                logger.info(
                    "Subconscious inject queued for busy session %s (delivery=queue)",
                    session_key,
                )
                enqueue(session_key, event, platform_adapter)
                return True

            async def _dispatch_injected_message(
                self,
                event: "MessageEvent",
                session_id: str,
                source: SessionSource,
                *,
                delivery: str = "queue",
            ) -> None:
                """Run gateway dispatch and deliver response via original platform."""
                runner = getattr(self, "gateway_runner", None)
                if runner is not None:
                    store = getattr(runner, "session_store", None)
                    if store is not None:
                        entry = store.lookup_by_session_id(session_id)
                        if entry is not None:
                            try:
                                store.switch_session(entry.session_key, session_id)
                            except Exception as exc:
                                logger.debug("Could not switch session: %s", exc)

                if self._try_queue_when_busy(event, source, delivery=delivery):
                    return

                if not self._message_handler:
                    logger.error("No _message_handler available")
                    return

                try:
                    response = await self._message_handler(event)
                except Exception as exc:
                    logger.error("Inject dispatch failed: %s", exc, exc_info=True)
                    return

                if not response:
                    return

                adapter = self._get_platform_adapter(source)
                if adapter is None:
                    logger.warning("No adapter for platform %s", source.platform.value)
                    return

                metadata = {}
                if source.thread_id:
                    metadata["thread_id"] = source.thread_id
                try:
                    await adapter.send(
                        chat_id=source.chat_id,
                        content=response,
                        metadata=metadata or None,
                    )
                except Exception as exc:
                    logger.error("Response delivery failed: %s", exc, exc_info=True)

            async def _handle_list_sessions(self, request):
                from aiohttp import web

                try:
                    db_path = _state_db_path()
                    if not db_path.exists():
                        return web.json_response({"sessions": []})

                    conn = sqlite3.connect(str(db_path))
                    rows = conn.execute(
                        "SELECT id, source, user_id, title, started_at, message_count "
                        "FROM sessions WHERE ended_at IS NULL AND source != 'cron' "
                        "ORDER BY started_at DESC LIMIT 100"
                    ).fetchall()
                    conn.close()

                    active_ids = self._active_route_ids()
                    sessions = [
                        {
                            "id": row[0],
                            "source": row[1],
                            "user_id": row[2],
                            "title": row[3],
                            "started_at": row[4],
                            "message_count": row[5],
                            "active": row[0] in active_ids,
                        }
                        for row in rows
                    ]
                    # When a source has a live route, hide stale open siblings
                    # so clients always see the session Hermes is actually on.
                    sources_with_route = {s["source"] for s in sessions if s["active"]}
                    if sources_with_route:
                        sessions = [
                            s
                            for s in sessions
                            if s["active"] or s["source"] not in sources_with_route
                        ]
                    return web.json_response({"sessions": sessions})
                except Exception as exc:
                    logger.error("Error listing sessions: %s", exc, exc_info=True)
                    return web.json_response({"error": str(exc)}, status=500)

            async def _handle_inject(self, request):
                from aiohttp import web

                try:
                    payload = await request.json()
                except Exception:
                    return web.json_response({"error": "Invalid JSON"}, status=400)

                session_id = payload.get("session_id", "")
                text = payload.get("text", "")
                delivery = str(payload.get("delivery", "queue")).strip().lower()
                if delivery not in {"queue", "interrupt"}:
                    return web.json_response(
                        {"error": "delivery must be queue or interrupt"},
                        status=400,
                    )

                if not session_id or not text:
                    return web.json_response(
                        {"error": "session_id and text are required"},
                        status=400,
                    )

                try:
                    session_id, source = self._canonicalize_inject_target(session_id)
                    if source is None:
                        return web.json_response(
                            {"error": f"Session {session_id} not found"},
                            status=404,
                        )

                    event = MessageEvent(
                        text=text,
                        message_type=MessageType.TEXT,
                        source=source,
                        message_id=None,
                        raw_message={"subconscious": True, "delivery": delivery},
                    )

                    asyncio.create_task(
                        self._dispatch_injected_message(
                            event,
                            session_id,
                            source,
                            delivery=delivery,
                        )
                    )

                    return web.json_response({
                        "ok": True,
                        "session_id": session_id,
                        "platform": source.platform.value,
                        "delivery": delivery,
                    })
                except Exception as exc:
                    logger.error("Error injecting: %s", exc, exc_info=True)
                    return web.json_response({"error": str(exc)}, status=500)

        return SubConsciousAdapter(config)

    ctx.register_platform(
        name="subconscious",
        label="SubConscious",
        adapter_factory=adapter_factory,
        check_fn=lambda: True,
        emoji="🧠",
    )

    logger.info("SubConscious adapter plugin registered")
