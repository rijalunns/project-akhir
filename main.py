import asyncio
import logging
import os
import secrets
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, HTTPException, Request, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, ConfigDict, Field

import proxmox_client
import telegram_notifier
from ai_agent import analyze_incident
from guardrails import sanitize_remediation_script
from telegram_notifier import esc, send_alert_notification

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("hyperheal")

APP_VERSION = "1.1.0"
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "100"))
API_KEY = os.getenv("HYPERHEAL_API_KEY", "").strip()
DRY_RUN = os.getenv("DRY_RUN", "false").strip().lower() in ("1", "true", "yes")
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]

# ----------------------------------------------------------------------------
# State in-memory (hilang saat restart)
# ----------------------------------------------------------------------------
pending_remediations: dict[str, dict] = {}
incident_history: deque[dict] = deque(maxlen=200)
stats = {
    "started_at": datetime.now(timezone.utc).isoformat(),
    "total_requests": 0,
    "rate_limited": 0,
    "unauthorized": 0,
    "alerts_received": 0,
    "rejected_by_guardrail": 0,
    "ai_fallback_used": 0,
    "remediation_executed": 0,
    "remediation_simulated": 0,
    "remediation_failed": 0,
    "remediation_ignored": 0,
    "remediation_superseded": 0,
}
_rate_buckets: dict[str, deque] = defaultdict(deque)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record(incident_id: str, **fields) -> None:
    for item in incident_history:
        if item["incident_id"] == incident_id:
            item.update(fields, updated_at=_now())
            return
    incident_history.append({"incident_id": incident_id, "created_at": _now(), **fields})


# ----------------------------------------------------------------------------
# Logika remediasi (dipakai oleh endpoint HTTP dan perintah Telegram)
# ----------------------------------------------------------------------------
async def execute_pending(vmid: str, via: str) -> dict:
    item = pending_remediations.pop(vmid, None)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Tidak ada perbaikan pending untuk VM {vmid}")

    script = item["script"]
    # Cek ulang guardrails sebelum eksekusi (defense in depth)
    is_safe, reason = sanitize_remediation_script(script)
    if not is_safe:
        stats["rejected_by_guardrail"] += 1
        _record(item["incident_id"], status="REJECTED_BY_GUARDRAIL", reason=reason)
        raise HTTPException(status_code=422, detail=reason)

    base = {"incident_id": item["incident_id"], "vmid": vmid, "script": script, "via": via}

    if DRY_RUN or not proxmox_client.is_configured():
        stats["remediation_simulated"] += 1
        note = "DRY_RUN aktif" if DRY_RUN else "Proxmox belum dikonfigurasi"
        _record(item["incident_id"], status="SIMULATED", via=via)
        return {**base, "action": "SIMULATED",
                "message": f"Script TIDAK dijalankan ({note}). Ini hanya simulasi."}

    try:
        result = await proxmox_client.execute_script(vmid, script)
    except Exception as e:  # ProxmoxError, httpx error, dll.
        # Kembalikan ke antrean supaya admin bisa mencoba lagi
        pending_remediations.setdefault(vmid, item)
        stats["remediation_failed"] += 1
        _record(item["incident_id"], status="EXECUTION_FAILED", error=str(e), via=via)
        logger.error("Eksekusi remediasi VM %s gagal: %s", vmid, e)
        raise HTTPException(status_code=502, detail=f"Gagal mengeksekusi di Proxmox: {e}")

    success = result["exited"] and result["exitcode"] == 0
    status = "EXECUTED" if success else ("RUNNING" if not result["exited"] else "EXECUTED_WITH_ERROR")
    stats["remediation_executed" if success else "remediation_failed"] += 1
    _record(item["incident_id"], status=status, via=via, result=result)
    return {**base, "action": status, "result": result,
            "message": f"Script dikirim ke QEMU Guest Agent VM {vmid} (node {result['node']})."}


def ignore_pending(vmid: str, via: str) -> dict:
    item = pending_remediations.pop(vmid, None)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Tidak ada perbaikan pending untuk VM {vmid}")
    stats["remediation_ignored"] += 1
    _record(item["incident_id"], status="IGNORED", via=via)
    return {"incident_id": item["incident_id"], "vmid": vmid, "action": "IGNORED"}


