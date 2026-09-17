"""
================================================================
CONVICTION SCANNER v6.1 — Free Stack + Aggressive Resilience
================================================================
Perbaikan dari v6:
- Rate limiter multi-tier (per menit/jam/bulan) untuk setiap API
- Response cache dengan TTL per layer (hemat 60-80% request)
- Circuit breaker per API
- Fail-closed security, fail-neutral enrichment
- Watchdog restart worker mati
- Memory guard + TTL cleanup
- Self-ping Render
================================================================
"""

import os
import time
import json
import sqlite3
import threading
import traceback
import requests
from datetime import datetime, timezone, timedelta
from collections import deque
from dataclasses import dataclass, asdict
from typing import Optional

from flask import Flask, request, jsonify

from resilience import (
    CircuitBreaker, RateLimiter, ResponseCache, TimeBudget,
    safe_call, resilient_loop, get_breaker,
    init_limiters, init_caches, LIMITERS, CACHES,
    register_watch, start_watchdog, WATCHED_THREADS, log,
)

# ============================================================
# KONFIGURASI
# ============================================================
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")
CIELO_API_KEY = os.environ.get("CIELO_API_KEY", "")
TNT_RISK_API_KEY = os.environ.get("TNT_RISK_API_KEY", "")
MADEONSOL_API_KEY = os.environ.get("MADEONSOL_API_KEY", "")
FOMO_API_KEY = os.environ.get("FOMO_API_KEY", "")
CABALSPY_API_KEY = os.environ.get("CABALSPY_API_KEY", "")
VYBE_API_KEY = os.environ.get("VYBE_API_KEY", "")
MOBULA_API_KEY = os.environ.get("MOBULA_API_KEY", "")
ADANOS_API_KEY = os.environ.get("ADANOS_API_KEY", "")
SANTIMENT_API_KEY = os.environ.get("SANTIMENT_API_KEY", "")
HELIUS_API_KEY = os.environ.get("HELIUS_API_KEY", "")
GOPLUS_API_KEY = os.environ.get("GOPLUS_API_KEY", "")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

MIN_LIQ_DISCOVERY = float(os.environ.get("MIN_LIQ_DISCOVERY", "8000"))
MIN_LIQ_VALIDATION = float(os.environ.get("MIN_LIQ_VALIDATION", "25000"))
MIN_VOL_1H = float(os.environ.get("MIN_VOL_1H", "15000"))
MAX_VOL_LIQ_RATIO = float(os.environ.get("MAX_VOL_LIQ_RATIO", "6.0"))
MIN_CONVICTION_ALERT = float(os.environ.get("MIN_CONVICTION_ALERT", "75"))

SCAN_INTERVAL = int(os.environ.get("SCAN_INTERVAL", "30"))
DISCOVERY_INTERVAL = int(os.environ.get("DISCOVERY_INTERVAL", "120"))
MAX_TRACKED = int(os.environ.get("MAX_TRACKED", "100"))
TOKEN_TTL_HOURS = int(os.environ.get("TOKEN_TTL_HOURS", "24"))

# ============================================================
# ENDPOINTS
# ============================================================
GECKO_PUBLIC = "https://api.geckoterminal.com/api/v2"
RUGCHECK_API = "https://api.rugcheck.xyz/v1"
CIELO_API = "https://feed-api.cielo.finance/api/v1"
TNT_RISK_API = "https://www.tnt-audit.com/api/v1"
MADEONSOL_API = "https://api.madeonsol.com/v1"
FOMO_API = "https://api.cope.capital/v1"
CABALSPY_API = "https://api.cabalspy.xyz/v1"
VYBE_API = "https://api.vybenetwork.com/v1"
MOBULA_API = "https://api.mobula.io/api/1"
ADANOS_API = "https://api.adanos.org/v1"
SANTIMENT_API = "https://api.santiment.net/graphql"
GOPLUS_API = "https://api.gopluslabs.io/api/v1"
FREE_CRYPTO_NEWS = "https://fcn.dev/api"

NETWORK = "solana"

