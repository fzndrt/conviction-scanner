"""
================================================================
CONVICTION SCANNER v7 — Render Edition
================================================================
Semua konfigurasi diambil dari Environment Variables Render.
Tidak ada file .env — cukup set env vars di dashboard Render.
================================================================
"""

# ================================================================
# BAGIAN 1: KONFIGURASI (ISI DI RENDER → ENVIRONMENT)
# ================================================================
#
# ⚠️ WAJIB DIISI di Render → Environment:
#   TG_BOT_TOKEN    = 8116104196:AAFxvC-xDEqBIM40nRGFBAn9_ndR9dG5Fm0
#   TG_CHAT_ID      = 1233826806
#
# 📝 OPSIONAL (isi kalau punya):
#   CABALSPY_API_KEY, ADANOS_API_KEY, SANTIMENT_API_KEY,
#   VYBE_API_KEY, MOBULA_API_KEY, MADEONSOL_API_KEY,
#   TNT_RISK_API_KEY, GOPLUS_API_KEY, CIELO_API_KEY
#
# ✅ TIDAK PERLU ISI (endpoint publik):
#   Binance Web3, free-crypto-news, GeckoTerminal, RugCheck
# ================================================================

import os

# ----- ⚠️ WAJIB -----
TG_BOT_TOKEN = os.environ.get("8116104196:AAFxvC-xDEqBIM40nRGFBAn9_ndR9dG5Fm0", "")
TG_CHAT_ID = os.environ.get("1233826806", "")

# ----- 📝 OPSIONAL -----
CABALSPY_API_KEY = os.environ.get("0lrKf1cI7gVKEoVoUv7EdXztVnV_NctGoM7mA-Rnxoo", "")
ADANOS_API_KEY = os.environ.get("sk_live_77b163ad2aeae1e18142f7ccde42cf89", "")
SANTIMENT_API_KEY = os.environ.get("huv7lodgehllk2pg_bnnaaaldukvo5wk5", "")
VYBE_API_KEY = os.environ.get("https://api.vybenetwork.xyz", "")
MOBULA_API_KEY = os.environ.get("f20e3df8-b790-4919-aea6-5d8a78c36cbd", "")
MADEONSOL_API_KEY = os.environ.get("msk_AynSEYvONwRo4J8UZaolrng_d4aBWwUpGPjiGj7qw1o", "")
TNT_RISK_API_KEY = os.environ.get("tnt_sk_666c437cdb9b903aaf5226060bf2e312e978268f8aab450a", "")
GOPLUS_API_KEY = os.environ.get("8AD881qrwNzDQDspCrNC", "")
CIELO_API_KEY = os.environ.get("CIELO_API_KEY", "")

# ----- 🔐 Webhook secret (opsional) -----
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

# ----- ⚙️ Threshold (default aman) -----
MIN_LIQ_DISCOVERY = float(os.environ.get("MIN_LIQ_DISCOVERY", "8000"))
MIN_LIQ_VALIDATION = float(os.environ.get("MIN_LIQ_VALIDATION", "25000"))
MIN_VOL_1H = float(os.environ.get("MIN_VOL_1H", "15000"))
MAX_VOL_LIQ_RATIO = float(os.environ.get("MAX_VOL_LIQ_RATIO", "6.0"))
MIN_CONVICTION_ALERT = float(os.environ.get("MIN_CONVICTION_ALERT", "75"))

SCAN_INTERVAL = int(os.environ.get("SCAN_INTERVAL", "30"))
DISCOVERY_INTERVAL = int(os.environ.get("DISCOVERY_INTERVAL", "120"))
MAX_TRACKED = int(os.environ.get("MAX_TRACKED", "100"))
TOKEN_TTL_HOURS = int(os.environ.get("TOKEN_TTL_HOURS", "24"))

# Render Free Tier: filesystem ephemeral.
# DB akan reset setiap restart — tidak masalah untuk scanner.
DB_FILE = os.environ.get("DB_FILE", "/tmp/conviction_v7.db")

# ================================================================
# BAGIAN 2: IMPORT & SETUP
# ================================================================

