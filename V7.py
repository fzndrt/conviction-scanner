"""
================================================================
REVERSAL SCANNER v2.0 (SOLANA SLEEPING GIANT & CTO RADAR)
Repo: https://github.com/fzndrt/conviction-scanner
All-In-One Script: Tidak memerlukan file tambahan!

Mendeteksi koin lama (umur 2 jam hingga 90+ hari) yang tiba-tiba
mengalami kebangkitan volume (Volume Spike > 2.2x) & Dominasi Pembeli.
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
logger = logging.getLogger("reversal-scanner")

app = Flask(__name__)

# Konfigurasi Telegram
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID", "")

bot = telebot.TeleBot(TG_BOT_TOKEN) if TG_BOT_TOKEN else None

# Parameter Filter Koin Lama Bangkit (Bisa disesuaikan via Render Environment)
MIN_TOKEN_AGE_HOURS = float(os.getenv("REV_MIN_AGE_HOURS", "2.0"))     # Minimal umur 2 jam (bukan koin baru lahir)
MAX_TOKEN_AGE_HOURS = float(os.getenv("REV_MAX_AGE_HOURS", "2160.0"))  # Hingga 90 hari! (Tidur lama bisa dideteksi)
MIN_MARKET_CAP = float(os.getenv("REV_MIN_MC", "15000.0"))             # Minimal MC $15k (hindari koin mati total)
MAX_MARKET_CAP = float(os.getenv("REV_MAX_MC", "650000.0"))           # Maksimal MC $650k (masih ada ruang 10x-50x)
MIN_VOL_H1 = float(os.getenv("REV_MIN_VOL_H1", "4000.0"))              # Volume 1 jam terakhir minimal $4,000
MIN_SPIKE_RATIO = float(os.getenv("REV_MIN_SPIKE_RATIO", "2.2"))       # Lonjakan volume minimal 2.2x dibanding jam lalu
MIN_BUY_SELL_RATIO = float(os.getenv("REV_MIN_BUY_RATIO", "1.3"))      # Pembeli minimal 1.3x lebih agresif dari penjual

stats = {
    "scans_completed": 0,
    "reversals_detected": 0,
    "last_scan_time": "Never"
}

# Cache anti-spam token (jangan alert token sama dalam 12 jam)
alerted_cache: Dict[str, float] = {}

def get_json(url: str) -> Optional[Any]:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=12) as res:
            return json.loads(res.read().decode('utf-8'))
    except Exception as e:
        logger.warning(f"Fetch error {url}: {e}")
        return None

def fetch_active_solana_candidates() -> List[str]:
    """Mengambil kandidat koin Solana yang profilnya diupdate (banner CTO baru) atau divoting komunitas"""
    candidates = set()
    
    # 1. Token profiles (indikasi dev ganti banner / komunitas takeover)
    profiles = get_json("https://api.dexscreener.com/token-profiles/latest/v1") or []
    for p in profiles:
        if p.get("chainId") == "solana" and p.get("tokenAddress"):
            candidates.add(p["tokenAddress"])
            
    # 2. Token boost aktif di DexScreener
    boosts = get_json("https://api.dexscreener.com/token-boosts/top/v1") or []
    for b in boosts:
        if b.get("chainId") == "solana" and b.get("tokenAddress"):
            candidates.add(b["tokenAddress"])
            
    return list(candidates)

def evaluate_reversal_pair(pair: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    base = pair.get("baseToken", {})
    mint = base.get("address")
    created_at = pair.get("pairCreatedAt")
    
    if not mint or not created_at:
        return None
        
    now_ms = time.time() * 1000
    age_hours = (now_ms - created_at) / 3600000.0
    
    # 1. Filter Umur Koin (2 Jam s/d 90 Hari)
    if age_hours < MIN_TOKEN_AGE_HOURS or age_hours > MAX_TOKEN_AGE_HOURS:
        return None
        
    # 2. Filter Market Cap
    mc = pair.get("marketCap") or pair.get("fdv") or 0.0
    if mc < MIN_MARKET_CAP or mc > MAX_MARKET_CAP:
        return None
        
    # 3. Filter Lonjakan Volume (Volume Spike Ratio)
    vol_h1 = pair.get("volume", {}).get("h1", 0.0)
    vol_h24 = pair.get("volume", {}).get("h24", 0.0)
    if vol_h1 < MIN_VOL_H1:
        return None
        
    avg_prev_hourly = (vol_h24 - vol_h1) / 23.0 if vol_h24 > vol_h1 else (vol_h24 / 24.0)
    spike_ratio = vol_h1 / max(100.0, avg_prev_hourly)
    
    if spike_ratio < MIN_SPIKE_RATIO:
        return None
        
    # 4. Filter Tekanan Beli (Order Buys vs Sells)
    txns_h1 = pair.get("txns", {}).get("h1", {})
    buys_h1 = txns_h1.get("buys", 0)
    sells_h1 = max(1, txns_h1.get("sells", 1))
    buy_ratio = round(buys_h1 / sells_h1, 2)
    
    if buy_ratio < MIN_BUY_SELL_RATIO:
        return None
        
    # Hitung Skor Reversal (0 - 100)
    score = 60
    if spike_ratio >= 5.0: score += 20
    elif spike_ratio >= 3.0: score += 10
    
    if buy_ratio >= 2.0: score += 15
    elif buy_ratio >= 1.5: score += 10
    
    if 25000 <= mc <= 200000: score += 10  # Sweet spot awal reversal
    
    score = min(99, score)
    
    return {
        "mint": mint,
        "name": base.get("name", "Unknown"),
        "symbol": base.get("symbol", "TOKEN"),
        "mc": mc,
        "age_hours": age_hours,
        "vol_h1": vol_h1,
        "spike_ratio": spike_ratio,
        "buys": buys_h1,
        "sells": sells_h1,
        "buy_ratio": buy_ratio,
        "score": score,
        "dex": pair.get("dexId", "solana").upper(),
        "url": pair.get("url")
    }

def format_telegram_alert(data: Dict[str, Any]) -> str:
    age_days = data['age_hours'] / 24.0
    if age_days >= 1.0:
        age_str = f"{age_days:.1f} Hari"
    else:
        age_str = f"{data['age_hours']:.1f} Jam"
    
    status_tag = "🔥 30+ DAYS OLD GIANT AWAKENING" if age_days >= 30 else "🔄 REVERSAL / CTO AWAKENING"
    
    msg = f"""
{status_tag}
━━━━━━━━━━━━━━━━━━━━
💎 <b>{data['name']} (${data['symbol']})</b>
🎯 <b>Reversal Score:</b> <code>{data['score']}/100</code>
💰 <b>Market Cap:</b> <code>${data['mc']:,.0f}</code>
⏳ <b>Umur Koin:</b> <code>{age_str} yang lalu</code>
━━━━━━━━━━━━━━━━━━━━
📈 <b>Indikator Lonjakan Volume:</b>
• <b>Volume 1 Jam:</b> <code>${data['vol_h1']:,.0f}</code>
• <b>Volume Spike:</b> <code>+{data['spike_ratio']:.1f}x Lipat!</code>
• <b>Tekanan Beli:</b> <code>{data['buys']} Beli vs {data['sells']} Jual ({data['buy_ratio']}x)</code>
• <b>Pool DEX:</b> <code>{data['dex']}</code>

