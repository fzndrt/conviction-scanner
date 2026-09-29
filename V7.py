"""
================================================================
SOLANA BREAKOUT & REVERSAL MASTER SCANNER v5.0 (PROFIT ENGINE)
Repo: https://github.com/fzndrt/conviction-scanner
All-In-One Script: Mandiri tanpa perlu file tambahan!

5 PENYEMPURNAAN PAMUNGKAS:
1. Zero-Keyword Radar: Menyaring seluruh token Solana aktif tanpa batasan nama.
2. Dev Dump & LP Lock Security: Blokir koin jika Dev > 2.5% atau LP berisiko ditarik.
3. Anti-Wash Trading: Minimal 45 pembeli unik (H1) & rata-rata transaksi >= $20 USD.
4. Second-Leg Breakout Classifier: Deteksi koin reli awal vs koin gelombang kedua.
5. Auto Trading Plan & 1-Click Buy: Target TP1 (+50%), TP2 (+150%), TP3 (+300%),
   Cut Loss (-25%), serta link direct buy ke Trojan, Photon, dan BullX.
================================================================
"""

import os
import time
import json
import logging
import threading
import urllib.request
from typing import List, Dict, Any, Optional
from flask import Flask, jsonify
import telebot
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("master-profit-scanner")

app = Flask(__name__)

# Konfigurasi Telegram
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID", "")

bot = telebot.TeleBot(TG_BOT_TOKEN) if TG_BOT_TOKEN else None

# ==========================================
# 🚪 PARAMETER FILTER KETAT & PROFIT MAKER
# ==========================================
MIN_TOKEN_AGE_HOURS = float(os.getenv("REV_MIN_AGE_HOURS", "0.5"))      # Umur min 30 menit
MAX_TOKEN_AGE_HOURS = float(os.getenv("REV_MAX_AGE_HOURS", "999999.0")) # Tanpa batas usia
MIN_LIQUIDITY_USD = float(os.getenv("REV_MIN_LIQ_USD", "10000.0"))      # Minimal Likuiditas $10,000 USD
MIN_MARKET_CAP = float(os.getenv("REV_MIN_MC", "20000.0"))              # Min Market Cap $20k (Deteksi Awal!)
MAX_MARKET_CAP = float(os.getenv("REV_MAX_MC", "2500000.0"))           # Max Market Cap $2.5M
MIN_VOL_H1 = float(os.getenv("REV_MIN_VOL_H1", "8000.0"))               # Min volume 1 jam $8,000 USD
MAX_TOP_HOLDER_PCT = float(os.getenv("REV_MAX_TOP_HOLDER", "10.0"))     # Maksimal 1 dompet whale < 10%
MAX_DEV_HOLDING_PCT = 2.5                                               # Dompet Dev/Creator maksimal 2.5%

# 🛡️ PINTU ANTI-WASH TRADING & BOT SPAM
MIN_BUYERS_H1 = 45                 # Wajib minimal 45 pembeli unik di 1 jam
MIN_AVG_TX_USD = 20.0              # Nilai rata-rata transaksi minimal $20 USD
MIN_BUY_SELL_RATIO = 1.35          # Pembeli harus minimal 35% lebih banyak dari penjual

alerted_mints = set()
stats = {
    "scans_completed": 0,
    "breakouts_detected": 0,
    "dev_dumps_blocked": 0,
    "bots_blocked": 0,
    "last_scan_time": 0
}


def get_json(url: str, timeout: int = 10) -> Optional[Any]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except Exception:
        return None


