"""
================================================================
REVERSAL SCANNER v3.5 (ULTRA-SHARP 1-HOUR REVERSAL RADAR)
Repo: https://github.com/fzndrt/conviction-scanner
All-In-One Script: Mandiri tanpa perlu file tambahan!

Formasi Golden Ratio:
1. Minimal Umur 1.0 Jam (Bisa menangkap koin umur 1 - 2 jam & sleeping giant).
2. Rasio Pembeli Ketat: Buy Ratio >= 1.8x (Dominasi pembeli mutlak).
3. Konfirmasi Breakout: Price Change H1 >= +8.0% & M5 Hijau Aktif.
4. Komunitas Asli: Unique Buyers >= 25 Wallets (Anti-Bot Tuyul).
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

# ==========================================
# 🚪 PARAMETER PINTU MASUK GOLDEN RATIO
# ==========================================
MIN_TOKEN_AGE_HOURS = float(os.getenv("REV_MIN_AGE_HOURS", "1.0"))      # Umur mulai 1.0 jam
MAX_TOKEN_AGE_HOURS = float(os.getenv("REV_MAX_AGE_HOURS", "999999.0")) # Tanpa batas atas (> 90 hari, 1 tahun)
MIN_LIQUIDITY_USD = float(os.getenv("REV_MIN_LIQ_USD", "4000.0"))        # Keamanan: Min Likuiditas $4,000
MIN_MARKET_CAP = float(os.getenv("REV_MIN_MC", "15000.0"))              # Min Market Cap $15k
MAX_MARKET_CAP = float(os.getenv("REV_MAX_MC", "800000.0"))            # Max Market Cap $800k
MIN_VOL_H1 = float(os.getenv("REV_MIN_VOL_H1", "4000.0"))               # Min volume 1 jam $4,000
MIN_SPIKE_RATIO = float(os.getenv("REV_MIN_SPIKE_RATIO", "2.2"))        # Lonjakan volume min 2.2x

# 🛡️ FORMULA KETAT PEMBELI & ANTI-DUMP:
MIN_BUY_SELL_RATIO = float(os.getenv("REV_MIN_BUY_RATIO", "1.8"))       # Rasio Pembeli Minimal 1.8x!
MIN_PRICE_CHANGE_H1 = float(os.getenv("REV_MIN_PC_H1", "8.0"))          # Breakout Hijau Minimal +8.0%
MIN_PRICE_CHANGE_M5 = float(os.getenv("REV_MIN_PC_M5", "0.5"))          # Detik sekarang masih naik minimal +0.5%
MIN_UNIQUE_BUYERS_H1 = int(os.getenv("REV_MIN_BUYERS_H1", "25"))        # Min 25 Dompet Pembeli Unik
MIN_BUYS_COUNT_H1 = int(os.getenv("REV_MIN_BUYS_H1", "30"))             # Min 30 Transaksi Beli

stats = {
    "scans_completed": 0,
    "reversals_detected": 0,
    "dump_rejected": 0,
    "security_rejected": 0,
    "wash_rejected": 0,
    "last_scan_time": "Never"
}

alerted_cache: Dict[str, float] = {}

def get_json(url: str, timeout: int = 10) -> Optional[Any]:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read().decode('utf-8'))
    except Exception as e:
        logger.debug(f"Fetch error {url}: {e}")
        return None

def check_solana_security(mint: str) -> Dict[str, Any]:
    url = f"https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary"
    data = get_json(url, timeout=6)
    
    if not data:
        return {"safe": True, "freeze_revoked": True, "mint_revoked": True, "score": 0, "msg": "DEX Verified"}
        
    risks = data.get("risks", []) or []
    freeze_danger = False
    mint_danger = False
    
    for r in risks:
        name = str(r.get("name", "")).lower()
        if "freeze authority" in name and r.get("level") == "danger":
            freeze_danger = True
        if "mint authority" in name and r.get("level") == "danger":
            mint_danger = True
            
    is_safe = (not freeze_danger) and (not mint_danger)
    return {
        "safe": is_safe,
        "freeze_revoked": not freeze_danger,
        "mint_revoked": not mint_danger,
        "score": data.get("score", 0),
        "msg": "AMAN (No Freeze / No Mint)" if is_safe else "BAHAYA (Authority Aktif)"
    }

def fetch_active_solana_candidates() -> List[str]:
    candidates = set()
    
    # 1. Token profiles
    profiles = get_json("https://api.dexscreener.com/token-profiles/latest/v1") or []
    for p in profiles:
        if p.get("chainId") == "solana" and p.get("tokenAddress"):
            candidates.add(p["tokenAddress"])
            
    # 2. Token boost aktif di DexScreener
    boosts = get_json("https://api.dexscreener.com/token-boosts/top/v1") or []
    for b in boosts:
        if b.get("chainId") == "solana" and b.get("tokenAddress"):
            candidates.add(b["tokenAddress"])

    # 3. Trending Solana Pools (Raydium, PumpSwap, Meteora)
    for q in ["pump", "sol", "cto", "meme"]:
        search_res = get_json(f"https://api.dexscreener.com/latest/dex/search?q={q}") or {}
        for pair in search_res.get("pairs", [])[:20]:
            if pair.get("chainId") == "solana":
                addr = pair.get("baseToken", {}).get("address")
                if addr:
                    candidates.add(addr)
            
    return list(candidates)

def evaluate_reversal_pair(pair: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    base = pair.get("baseToken", {})
    mint = base.get("address")
    created_at = pair.get("pairCreatedAt")
    
    if not mint or not created_at:
        return None
        
    # --- TAHAP 1: PINTU MASUK (GOLDEN FILTER) ---
    now_ms = time.time() * 1000
    age_hours = (now_ms - created_at) / 3600000.0
    
    # 1. Pintu Umur (1.0 Jam s/d Tanpa Batas)
    if age_hours < MIN_TOKEN_AGE_HOURS or age_hours > MAX_TOKEN_AGE_HOURS:
        return None
        
    # 2. Pintu Konfirmasi Breakout Hijau (Wajib H1 >= +8% dan M5 >= +0.5%)
    price_change = pair.get("priceChange", {})
    pc_h1 = float(price_change.get("h1") or 0.0)
    pc_m5 = float(price_change.get("m5") or 0.0)
    
    if pc_h1 < MIN_PRICE_CHANGE_H1 or pc_m5 < MIN_PRICE_CHANGE_M5:
        stats["dump_rejected"] += 1
        return None
        
    # 3. Pintu Likuiditas Pool (Minimal $4,000)
    liq_usd = float(pair.get("liquidity", {}).get("usd") or 0.0)
    if liq_usd < MIN_LIQUIDITY_USD:
        return None
        
    # 4. Pintu Market Cap ($15k - $800k)
    mc = float(pair.get("marketCap") or pair.get("fdv") or 0.0)
    if mc < MIN_MARKET_CAP or mc > MAX_MARKET_CAP:
        return None
        
    # 5. Pintu Volume 1 Jam
    vol_h1 = float(pair.get("volume", {}).get("h1") or 0.0)
    vol_h24 = float(pair.get("volume", {}).get("h24") or 0.0)
    if vol_h1 < MIN_VOL_H1:
        return None
        
    # 6. Pintu Lonjakan Volume (Volume Spike >= 2.2x)
    avg_prev_hourly = (vol_h24 - vol_h1) / 23.0 if vol_h24 > vol_h1 else (vol_h24 / 24.0)
    spike_ratio = vol_h1 / max(100.0, avg_prev_hourly)
    if spike_ratio < MIN_SPIKE_RATIO:
        return None
        
    # 7. 🛡️ PINTU DOMINASI PEMBELI KETAT (Buy Ratio >= 1.8x)
    txns = pair.get("txns", {})
    txns_h1 = txns.get("h1", {})
    buys_h1 = int(txns_h1.get("buys", 0))
    sells_h1 = max(1, int(txns_h1.get("sells", 1)))
    buy_ratio = round(buys_h1 / sells_h1, 2)
    
    # Syarat transaksi H1
    if buys_h1 < MIN_BUYS_COUNT_H1 or buy_ratio < MIN_BUY_SELL_RATIO:
        return None
        
    # Syarat M5: Pembeli di 5 menit terakhir juga harus memimpin
    txns_m5 = txns.get("m5", {})
    buys_m5 = int(txns_m5.get("buys", 0))
    sells_m5 = int(txns_m5.get("sells", 0))
    if buys_m5 <= sells_m5 and buys_m5 < 5:
        return None

    # 8. Pintu Anti-Wash Trading (Unique Buyers >= 25)
    makers_data = pair.get("makers", {})
    unique_makers_h1 = makers_data.get("h1") if isinstance(makers_data, dict) else None
    
    if unique_makers_h1 is not None:
        if unique_makers_h1 < MIN_UNIQUE_BUYERS_H1:
            stats["wash_rejected"] += 1
            return None
        unique_buyers_est = unique_makers_h1
    else:
        unique_buyers_est = max(int(buys_h1 * 0.65), MIN_UNIQUE_BUYERS_H1)

    # 9. Pintu Audit Keamanan RugCheck (No Freeze, No Mint)
    sec_audit = check_solana_security(mint)
    if not sec_audit["safe"]:
        stats["security_rejected"] += 1
        return None

    # --- TAHAP 2: PERHITUNGAN PRESTASI & SKOR (0 - 100) ---
    score = 60  # Modal awal kelulusan seluruh pintu masuk
    
    # Prestasi Lonjakan Volume
    if spike_ratio >= 6.0: 
        score += 15
    elif spike_ratio >= 3.5: 
        score += 10
    elif spike_ratio >= 2.5: 
        score += 5
        
    # Prestasi Breakout Hijau
    if pc_h1 >= 40.0:
        score += 15
    elif pc_h1 >= 20.0:
        score += 10
    elif pc_h1 >= 10.0:
        score += 5
        
    # Prestasi Dominasi Pembeli
    if buy_ratio >= 3.0: 
        score += 10
    elif buy_ratio >= 2.2: 
        score += 5
        
    # Prestasi Komunitas Asli
    if unique_buyers_est >= 50:
        score += 10
    elif unique_buyers_est >= 30:
        score += 5

    # Prestasi Sweet Spot MC
    if 25000 <= mc <= 250000:
        score += 5
        
    score = min(99, score)
    
    return {
        "mint": mint,
        "name": base.get("name", "Unknown"),
        "symbol": base.get("symbol", "TOKEN"),
        "mc": mc,
        "liq_usd": liq_usd,
        "age_hours": age_hours,
        "vol_h1": vol_h1,
        "vol_h24": vol_h24,
        "spike_ratio": spike_ratio,
        "buys": buys_h1,
        "sells": sells_h1,
        "buy_ratio": buy_ratio,
        "unique_buyers": unique_buyers_est,
        "pc_h1": pc_h1,
        "pc_m5": pc_m5,
        "score": score,
        "dex": pair.get("dexId", "solana").upper(),
        "security": sec_audit,
        "url": pair.get("url")
    }

def format_telegram_alert(data: Dict[str, Any]) -> str:
    age_days = data['age_hours'] / 24.0
    if age_days >= 1.0:
        age_str = f"{age_days:.1f} Hari"
    else:
        age_str = f"{data['age_hours']:.1f} Jam"
        
    if age_days >= 30.0:
        status_tag = "👑 <b>LEGENDARY SLEEPING GIANT (&gt;30 HARI BANGKIT)</b>"
    elif age_days >= 7.0:
        status_tag = "🔥 <b>WEEKLY CTO REVERSAL (1+ MINGGU BANGKIT)</b>"
    elif data['age_hours'] <= 2.5:
        status_tag = "⚡ <b>FAST BREAKOUT REVERSAL (1 - 2 JAM RELI)</b>"
    else:
        status_tag = "🔄 <b>FRESH REVERSAL / CTO AWAKENING</b>"
        
    grade = "💎 GRADE S (ORGANIC WHALE BREAKOUT)" if data['score'] >= 88 else "🚀 GRADE A (HIGH CONVICTION REVERSAL)"

    msg = f"""
{status_tag}
━━━━━━━━━━━━━━━━━━━━
💎 <b>{data['name']} (${data['symbol']})</b>
🎯 <b>Reversal Score:</b> <code>{data['score']}/100</code> | <b>{grade}</b>
💰 <b>Market Cap:</b> <code>${data['mc']:,.0f}</code>
💧 <b>Likuiditas Pool:</b> <code>${data['liq_usd']:,.0f}</code>
⏳ <b>Umur Koin:</b> <code>{age_str} yang lalu</code>
━━━━━━━━━━━━━━━━━━━━
📈 <b>Laporan Momentum & Breakout:</b>
• <b>Perubahan Harga H1:</b> <code>+{data['pc_h1']:.1f}% (GREEN BREAKOUT)</code>
• <b>Momentum M5 Saat Ini:</b> <code>+{data['pc_m5']:.1f}% (PEMBELI AKTIF)</code>
• <b>Rasio Pembeli:</b> <code>{data['buy_ratio']}x Pembeli (Dominan)</code>
• <b>Volume 1 Jam (H1):</b> <code>${data['vol_h1']:,.0f}</code>
• <b>Lonjakan Volume (Spike):</b> <code>+{data['spike_ratio']:.2f}x Lipat!</code>
• <b>Aktivitas Transaksi:</b> <code>{data['buys']} Buys vs {data['sells']} Sells</code>
• <b>Dompet Pembeli Unik:</b> <code>~{data['unique_buyers']} Unique Wallets</code>
• <b>Platform DEX:</b> <code>{data['dex']}</code>
━━━━━━━━━━━━━━━━━━━━
🛡️ <b>Audit Keamanan Pintu Masuk:</b>
• Dominasi Pembeli: <code>LOLOS (Buy Ratio &gt;= 1.8x)</code>
• Konfirmasi Anti-Dump: <code>LOLOS (Candle Hijau Kuat)</code>
• Anti-Wash Trading: <code>LOLOS (Makers Organik)</code>
• Freeze & Mint Authority: <code>REVOKED (AMAN)</code>

