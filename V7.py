"""
================================================================
CONVICTION SCANNER v7 — Final Render Edition
================================================================
"""

# ================================================================
# BAGIAN 1: KONFIGURASI (isi di Render → Environment)
# ================================================================
import os

TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")

CABALSPY_API_KEY = os.environ.get("CABALSPY_API_KEY", "")
ADANOS_API_KEY = os.environ.get("ADANOS_API_KEY", "")
SANTIMENT_API_KEY = os.environ.get("SANTIMENT_API_KEY", "")
VYBE_API_KEY = os.environ.get("VYBE_API_KEY", "")
MOBULA_API_KEY = os.environ.get("MOBULA_API_KEY", "")
MADEONSOL_API_KEY = os.environ.get("MADEONSOL_API_KEY", "")
TNT_RISK_API_KEY = os.environ.get("TNT_RISK_API_KEY", "")
GOPLUS_API_KEY = os.environ.get("GOPLUS_API_KEY", "")
CIELO_API_KEY = os.environ.get("CIELO_API_KEY", "")

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

DB_FILE = os.environ.get("DB_FILE", "/tmp/conviction_v7.db")

# ================================================================
# BAGIAN 2: IMPORT
# ================================================================
import time
import json
import sqlite3
import threading
import traceback
import requests
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, asdict

from flask import Flask, request, jsonify

from resilience import (
    safe_call, resilient_loop, TimeBudget,
    init_limiters, init_caches, LIMITERS, CACHES,
    register_watch, start_watchdog, WATCHED_THREADS, log,
)

# ================================================================
# BAGIAN 3: CEK KONFIGURASI
# ================================================================
def mask(v):
    if not v: return "(kosong)"
    if len(v) <= 8: return "***"
    return v[:4] + "..." + v[-4:]

log.info("=" * 65)
log.info("STATUS KONFIGURASI")
log.info("=" * 65)
log.info(f"  WAJIB:")
log.info(f"    TG_BOT_TOKEN    : {mask(TG_BOT_TOKEN)}")
log.info(f"    TG_CHAT_ID      : {TG_CHAT_ID or '(kosong)'}")
log.info(f"  OPSIONAL:")
log.info(f"    CABALSPY        : {mask(CABALSPY_API_KEY)}")
log.info(f"    ADANOS          : {mask(ADANOS_API_KEY)}")
log.info(f"    SANTIMENT       : {mask(SANTIMENT_API_KEY)}")
log.info(f"    VYBE            : {mask(VYBE_API_KEY)}")
log.info(f"    MOBULA          : {mask(MOBULA_API_KEY)}")
log.info(f"    MADEONSOL       : {mask(MADEONSOL_API_KEY)}")
log.info(f"    TNT_RISK        : {mask(TNT_RISK_API_KEY)}")
log.info(f"    GOPLUS          : {mask(GOPLUS_API_KEY)}")
log.info(f"    CIELO           : {mask(CIELO_API_KEY)}")
log.info("=" * 65)

if not TG_BOT_TOKEN or not TG_CHAT_ID:
    log.warning("TG_BOT_TOKEN atau TG_CHAT_ID KOSONG!")
    log.warning("Bot tidak bisa kirim alert.")

# ================================================================
# BAGIAN 4: ENDPOINT
# ================================================================
GECKO_PUBLIC = "https://api.geckoterminal.com/api/v2"  # fallback (tidak dipakai)
DEXSCREENER_API = "https://api.dexscreener.com"
DEXSCREENER_CHAIN = "solana"
RUGCHECK_API = "https://api.rugcheck.xyz/v1"
TNT_RISK_API = "https://www.tnt-audit.com/api/v1"
MADEONSOL_API = "https://api.madeonsol.com/v1"
CABALSPY_API = "https://api.cabalspy.xyz/v1"
VYBE_API = "https://api.vybenetwork.com/v1"
MOBULA_API = "https://api.mobula.io/api/1"
ADANOS_API = "https://api.adanos.org/v1"
SANTIMENT_API = "https://api.santiment.net/graphql"
GOPLUS_API = "https://api.gopluslabs.io/api/v1"
BINANCE_WEB3_API = "https://web3.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/web/signal/smart-money"
FREE_CRYPTO_NEWS = "https://fcn.dev/api"
NETWORK = "solana"

