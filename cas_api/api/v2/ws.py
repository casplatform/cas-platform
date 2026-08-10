"""Live conjunction stream over WebSocket — /api/v2/ws/conjunctions

Sprint #8. Near-real-time push of new conjunctions affecting a user's watchlist.

Architecture (deliberately minimal — no new infra):
  * DB-POLLED, not event-driven. The synchronous engine (port 8765) writes new
    conjunctions to conjunction_events; this async endpoint polls that table on
    a fixed interval and pushes rows newer than the client's last-seen id. No
    Redis, no message queue, no asyncpg — reuses the existing psycopg2 pool via
    asyncio.to_thread so the event loop never blocks.
  * Per-connection loop. Each WebSocket runs its own poll loop scoped to the
    authenticated user's watchlist. With uvicorn --workers 2 each worker handles
    its own connections independently (no cross-worker fan-out needed because
    every worker polls the same DB — no Redis pub/sub required).
  * Auth via query param (?token=JWT). Browsers can't set Authorization headers
    on WebSocket handshakes, so the JWT is passed as a query parameter and
    decoded with the same decode_token used by the REST layer.

IS / IS-NOT honesty:
  IS  : near-real-time (~POLL_INTERVAL_S latency) push of watchlist-relevant new
        conjunctions; heartbeat + clean disconnect; per-user scoping.
  ISNOT: sub-second event-driven push (would need engine->broker wiring);
        production-scale multi-instance fan-out (would need Redis pub/sub —
        Phase 2). Latency is bounded by the poll interval, not the wire.
"""
import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from core.auth import decode_token, fetch_user
from core.tier_features import has_feature, min_tier_for, tier_name
from core.database import get_dict_cursor

log = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])

POLL_INTERVAL_S = 15          # DB poll cadence (latency bound)
HEARTBEAT_EVERY = 2           # send a heartbeat every N polls of silence
MAX_PUSH_PER_POLL = 25        # cap a single push payload


def _json_default(o: Any):
    if isinstance(o, datetime):
        return o.isoformat()
    return str(o)


def _watchlist_norads(user_id: int) -> List[str]:
    """NORAD ids in this user's watchlist (sync; called via to_thread)."""
    try:
        with get_dict_cursor() as cur:
            cur.execute("SELECT norad_id FROM watchlist WHERE user_id=%s", (user_id,))
            return [str(r["norad_id"]).strip() for r in cur.fetchall() if r.get("norad_id")]
    except Exception as e:
        log.warning("ws watchlist query failed: %s", e)
        return []


def _max_event_id() -> int:
    """Current max conjunction_events.id (baseline so we only push NEW rows)."""
    try:
        with get_dict_cursor() as cur:
            cur.execute("SELECT COALESCE(MAX(id), 0) AS m FROM conjunction_events")
            return int(cur.fetchone()["m"])
    except Exception as e:
        log.warning("ws max-id query failed: %s", e)
        return 0


def _new_conjunctions(user_id: int, since_id: int, norads: List[str]) -> List[Dict[str, Any]]:
    """Conjunctions with id > since_id involving any of the user's watchlist
    NORADs. Sync; called via to_thread. Returns newest-first, capped."""
    if not norads:
        return []
    try:
        with get_dict_cursor() as cur:
            # ANY(%s) over the NORAD list, on either side of the pair.
            cur.execute(
                """
                SELECT id, cdm_id, sat1, sat2, norad1, norad2, tca, miss_dist_m, pc, risk, fetched_at
                FROM conjunction_events
                WHERE id > %s
                  AND (norad1 = ANY(%s) OR norad2 = ANY(%s))
                ORDER BY id DESC
                LIMIT %s
                """,
                (since_id, norads, norads, MAX_PUSH_PER_POLL),
            )
            rows = cur.fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        log.warning("ws new-conjunctions query failed: %s", e)
        return []


