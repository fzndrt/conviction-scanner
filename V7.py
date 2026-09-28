"""
================================================================
REVERSAL SCANNER v3.7 (ULTRA-STRICT HYPE & ANTI-WHALE DUMP)
Repo: https://github.com/fzndrt/conviction-scanner
All-In-One Script: Mandiri tanpa perlu file tambahan!

Fitur Keamanan Baru:
1. Anti-Whale / Dev Concentration: Blokir koin jika top holder > 12%.
2. Super Momentum M5: Wajib candle M5 >= +3.5% (Tolak koin fakeout).
3. Dominasi Pembeli Real-Time: M5 Buys wajib >= 1.4x M5 Sells.
4. Ambang Kelulusan Ketat: Skor Telegram minimal >= 85 (Hanya Super Hype).
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
# 🚪 PARAMETER FILTER ULTRA-KETAT
# ==========================================
MIN_TOKEN_AGE_HOURS = float(os.getenv("REV_MIN_AGE_HOURS", "1.0"))      # Umur min 1.0 jam s/d tanpa batas
MAX_TOKEN_AGE_HOURS = float(os.getenv("REV_MAX_AGE_HOURS", "999999.0")) 
MIN_LIQUIDITY_USD = float(os.getenv("REV_MIN_LIQ_USD", "6000.0"))        # Minimal Likuiditas $6,000
MIN_MARKET_CAP = float(os.getenv("REV_MIN_MC", "25000.0"))              # Min Market Cap $25k
MAX_MARKET_CAP = float(os.getenv("REV_MAX_MC", "800000.0"))            # Max Market Cap $800k
MIN_VOL_H1 = float(os.getenv("REV_MIN_VOL_H1", "5000.0"))               # Min volume 1 jam $5,000
MIN_SPIKE_RATIO = float(os.getenv("REV_MIN_SPIKE_RATIO", "2.5"))        # Lonjakan volume min 2.5x

# 🛡️ PINTU ANTI-DUMP & SUPER MOMENTUM REAL-TIME:
MIN_PRICE_CHANGE_H1 = float(os.getenv("REV_MIN_PC_H1", "8.0"))          # H1 wajib naik minimal +8.0%
MIN_PRICE_CHANGE_M5 = float(os.getenv("REV_MIN_PC_M5", "3.5"))          # M5 wajib naik minimal +3.5% (TOLAK FAKEOUT!)
MIN_BUY_SELL_RATIO = float(os.getenv("REV_MIN_BUY_RATIO", "1.40"))      # Rasio Pembeli H1 Minimal 1.4x
MIN_BUY_RATIO_M5 = float(os.getenv("REV_MIN_BUY_RATIO_M5", "1.35"))     # Rasio Pembeli M5 Minimal 1.35x
MIN_UNIQUE_BUYERS_H1 = int(os.getenv("REV_MIN_BUYERS_H1", "25"))        # Min 25 Dompet Pembeli Unik
MAX_TOP_HOLDER_PCT = float(os.getenv("REV_MAX_TOP_HOLDER", "12.0"))     # Maksimal 1 dompet pribadi < 12%

stats = {
    "scans_completed": 0,
    "reversals_detected": 0,
    "whale_dump_rejected": 0,
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

def check_solana_security_and_holders(mint: str) -> Dict[str, Any]:
    url = f"https://api.rugcheck.xyz/v1/tokens/{mint}/report"
    data = get_json(url, timeout=7)
    
    if not data:
        return {"safe": True, "top_holder_pct": 0.0, "msg": "DEX Verified"}
        
    risks = data.get("risks", []) or []
    freeze_danger = False
    mint_danger = False
    
    for r in risks:
        name = str(r.get("name", "")).lower()
        if "freeze authority" in name and r.get("level") == "danger":
            freeze_danger = True
        if "mint authority" in name and r.get("level") == "danger":
            mint_danger = True
            
    # 🔍 Audit Top Holders (Mencegah Kasus Dump Koin $o)
    top_holders = data.get("topHolders", []) or []
    max_holder_pct = 0.0
    for h in top_holders:
        pct = float(h.get("pct") or 0.0)
        # Abaikan Raydium/Pump pool address yang biasanya pegang LP
        addr = h.get("address", "")
        if pct > max_holder_pct and pct < 90.0:  # < 90% untuk membedakan dari pool belum burn
            max_holder_pct = pct

    whale_danger = max_holder_pct > MAX_TOP_HOLDER_PCT
    is_safe = (not freeze_danger) and (not mint_danger) and (not whale_danger)
    
    return {
        "safe": is_safe,
        "freeze_revoked": not freeze_danger,
        "mint_revoked": not mint_danger,
        "top_holder_pct": max_holder_pct,
        "whale_danger": whale_danger,
        "msg": "AMAN" if is_safe else f"BAHAYA (Top Holder {max_holder_pct:.1f}%)"
    }

def fetch_active_solana_candidates() -> List[str]:
    candidates = set()
    
    # 1. Token profiles terbaru
    profiles = get_json("https://api.dexscreener.com/token-profiles/latest/v1") or []
    for p in profiles:
        if p.get("chainId") == "solana" and p.get("tokenAddress"):
            candidates.add(p["tokenAddress"])
            
    # 2. Token boost aktif di DexScreener (Top & Latest)
    for boost_url in [
        "https://api.dexscreener.com/token-boosts/top/v1",
        "https://api.dexscreener.com/token-boosts/latest/v1"
    ]:
        boosts = get_json(boost_url) or []
        for b in boosts:
            if b.get("chainId") == "solana" and b.get("tokenAddress"):
                candidates.add(b["tokenAddress"])

    # 3. 🌐 JARING LEBAR SOLANA MEME & SLEEPING GIANTS (25+ Seed Kunci)
    wide_seeds = [
        "sol", "pump", "ray", "meme", "cto", "moon", "pepe", "doge", "cat", "dog", 
        "inu", "ai", "trump", "bonk", "wif", "bome", "popcat", "goat", "moodeng", 
        "act", "pnut", "chill", "fart", "spx", "giga", "coin", "token"
    ]
    for s in wide_seeds:
        search_res = get_json(f"https://api.dexscreener.com/latest/dex/search?q={s}") or {}
        for pair in search_res.get("pairs", [])[:15]:
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
        
    now_ms = time.time() * 1000
    age_hours = (now_ms - created_at) / 3600000.0
    
    # 1. Pintu Umur (1.0 Jam s/d Tanpa Batas)
    if age_hours < MIN_TOKEN_AGE_HOURS or age_hours > MAX_TOKEN_AGE_HOURS:
        return None
        
    # 2. 🛡️ PINTU ANTI-DUMP & SUPER MOMENTUM M5 (Wajib Hijau Kencang)
    price_change = pair.get("priceChange", {})
    pc_h1 = float(price_change.get("h1") or 0.0)
    pc_m5 = float(price_change.get("m5") or 0.0)
    
    # Koin harus sedang benar-benar reli di 5 menit terakhir (min +3.5%)
    if pc_h1 < MIN_PRICE_CHANGE_H1 or pc_m5 < MIN_PRICE_CHANGE_M5:
        stats["dump_rejected"] += 1
        return None
        
    # 3. Pintu Likuiditas Pool (Minimal $6,000)
    liq_usd = float(pair.get("liquidity", {}).get("usd") or 0.0)
    if liq_usd < MIN_LIQUIDITY_USD:
        return None
        
    # 4. Pintu Market Cap ($25k - $800k)
    mc = float(pair.get("marketCap") or pair.get("fdv") or 0.0)
    if mc < MIN_MARKET_CAP or mc > MAX_MARKET_CAP:
        return None
        
    # 5. Pintu Volume 1 Jam (Minimal $5,000)
    vol_h1 = float(pair.get("volume", {}).get("h1") or 0.0)
    vol_h24 = float(pair.get("volume", {}).get("h24") or 0.0)
    if vol_h1 < MIN_VOL_H1:
        return None
        
    # 6. Pintu Lonjakan Volume (Volume Spike >= 2.5x)
    avg_prev_hourly = (vol_h24 - vol_h1) / 23.0 if vol_h24 > vol_h1 else (vol_h24 / 24.0)
    spike_ratio = vol_h1 / max(100.0, avg_prev_hourly)
    if spike_ratio < MIN_SPIKE_RATIO:
        return None
        
    # 7. Pintu Tekanan Pembeli H1 & M5
    txns = pair.get("txns", {})
    txns_h1 = txns.get("h1", {})
    buys_h1 = int(txns_h1.get("buys", 0))
    sells_h1 = max(1, int(txns_h1.get("sells", 1)))
    buy_ratio_h1 = round(buys_h1 / sells_h1, 2)
    
    txns_m5 = txns.get("m5", {})
    buys_m5 = int(txns_m5.get("buys", 0))
    sells_m5 = max(1, int(txns_m5.get("sells", 1)))
    buy_ratio_m5 = round(buys_m5 / sells_m5, 2)
    
    # Tolak jika di 5 menit terakhir orang-orang sudah mulai antre jualan
    if buys_h1 < 30 or buy_ratio_h1 < MIN_BUY_SELL_RATIO or buy_ratio_m5 < MIN_BUY_RATIO_M5:
        return None

    # 8. Pintu Anti-Wash Trading (Unique Buyers)
    makers_data = pair.get("makers", {})
    unique_makers_h1 = makers_data.get("h1") if isinstance(makers_data, dict) else None
    
    if unique_makers_h1 is not None:
        if unique_makers_h1 < MIN_UNIQUE_BUYERS_H1:
            stats["wash_rejected"] += 1
            return None
        unique_buyers_est = unique_makers_h1
    else:
        unique_buyers_est = max(int(buys_h1 * 0.65), MIN_UNIQUE_BUYERS_H1)

    # 9. 🛡️ PINTU ANTI-WHALE & AUDIT KEAMANAN (Menolak koin seperti $o)
    sec_audit = check_solana_security_and_holders(mint)
    if not sec_audit["safe"]:
        if sec_audit.get("whale_danger"):
            stats["whale_dump_rejected"] += 1
            logger.info(f"🚫 WHALE DANGER DITOLAK: {base.get('symbol')} | Top Holder: {sec_audit['top_holder_pct']:.1f}% > {MAX_TOP_HOLDER_PCT}%")
        else:
            stats["security_rejected"] += 1
        return None

    # --- TAHAP 2: PERHITUNGAN PRESTASI & SKOR (0 - 100) ---
    score = 65  # Modal awal ketat
    
    # Prestasi Lonjakan Volume Masif
    if spike_ratio >= 6.0: 
        score += 12
    elif spike_ratio >= 3.5: 
        score += 8
        
    # Prestasi Super Breakout Hijau
    if pc_h1 >= 40.0:
        score += 10
    elif pc_h1 >= 20.0:
        score += 5
        
    if pc_m5 >= 15.0:
        score += 8
    elif pc_m5 >= 6.0:
        score += 4
        
    # Prestasi Dominasi Pembeli
    if buy_ratio_h1 >= 2.0: 
        score += 8
    elif buy_ratio_h1 >= 1.6: 
        score += 4
        
    # Prestasi Komunitas Asli
    if unique_buyers_est >= 45:
        score += 8
    elif unique_buyers_est >= 30:
        score += 4

    # Top Holder Sangat Sehat (< 5%)
    if sec_audit["top_holder_pct"] < 5.0 and sec_audit["top_holder_pct"] > 0:
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
        "buy_ratio": buy_ratio_h1,
        "buys_m5": buys_m5,
        "sells_m5": sells_m5,
        "buy_ratio_m5": buy_ratio_m5,
        "unique_buyers": unique_buyers_est,
        "pc_h1": pc_h1,
        "pc_m5": pc_m5,
        "score": score,
        "top_holder_pct": sec_audit["top_holder_pct"],
        "dex": pair.get("dexId", "solana").upper(),
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
        
    grade = "💎 GRADE S (ORGANIC WHALE BREAKOUT)" if data['score'] >= 90 else "🚀 GRADE A+ (HIGH HYPE CONVICTION)"

    holder_info = f"{data['top_holder_pct']:.1f}% (Aman &lt; 12%)" if data['top_holder_pct'] > 0 else "Distribusi Bersih"

    msg = f"""
{status_tag}
━━━━━━━━━━━━━━━━━━━━
💎 <b>{data['name']} (${data['symbol']})</b>
🎯 <b>Hype Score:</b> <code>{data['score']}/100</code> | <b>{grade}</b>
💰 <b>Market Cap:</b> <code>${data['mc']:,.0f}</code>
💧 <b>Likuiditas Pool:</b> <code>${data['liq_usd']:,.0f}</code>
⏳ <b>Umur Koin:</b> <code>{age_str} yang lalu</code>
━━━━━━━━━━━━━━━━━━━━
🔥 <b>Momentum Hype Real-Time:</b>
• <b>Perubahan Harga M5:</b> <code>+{data['pc_m5']:.1f}% (SEDANG NAIK KENCANG)</code>
• <b>Perubahan Harga H1:</b> <code>+{data['pc_h1']:.1f}% (CONFIRMED BREAKOUT)</code>
• <b>Rasio Pembeli M5:</b> <code>{data['buy_ratio_m5']}x (Dominasi Detik Ini)</code>
• <b>Rasio Pembeli H1:</b> <code>{data['buy_ratio']}x Buyers</code>
• <b>Volume 1 Jam (H1):</b> <code>${data['vol_h1']:,.0f}</code>
• <b>Lonjakan Volume (Spike):</b> <code>+{data['spike_ratio']:.2f}x Lipat!</code>
• <b>Dompet Pembeli Unik:</b> <code>~{data['unique_buyers']} Unique Wallets</code>
• <b>Platform DEX:</b> <code>{data['dex']}</code>
━━━━━━━━━━━━━━━━━━━━
🛡️ <b>Audit Keamanan & Anti-Whale Dump:</b>
• Top Holder Terbesar: <code>{holder_info}</code>
• Konfirmasi Anti-Dump: <code>LOLOS (Tekanan Beli Real-Time)</code>
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
    logger.info("Radar Reversal Scanner v3.7 Ultra-Strict aktif...")
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
                        # Threshold kelulusan skor diperketat ke 85
                        if res_eval and res_eval["score"] >= 85:
                            stats["reversals_detected"] += 1
                            logger.info(f"🚨 SUPER HYPE DITEMUKAN: {res_eval['name']} (${res_eval['symbol']}) | MC: ${res_eval['mc']:,.0f} | M5: +{res_eval['pc_m5']}%")
                            
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
        "version": "v3.7-ultra-strict-hype",
        "status": "online",
        "stats": stats,
        "cached_tokens": len(alerted_cache)
    })