async def telegram_command_handler(action: str, vmid: str) -> str:
    if action == "pending":
        if not pending_remediations:
            return "✅ Tidak ada remediasi yang menunggu persetujuan."
        lines = [f"• VM <b>{esc(v)}</b> — {esc(i['alert_name'])} (<code>{esc(i['incident_id'])}</code>)"
                 for v, i in pending_remediations.items()]
        return "⏳ <b>Remediasi pending:</b>\n" + "\n".join(lines)

    try:
        if action == "abaikan":
            r = ignore_pending(vmid, via="telegram")
            return f"🚫 Remediasi VM <b>{esc(vmid)}</b> (<code>{esc(r['incident_id'])}</code>) dibatalkan."

        r = await execute_pending(vmid, via="telegram")
    except HTTPException as e:
        return f"❌ {esc(e.detail)}"

    icon = {"EXECUTED": "✅", "SIMULATED": "🧪", "RUNNING": "⏳"}.get(r["action"], "⚠️")
    text = f"{icon} <b>{esc(r['action'])}</b> — VM {esc(vmid)} (<code>{esc(r['incident_id'])}</code>)\n{esc(r['message'])}"
    result = r.get("result")
    if result:
        text += f"\nExit code: <code>{esc(result.get('exitcode'))}</code>"
        if result.get("stdout"):
            text += f"\n<b>stdout:</b>\n<pre>{esc(result['stdout'][-1500:])}</pre>"
        if result.get("stderr"):
            text += f"\n<b>stderr:</b>\n<pre>{esc(result['stderr'][-1500:])}</pre>"
    return text


# ----------------------------------------------------------------------------
# App, lifespan & middleware
# ----------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(_: FastAPI):
    task = None
    if os.getenv("TELEGRAM_POLLING", "true").strip().lower() in ("1", "true", "yes"):
        task = asyncio.create_task(telegram_notifier.run_command_listener(telegram_command_handler))
    if not API_KEY:
        logger.warning("HYPERHEAL_API_KEY belum diatur — endpoint tidak memakai autentikasi!")
    yield
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="HyperHeal AIOps Orchestrator", version=APP_VERSION, lifespan=lifespan)

if CORS_ORIGINS:
    app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["GET", "POST"],
                       allow_headers=["Content-Type", "X-API-Key"])


@app.middleware("http")
async def rate_limit_and_security_headers(request: Request, call_next):
    stats["total_requests"] += 1
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    bucket = _rate_buckets[ip]
    while bucket and now - bucket[0] > 60:
        bucket.popleft()
    if len(bucket) >= RATE_LIMIT_PER_MINUTE:
        stats["rate_limited"] += 1
        return JSONResponse(status_code=429, content={"detail": "Terlalu banyak request, coba lagi nanti."},
                            headers={"Retry-After": "60"})
    bucket.append(now)

    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    return response


_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(key: str | None = Security(_api_key_header)) -> None:
    if not API_KEY:
        return  # autentikasi dinonaktifkan (lihat warning saat startup)
    if not key or not secrets.compare_digest(key, API_KEY):
        stats["unauthorized"] += 1
        raise HTTPException(status_code=401, detail="API key tidak valid (header X-API-Key)")


# ----------------------------------------------------------------------------
# Schema
# ----------------------------------------------------------------------------
class AlertWebhookPayload(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "vmid": "104",
        "vm_name": "cbt-server",
        "alert_name": "HighCPU_HighRAM",
        "metrics": {"cpu_usage_pct": 98.2, "ram_usage_pct": 94.5, "swap_usage_pct": 52.0},
        "top_processes": [
            "USER       PID %CPU %MEM COMMAND",
            "www-data  4821 48.2 12.1 php-fpm: pool www",
            "mysql     1205  2.1 28.4 /usr/sbin/mysqld",
        ],
        "recent_logs": [
            "php-fpm[4821]: [WARNING] script execution duration exceeded 30s in /var/www/cbt/submit.php",
            "php-fpm[4822]: [ERROR] MySQL server has gone away",
        ],
    }]})

    vmid: str = Field(..., pattern=r"^\d{1,9}$", description="VMID Proxmox")
    vm_name: str = Field("", max_length=128)
    alert_name: str = Field(..., min_length=1, max_length=128)
    metrics: dict[str, float] = Field(default_factory=dict)
    top_processes: list[str] = Field(default_factory=list, max_length=50)
    recent_logs: list[str] = Field(default_factory=list, max_length=100)