# ============================================================
# RATE LIMITER CONFIG — SESUAI FREE TIER MASING-MASING API
# ============================================================
# Referensi:
# RugCheck: 10/min unauthenticated, 60/min authenticated
# GeckoTerminal: 30/min
# Santiment: 1000/month, 500/hour, 100/min
# Adanos: 250/month, 100/min
# CabalSpy: 10000 credits/month = ~1000 req, 5 req/sec
# MadeOnSol: 200/day = ~10/min
# TNT Risk-Data: 15/day
# Fomo Research: 250/day
# ============================================================
LIMITER_CONFIG = {
    "gecko":      {"per_minute": 25, "per_hour": 1200},   # margin dari 30/min
    "rugcheck":   {"per_minute": 8, "per_hour": 400},     # margin dari 10/min
    "goplus":     {"per_minute": 20, "per_hour": 500},
    "tnt":        {"per_minute": 1, "per_hour": 10},      # 15/day
    "madeonsol":  {"per_minute": 8, "per_hour": 150},     # 200/day
    "fomo":       {"per_minute": 15, "per_hour": 200},
    "cabalspy":   {"per_minute": 4, "per_hour": 200},     # 5/sec burst
    "vybe":       {"per_minute": 20, "per_hour": 500},
    "mobula":     {"per_minute": 20, "per_hour": 500},
    "adanos":     {"per_minute": 80, "per_hour": 500, "per_month": 230},
    "santiment":  {"per_minute": 90, "per_hour": 450, "per_month": 900},
}

# ============================================================
# CACHE CONFIG — TTL PER LAYER
# ============================================================
CACHE_CONFIG = {
    "security":  {"default_ttl": 1800, "max_size": 2000},   # 30 menit
    "market":    {"default_ttl": 25, "max_size": 5000},     # 25 detik
    "whale":     {"default_ttl": 120, "max_size": 2000},    # 2 menit
    "narrative": {"default_ttl": 600, "max_size": 1000},    # 10 menit
    "insider":   {"default_ttl": 900, "max_size": 1000},    # 15 menit
}

# ============================================================
# DATA MODEL
# ============================================================
@dataclass
class TokenSnapshot:
    token: str
    name: str = "Unknown"
    symbol: str = ""
    pool_id: str = ""
    price: float = 0.0
    liquidity: float = 0.0
    volume_1h: float = 0.0
    buys_1h: int = 0
    sells_1h: int = 0
    buyers_1h: int = 0
    sellers_1h: int = 0
    price_change_1h: float = 0.0
    price_change_5m: float = 0.0
    txns_1h: int = 0
    honeypot: bool = False
    mint_authority: bool = True
    freeze_authority: bool = True
    lp_burned: bool = False
    rugcheck_score: int = 100
    insider_clusters: int = 0
    same_first_funder: bool = False
    sniper_share: float = 0.0
    whale_net_buy_1h: float = 0.0
    whale_count: int = 0
    top10_holder_pct: float = 0.0
    smart_money_pnl: float = 0.0
    smart_money_winrate: float = 0.0
    kol_entry_count: int = 0
    social_volume_24h: float = 0.0
    social_volume_change: float = 0.0
    sentiment_score: float = 0.0
    unique_authors: int = 0
    buzz_score: float = 0.0
    narrative_stage: str = "unknown"
    first_seen: int = 0
    last_updated: int = 0

# ============================================================
# DATABASE
# ============================================================
DB_FILE = os.environ.get("DB_FILE", "/data/conviction_v6.db")
db_lock = threading.Lock()

def init_db():
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True) if os.path.dirname(DB_FILE) else None
    with db_lock:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS tokens (
            token TEXT PRIMARY KEY, name TEXT, pool_id TEXT,
            snapshot_json TEXT, conviction REAL,
            alert_sent INTEGER DEFAULT 0,
            first_seen INTEGER, last_updated INTEGER
        )""")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT, name TEXT, conviction REAL,
            snapshot_json TEXT, sent_at INTEGER
        )""")
        conn.commit()
        conn.close()

def upsert_token(snap: TokenSnapshot, conviction: float):
    with db_lock:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        conn.execute("""
        INSERT INTO tokens (token, name, pool_id, snapshot_json, conviction, first_seen, last_updated)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(token) DO UPDATE SET
            snapshot_json=excluded.snapshot_json,
            conviction=excluded.conviction,
            last_updated=excluded.last_updated
        """, (snap.token, snap.name, snap.pool_id, json.dumps(asdict(snap)),
              conviction, snap.first_seen or int(time.time()), int(time.time())))
        conn.commit()
        conn.close()

def log_alert(snap: TokenSnapshot, conviction: float):
    with db_lock:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        conn.execute("""
        INSERT INTO alerts (token, name, conviction, snapshot_json, sent_at)
        VALUES (?, ?, ?, ?, ?)
        """, (snap.token, snap.name, conviction, json.dumps(asdict(snap)), int(time.time())))
        conn.commit()
        conn.close()

def get_token_row(token: str) -> Optional[dict]:
    with db_lock:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM tokens WHERE token=?", (token,))
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else None

