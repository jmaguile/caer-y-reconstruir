"""Polymarket CLOB websocket client — live book + last-trade stream.

Architecture: one background daemon thread owns a persistent websocket
connection to wss://ws-subscriptions-clob.polymarket.com/ws/market.
Streamlit holds the instance via @st.cache_resource so it survives
script reruns. The fragment in dashboard.py polls .latest(token_id)
every few seconds to render live ticker state.

Subscription model: Polymarket lets you subscribe to multiple asset_ids
in one connection. We re-subscribe whenever a new market is selected
in the dashboard. Old subscriptions are dropped server-side when we
send a new subscribe payload.
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from typing import Optional

import websocket  # provided by `websocket-client`

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
TICK_HISTORY_CAP = 240   # ~8 minutes of 2-sec ticks
RECONNECT_BACKOFF = [1, 2, 5, 10, 15, 30]


class PolymarketWS:
    def __init__(self):
        self._lock = threading.Lock()
        self._subscribed: set[str] = set()
        self._latest: dict[str, dict] = {}      # token_id -> {last_price, best_bid, best_ask, ts}
        self._ticks: dict[str, deque] = {}      # token_id -> deque[(ts, mid_price)]
        self._trades: dict[str, deque] = {}     # token_id -> deque[(ts, price, size, side)]
        self._ws: Optional[websocket.WebSocketApp] = None
        self._stop_flag = threading.Event()
        self._connected = threading.Event()
        self._connect_attempts = 0
        self._last_msg_ts = 0.0
        self._thread = threading.Thread(target=self._run_forever, daemon=True,
                                        name="polymarket-ws")
        self._thread.start()

    # ---------- public API ----------

    def ensure_subscribed(self, token_ids: list[str]) -> None:
        """Make sure these token_ids are part of the active subscription.
        Polymarket requires re-sending the full subscribe payload to change
        the set, so we always send the union.
        """
        token_ids = [t for t in token_ids if t]
        if not token_ids:
            return
        with self._lock:
            new = set(token_ids) - self._subscribed
            if not new and self._subscribed:
                return
            self._subscribed.update(token_ids)
            for tid in token_ids:
                self._ticks.setdefault(tid, deque(maxlen=TICK_HISTORY_CAP))
                self._trades.setdefault(tid, deque(maxlen=50))
            payload = {"type": "market",
                       "assets_ids": sorted(self._subscribed)}
        self._send(json.dumps(payload))

    def latest(self, token_id: str) -> Optional[dict]:
        with self._lock:
            row = self._latest.get(token_id)
            return dict(row) if row else None

    def tick_series(self, token_id: str) -> list[tuple[float, float]]:
        with self._lock:
            return list(self._ticks.get(token_id, ()))

    def recent_trades(self, token_id: str, n: int = 10) -> list[tuple]:
        with self._lock:
            return list(self._trades.get(token_id, ()))[-n:]

    def status(self) -> dict:
        return {
            "connected": self._connected.is_set(),
            "subscribed": sorted(self._subscribed),
            "last_msg_ago": (time.time() - self._last_msg_ts) if self._last_msg_ts else None,
            "reconnects": self._connect_attempts,
        }

    def stop(self) -> None:
        self._stop_flag.set()
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass

    # ---------- internals ----------

    def _send(self, raw: str) -> None:
        ws = self._ws
        if ws is None or not self._connected.is_set():
            return
        try:
            ws.send(raw)
        except Exception:
            pass  # _on_close will trigger reconnect

    def _run_forever(self) -> None:
        while not self._stop_flag.is_set():
            self._connect_attempts += 1
            try:
                self._ws = websocket.WebSocketApp(
                    WS_URL,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                self._ws.run_forever(ping_interval=15, ping_timeout=10)
            except Exception:
                pass
            self._connected.clear()
            if self._stop_flag.is_set():
                break
            backoff = RECONNECT_BACKOFF[min(self._connect_attempts - 1,
                                            len(RECONNECT_BACKOFF) - 1)]
            time.sleep(backoff)

    def _on_open(self, ws):
        self._connected.set()
        # Re-send subscription on every reconnect
        with self._lock:
            subs = sorted(self._subscribed)
        if subs:
            try:
                ws.send(json.dumps({"type": "market", "assets_ids": subs}))
            except Exception:
                pass

    def _on_message(self, ws, message: str):
        self._last_msg_ts = time.time()
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            return
        # Polymarket sends either a single event dict or a list of them
        events = data if isinstance(data, list) else [data]
        for ev in events:
            self._handle_event(ev)

    def _handle_event(self, ev: dict):
        et = ev.get("event_type") or ev.get("type")
        tid = str(ev.get("asset_id") or ev.get("market") or "")
        if not tid:
            return
        ts = time.time()
        with self._lock:
            row = self._latest.setdefault(tid, {})
            row["ts"] = ts
            if et == "book":
                bids = ev.get("bids") or []
                asks = ev.get("asks") or []
                best_bid = max((float(b.get("price", 0)) for b in bids), default=0.0)
                best_ask = min((float(a.get("price", 0)) for a in asks), default=0.0)
                row["best_bid"] = best_bid
                row["best_ask"] = best_ask
                if best_bid > 0 and best_ask > 0:
                    mid = round((best_bid + best_ask) / 2, 4)
                    row["mid"] = mid
                    self._ticks.setdefault(tid, deque(maxlen=TICK_HISTORY_CAP)).append((ts, mid))
            elif et == "price_change":
                # changes is a list of {price, side, size}
                for ch in ev.get("changes", []) or []:
                    price = float(ch.get("price", 0))
                    side = (ch.get("side") or "").lower()
                    if side == "buy":
                        row["best_bid"] = max(row.get("best_bid", 0), price)
                    elif side == "sell":
                        row["best_ask"] = min(row.get("best_ask", 1) or 1, price)
                bid = row.get("best_bid", 0)
                ask = row.get("best_ask", 0)
                if bid > 0 and ask > 0:
                    mid = round((bid + ask) / 2, 4)
                    row["mid"] = mid
                    self._ticks.setdefault(tid, deque(maxlen=TICK_HISTORY_CAP)).append((ts, mid))
            elif et in ("last_trade_price", "trade"):
                price = float(ev.get("price", 0))
                size = float(ev.get("size", 0))
                side = (ev.get("side") or "").lower() or "?"
                row["last_price"] = price
                row["last_trade_size"] = size
                row["last_trade_side"] = side
                self._trades.setdefault(tid, deque(maxlen=50)).append((ts, price, size, side))
            elif et == "tick_size_change":
                row["tick_size"] = ev.get("new_tick_size") or ev.get("tick_size")

    def _on_error(self, ws, err):
        pass  # silenced; reconnect loop handles it

    def _on_close(self, ws, code, msg):
        self._connected.clear()