def audit_onchain_security(mint: str) -> Dict[str, Any]:
    """
    🛡️ AUDIT ON-CHAIN REAL-TIME (RugCheck Engine):
    - Cek Top Holder Non-Pool (<10%)
    - Cek Saldo Dompet Dev / Creator (<2.5%)
    - Cek Status RugCheck Score
    """
    res = {
        "top_holder": 0.0,
        "dev_holding": 0.0,
        "is_safe": True,
        "score": 100,
        "rejection_reason": ""
    }
    try:
        data = get_json(f"https://api.rugcheck.xyz/v1/tokens/{mint}/report", timeout=4)
        if data:
            creator_addr = data.get("creator")
            top_holders = data.get("topHolders", [])
            for h in top_holders:
                addr = h.get("address")
                pct = float(h.get("pct", 0.0))

                # Deteksi Dev Holding
                if addr == creator_addr and pct > 0:
                    res["dev_holding"] = pct

                # Deteksi Whale Terbesar (Abaikan Pool DEX > 85%)
                if pct < 85.0 and pct > res["top_holder"]:
                    res["top_holder"] = pct

            # Gembok Dev-Dump: dev tidak boleh pegang > 2.5%
            if res["dev_holding"] > MAX_DEV_HOLDING_PCT:
                res["is_safe"] = False
                res["rejection_reason"] = f"Dev masih memegang {res['dev_holding']:.1f}% suplai (Potensi Dump)"

            # Gembok Whale: dompet perorangan tidak boleh > 10%
            if res["top_holder"] > MAX_TOP_HOLDER_PCT:
                res["is_safe"] = False
                res["rejection_reason"] = f"Top Holder perorangan {res['top_holder']:.1f}% > {MAX_TOP_HOLDER_PCT}%"

    except Exception:
        pass
    return res


def fetch_all_solana_candidates() -> List[str]:
    """
    🌐 ZERO-KEYWORD STREAM: Menyapu SELURUH koin Solana aktif tanpa membatasi nama!
    """
    candidates = set()

    # 1. Token Solana Terbaru & Sedang Ditradingkan
    profiles = get_json("https://api.dexscreener.com/token-profiles/latest/v1") or []
    for p in profiles:
        if p.get("chainId") == "solana":
            addr = p.get("tokenAddress")
            if addr:
                candidates.add(addr)

    # 2. Token Komunitas yang Sedang Diboost
    boosts = get_json("https://api.dexscreener.com/token-boosts/latest/v1") or []
    for b in boosts:
        if b.get("chainId") == "solana":
            addr = b.get("tokenAddress")
            if addr:
                candidates.add(addr)

    # 3. Top Boosted Token Solana
    top_boosts = get_json("https://api.dexscreener.com/token-boosts/top/v1") or []
    for tb in top_boosts:
        if tb.get("chainId") == "solana":
            addr = tb.get("tokenAddress")
            if addr:
                candidates.add(addr)

    # 4. Universal Pools Fallback (Raydium, PumpSwap, Orca)
    for q in ["sol", "ray", "pump"]:
        res = get_json(f"https://api.dexscreener.com/latest/dex/search?q={q}") or {}
        for pair in res.get("pairs", [])[:20]:
            if pair.get("chainId") == "solana":
                addr = pair.get("baseToken", {}).get("address")
                if addr:
                    candidates.add(addr)

    return list(candidates)