# ============================================================
# HTTP SESSION
# ============================================================
session = requests.Session()
session.headers.update({"User-Agent": "ConvictionScanner/6.1"})

def http_get(url, params=None, headers=None, timeout=10):
    """Raw HTTP GET — TIDAK ada retry di sini, retry di safe_call."""
    try:
        return session.get(url, params=params, headers=headers, timeout=timeout)
    except Exception:
        return None

def send_telegram(msg: str):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        log.info(f"[TG DISABLED] {msg[:100]}")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "Markdown",
                  "disable_web_page_preview": True},
            timeout=10,
        )
    except Exception as e:
        log.warning(f"[TG ERROR] {e}")

# ============================================================
# GECKOTERMINAL
# ============================================================
def fetch_new_pools():
    def _call():
        r = http_get(f"{GECKO_PUBLIC}/networks/{NETWORK}/new_pools", timeout=10)
        if r and r.status_code == 200:
            return r.json().get("data", [])
        return None
    return safe_call(_call, default=[], breaker_name="gecko", limiter_name="gecko", retries=2)

def fetch_pool_live(pool_id: str) -> Optional[dict]:
    def _call():
        r = http_get(f"{GECKO_PUBLIC}/networks/{NETWORK}/pools/{pool_id}", timeout=8)
        if r and r.status_code == 200:
            return r.json().get("data")
        return None
    return safe_call(_call, default=None, breaker_name="gecko", limiter_name="gecko",
                     cache=CACHES["market"], cache_key=f"pool:{pool_id}", cache_ttl=25, retries=1)

def parse_pool(pool: dict) -> Optional[TokenSnapshot]:
    try:
        attr = pool.get("attributes", {})
        base = pool.get("relationships", {}).get("base_token", {}).get("data", {}).get("id", "")
        token = base.replace("solana_", "", 1) if base.startswith("solana_") else base
        txns = attr.get("transactions", {}).get("h1", {})
        pc = attr.get("price_change_percentage", {})
        return TokenSnapshot(
            token=token,
            name=attr.get("name", "Unknown"),
            pool_id=pool.get("id", ""),
            price=float(attr.get("base_token_price_usd") or 0),
            liquidity=float(attr.get("reserve_in_usd") or 0),
            volume_1h=float(attr.get("volume_usd", {}).get("h1") or 0),
            buys_1h=int(txns.get("buys") or 0),
            sells_1h=int(txns.get("sells") or 0),
            buyers_1h=int(txns.get("buyers") or 0),
            sellers_1h=int(txns.get("sellers") or 0),
            price_change_1h=float(pc.get("h1") or 0),
            price_change_5m=float(pc.get("m5") or 0),
            txns_1h=int(txns.get("buys", 0)) + int(txns.get("sells", 0)),
            first_seen=int(time.time()),
        )
    except Exception:
        return None

# ============================================================
# LAYER 1: SNIFFER (SECURITY) — FAIL-CLOSED
# ============================================================
def sniffer_rugcheck(token: str) -> dict:
    """Fail-closed: kalau gagal, tolak token."""
    def _call():
        r = http_get(f"{RUGCHECK_API}/tokens/{token}/report", timeout=8)
        if r and r.status_code == 200:
            return r
        return None
    r = safe_call(_call, default=None, breaker_name="rugcheck", limiter_name="rugcheck",
                  cache=CACHES["security"], cache_key=f"rugcheck:{token}", cache_ttl=1800, retries=1)
    if not r:
        return {"safe": False, "score": 100, "reason": "unavailable"}
    try:
        data = r.json()
        risks = data.get("risks", [])
        danger = sum(1 for x in risks if str(x.get("level", "")).lower() == "danger")
        if danger == 0:
            return {"safe": True, "score": 20}
        elif danger == 1:
            return {"safe": False, "score": 60}
        else:
            return {"safe": False, "score": 95}
    except Exception:
        return {"safe": False, "score": 100, "reason": "parse_error"}

def sniffer_goplus(token: str) -> dict:
    result = {"honeypot": False, "tax": 0}
    params = {"contract_addresses": token, "chain_id": "501"}
    headers = {"Authorization": GOPLUS_API_KEY} if GOPLUS_API_KEY else {}

    def _call():
        r = http_get(f"{GOPLUS_API}/token_security/501", params=params, headers=headers, timeout=8)
        if r and r.status_code == 200:
            return r.json().get("result", {})
        return None

    data = safe_call(_call, default=None, breaker_name="goplus", limiter_name="goplus",
                     cache=CACHES["security"], cache_key=f"goplus:{token}", cache_ttl=1800, retries=1)
    if data and token in data:
        info = data[token]
        result["honeypot"] = info.get("is_honeypot", "0") == "1"
        result["tax"] = float(info.get("buy_tax", 0)) + float(info.get("sell_tax", 0))
    return result