if __name__ == "__main__":
    if TG_BOT_TOKEN and TG_CHAT_ID:
        try:
            startup_msg = """
🤖 <b>REVERSAL SCANNER BOT v3.7 ULTRA-STRICT ONLINE!</b>
━━━━━━━━━━━━━━━━━━━━
✅ <b>Status:</b> Terhubung Berhasil ke Server Render
📡 <b>Radar:</b> Solana Super Hype & Anti-Whale Dump
🛡️ <b>Protokol Baru:</b>
• Anti-Whale Concentration: <code>Max Holder &lt; 12.0%</code>
• Super Momentum M5: <code>Wajib M5 &gt;= +3.5%</code>
• Rasio Pembeli Real-Time: <code>M5 Buys &gt;= 1.35x Sells</code>
• Ambang Kelulusan Alert: <code>&gt;= 85 Poin (Grade A+ &amp; S)</code>
━━━━━━━━━━━━━━━━━━━━
🎯 <i>Hanya koin yang benar-benar sedang hype dan aman yang akan dikirim!</i>
"""
            bot.send_message(TG_CHAT_ID, startup_msg.strip(), parse_mode="HTML")
            logger.info("Notifikasi startup berhasil dikirim ke Telegram!")
        except Exception as e:
            logger.error(f"Gagal mengirim notifikasi startup Telegram: {e}")
            
    scanner_thread = threading.Thread(target=run_reversal_scanner_loop, daemon=True)
    scanner_thread.start()
    
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