LIMITER_CONFIG = {
    "dexscreener":           {"per_minute": 200, "per_hour": 5000},
    "dexscreener_profiles":  {"per_minute": 50, "per_hour": 500},
    "dexscreener_pairs":     {"per_minute": 150, "per_hour": 3000},
    "gecko":                 {"per_minute": 8, "per_hour": 200},  # fallback saja
    "rugcheck":              {"per_minute": 8, "per_hour": 400},
    "goplus":                {"per_minute": 20, "per_hour": 500},
    "tnt":                   {"per_minute": 1, "per_hour": 10},
    "madeonsol":             {"per_minute": 8, "per_hour": 150},
    "binance":               {"per_minute": 20, "per_hour": 500},
    "cabalspy":              {"per_minute": 4, "per_hour": 200},
    "vybe":                  {"per_minute": 20, "per_hour": 500},
    "mobula":                {"per_minute": 20, "per_hour": 500},
    "adanos":                {"per_minute": 80, "per_hour": 500, "per_month": 230},
    "santiment":             {"per_minute": 90, "per_hour": 450, "per_month": 900},
    "fcn":                   {"per_minute": 30, "per_hour": 500},
}

CACHE_CONFIG = {
    "security":  {"default_ttl": 1800, "max_size": 2000},
    "market":    {"default_ttl": 25, "max_size": 5000},
    "whale":     {"default_ttl": 120, "max_size": 2000},
    "narrative": {"default_ttl": 600, "max_size": 1000},
    "insider":   {"default_ttl": 900, "max_size": 1000},
}

# ================================================================
# BAGIAN 5: DATA MODEL
# ================================================================
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
    whale_count: int = 0
    top10_holder_pct: float = 0.0
    sm_direction: str = "neutral"
    social_volume_change: float = 0.0
    sentiment_score: float = 0.0
    unique_authors: int = 0
    buzz_score: float = 0.0
    narrative_stage: str = "unknown"
    first_seen: int = 0
    last_updated: int = 0

# ================================================================
# BAGIAN 6: DATABASE
# ================================================================
db_lock = threading.Lock()

def init_db():
    if os.path.dirname(DB_FILE):
        os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
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
    log.info(f"DB initialized: {DB_FILE}")

def upsert_token(snap, conviction):
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

def log_alert(snap, conviction):
    with db_lock:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        conn.execute("""
        INSERT INTO alerts (token, name, conviction, snapshot_json, sent_at)
        VALUES (?, ?, ?, ?, ?)
        """, (snap.token, snap.name, conviction, json.dumps(asdict(snap)), int(time.time())))
        conn.commit()
        conn.close()

def get_token_row(token):
    with db_lock:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM tokens WHERE token=?", (token,))
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else None

# ================================================================
# BAGIAN 7: HTTP HELPER
# ================================================================
session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; ConvictionScanner/7.0)",
    "Accept": "application/json",
})
def http_get(url, params=None, headers=None, timeout=10):
    try:
        return session.get(url, params=params, headers=headers, timeout=timeout)
    except Exception:
        return None

def http_post(url, json_data=None, headers=None, timeout=10):
    try:
        return session.post(url, json=json_data, headers=headers, timeout=timeout)
    except Exception:
        return None