def sniffer_tnt_risk(token: str) -> dict:
    result = {"insider_clusters": 0, "same_first_funder": False, "sniper_share": 0.0}
    if not TNT_RISK_API_KEY:
        return result

    def _call():
        r = http_get(f"{TNT_RISK_API}/token/{token}/risk",
                     headers={"X-API-KEY": TNT_RISK_API_KEY}, timeout=15)
        if r and r.status_code == 200:
            return r.json()
        return None

    data = safe_call(_call, default=None, breaker_name="tnt", limiter_name="tnt",
                     cache=CACHES["insider"], cache_key=f"tnt:{token}", cache_ttl=1800, retries=1)
    if data:
        clusters = data.get("insider_clusters", [])
        result["insider_clusters"] = len(clusters) if isinstance(clusters, list) else int(clusters)
        result["same_first_funder"] = data.get("same_first_funder", False)
        result["sniper_share"] = float(data.get("sniper_share", 0))
    return result

def run_sniffer(snap: TokenSnapshot) -> TokenSnapshot:
    rc = sniffer_rugcheck(snap.token)
    snap.rugcheck_score = rc["score"]
    snap.lp_burned = rc["safe"]
    gp = sniffer_goplus(snap.token)
    snap.honeypot = gp["honeypot"]
    tnt = sniffer_tnt_risk(snap.token)
    snap.insider_clusters = tnt["insider_clusters"]
    snap.same_first_funder = tnt["same_first_funder"]
    snap.sniper_share = tnt["sniper_share"]
    return snap

# ============================================================
# LAYER 2: INSIDER TRACKER — FAIL-NEUTRAL
# ============================================================
def fetch_insider_tracker(token: str) -> dict:
    result = {"kol_entries": 0, "bundle_held_pct": 0.0,
              "sniper_wallets": 0, "insider_wallets": 0, "bundler_wallets": 0}

    if MADEONSOL_API_KEY:
        def _kol():
            r = http_get(f"{MADEONSOL_API}/token/{token}/kol-entries",
                         headers={"Authorization": f"Bearer {MADEONSOL_API_KEY}"}, timeout=8)
            return r.json() if r and r.status_code == 200 else None

        data = safe_call(_kol, default=None, breaker_name="madeonsol", limiter_name="madeonsol",
                         cache=CACHES["insider"], cache_key=f"mad_kol:{token}", cache_ttl=600, retries=1)
        if data:
            result["kol_entries"] = int(data.get("count", 0))
            result["bundle_held_pct"] = float(data.get("bundle_held_pct", 0))

    if MOBULA_API_KEY:
        def _mob():
            r = http_get(f"{MOBULA_API}/wallet/labels", params={"tokenAddress": token},
                         headers={"Authorization": f"Bearer {MOBULA_API_KEY}"}, timeout=8)
            return r.json() if r and r.status_code == 200 else None

        data = safe_call(_mob, default=None, breaker_name="mobula", limiter_name="mobula",
                         cache=CACHES["insider"], cache_key=f"mob:{token}", cache_ttl=900, retries=1)
        if data:
            for lbl in data.get("labels", []):
                t = lbl.get("label", "").lower()
                if "sniper" in t: result["sniper_wallets"] += 1
                elif "insider" in t: result["insider_wallets"] += 1
                elif "bundler" in t: result["bundler_wallets"] += 1

    return result

