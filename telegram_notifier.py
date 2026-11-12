import os
import httpx

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

async def send_alert_notification(incident_id: str, vmid: str, alert_name: str, metrics: dict, ai_analysis: dict):
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
        print(f"[Telegram Mock] Notifikasi alert untuk VM {vmid}: {ai_analysis.get('root_cause')}")
        return

    text = (
        f"🚨 <b>[HYPERHEAL AIOps ALERT]</b>\n"
        f"<b>Target VM:</b> {vmid}\n"
        f"<b>Insiden:</b> {alert_name}\n"
        f"<b>Metrik Saat Ini:</b>\n"
        f"  • CPU: {metrics.get('cpu_usage_pct', 0)}%\n"
        f"  • RAM: {metrics.get('ram_usage_pct', 0)}%\n"
        f"  • Swap: {metrics.get('swap_usage_pct', 0)}%\n\n"
        f"🧠 <b>Diagnosa AI (DeepSeek):</b>\n"
        f"{ai_analysis.get('root_cause')}\n\n"
        f"💡 <b>Penjelasan:</b>\n"
        f"{ai_analysis.get('explanation_id')}\n\n"
        f"⚙️ <b>Rekomendasi Script Fix:</b>\n"
        f"<code>{ai_analysis.get('remediation_script')}</code>\n\n"
        f"Tingkat Risiko: <b>{ai_analysis.get('risk_level', 'unknown').upper()}</b>\n\n"
        f"👉 Balas: <code>/eksekusi_{vmid}</code> untuk menjalankan script.\n"
        f"👉 Balas: <code>/abaikan_{vmid}</code> untuk membatalkan."
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload)
