import asyncio
import html
import logging
import os
import re
from typing import Awaitable, Callable

import httpx

logger = logging.getLogger("hyperheal.telegram")

PLACEHOLDER_TOKENS = {"", "YOUR_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN_HERE"}
TELEGRAM_MAX_LEN = 4096

# Handler perintah: (action, vmid) -> teks balasan (HTML)
CommandHandler = Callable[[str, str], Awaitable[str]]


def _token() -> str:
    return os.getenv("TELEGRAM_BOT_TOKEN", "").strip()


def _chat_id() -> str:
    return os.getenv("TELEGRAM_CHAT_ID", "").strip()


def is_configured() -> bool:
    return _token() not in PLACEHOLDER_TOKENS and bool(_chat_id())


def _api(method: str) -> str:
    return f"https://api.telegram.org/bot{_token()}/{method}"


def esc(value) -> str:
    """Escape nilai apa pun agar aman dipakai di parse_mode=HTML."""
    return html.escape("" if value is None else str(value), quote=False)


async def send_message(text: str, chat_id: str | None = None) -> bool:
    if not is_configured():
        logger.info("[Telegram Mock] %s", re.sub(r"<[^>]+>", "", text)[:500])
        return False

    if len(text) > TELEGRAM_MAX_LEN:
        text = text[:TELEGRAM_MAX_LEN - 20] + "\n…(dipotong)"

    payload = {"chat_id": chat_id or _chat_id(), "text": text, "parse_mode": "HTML",
               "disable_web_page_preview": True}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(_api("sendMessage"), json=payload)
        if res.status_code != 200:
            logger.error("Telegram sendMessage gagal (%s): %s", res.status_code, res.text[:300])
            # Coba ulang tanpa format HTML agar pesan tetap sampai
            payload.pop("parse_mode")
            payload["text"] = re.sub(r"<[^>]+>", "", text)
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(_api("sendMessage"), json=payload)
        return res.status_code == 200
    except httpx.HTTPError as e:
        logger.error("Telegram tidak terjangkau: %r", e)
        return False


async def send_alert_notification(incident_id: str, vmid: str, alert_name: str,
                                  metrics: dict, ai_analysis: dict) -> bool:
    source = "AI" if ai_analysis.get("source") == "ai" else "Fallback (AI tidak tersedia)"
    text = (
        f"🚨 <b>[HYPERHEAL AIOps ALERT]</b>\n"
        f"<b>Incident:</b> <code>{esc(incident_id)}</code>\n"
        f"<b>Target VM:</b> {esc(vmid)}\n"
        f"<b>Insiden:</b> {esc(alert_name)}\n"
        f"<b>Metrik Saat Ini:</b>\n"
        f"  • CPU: {esc(metrics.get('cpu_usage_pct', 0))}%\n"
        f"  • RAM: {esc(metrics.get('ram_usage_pct', 0))}%\n"
        f"  • Swap: {esc(metrics.get('swap_usage_pct', 0))}%\n\n"
        f"🧠 <b>Diagnosa ({esc(source)}):</b>\n"
        f"{esc(ai_analysis.get('root_cause'))}\n\n"
        f"💡 <b>Penjelasan:</b>\n"
        f"{esc(ai_analysis.get('explanation_id'))}\n\n"
        f"⚙️ <b>Rekomendasi Script Fix:</b>\n"
        f"<pre>{esc(ai_analysis.get('remediation_script'))}</pre>\n"
        f"↩️ <b>Rollback:</b> <code>{esc(ai_analysis.get('rollback_script'))}</code>\n\n"
        f"Tingkat Risiko: <b>{esc(str(ai_analysis.get('risk_level', 'unknown')).upper())}</b>\n\n"
        f"👉 Balas: /eksekusi_{esc(vmid)} untuk menjalankan script.\n"
        f"👉 Balas: /abaikan_{esc(vmid)} untuk membatalkan."
    )
    return await send_message(text)


# ----------------------------------------------------------------------------
# Listener perintah Telegram (long polling)
# ----------------------------------------------------------------------------
_CMD_RE = re.compile(r"^/(eksekusi|abaikan|pending|start|help)(?:[_ ]([\w.-]+))?(?:@\w+)?\s*$", re.IGNORECASE)

HELP_TEXT = (
    "🤖 <b>HyperHeal Bot</b>\n"
    "/pending — daftar remediasi yang menunggu persetujuan\n"
    "/eksekusi_&lt;vmid&gt; — jalankan script remediasi\n"
    "/abaikan_&lt;vmid&gt; — batalkan script remediasi"
)


async def _handle_update(update: dict, handler: CommandHandler) -> None:
    message = update.get("message") or update.get("edited_message") or {}
    text = (message.get("text") or "").strip()
    chat_id = str((message.get("chat") or {}).get("id", ""))
    if not text.startswith("/"):
        return

    # Hanya chat admin yang boleh memberi perintah
    if chat_id != _chat_id():
        logger.warning("Perintah ditolak dari chat tidak dikenal %s: %s", chat_id, text[:50])
        return

    match = _CMD_RE.match(text)
    if not match:
        await send_message("Perintah tidak dikenal.\n\n" + HELP_TEXT, chat_id)
        return

    action, vmid = match.group(1).lower(), match.group(2) or ""
    if action in ("start", "help"):
        await send_message(HELP_TEXT, chat_id)
        return
    if action in ("eksekusi", "abaikan") and not vmid:
        await send_message(f"Format: /{action}_&lt;vmid&gt;", chat_id)
        return

    try:
        reply = await handler(action, vmid)
    except Exception as e:  # jangan sampai listener mati karena satu perintah
        logger.exception("Gagal memproses perintah %s", text)
        reply = f"❌ Error saat memproses perintah: <code>{esc(e)}</code>"
    await send_message(reply, chat_id)


async def run_command_listener(handler: CommandHandler) -> None:
    """Long-polling getUpdates. Dijalankan sebagai background task saat app start."""
    if not is_configured():
        logger.info("Telegram belum dikonfigurasi — listener perintah tidak dijalankan.")
        return

    offset = None
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        # Lewati perintah lama yang menumpuk sebelum app start (hindari eksekusi tak sengaja)
        try:
            res = await client.get(_api("getUpdates"), params={"offset": -1, "timeout": 0})
            results = res.json().get("result", [])
            if results:
                offset = results[-1]["update_id"] + 1
        except Exception as e:
            logger.warning("Gagal membaca update awal Telegram: %r", e)

        logger.info("Telegram command listener aktif.")
        while True:
            try:
                params = {"timeout": 50, "allowed_updates": '["message","edited_message"]'}
                if offset is not None:
                    params["offset"] = offset
                res = await client.get(_api("getUpdates"), params=params)
                data = res.json()
                if not data.get("ok"):
                    # 409 = bot sedang memakai webhook / ada instance lain yang polling
                    logger.error("getUpdates gagal: %s", str(data)[:300])
                    await asyncio.sleep(15)
                    continue
                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    await _handle_update(update, handler)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("Telegram polling error: %r — coba lagi 5 detik", e)
                await asyncio.sleep(5)