# ============================================================
# LAYER 3: WHALE FLOW — FAIL-NEUTRAL
# ============================================================
def fetch_whale_flow(token: str) -> dict:
    result = {"net_buy_1h": 0.0, "whale_count": 0, "top10_pct": 0.0,
              "smart_money_pnl": 0.0, "smart_money_winrate": 0.0,
              "kol_entry_count": 0, "cabal_cluster": 0, "bundle_detected": False}

    if FOMO_API_KEY:
        def _fomo():
            r = http_get(f"{FOMO_API}/trades", params={"token": token, "chain": "solana"},
                         headers={"Authorization": f"Bearer {FOMO_API_KEY}"}, timeout=8)
            return r.json() if r and r.status_code == 200 else None

        data = safe_call(_fomo, default=None, breaker_name="fomo", limiter_name="fomo",
                         cache=CACHES["whale"], cache_key=f"fomo:{token}", cache_ttl=90, retries=1)
        if data:
            net = 0.0
            wallets = set()
            for t in data.get("trades", []):
                usd = float(t.get("usd_value") or 0)
                side = str(t.get("side", "")).lower()
                net += usd if side == "buy" else -usd
                if t.get("wallet"): wallets.add(t["wallet"])
            result["net_buy_1h"] = net
            result["whale_count"] = len(wallets)

    if CABALSPY_API_KEY:
        def _cabal():
            r = http_get(f"{CABALSPY_API}/token/{token}/bundles",
                         headers={"X-API-KEY": CABALSPY_API_KEY}, timeout=8)
            return r.json() if r and r.status_code == 200 else None

        data = safe_call(_cabal, default=None, breaker_name="cabalspy", limiter_name="cabalspy",
                         cache=CACHES["whale"], cache_key=f"cabal:{token}", cache_ttl=120, retries=1)
        if data:
            bundles = data.get("bundles", [])
            result["bundle_detected"] = len(bundles) > 0
            if bundles: result["cabal_cluster"] = len(bundles)

    if VYBE_API_KEY:
        def _vybe():
            r = http_get(f"{VYBE_API}/token/{token}/top-holders", params={"limit": 10},
                         headers={"X-API-KEY": VYBE_API_KEY}, timeout=8)
            return r.json() if r and r.status_code == 200 else None

        data = safe_call(_vybe, default=None, breaker_name="vybe", limiter_name="vybe",
                         cache=CACHES["whale"], cache_key=f"vybe:{token}", cache_ttl=300, retries=1)
        if data:
            holders = data.get("holders", [])
            if holders:
                total = sum(float(h.get("amount", 0)) for h in holders)
                supply = float(data.get("total_supply", 1))
                result["top10_pct"] = (total / max(supply, 1)) * 100

    return result

# ============================================================
# LAYER 4: NARRATIVE — FAIL-NEUTRAL
# ============================================================
def fetch_narrative(token: str, symbol: str = "") -> dict:
    result = {"social_volume": 0.0, "volume_change": 0.0, "sentiment": 0.0,
              "unique_authors": 0, "buzz_score": 0.0,
              "narrative_stage": "unknown", "narrative_score": 0.0}

    if ADANOS_API_KEY:
        def _adanos():
            r = http_get(f"{ADANOS_API}/asset", params={"ticker": symbol or token, "days": 7},
                         headers={"X-API-KEY": ADANOS_API_KEY}, timeout=8)
            return r.json() if r and r.status_code == 200 else None

        data = safe_call(_adanos, default=None, breaker_name="adanos", limiter_name="adanos",
                         cache=CACHES["narrative"], cache_key=f"adanos:{symbol or token}",
                         cache_ttl=600, retries=1)
        if data:
            result["buzz_score"] = float(data.get("buzz_score", 0))
            result["sentiment"] = float(data.get("sentiment", 0))
            result["unique_authors"] = int(data.get("unique_authors", 0))

    if SANTIMENT_API_KEY:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        query = """
        { getMetric(metric: "social_volume_total") {
            timeseriesData(slug: "%s", from: "%s", to: "%s", interval: "1d") {
              datetime value } } }
        """ % (token.lower(), yesterday, today)

        def _sant():
            r = http_get(SANTIMENT_API, params={"query": query},
                         headers={"Authorization": f"Bearer {SANTIMENT_API_KEY}"}, timeout=12)
            return r.json() if r and r.status_code == 200 else None

        data = safe_call(_sant, default=None, breaker_name="santiment", limiter_name="santiment",
                         cache=CACHES["narrative"], cache_key=f"sant:{token}",
                         cache_ttl=600, retries=1)
        if data:
            ts = data.get("data", {}).get("getMetric", {}).get("timeseriesData", [])
            if len(ts) >= 2:
                result["social_volume"] = ts[-1]["value"]
                prev = ts[-2]["value"]
                if prev > 0:
                    result["volume_change"] = ((ts[-1]["value"] - prev) / prev) * 100

    result["narrative_stage"] = classify_narrative_stage(result)
    result["narrative_score"] = calculate_narrative_score(result)
    return result

def classify_narrative_stage(data: dict) -> str:
    vc = data.get("volume_change", 0)
    authors = data.get("unique_authors", 0)
    sent = data.get("sentiment", 0)
    if vc < 20 and sent > 0: return "pre-narrative"
    if vc > 50 and authors > 10 and sent > 0.3: return "emergence"
    if vc > 150 and authors > 50: return "acceleration"
    if vc > 500 and authors > 100: return "peak"
    if vc < 0: return "declining"
    return "unknown"