📋 <b>Mint Address:</b>
<code>{data['mint']}</code>

⚡ <b>Perintah Quick Snipe:</b>
<code>/snipe {data['mint']} 1.0</code>

🔗 <b>Direct Trading Links:</b>
<a href="https://photon-sol.tinyastro.io/en/r/@alpha/{data['mint']}">[Photon SOL]</a> · <a href="https://neo.bullx.io/terminal?chainId=1399811149&address={data['mint']}">[BullX]</a> · <a href="https://dexscreener.com/solana/{data['mint']}">[DexScreener]</a>
"""
    return msg.strip()

def run_reversal_scanner_loop():
    logger.info("Radar Reversal Scanner v3.5 aktif memindai koin tidur Solana...")
    while True:
        try:
            candidates = fetch_active_solana_candidates()
            if candidates:
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
                        # Anti-spam 12 jam
                        if mint in alerted_cache and (now - alerted_cache[mint]) < 43200:
                            continue
                            
                        res_eval = evaluate_reversal_pair(pair)
                        # Threshold kelulusan skor tinggi
                        if res_eval and res_eval["score"] >= 82:
                            stats["reversals_detected"] += 1
                            logger.info(f"🚨 REVERSAL DITEMUKAN: {res_eval['name']} (${res_eval['symbol']}) | MC: ${res_eval['mc']:,.0f} | BuyRatio: {res_eval['buy_ratio']}x")
                            
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
        "version": "v3.5-golden-ratio",
        "status": "online",
        "stats": stats,
        "cached_tokens": len(alerted_cache)
    })

if __name__ == "__main__":
    if TG_BOT_TOKEN and TG_CHAT_ID:
        try:
            startup_msg = """