@router.websocket("/ws/conjunctions")
async def ws_conjunctions(websocket: WebSocket):
    """Authenticated live stream of new watchlist-relevant conjunctions.

    Handshake: client connects to /api/v2/ws/conjunctions?token=<JWT>.
    Protocol (server -> client JSON):
      {"type":"hello", "user_id":.., "watchlist_count":.., "baseline_id":.., "poll_interval_s":..}
      {"type":"conjunctions", "items":[...], "last_id":..}
      {"type":"heartbeat", "ts":".."}
      {"type":"error", "detail":".."}
    Client may send {"type":"ping"} -> server replies {"type":"pong"}.
    """
    # ── Auth via query param (browsers can't set WS Authorization headers) ──
    token = websocket.query_params.get("token", "")
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    try:
        payload = decode_token(token)  # raises HTTPException on bad/expired
        user_id = payload.get("uid") or payload.get("user_id") or payload.get("id")
        if not user_id:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        user_id = int(user_id)
        user_row = await asyncio.to_thread(fetch_user, user_id)
        if not user_row:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
    except Exception:
        # decode_token raises HTTPException; any auth failure -> policy close.
        try:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        except Exception:
            pass
        return

    # ── Tier gate: the live push stream is real-time data (Starter+) ──
    _tier = (user_row.get("tier") or "free")
    if not has_feature(_tier, "realtime_data"):
        # Accept briefly so the client can read WHY, then close (mirrors REST 403).
        await websocket.accept()
        try:
            await websocket.send_json({
                "type": "error",
                "error": "tier_upgrade_required",
                "feature": "realtime_data",
                "current_tier": tier_name(_tier),
                "required_tier": min_tier_for("realtime_data"),
                "detail": f"The live conjunction stream requires the {min_tier_for('realtime_data')} plan or higher.",
            })
        except Exception:
            pass
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()

    # Baseline: only push conjunctions newer than NOW, so a fresh connection
    # doesn't replay the entire backlog.
    norads = await asyncio.to_thread(_watchlist_norads, user_id)
    last_id = await asyncio.to_thread(_max_event_id)

    try:
        await websocket.send_text(json.dumps({
            "type": "hello",
            "user_id": user_id,
            "watchlist_count": len(norads),
            "baseline_id": last_id,
            "poll_interval_s": POLL_INTERVAL_S,
        }, default=_json_default))
    except Exception:
        return

    silent_polls = 0
    try:
        while True:
            # Drain any client->server messages (ping) without blocking long.
            try:
                incoming = await asyncio.wait_for(websocket.receive_text(), timeout=POLL_INTERVAL_S)
                try:
                    msg = json.loads(incoming)
                    if isinstance(msg, dict) and msg.get("type") == "ping":
                        await websocket.send_text(json.dumps({"type": "pong"}))
                except Exception:
                    pass  # ignore malformed client messages
                # After handling a client message, loop again to keep polling cadence.
                continue
            except asyncio.TimeoutError:
                pass  # no client message within the interval -> time to poll
            except WebSocketDisconnect:
                break

            # ── Poll for new watchlist-relevant conjunctions ──
            # Refresh watchlist occasionally (cheap) so added sats start streaming.
            norads = await asyncio.to_thread(_watchlist_norads, user_id)
            items = await asyncio.to_thread(_new_conjunctions, user_id, last_id, norads)

            if items:
                new_last = max(int(i["id"]) for i in items)
                last_id = max(last_id, new_last)
                silent_polls = 0
                await websocket.send_text(json.dumps({
                    "type": "conjunctions",
                    "items": items,
                    "last_id": last_id,
                }, default=_json_default))
            else:
                silent_polls += 1
                if silent_polls >= HEARTBEAT_EVERY:
                    silent_polls = 0
                    await websocket.send_text(json.dumps({
                        "type": "heartbeat",
                        "ts": datetime.utcnow().isoformat(),
                    }))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.warning("ws loop error (user %s): %s", user_id, e)
        try:
            await websocket.send_text(json.dumps({"type": "error", "detail": "stream error"}))
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


@router.get("/ws/info")
async def ws_info() -> Dict[str, Any]:
    """Describe the live-stream capability and its honest boundaries."""
    return {
        "capability": "Live conjunction stream (WebSocket)",
        "endpoint": "/api/v2/ws/conjunctions?token=<JWT>",
        "protocol": {
            "server_to_client": ["hello", "conjunctions", "heartbeat", "error"],
            "client_to_server": ["ping -> pong"],
        },
        "scope": "New conjunctions (id > baseline) involving the authenticated "
                 "user's watchlist NORADs, pushed every poll interval.",
        "poll_interval_s": POLL_INTERVAL_S,
        "is": ["near-real-time push of watchlist-relevant new conjunctions",
               "heartbeat + clean disconnect", "per-user scoping", "JWT auth"],
        "is_not": ["sub-second event-driven push (would need engine->broker wiring)",
                   "multi-instance fan-out (Redis pub/sub — Phase 2)"],
        "auth": "JWT via ?token= query param",
    }