def send_telegram(msg):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        log.info(f"[TG DISABLED] {msg[:80]}")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "Markdown",
                  "disable_web_page_preview": True},
            timeout=10,
        )
        if r.status_code != 200:
            log.warning(f"[TG] HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        log.warning(f"[TG] {e}")

# ================================================================
# BAGIAN 8: SNIFFER
# ================================================================
def sniffer_rugcheck(token):
    def _call():
        r = http_get(f"{RUGCHECK_API}/tokens/{token}/report", timeout=8)
        if r and r.status_code == 200: return r
        return None
    r = safe_call(_call, default=None, breaker_name="rugcheck", limiter_name="rugcheck",
                  cache=CACHES["security"], cache_key=f"rugcheck:{token}",
                  cache_ttl=1800, retries=1)
    if not r:
        return {"safe": False, "score": 100}
    try:
        risks = r.json().get("risks", [])
        danger = sum(1 for x in risks if str(x.get("level", "")).lower() == "danger")
        if danger == 0: return {"safe": True, "score": 20}
        elif danger == 1: return {"safe": False, "score": 60}
        else: return {"safe": False, "score": 95}
    except Exception:
        return {"safe": False, "score": 100}

def sniffer_goplus(token):
    result = {"honeypot": False, "tax": 0}
    params = {"contract_addresses": token, "chain_id": "501"}
    headers = {"Authorization": GOPLUS_API_KEY} if GOPLUS_API_KEY else {}
    def _call():
        r = http_get(f"{GOPLUS_API}/token_security/501",
                     params=params, headers=headers, timeout=8)
        if r and r.status_code == 200: return r.json().get("result", {})
        return None
    data = safe_call(_call, default=None, breaker_name="goplus", limiter_name="goplus",
                     cache=CACHES["security"], cache_key=f"goplus:{token}",
                     cache_ttl=1800, retries=1)
    if data and token in data:
        info = data[token]
        result["honeypot"] = info.get("is_honeypot", "0") == "1"
        result["tax"] = float(info.get("buy_tax", 0)) + float(info.get("sell_tax", 0))
    return result

def sniffer_tnt_risk(token):
    result = {"insider_clusters": 0, "same_first_funder": False, "sniper_share": 0.0}
    if not TNT_RISK_API_KEY: return result
    def _call():
        r = http_get(f"{TNT_RISK_API}/token/{token}/risk",
                     headers={"X-API-KEY": TNT_RISK_API_KEY}, timeout=15)
        if r and r.status_code == 200: return r.json()
        return None
    data = safe_call(_call, default=None, breaker_name="tnt", limiter_name="tnt",
                     cache=CACHES["insider"], cache_key=f"tnt:{token}",
                     cache_ttl=1800, retries=1)
    if data:
        clusters = data.get("insider_clusters", [])
        result["insider_clusters"] = len(clusters) if isinstance(clusters, list) else int(clusters)
        result["same_first_funder"] = data.get("same_first_funder", False)
        result["sniper_share"] = float(data.get("sniper_share", 0))
    return result

def run_sniffer(snap):
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

# ================================================================
# BAGIAN 9: INSIDER TRACKER
# ================================================================
def fetch_insider_tracker(token):
    result = {"kol_entries": 0, "bundle_held_pct": 0.0,
              "sniper_wallets": 0, "insider_wallets": 0, "bundler_wallets": 0}
    if MADEONSOL_API_KEY:
        def _kol():
            r = http_get(f"{MADEONSOL_API}/token/{token}/kol-entries",
                         headers={"Authorization": f"Bearer {MADEONSOL_API_KEY}"}, timeout=8)
            return r.json() if r and r.status_code == 200 else None
        data = safe_call(_kol, default=None, breaker_name="madeonsol", limiter_name="madeonsol",
                         cache=CACHES["insider"], cache_key=f"mad_kol:{token}",
                         cache_ttl=600, retries=1)
        if data:
            result["kol_entries"] = int(data.get("count", 0))
            result["bundle_held_pct"] = float(data.get("bundle_held_pct", 0))
    if MOBULA_API_KEY:
        def _mob():
            r = http_get(f"{MOBULA_API}/wallet/labels", params={"tokenAddress": token},
                         headers={"Authorization": f"Bearer {MOBULA_API_KEY}"}, timeout=8)
            return r.json() if r and r.status_code == 200 else None
        data = safe_call(_mob, default=None, breaker_name="mobula", limiter_name="mobula",
                         cache=CACHES["insider"], cache_key=f"mob:{token}",
                         cache_ttl=900, retries=1)
        if data:
            for lbl in data.get("labels", []):
                t = lbl.get("label", "").lower()
                if "sniper" in t: result["sniper_wallets"] += 1
                elif "insider" in t: result["insider_wallets"] += 1
                elif "bundler" in t: result["bundler_wallets"] += 1
    return result

# ================================================================
# BAGIAN 10: WHALE FLOW
# ================================================================
def fetch_binance_smart_money(token):
    result = {"sm_count": 0, "sm_direction": "neutral"}
    def _call():
        payload = {"page": 1, "pageSize": 100, "chainId": "CT_501"}
        headers = {"Content-Type": "application/json",
                   "Accept-Encoding": "identity",
                   "User-Agent": "binance-web3/1.1"}
        r = http_post(BINANCE_WEB3_API, json_data=payload, headers=headers, timeout=8)
        if r and r.status_code == 200: return r.json()
        return None
    data = safe_call(_call, default=None, breaker_name="binance", limiter_name="binance",
                     cache=CACHES["whale"], cache_key=f"binance_sm:{token}",
                     cache_ttl=120, retries=1)
    if data:
        try:
            for item in data.get("data", []):
                if item.get("contractAddress", "").lower() == token.lower():
                    result["sm_count"] = int(item.get("smartMoneyCount", 0) or 0)
                    result["sm_direction"] = item.get("direction", "neutral")
                    break
        except Exception:
            pass
    return result

def fetch_whale_flow(token):
    result = {"whale_count": 0, "top10_pct": 0.0, "bundle_detected": False,
              "sm_direction": "neutral"}
    bsm = fetch_binance_smart_money(token)
    result["whale_count"] = bsm["sm_count"]
    result["sm_direction"] = bsm["sm_direction"]

    if CABALSPY_API_KEY:
        def _cabal():
            r = http_get(f"{CABALSPY_API}/token/{token}/bundles",
                         headers={"X-API-KEY": CABALSPY_API_KEY}, timeout=8)
            return r.json() if r and r.status_code == 200 else None
        data = safe_call(_cabal, default=None, breaker_name="cabalspy", limiter_name="cabalspy",
                         cache=CACHES["whale"], cache_key=f"cabal:{token}",
                         cache_ttl=120, retries=1)
        if data:
            result["bundle_detected"] = len(data.get("bundles", [])) > 0

    if VYBE_API_KEY:
        def _vybe():
            r = http_get(f"{VYBE_API}/token/{token}/top-holders", params={"limit": 10},
                         headers={"X-API-KEY": VYBE_API_KEY}, timeout=8)
            return r.json() if r and r.status_code == 200 else None
        data = safe_call(_vybe, default=None, breaker_name="vybe", limiter_name="vybe",
                         cache=CACHES["whale"], cache_key=f"vybe:{token}",
                         cache_ttl=300, retries=1)
        if data:
            holders = data.get("holders", [])
            if holders:
                total = sum(float(h.get("amount", 0)) for h in holders)
                supply = float(data.get("total_supply", 1))
                result["top10_pct"] = (total / max(supply, 1)) * 100
    return result

# ================================================================
# BAGIAN 11: NARRATIVE
# ================================================================
def fetch_fcn_sentiment(symbol):
    def _call():
        r = http_get(f"{FREE_CRYPTO_NEWS}/social/x/sentiment",
                     params={"token": symbol}, timeout=8)
        if r and r.status_code == 200: return r.json()
        return None
    return safe_call(_call, default=None, breaker_name="fcn", limiter_name="fcn",
                     cache=CACHES["narrative"], cache_key=f"fcn:{symbol}",
                     cache_ttl=600, retries=1)

def fetch_narrative(token, symbol=""):
    result = {"social_volume": 0.0, "volume_change": 0.0, "sentiment": 0.0,
              "unique_authors": 0, "buzz_score": 0.0,
              "narrative_stage": "unknown", "narrative_score": 0.0}
    if ADANOS_API_KEY:
        def _adanos():
            r = http_get(f"{ADANOS_API}/asset",
                         params={"ticker": symbol or token, "days": 7},
                         headers={"X-API-KEY": ADANOS_API_KEY}, timeout=8)
            return r.json() if r and r.status_code == 200 else None
        data = safe_call(_adanos, default=None, breaker_name="adanos", limiter_name="adanos",
                         cache=CACHES["narrative"], cache_key=f"adanos:{symbol or token}",
                         cache_ttl=600, retries=1)
        if data:
            result["buzz_score"] = float(data.get("buzz_score", 0))
            result["sentiment"] = float(data.get("sentiment", 0))
            result["unique_authors"] = int(data.get("unique_authors", 0))

    fcn = fetch_fcn_sentiment(symbol or token)
    if fcn:
        try:
            x_sent = float(fcn.get("sentiment", 0) or 0)
            if result["sentiment"] == 0:
                result["sentiment"] = x_sent
            elif x_sent != 0:
                result["sentiment"] = (result["sentiment"] + x_sent) / 2
        except Exception:
            pass

    if SANTIMENT_API_KEY:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        yest = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        query = """
        { getMetric(metric: "social_volume_total") {
            timeseriesData(slug: "%s", from: "%s", to: "%s", interval: "1d") {
              datetime value } } }
        """ % (token.lower(), yest, today)
        def _sant():
            r = http_get(SANTIMENT_API, params={"query": query},
                         headers={"Authorization": f"Bearer {SANTIMENT_API_KEY}"},
                         timeout=12)
            return r.json() if r and r.status_code == 200 else None
        data = safe_call(_sant, default=None, breaker_name="santiment", limiter_name="santiment",
                         cache=CACHES["narrative"], cache_key=f"sant:{token}",
                         cache_ttl=600, retries=1)
        if data:
            ts = data.get("data", {}).get("getMetric", {}).get("timeseriesData", [])
            if len(ts) >= 2:
                prev = ts[-2]["value"]
                if prev > 0:
                    result["volume_change"] = ((ts[-1]["value"] - prev) / prev) * 100

    result["narrative_stage"] = classify_narrative_stage(result)
    result["narrative_score"] = calculate_narrative_score(result)
    return result

def classify_narrative_stage(d):
    vc = d.get("volume_change", 0)
    au = d.get("unique_authors", 0)
    se = d.get("sentiment", 0)
    if vc < 20 and se > 0: return "pre-narrative"
    if vc > 50 and au > 10 and se > 0.3: return "emergence"
    if vc > 150 and au > 50: return "acceleration"
    if vc > 500 and au > 100: return "peak"
    if vc < 0: return "declining"
    return "unknown"

def calculate_narrative_score(d):
    s = 0.0
    vc, se, au = d.get("volume_change", 0), d.get("sentiment", 0), d.get("unique_authors", 0)
    stage = d.get("narrative_stage", "unknown")
    if vc > 200: s += 30
    elif vc > 100: s += 20
    elif vc > 50: s += 10
    if se > 0.7: s += 25
    elif se > 0.4: s += 15
    elif se > 0: s += 5
    elif se < -0.3: s -= 10
    s += min(20, (au / max(d.get("total_mentions", 1), 1)) * 40)
    s += {"pre-narrative": 5, "emergence": 15, "acceleration": 10,
          "peak": 0, "declining": -10}.get(stage, 0)
    s += min(10, d.get("buzz_score", 0) / 10)
    return max(0, min(100, s))

# ================================================================
# BAGIAN 12: EUPHORIA
# ================================================================
def detect_euphoria(snap):
    result = {"euphoria_score": 0.0, "is_healthy": True, "reasons": []}
    if snap.liquidity > 0:
        vlr = snap.volume_1h / snap.liquidity
        if vlr > MAX_VOL_LIQ_RATIO:
            result["euphoria_score"] += 30
            result["reasons"].append(f"Volume/Liq {vlr:.1f}x")
        elif vlr > 3.0:
            result["euphoria_score"] += 15
    total = snap.buys_1h + snap.sells_1h
    if total > 0:
        br = snap.buys_1h / total
        if br > 0.85:
            result["euphoria_score"] += 20
            result["reasons"].append(f"Buy ratio {br:.0%} FOMO")
        elif br < 0.35:
            result["euphoria_score"] += 25
            result["reasons"].append(f"Buy ratio {br:.0%} dump")
    if snap.price_change_5m > 40 and snap.liquidity < 50000:
        result["euphoria_score"] += 25
    if snap.top10_holder_pct > 35:
        result["euphoria_score"] += 20
    if snap.insider_clusters > 3:
        result["euphoria_score"] += 25
    result["euphoria_score"] = min(100, result["euphoria_score"])
    result["is_healthy"] = result["euphoria_score"] < 50
    return result

# ================================================================
# BAGIAN 13: CONVICTION
# ================================================================
def calculate_conviction(snap, insider, whale, narrative, euphoria):
    score = 0.0
    if snap.honeypot: return 0.0
    if not snap.mint_authority and not snap.freeze_authority: score += 20
    if snap.lp_burned: score += 5
    ins = 0.0
    if snap.insider_clusters == 0: ins += 10
    if not snap.same_first_funder: ins += 5
    if snap.sniper_share < 0.05: ins += 3
    if insider.get("sniper_wallets", 0) == 0: ins += 2
    score += min(20, ins)
    wf = 0.0
    if whale["whale_count"] >= 3: wf += 8
    elif whale["whale_count"] >= 1: wf += 4
    if not whale.get("bundle_detected"): wf += 3
    if whale["top10_pct"] < 20: wf += 4
    elif whale["top10_pct"] > 40: wf -= 6
    score += max(0, min(15, wf))
    nar = narrative.get("narrative_score", 0)
    st = narrative.get("narrative_stage", "unknown")
    if st == "emergence": nar = min(100, nar * 1.3)
    elif st == "peak": nar *= 0.3
    elif st == "declining": nar = 0
    score += (nar / 100) * 20
    sm = 0.0
    if whale.get("whale_count", 0) >= 2: sm += 5
    if whale.get("top10_pct", 100) < 25: sm += 5
    score += min(10, sm)
    mom = 0.0
    if snap.liquidity >= MIN_LIQ_VALIDATION: mom += 2
    if snap.volume_1h >= MIN_VOL_1H: mom += 2
    if snap.buyers_1h >= 15: mom += 1
    score += min(5, mom)
    score += max(0, 5 * (1 - euphoria["euphoria_score"] / 100))
    return round(min(100, max(0, score)), 1)

# ================================================================
# BAGIAN 14: GECKOTERMINAL
# ================================================================
def fetch_new_pools():
    """
    Discovery via DexScreener: ambil token profiles terbaru,
    filter Solana, lalu ambil pairs untuk masing-masing token.
    """
    # Step 1: ambil latest token profiles
    def _get_profiles():
        r = http_get(f"{DEXSCREENER_API}/token-profiles/latest/v1", timeout=10)
        if r and r.status_code == 200:
            return r.json()
        return None

    profiles = safe_call(_get_profiles, default=None, breaker_name="dexscreener_profiles",
                         limiter_name="dexscreener", cache=CACHES["market"],
                         cache_key="ds_profiles_latest", cache_ttl=60, retries=1)

    if not profiles:
        log.info("[DEX] No profiles fetched")
        return []

    # Step 2: filter Solana, ambil token address
    solana_tokens = []
    for p in profiles:
        if p.get("chainId") == DEXSCREENER_CHAIN:
            addr = p.get("tokenAddress")
            if addr:
                solana_tokens.append(addr)

    log.info(f"[DEX] {len(solana_tokens)} Solana profiles fetched")

    # Step 3: untuk setiap token, ambil pairs (batasi 30 untuk hemat rate limit)
    pools = []
    for token_addr in solana_tokens[:30]:
        def _get_pairs(addr=token_addr):
            r = http_get(f"{DEXSCREENER_API}/token-pairs/v1/{DEXSCREENER_CHAIN}/{addr}",
                         timeout=8)
            if r and r.status_code == 200:
                return r.json()
            return None

        data = safe_call(_get_pairs, default=None, breaker_name="dexscreener_pairs",
                         limiter_name="dexscreener", cache=CACHES["market"],
                         cache_key=f"ds_pairs:{token_addr}", cache_ttl=30, retries=1)

        if data and isinstance(data, list):
            pools.extend(data)

    log.info(f"[DEX] {len(pools)} pairs collected")
    return pools

def fetch_pool_live(pair_address):
    """Live pair data via DexScreener. Return single pair dict."""
    def _call():
        r = http_get(f"{DEXSCREENER_API}/latest/dex/pairs/{DEXSCREENER_CHAIN}/{pair_address}",
                     timeout=8)
        if r and r.status_code == 200:
            data = r.json()
            pairs = data.get("pairs", [])
            return pairs[0] if pairs else None
        return None
    return safe_call(_call, default=None, breaker_name="dexscreener",
                     limiter_name="dexscreener", cache=CACHES["market"],
                     cache_key=f"ds_pair:{pair_address}", cache_ttl=25, retries=1)

def parse_pool(pool):
    """
    Parse DexScreener pair response ke TokenSnapshot.
    DexScreener response format berbeda dari GeckoTerminal.
    """
    try:
        # DexScreener: baseToken.address, pairAddress, liquidity.usd, volume.h1, dll
        base_token = pool.get("baseToken", {})
        token = base_token.get("address", "")
        if not token:
            return None

        txns_h1 = pool.get("txns", {}).get("h1", {})
        volume_h1 = float(pool.get("volume", {}).get("h1", 0) or 0)
        price_change = pool.get("priceChange", {})
        liquidity = float(pool.get("liquidity", {}).get("usd", 0) or 0)

        return TokenSnapshot(
            token=token,
            name=base_token.get("name", "Unknown"),
            symbol=base_token.get("symbol", ""),
            pool_id=pool.get("pairAddress", ""),
            price=float(pool.get("priceUsd", 0) or 0),
            liquidity=liquidity,
            volume_1h=volume_h1,
            buys_1h=int(txns_h1.get("buys", 0) or 0),
            sells_1h=int(txns_h1.get("sells", 0) or 0),
            buyers_1h=int(txns_h1.get("buys", 0) or 0),
            sellers_1h=int(txns_h1.get("sells", 0) or 0),
            price_change_1h=float(price_change.get("h1", 0) or 0),
            price_change_5m=float(price_change.get("m5", 0) or 0),
            txns_1h=int(txns_h1.get("buys", 0) or 0) + int(txns_h1.get("sells", 0) or 0),
            first_seen=int(time.time()),
        )
    except Exception as e:
        log.warning(f"[PARSE] error: {e}")
        return None

# ================================================================
# BAGIAN 15: PROCESS TOKEN
# ================================================================
tracked = {}
tracked_lock = threading.Lock()

def process_token(snap):
    budget = TimeBudget(20, f"token:{snap.name[:20]}")
    snap = run_sniffer(snap)
    if snap.honeypot:
        log.info(f"HONEYPOT: {snap.name} ({snap.token})")
        return
    if budget.check("sniffer"): return
    insider = fetch_insider_tracker(snap.token)
    if budget.check("insider"): return
    whale = fetch_whale_flow(snap.token)
    if budget.check("whale"): return
    narrative = fetch_narrative(snap.token, snap.symbol)
    if budget.check("narrative"): return
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
    if snap.mint_authority: flags.append("Mint authority aktif")
    if snap.freeze_authority: flags.append("Freeze authority aktif")
    if not snap.lp_burned: flags.append("LP belum burn")
    if snap.insider_clusters > 3: flags.append(f"{snap.insider_clusters} insider clusters")
    if snap.same_first_funder: flags.append("Same first funder")
    if whale.get("bundle_detected"): flags.append("Bundle detected")
    if euphoria["euphoria_score"] > 50:
        flags.append(f"Euphoria {euphoria['euphoria_score']:.0f}/100")
    flag_text = "\n".join(flags) if flags else "Tidak ada red flag utama"
    msg = (
        f"CONVICTION ALERT v7 - Skor {conviction}/100\n"
        f"=====================================\n"
        f"Nama: {snap.name} ({snap.symbol})\n"
        f"Token: {snap.token}\n\n"
        f"Harga: ${snap.price:.8f}\n"
        f"Likuiditas: ${snap.liquidity:,.0f}\n"
        f"Volume 1H: ${snap.volume_1h:,.0f}\n"
        f"Perubahan 1H: {snap.price_change_1h:+.1f}%\n"
        f"Buyer/Seller: {snap.buyers_1h}/{snap.sellers_1h}\n\n"
        f"Security - RugCheck: {snap.rugcheck_score}/100\n"
        f"Whale: {whale['whale_count']} smart money | Top10: {whale['top10_pct']:.1f}%\n"
        f"Narrative: {narrative.get('narrative_stage','unknown')} | "
        f"Sent: {narrative.get('sentiment',0):+.2f}\n"
        f"Euphoria: {euphoria['euphoria_score']:.0f}/100\n\n"
        f"Red Flags:\n{flag_text}\n\n"
        f"DEXScreener: {dex_url}"
    )
    send_telegram(msg)
    log_alert(snap, conviction)

# ================================================================
# BAGIAN 16: WORKERS
# ================================================================
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
            sorted_items = sorted(tracked.items(), key=lambda x: x[1].get("first_seen", 0))
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

# ================================================================
# BAGIAN 17: FLASK — dengan route "/"
# ================================================================
app = Flask(__name__)

@app.errorhandler(404)
def handle_404(e):
    return jsonify({
        "status": "not_found",
        "hint": "Gunakan /health atau /test-alert",
    }), 404

@app.errorhandler(Exception)
def handle_exception(e):
    log.error(f"[FLASK ERROR] {e}")
    return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/")
def root():
    with tracked_lock:
        n = len(tracked)
    return jsonify({
        "name": "Conviction Scanner v7",
        "status": "ONLINE",
        "tg_configured": bool(TG_BOT_TOKEN and TG_CHAT_ID),
        "tracked_tokens": n,
        "endpoints": {
            "health": "/health",
            "test_alert": "/test-alert",
        },
    }), 200
@app.route("/debug/tokens")
def debug_tokens():
    """Lihat semua tracked token dan conviction score-nya."""
    with db_lock:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT token, name, conviction, alert_sent FROM tokens ORDER BY conviction DESC LIMIT 50")
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
    with tracked_lock:
        tracked_count = len(tracked)
    return jsonify({
        "tracked_count": tracked_count,
        "threshold": MIN_CONVICTION_ALERT,
        "tokens": rows,
    }), 200


@app.route("/debug/force-alert")
def force_alert():
    """Force kirim alert untuk test — abaikan threshold."""
    send_telegram(
        "FORCE ALERT TEST\n\n"
        "Kalau kamu lihat pesan ini, Telegram API bekerja.\n"
        f"Threshold saat ini: {MIN_CONVICTION_ALERT}/100\n"
        f"Tracked tokens: {len(tracked)}"
    )
    return jsonify({"status": "sent", "threshold": MIN_CONVICTION_ALERT}), 200
@app.route("/health")
def health():
    with tracked_lock:
        n = len(tracked)
    return jsonify({
        "status": "ONLINE",
        "version": "7.0",
        "tg_configured": bool(TG_BOT_TOKEN and TG_CHAT_ID),
        "tracked_tokens": n,
    }), 200

# ================================================================
# BAGIAN 18: MAIN
# ================================================================
def main():
    init_db()
    init_limiters(LIMITER_CONFIG)
    init_caches(CACHE_CONFIG)
    log.info("Conviction Scanner v7 starting...")

    register_watch("discovery", discovery_loop)
    register_watch("monitoring", monitoring_loop)
    register_watch("memory_guard", memory_guard_loop)
    register_watch("cache_cleanup", cache_cleanup_loop)

    for name, target in [
        ("discovery", discovery_loop),
        ("monitoring", monitoring_loop),
        ("memory_guard", memory_guard_loop),
        ("cache_cleanup", cache_cleanup_loop),
    ]:
        t = threading.Thread(target=target, daemon=True, name=name)
        t.start()
        WATCHED_THREADS[name] = t

    start_watchdog()

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, use_reloader=False, threaded=True)

if __name__ == "__main__":
    main()