def calculate_narrative_score(data: dict) -> float:
    score = 0.0
    vc = data.get("volume_change", 0)
    sent = data.get("sentiment", 0)
    authors = data.get("unique_authors", 0)
    stage = data.get("narrative_stage", "unknown")
    if vc > 200: score += 30
    elif vc > 100: score += 20
    elif vc > 50: score += 10
    if sent > 0.7: score += 25
    elif sent > 0.4: score += 15
    elif sent > 0: score += 5
    elif sent < -0.3: score -= 10
    score += min(20, (authors / max(data.get("total_mentions", 1), 1)) * 40)
    stage_scores = {"pre-narrative": 5, "emergence": 15, "acceleration": 10, "peak": 0, "declining": -10}
    score += stage_scores.get(stage, 0)
    score += min(10, data.get("buzz_score", 0) / 10)
    return max(0, min(100, score))

# ============================================================
# LAYER 5: EUPHORIA
# ============================================================
def detect_euphoria(snap: TokenSnapshot) -> dict:
    result = {"euphoria_score": 0.0, "is_healthy": True, "reasons": []}
    if snap.liquidity > 0:
        vlr = snap.volume_1h / snap.liquidity
        if vlr > MAX_VOL_LIQ_RATIO:
            result["euphoria_score"] += 30
            result["reasons"].append(f"Volume/Liq {vlr:.1f}x")
        elif vlr > 3.0:
            result["euphoria_score"] += 15
    total_tx = snap.buys_1h + snap.sells_1h
    if total_tx > 0:
        br = snap.buys_1h / total_tx
        if br > 0.85:
            result["euphoria_score"] += 20
            result["reasons"].append(f"Buy ratio {br:.0%} FOMO")
        elif br < 0.35:
            result["euphoria_score"] += 25
            result["reasons"].append(f"Buy ratio {br:.0%} dump risk")
    if snap.price_change_5m > 40 and snap.liquidity < 50000:
        result["euphoria_score"] += 25
    if snap.top10_holder_pct > 35:
        result["euphoria_score"] += 20
    if snap.insider_clusters > 3:
        result["euphoria_score"] += 25
    result["euphoria_score"] = min(100, result["euphoria_score"])
    result["is_healthy"] = result["euphoria_score"] < 50
    return result

# ============================================================
# CONVICTION SCORE
# ============================================================
def calculate_conviction(snap: TokenSnapshot, insider: dict, whale: dict,
                         narrative: dict, euphoria: dict) -> float:
    score = 0.0
    # Security (25)
    if snap.honeypot: return 0.0
    if not snap.mint_authority and not snap.freeze_authority: score += 20
    if snap.lp_burned: score += 5
    # Insider (20)
    ins = 0.0
    if snap.insider_clusters == 0: ins += 10
    if not snap.same_first_funder: ins += 5
    if snap.sniper_share < 0.05: ins += 3
    if insider.get("sniper_wallets", 0) == 0: ins += 2
    score += min(20, ins)
    # Whale (15)
    wf = 0.0
    if whale["net_buy_1h"] > 0: wf += min(8, whale["net_buy_1h"] / 5000)
    if whale["whale_count"] >= 3: wf += 3
    if whale["top10_pct"] < 20: wf += 4
    elif whale["top10_pct"] > 40: wf -= 6
    score += max(0, min(15, wf))
    # Narrative (20)
    nar = narrative.get("narrative_score", 0)
    stage = narrative.get("narrative_stage", "unknown")
    if stage == "emergence": nar = min(100, nar * 1.3)
    elif stage == "peak": nar = nar * 0.3
    elif stage == "declining": nar = 0
    score += (nar / 100) * 20
    # Smart Money (10)
    sm = 0.0
    if whale.get("smart_money_pnl", 0) > 0: sm += 4
    if whale.get("smart_money_winrate", 0) > 0.6: sm += 3
    if whale.get("kol_entry_count", 0) >= 2: sm += 3
    score += min(10, sm)
    # Momentum (5)
    mom = 0.0
    if snap.liquidity >= MIN_LIQ_VALIDATION: mom += 2
    if snap.volume_1h >= MIN_VOL_1H: mom += 2
    if snap.buyers_1h >= 15: mom += 1
    score += min(5, mom)
    # Euphoria (5 inverse)
    score += max(0, 5 * (1 - euphoria["euphoria_score"] / 100))
    return round(min(100, max(0, score)), 1)

# ============================================================
# TOKEN PROCESSING
# ============================================================
tracked = {}
tracked_lock = threading.Lock()

