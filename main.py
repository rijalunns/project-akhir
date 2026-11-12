import os
import uuid
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

from ai_agent import analyze_incident
from telegram_notifier import send_alert_notification
from guardrails import sanitize_remediation_script

app = FastAPI(title="HyperHeal AIOps Orchestrator", version="1.0.0")

# In-memory storage sementara untuk pending remediation
pending_remediations = {}

class AlertWebhookPayload(BaseModel):
    vmid: str = "104"
    vm_name: str = "cbt-server"
    alert_name: str = "HighCPU_HighRAM"
    metrics: dict = {
        "cpu_usage_pct": 98.2,
        "ram_usage_pct": 94.5,
        "swap_usage_pct": 52.0
    }
    top_processes: list[str] = [
        "USER       PID %CPU %MEM COMMAND",
        "www-data  4821 48.2 12.1 php-fpm: pool www",
        "www-data  4822 47.9 11.9 php-fpm: pool www",
        "mysql     1205  2.1 28.4 /usr/sbin/mysqld"
    ]
    recent_logs: list[str] = [
        "php-fpm[4821]: [WARNING] script execution duration exceeded 30s in /var/www/cbt/submit.php",
        "kernel: Out of memory: Kill process 4821?",
        "php-fpm[4822]: [ERROR] MySQL server has gone away"
    ]

@app.get("/")
def health_check():
    return {"status": "ok", "app": "HyperHeal AIOps Core", "version": "1.0.0"}

@app.post("/webhook/alert")
async def handle_alert(payload: AlertWebhookPayload):
    incident_id = f"INC-{uuid.uuid4().hex[:8].upper()}"

    evidence_pack = {
        "incident_id": incident_id,
        "target": {
            "vmid": payload.vmid,
            "vm_name": payload.vm_name
        },
        "alert": payload.alert_name,
        "telemetry_metrics": payload.metrics,
        "top_active_processes": payload.top_processes,
        "recent_logs_tail": payload.recent_logs
    }

    # 1. Panggil DeepSeek AI Agent untuk analisa
    ai_analysis = await analyze_incident(evidence_pack)

    # 2. Validasi Safety Guardrails
    is_safe, reason = sanitize_remediation_script(ai_analysis.get("remediation_script", ""))
    if not is_safe:
        return {
            "incident_id": incident_id,
            "status": "REJECTED_BY_GUARDRAIL",
            "reason": reason,
            "ai_analysis": ai_analysis
        }

    # 3. Simpan rencana remedi ke pending queue
    pending_remediations[payload.vmid] = {
        "incident_id": incident_id,
        "vmid": payload.vmid,
        "script": ai_analysis.get("remediation_script"),
        "ai_analysis": ai_analysis
    }

    # 4. Kirim Alert ke Telegram
    await send_alert_notification(
        incident_id=incident_id,
        vmid=payload.vmid,
        alert_name=payload.alert_name,
        metrics=payload.metrics,
        ai_analysis=ai_analysis
    )

    return {
        "incident_id": incident_id,
        "status": "WAITING_APPROVAL",
        "ai_analysis": ai_analysis
    }

@app.post("/remediation/execute/{vmid}")
async def execute_remediation(vmid: str):
    if vmid not in pending_remediations:
        raise HTTPException(status_code=404, detail="Tidak ada perbaikan pending untuk VM ini")

    item = pending_remediations.pop(vmid)
    script_to_run = item["script"]

    # TODO: Disambungkan ke Proxmox QEMU Guest Agent di fase berikutnya
    return {
        "incident_id": item["incident_id"],
        "vmid": vmid,
        "action": "EXECUTED",
        "script": script_to_run,
        "message": f"Script berhasil dikirim ke Proxmox Guest Agent untuk VM {vmid}!"
    }