import time
import json
import sqlite3
import threading
import traceback
import requests
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, asdict
from typing import Optional

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
log.info("🔍 STATUS KONFIGURASI")
log.info("=" * 65)
log.info(f"  ⚠️  WAJIB:")
log.info(f"      TG_BOT_TOKEN    : {mask(TG_BOT_TOKEN)}")
log.info(f"      TG_CHAT_ID      : {1233826806 or '(kosong)'}")
log.info(f"  📝 OPSIONAL:")
log.info(f"      CABALSPY        : {mask(0lrKf1cI7gVKEoVoUv7EdXztVnV_NctGoM7mA-Rnxoo)}")
log.info(f"      ADANOS          : {mask(sk_live_77b163ad2aeae1e18142f7ccde42cf89)}")
log.info(f"      SANTIMENT       : {mask(huv7lodgehllk2pg_bnnaaaldukvo5wk5)}")
log.info(f"      VYBE            : {mask(https://api.vybenetwork.xyz)}")
log.info(f"      MOBULA          : {mask(f20e3df8-b790-4919-aea6-5d8a78c36cbd)}")
log.info(f"      MADEONSOL       : {mask(msk_AynSEYvONwRo4J8UZaolrng_d4aBWwUpGPjiGj7qw1o)}")
log.info(f"      TNT_RISK        : {mask(tnt_sk_666c437cdb9b903aaf5226060bf2e312e978268f8aab450a)}")
log.info(f"      GOPLUS          : {mask(8AD881qrwNzDQDspCrNC)}")
log.info(f"      CIELO           : {mask(CIELO_API_KEY)}")
log.info(f"  ✅ PUBLIC: Binance Web3, free-crypto-news, Gecko, RugCheck")
log.info("=" * 65)


if not TG_BOT_TOKEN or not TG_CHAT_ID:
    log.warning("⚠️  TG_BOT_TOKEN atau TG_CHAT_ID KOSONG!")
    log.warning("⚠️  Bot tidak bisa kirim alert. Isi di Render → Environment.")

# ================================================================
# BAGIAN 4: ENDPOINT & KONFIG API
# ================================================================

GECKO_PUBLIC = "https://api.geckoterminal.com/api/v2"
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
    "gecko":      {"per_minute": 25, "per_hour": 1200},
    "rugcheck":   {"per_minute": 8, "per_hour": 400},
    "goplus":     {"per_minute": 20, "per_hour": 500},
    "tnt":        {"per_minute": 1, "per_hour": 10},
    "madeonsol":  {"per_minute": 8, "per_hour": 150},
    "binance":    {"per_minute": 20, "per_hour": 500},
    "cabalspy":   {"per_minute": 4, "per_hour": 200},
    "vybe":       {"per_minute": 20, "per_hour": 500},
    "mobula":     {"per_minute": 20, "per_hour": 500},
    "adanos":     {"per_minute": 80, "per_hour": 500, "per_month": 230},
    "santiment":  {"per_minute": 90, "per_hour": 450, "per_month": 900},
    "fcn":        {"per_minute": 30, "per_hour": 500},
}

CACHE_CONFIG = {
    "security":  {"default_ttl": 1800, "max_size": 2000},
    "market":    {"default_ttl": 25, "max_size": 5000},
    "whale":     {"default_ttl": 120, "max_size": 2000},
    "narrative": {"default_ttl": 600, "max_size": 1000},
    "insider":   {"default_ttl": 900, "max_size": 1000},
}

# ... (lanjutan sama seperti jawaban sebelumnya — dataclass, DB, HTTP helper,
#      sniffer, insider, whale, narrative, euphoria, conviction, workers, Flask)

# ================================================================
# BAGIAN 16: FLASK — endpoint /health untuk UptimeRobot
# ================================================================

app = Flask(__name__)


@app.errorhandler(Exception)
def handle_exception(e):
    log.error(f"[FLASK ERROR] {e}\n{traceback.format_exc()}")
    return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/health")
def health():
    """Endpoint untuk UptimeRobot. Selalu return 200."""
    with tracked_lock:
        n = len(tracked)
    return jsonify({
        "status": "ONLINE",
        "version": "7.0",
        "tg_configured": bool(TG_BOT_TOKEN and TG_CHAT_ID),
        "tracked_tokens": n,
    }), 200


@app.route("/test-alert")
def test_alert():
    """HAPUS setelah test berhasil."""
    send_telegram("🧪 *TEST ALERT*\n\nBot Telegram sudah terhubung!")
    return jsonify({"status": "sent"}), 200


# ================================================================
# BAGIAN 17: MAIN
# ================================================================

def main():
    init_db()
    init_limiters(LIMITER_CONFIG)
    init_caches(CACHE_CONFIG)
    log.info("🚀 Conviction Scanner v7 starting...")

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