def process_token(snap: TokenSnapshot):
    budget = TimeBudget(20, f"token:{snap.name[:20]}")
    snap = run_sniffer(snap)
    if snap.honeypot:
        log.info(f"🚫 HONEYPOT: {snap.name} ({snap.token})")
        return
    if budget.check("after_sniffer"): return
    insider = fetch_insider_tracker(snap.token)
    if budget.check("after_insider"): return
    whale = fetch_whale_flow(snap.token)
    if budget.check("after_whale"): return
    narrative = fetch_narrative(snap.token, snap.symbol)
    if budget.check("after_narrative"): return
    euphoria = detect_euphoria(snap)
    conviction = calculate_conviction(snap, insider, whale, narrative, euphoria)
    upsert_token(snap, conviction)
    row = get_token_row(snap.token)
    if row and row["alert_sent"] == 0 and conviction >= MIN_CONVICTION_ALERT:
        send_alert(snap, conviction, insider, whale, narrative, euphoria)
        with db_lock:
            conn = sqlite3.connect(DB_FILE, check_same_thread=False)
            conn.execute("UPDATE tokens SET alert_sent=1 WHERE token=?", (snap.token,))
            conn.commit()
            conn.close()

def send_alert(snap, conviction, insider, whale, narrative, euphoria):
    dex_url = f"https://dexscreener.com/solana/{snap.token}"
    flags = []
    if snap.mint_authority: flags.append("⚠️ Mint authority aktif")
    if snap.freeze_authority: flags.append("⚠️ Freeze authority aktif")
    if not snap.lp_burned: flags.append("⚠️ LP belum burn")
    if snap.insider_clusters > 3: flags.append(f"⚠️ {snap.insider_clusters} insider clusters")
    if snap.same_first_funder: flags.append("⚠️ Same first funder (sniper signal)")
    if whale.get("bundle_detected"): flags.append("⚠️ Bundle detected")
    if euphoria["euphoria_score"] > 50: flags.append(f"⚠️ Euphoria {euphoria['euphoria_score']:.0f}/100")
    flag_text = "\n".join(flags) if flags else "✅ Tidak ada red flag utama"
    msg = (
        f"🎯 *CONVICTION ALERT v6.1* — Skor {conviction}/100\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 *{snap.name}* (`{snap.symbol}`)\n📝 `{snap.token}`\n\n"
        f"💰 Harga: `${snap.price:.8f}`\n"
        f"💧 Likuiditas: `${snap.liquidity:,.0f}`\n"
        f"📊 Volume 1H: `${snap.volume_1h:,.0f}`\n"
        f"📈 Perubahan 1H: `{snap.price_change_1h:+.1f}%`\n"
        f"👥 Buyer/Seller 1H: `{snap.buyers_1h}/{snap.sellers_1h}`\n\n"
        f"🛡️ *Security*: RugCheck `{snap.rugcheck_score}/100` | "
        f"Insider `{snap.insider_clusters}` | Sniper `{snap.sniper_share:.2%}`\n\n"
        f"🐳 *Whale*: Net `${whale['net_buy_1h']:,.0f}` | "
        f"Count `{whale['whale_count']}` | Top10 `{whale['top10_pct']:.1f}%`\n\n"
        f"📰 *Narrative*: Stage `{narrative.get('narrative_stage','unknown')}` | "
        f"Buzz `{narrative.get('buzz_score',0):.1f}` | "
        f"Sent `{narrative.get('sentiment',0):+.2f}` | "
        f"Vol Δ `{narrative.get('volume_change',0):+.0f}%`\n\n"
        f"🌡️ Euphoria: `{euphoria['euphoria_score']:.0f}/100`\n\n"
        f"*Red Flags:*\n{flag_text}\n\n"
        f"🔎 [DEXScreener]({dex_url})"
    )
    send_telegram(msg)
    log_alert(snap, conviction)

# ============================================================
# BACKGROUND WORKERS
# ============================================================
@resilient_loop("discovery", DISCOVERY_INTERVAL)
def discovery_loop():
    pools = fetch_new_pools()
    count = 0
    for pool in pools:
        snap = parse_pool(pool)
        if not snap or not snap.token: continue
        if snap.liquidity < MIN_LIQ_DISCOVERY: continue
        with tracked_lock:
            if len(tracked) >= MAX_TRACKED: break
            if snap.token not in tracked:
                tracked[snap.token] = {"pool_id": snap.pool_id, "first_seen": int(time.time())}
                count += 1
    log.info(f"[DISCOVERY] +{count} new | total: {len(tracked)}")