📋 <b>Mint Address:</b>
<code>{data['mint']}</code>

⚡ <b>Perintah Quick Snipe:</b>
<code>/snipe {data['mint']} 1.0</code>

🔗 <b>Direct Trading Links:</b>
<a href="https://photon-sol.tinyastro.io/en/r/@alpha/{data['mint']}">[Photon SOL]</a> · <a href="https://neo.bullx.io/terminal?chainId=1399811149&address={data['mint']}">[BullX]</a> · <a href="https://dexscreener.com/solana/{data['mint']}">[DexScreener]</a>
"""
    return msg.strip()

def run_reversal_scanner_loop():
    logger.info("Radar Reversal Scanner aktif memindai koin tidur Solana...")
    while True:
        try:
            candidates = fetch_active_solana_candidates()
            if candidates:
                logger.info(f"[Scan] Mengevaluasi {len(candidates)} kandidat koin...")
                chunk_size = 30
                for i in range(0, len(candidates), chunk_size):
                    chunk = candidates[i:i + chunk_size]
                    url = f"https://api.dexscreener.com/latest/dex/tokens/{','.join(chunk)}"
                    res = get_json(url)
                    if not res or "pairs" not in res:
                        continue
                        
                    for pair in res.get("pairs") or []:
                        if pair.get("chainId") != "solana":
                            continue
                            
                        mint = pair.get("baseToken", {}).get("address")
                        now = time.time()
                        if mint in alerted_cache and (now - alerted_cache[mint]) < 43200:
                            continue
                            
                        res_eval = evaluate_reversal_pair(pair)
                        if res_eval and res_eval["score"] >= 75:
                            stats["reversals_detected"] += 1
                            logger.info(f"🚨 REVERSAL DITEMUKAN: {res_eval['name']} (${res_eval['symbol']}) | MC: ${res_eval['mc']:,.0f}")
                            
                            if bot and TG_CHAT_ID:
                                text = format_telegram_alert(res_eval)
                                try:
                                    bot.send_message(TG_CHAT_ID, text, parse_mode="HTML", disable_web_page_preview=True)
                                    alerted_cache[mint] = now
                                except Exception as e:
                                    logger.error(f"Gagal kirim Telegram: {e}")
                    time.sleep(1.0)
            stats["scans_completed"] += 1
            stats["last_scan_time"] = time.strftime('%Y-%m-%d %H:%M:%S UTC')
        except Exception as e:
            logger.error(f"Error pada scanner loop: {e}")
        time.sleep(45)

@app.route("/")
def index():
    return jsonify({
        "service": "solana-reversal-scanner",
        "status": "online",
        "stats": stats,
        "cached_tokens": len(alerted_cache)
    })

if __name__ == "__main__":
    if TG_BOT_TOKEN and TG_CHAT_ID:
        try:
            bot.send_message(
                TG_CHAT_ID,
                "🔄 <b>SOLANA REVERSAL SCANNER ONLINE!</b>\nRadar koin tidur bangkit (2 jam s/d 90 hari) aktif.",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.warning(f"Gagal kirim notif startup: {e}")
            
    scanner_thread = threading.Thread(target=run_reversal_scanner_loop, daemon=True)
    scanner_thread.start()
    
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