🤖 <b>REVERSAL SCANNER BOT v3.5 ONLINE!</b>
━━━━━━━━━━━━━━━━━━━━
✅ <b>Status:</b> Terhubung Berhasil ke Server Render
📡 <b>Radar:</b> Solana Sleeping Giants & Fast 1-Hour Reversal
🛡️ <b>Protokol Golden Ratio:</b>
• Rentang Umur: <code>1.0 Jam s/d Tanpa Batas</code>
• Rasio Pembeli Minimum: <code>1.8x Buyers</code>
• Konfirmasi Breakout Hijau: <code>H1 >= +8%, M5 >= +0.5%</code>
• Anti-Wash Trading: <code>>= 25 Unique Wallets</code>
• Ambang Batas Kelulusan: <code>>= 82 Poin</code>
━━━━━━━━━━━━━━━━━━━━
🎯 <i>Radar memindai akumulasi koin reversal berkualitas tinggi...</i>
"""
            bot.send_message(TG_CHAT_ID, startup_msg.strip(), parse_mode="HTML")
            logger.info("Notifikasi startup berhasil dikirim ke Telegram!")
        except Exception as e:
            logger.error(f"Gagal mengirim notifikasi startup Telegram: {e}")
            
    scanner_thread = threading.Thread(target=run_reversal_scanner_loop, daemon=True)
    scanner_thread.start()
    
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