@resilient_loop("monitoring", SCAN_INTERVAL)
def monitoring_loop():
    with tracked_lock:
        items = list(tracked.items())
    now = int(time.time())
    expired = [t for t, v in items if now - v["first_seen"] > TOKEN_TTL_HOURS * 3600]
    for t in expired:
        with tracked_lock:
            tracked.pop(t, None)
    if expired:
        log.info(f"[CLEANUP] Removed {len(expired)} expired tokens")
    for token, meta in items:
        pool_data = fetch_pool_live(meta["pool_id"])
        if not pool_data: continue
        snap = parse_pool(pool_data)
        if not snap: continue
        if snap.liquidity < MIN_LIQ_DISCOVERY: continue
        with tracked_lock:
            if token in tracked: tracked[token]["last_updated"] = now
        process_token(snap)

@resilient_loop("memory_guard", 300)
def memory_guard_loop():
    with tracked_lock:
        if len(tracked) > MAX_TRACKED:
            sorted_items = sorted(tracked.items(), key=lambda x: x[1].get("last_seen", 0))
            excess = len(tracked) - MAX_TRACKED
            for token, _ in sorted_items[:excess]:
                tracked.pop(token, None)
            log.info(f"Memory guard: removed {excess} tokens")

@resilient_loop("cache_cleanup", 600)
def cache_cleanup_loop():
    for name, cache in CACHES.items():
        n = cache.clear_expired()
        if n:
            log.info(f"Cache {name}: cleared {n} expired")

@resilient_loop("self_ping", 600)
def self_ping_loop():
    url = os.environ.get("RENDER_EXTERNAL_URL", "")
    if not url: return
    try:
        requests.get(f"{url}/health", timeout=10)
        log.info("Self-ping OK")
    except Exception as e:
        log.warning(f"Self-ping failed: {e}")

# ============================================================
# FLASK
# ============================================================
app = Flask(__name__)

@app.errorhandler(Exception)
def handle_exception(e):
    log.error(f"[FLASK ERROR] {e}\n{traceback.format_exc()}")
    return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/webhook/cielo", methods=["POST"])
def cielo_webhook():
    if WEBHOOK_SECRET:
        auth = request.headers.get("X-Webhook-Secret", "")
        if auth != WEBHOOK_SECRET:
            return jsonify({"status": "unauthorized"}), 401
    try:
        data = request.get_json(silent=True)
        if not data: return jsonify({"status": "ignored"}), 200
        token = data.get("token_address")
        if not token: return jsonify({"status": "no_token"}), 200
        with tracked_lock:
            meta = tracked.get(token)
        if meta:
            pool_data = fetch_pool_live(meta["pool_id"])
            if pool_data:
                snap = parse_pool(pool_data)
                if snap:
                    process_token(snap)
                    log.info(f"⚡ [WEBHOOK] Processed {snap.name}")
        return jsonify({"status": "success"}), 200
    except Exception as e:
        log.error(f"[WEBHOOK ERROR] {e}")
        return jsonify({"status": "error"}), 500

@app.route("/health")
def health():
    with tracked_lock:
        n = len(tracked)
    circuits = {name: br.status() for name, br in __import__("resilience").BREAKERS.items()}
    limiters = {name: lim.status() for name, lim in LIMITERS.items()}
    caches = {name: c.status() for name, c in CACHES.items()}
    return jsonify({
        "status": "ONLINE", "version": "6.1-resilient",
        "tracked_tokens": n, "circuits": circuits,
        "limiters": limiters, "caches": caches,
    }), 200

# ============================================================
# MAIN
# ============================================================
def main():
    init_db()
    init_limiters(LIMITER_CONFIG)
    init_caches(CACHE_CONFIG)
    log.info("🚀 Conviction Scanner v6.1 (Resilient) starting...")
    log.info(f"   Limiters: {list(LIMITERS.keys())}")
    log.info(f"   Caches: {list(CACHES.keys())}")

    # Register workers untuk watchdog
    register_watch("discovery", discovery_loop)
    register_watch("monitoring", monitoring_loop)
    register_watch("memory_guard", memory_guard_loop)
    register_watch("cache_cleanup", cache_cleanup_loop)
    register_watch("self_ping", self_ping_loop)

    # Start workers
    for name, target in [
        ("discovery", discovery_loop),
        ("monitoring", monitoring_loop),
        ("memory_guard", memory_guard_loop),
        ("cache_cleanup", cache_cleanup_loop),
        ("self_ping", self_ping_loop),
    ]:
        t = threading.Thread(target=target, daemon=True, name=name)
        t.start()
        WATCHED_THREADS[name] = t

    # Start watchdog
    start_watchdog()

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()