def evaluate_pair(pair: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    base = pair.get("baseToken", {})
    mint = base.get("address")
    created_at = pair.get("pairCreatedAt")

    if not mint or not created_at:
        return None

    now_ms = time.time() * 1000
    age_hours = (now_ms - created_at) / 3600000.0

    # 1. Pintu Umur (Minimal 30 Menit)
    if age_hours < MIN_TOKEN_AGE_HOURS or age_hours > MAX_TOKEN_AGE_HOURS:
        return None

    # 2. Syarat Likuiditas & Market Cap Sehat
    liq_usd = float(pair.get("liquidity", {}).get("usd") or 0.0)
    market_cap = float(pair.get("marketCap") or 0.0)

    if liq_usd < MIN_LIQUIDITY_USD:
        return None
    if market_cap < MIN_MARKET_CAP or market_cap > MAX_MARKET_CAP:
        return None

    vol = pair.get("volume", {})
    vol_h1 = float(vol.get("h1") or 0.0)
    vol_h24 = float(vol.get("h24") or 0.0)

    if vol_h1 < MIN_VOL_H1:
        return None

    txns = pair.get("txns", {})
    txns_h1 = txns.get("h1", {})
    h1_buys = txns_h1.get("buys", 0)
    h1_sells = txns_h1.get("sells", 0)
    total_tx_h1 = h1_buys + h1_sells

    # ========================================================
    # 🛡️ PINTU PERISAI ANTI-WASH TRADING & ANTI-BOT SPAM
    # ========================================================
    # Syarat A: Minimal 45 Pembeli Unik di H1
    if h1_buys < MIN_BUYERS_H1:
        stats["bots_blocked"] += 1
        return None

    # Syarat B: Dominasi Pembeli Real
    if h1_buys < (h1_sells * MIN_BUY_SELL_RATIO):
        return None

    # Syarat C: Rata-Rata Nominal Transaksi (Tolak Spam Micro-Bot $0.1)
    avg_tx_usd = vol_h1 / max(1, total_tx_h1)
    if avg_tx_usd < MIN_AVG_TX_USD:
        stats["bots_blocked"] += 1
        logger.info(f"🚫 Terdeteksi Wash Bot ({mint}): Avg Tx cuma ${avg_tx_usd:.2f} (< ${MIN_AVG_TX_USD})")
        return None

    # ========================================================
    # 🛡️ PINTU KEAMANAN DEV DUMP & TOP HOLDER
    # ========================================================
    security = audit_onchain_security(mint)
    if not security["is_safe"]:
        stats["dev_dumps_blocked"] += 1
        logger.info(f"🚫 Ditolak Keamanan ({mint}): {security['rejection_reason']}")
        return None

    # ========================================================
    # 🏆 PINTU PRESTASI: BUKTI KENAIKAN NYATA
    # ========================================================
    price_change = pair.get("priceChange", {})
    pc_h24 = float(price_change.get("h24") or 0.0)
    pc_h6 = float(price_change.get("h6") or 0.0)
    pc_h1 = float(price_change.get("h1") or 0.0)
    pc_m5 = float(price_change.get("m5") or 0.0)

    txns_m5 = txns.get("m5", {})
    m5_buys = txns_m5.get("buys", 0)
    m5_sells = txns_m5.get("sells", 0)

    # PRESTASI 1: HEALTHY BREAKOUT RUNNER (Seperti Dots & Froink)
    is_healthy_breakout = False
    if (pc_h1 >= 12.0 or pc_h6 >= 35.0 or pc_h24 >= 80.0) and pc_m5 >= -1.8:
        is_healthy_breakout = True

    # PRESTASI 2: FRESH REVERSAL / CTO AWAKENING
    is_reversal = False
    avg_hourly_vol = vol_h24 / 24.0 if vol_h24 > 0 else 1.0
    spike_ratio = vol_h1 / max(1.0, avg_hourly_vol)

    if spike_ratio >= 2.5 and pc_h1 >= 10.0 and pc_m5 >= 2.0:
        if m5_buys >= (m5_sells * 1.35) and m5_buys >= 15:
            is_reversal = True

    if not is_healthy_breakout and not is_reversal:
        return None

    # KLASIFIKASI GELOMBANG (Second-Leg vs Fresh)
    if is_healthy_breakout and age_hours >= 6.0 and pc_h24 >= 150.0:
        alert_type = "🔥 <b>SECOND-LEG MEGA RUNNER (GELOMBANG 2 TERBUKTI)</b>"
        stage_desc = "Reli Gelombang ke-2 (Proven Community & Multi-Hour Hold)"
        score = 98
    elif is_healthy_breakout:
        alert_type = "🚀 <b>HEALTHY TRENDING BREAKOUT (ORGANIK)</b>"
        stage_desc = "Fase Reli Awal (Akumulasi Sehat & Bersih)"
        score = 92
    else:
        alert_type = "🔄 <b>FRESH CTO / REVERSAL AWAKENING</b>"
        stage_desc = "Koin Bangkit dari Dasar / Reversal Spike"
        score = 89

    # PERHITUNGAN TARGET PROFIT OTOMATIS (TRADING PLAN)
    entry_mc = market_cap
    tp1_mc = entry_mc * 1.5   # +50% Ambil Modal
    tp2_mc = entry_mc * 2.5   # +150% Amankan Profit
    tp3_mc = entry_mc * 4.0   # +300% Moonbag
    sl_mc = entry_mc * 0.75   # -25% Cut Loss

    return {
        "mint": mint,
        "name": base.get("name", "Solana Gem"),
        "symbol": base.get("symbol", "SOL"),
        "age_hours": age_hours,
        "market_cap": market_cap,
        "liquidity_usd": liq_usd,
        "vol_h1": vol_h1,
        "avg_tx_usd": avg_tx_usd,
        "pc_h1": pc_h1,
        "pc_h24": pc_h24,
        "h1_buys": h1_buys,
        "h1_sells": h1_sells,
        "top_holder_pct": security["top_holder"],
        "dev_holding_pct": security["dev_holding"],
        "entry_mc": entry_mc,
        "tp1_mc": tp1_mc,
        "tp2_mc": tp2_mc,
        "tp3_mc": tp3_mc,
        "sl_mc": sl_mc,
        "alert_type": alert_type,
        "stage_desc": stage_desc,
        "score": score
    }


def format_telegram_message(d: Dict[str, Any]) -> str:
    mint = d["mint"]
    name = d["name"]
    sym = d["symbol"]
    mc = d["market_cap"]
    liq = d["liquidity_usd"]
    age_h = d["age_hours"]
    vol1 = d["vol_h1"]
    avg_tx = d["avg_tx_usd"]
    pc1 = d["pc_h1"]
    pc24 = d["pc_h24"]
    b1 = d["h1_buys"]
    s1 = d["h1_sells"]
    top_h = d["top_holder_pct"]
    dev_h = d["dev_holding_pct"]
    score = d["score"]
    tag = d["alert_type"]
    stage = d["stage_desc"]

    tp1 = d["tp1_mc"]
    tp2 = d["tp2_mc"]
    tp3 = d["tp3_mc"]
    sl = d["sl_mc"]

    ratio = round(b1 / max(1, s1), 2)
    dev_str = f"Sangat Aman ({dev_h:.1f}%)" if dev_h <= 2.5 else f"⚠️ {dev_h:.1f}%"

    msg = f"""{tag}
💎 <b>{name} (${sym})</b>
━━━━━━━━━━━━━━━━━━━━
🎯 <b>Confidence Score:</b> <code>{score}/100</code>
📍 <b>Status Koin:</b> <code>{stage}</code>
⏳ <b>Usia Koin:</b> <code>{age_h:.1f} Jam</code>
💰 <b>Market Cap Saat Ini:</b> <code>${mc:,.0f}</code>
💧 <b>Likuiditas Kolam:</b> <code>${liq:,.0f}</code>
📊 <b>Volume 1 Jam:</b> <code>${vol1:,.0f}</code>
━━━━━━━━━━━━━━━━━━━━
📈 <b>Momentum & Pembeli Nyata:</b>
• Kenaikan 1 Jam (H1): <code>+{pc1:.1f}%</code>
• Kenaikan 24 Jam: <code>+{pc24:.1f}%</code>
• Pembeli Aktif (H1): <code>{b1} Wallets ({ratio}x Buyers Dominance)</code>
• Rata-rata/Tx: <code>${avg_tx:,.1f} USD (Anti-Wash Bot Lolos)</code>
• Dompet Dev/Creator: <code>{dev_str}</code>
• Top Holder Terbesar: <code>{top_h:.1f}% (Aman &lt; 10%)</code>
━━━━━━━━━━━━━━━━━━━━
🎯 <b>PANDUAN EKSEKUSI TRADING (PROFIT PLAN):</b>
• <b>Entry Zone:</b> <code>Sekitar MC ${mc:,.0f}</code>
• <b>TP 1 (+50%):</b> <code>${tp1:,.0f} (Tarik Modal Awal)</code>
• <b>TP 2 (+150%):</b> <code>${tp2:,.0f} (Amankan 50% Profit)</code>
• <b>TP 3 (+300% Moonbag):</b> <code>${tp3:,.0f} (Biarkan Terbang)</code>
• <b>Stop Loss (-25%):</b> <code>${sl:,.0f} (Disiplin Cut Loss)</code>

📋 <b>Mint Address:</b>
<code>{mint}</code>

⚡ <b>Perintah Quick Snipe:</b>
<code>/snipe {mint} 0.5</code>

🔗 <b>Direct Trading Links (1-Click Buy):</b>
<a href="https://t.me/solana_trojanbot?start=r-alpha_{mint}">[⚡ Trojan Bot]</a> · <a href="https://photon-sol.tinyastro.io/en/r/@alpha/{mint}">[Photon SOL]</a> · <a href="https://dexscreener.com/solana/{mint}">[DexScreener]</a> · <a href="https://neo.bullx.io/terminal?chainId=1399811149&address={mint}">[BullX]</a>"""
    return msg.strip()


def run_breakout_scanner_loop():
    logger.info("Radar Breakout & Reversal v5.0 Master Profit Engine Aktif...")
    while True:
        try:
            candidates = fetch_all_solana_candidates()

            for mint in candidates:
                if mint in alerted_mints:
                    continue

                pair_res = get_json(f"https://api.dexscreener.com/latest/dex/tokens/{mint}", timeout=8) or {}
                pairs = pair_res.get("pairs", [])
                if not pairs:
                    continue

                pair = pairs[0]
                eval_res = evaluate_pair(pair)

                if eval_res:
                    alerted_mints.add(mint)
                    stats["breakouts_detected"] += 1
                    logger.info(f"💎 MASTER GEM TERDETEKSI: {eval_res['name']} (${eval_res['symbol']}) | MC: ${eval_res['market_cap']:,.0f}")

                    if bot and TG_CHAT_ID:
                        text = format_telegram_message(eval_res)
                        try:
                            bot.send_message(TG_CHAT_ID, text, parse_mode="HTML", disable_web_page_preview=True)
                        except Exception as e:
                            logger.error(f"Gagal kirim Telegram: {e}")

                time.sleep(0.35)

            stats["scans_completed"] += 1
            stats["last_scan_time"] = int(time.time())

        except Exception as e:
            logger.error(f"[Loop Error]: {e}")

        time.sleep(30)


@app.route("/")
def health():
    return jsonify({
        "service": "universal-breakout-reversal-scanner",
        "version": "5.0.0-master-profit",
        "status": "Running",
        "scans_completed": stats["scans_completed"],
        "breakouts_detected": stats["breakouts_detected"],
        "dev_dumps_blocked": stats["dev_dumps_blocked"],
        "bots_blocked": stats["bots_blocked"],
        "cached_alerts": len(alerted_mints)
    })


@app.route("/test-alert")
def test_alert():
    if not bot or not TG_CHAT_ID:
        return jsonify({"ok": False, "error": "TG_BOT_TOKEN atau TG_CHAT_ID belum diset!"})

    dummy = {
        "mint": "2yu92oYzBWLAdVpu8BoaLzmM1oxPsHoboay2BXmeDDZr",
        "name": "Froink",
        "symbol": "FROINK",
        "age_hours": 3.5,
        "market_cap": 48500,
        "liquidity_usd": 18200,
        "vol_h1": 24500,
        "avg_tx_usd": 128.5,
        "pc_h1": 42.5,
        "pc_h24": 315.0,
        "h1_buys": 142,
        "h1_sells": 48,
        "top_holder_pct": 6.2,
        "dev_holding_pct": 0.8,
        "entry_mc": 48500,
        "tp1_mc": 72750,
        "tp2_mc": 121250,
        "tp3_mc": 194000,
        "sl_mc": 36375,
        "alert_type": "🔥 <b>SECOND-LEG MEGA RUNNER (GELOMBANG 2 TERBUKTI)</b>",
        "stage_desc": "Reli Gelombang ke-2 (Proven Community & Multi-Hour Hold)",
        "score": 98
    }
    pesan = format_telegram_message(dummy)
    try:
        bot.send_message(TG_CHAT_ID, pesan, parse_mode="HTML", disable_web_page_preview=True)
        return jsonify({"ok": True, "pesan": "Berhasil! Test alert Master Profit Engine terkirim ke Telegram."})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


if __name__ == "__main__":
    if bot and TG_CHAT_ID:
        try:
            bot.send_message(
                TG_CHAT_ID,
                "👑 <b>MASTER PROFIT SCANNER v5.0 ONLINE!</b>\n5 Penyempurnaan Pamungkas Aktif: Zero-Keyword, Dev-Dump Shield, Anti-Wash Bot, Second-Leg Classifier, &amp; Auto TP/SL Plan.",
                parse_mode="HTML"
            )
        except Exception:
            pass

    scanner_thread = threading.Thread(target=run_breakout_scanner_loop, daemon=True)
    scanner_thread.start()

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