# ----------------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------------
@app.get("/")
def health_check():
    return {"status": "ok", "app": "HyperHeal AIOps Core", "version": APP_VERSION}


@app.post("/webhook/alert", dependencies=[Depends(require_api_key)])
async def handle_alert(payload: AlertWebhookPayload):
    stats["alerts_received"] += 1
    incident_id = f"INC-{uuid.uuid4().hex[:8].upper()}"

    evidence_pack = {
        "incident_id": incident_id,
        "target": {"vmid": payload.vmid, "vm_name": payload.vm_name},
        "alert": payload.alert_name,
        "telemetry_metrics": payload.metrics,
        "top_active_processes": [p[:300] for p in payload.top_processes],
        "recent_logs_tail": [line[:500] for line in payload.recent_logs],
    }

    # 1. Analisa AI (selalu mengembalikan dict, fallback jika AI gagal)
    ai_analysis = await analyze_incident(evidence_pack)
    if ai_analysis.get("source") != "ai":
        stats["ai_fallback_used"] += 1

    # 2. Validasi Safety Guardrails
    is_safe, reason = sanitize_remediation_script(ai_analysis.get("remediation_script", ""))
    if not is_safe:
        stats["rejected_by_guardrail"] += 1
        _record(incident_id, vmid=payload.vmid, alert_name=payload.alert_name,
                status="REJECTED_BY_GUARDRAIL", reason=reason)
        await telegram_notifier.send_message(
            f"⛔ <b>Remediasi diblokir guardrails</b>\nVM {esc(payload.vmid)} — {esc(payload.alert_name)}\n"
            f"<code>{esc(incident_id)}</code>\nAlasan: {esc(reason)}\n"
            f"<pre>{esc(ai_analysis.get('remediation_script'))}</pre>")
        return {"incident_id": incident_id, "status": "REJECTED_BY_GUARDRAIL",
                "reason": reason, "ai_analysis": ai_analysis}

    # 3. Simpan ke antrean (1 pending per VM; alert baru menggantikan yang lama)
    previous = pending_remediations.get(payload.vmid)
    if previous:
        stats["remediation_superseded"] += 1
        _record(previous["incident_id"], status="SUPERSEDED", superseded_by=incident_id)

    pending_remediations[payload.vmid] = {
        "incident_id": incident_id,
        "vmid": payload.vmid,
        "alert_name": payload.alert_name,
        "script": ai_analysis["remediation_script"],
        "rollback_script": ai_analysis.get("rollback_script", "true"),
        "ai_analysis": ai_analysis,
        "created_at": _now(),
    }
    _record(incident_id, vmid=payload.vmid, alert_name=payload.alert_name, status="WAITING_APPROVAL",
            risk_level=ai_analysis.get("risk_level"), source=ai_analysis.get("source"))

    # 4. Kirim notifikasi Telegram
    notified = await send_alert_notification(incident_id=incident_id, vmid=payload.vmid,
                                             alert_name=payload.alert_name, metrics=payload.metrics,
                                             ai_analysis=ai_analysis)

    return {"incident_id": incident_id, "status": "WAITING_APPROVAL", "telegram_sent": notified,
            "superseded": previous["incident_id"] if previous else None, "ai_analysis": ai_analysis}


@app.get("/remediation/pending", dependencies=[Depends(require_api_key)])
def list_pending():
    return {"count": len(pending_remediations), "items": list(pending_remediations.values())}


@app.post("/remediation/execute/{vmid}", dependencies=[Depends(require_api_key)])
async def execute_remediation(vmid: str):
    return await execute_pending(vmid, via="api")


@app.post("/remediation/ignore/{vmid}", dependencies=[Depends(require_api_key)])
def ignore_remediation(vmid: str):
    return ignore_pending(vmid, via="api")


@app.get("/stats", dependencies=[Depends(require_api_key)])
def get_stats():
    return {
        **stats,
        "pending_count": len(pending_remediations),
        "proxmox_configured": proxmox_client.is_configured(),
        "telegram_configured": telegram_notifier.is_configured(),
        "dry_run": DRY_RUN,
        "auth_enabled": bool(API_KEY),
        "recent_incidents": list(incident_history)[-20:],
    }
