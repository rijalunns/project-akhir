"""Eksekusi skrip remediasi di VM lewat Proxmox VE API + QEMU Guest Agent.

Kebutuhan:
- VM harus menjalankan `qemu-guest-agent` dan opsi "QEMU Guest Agent" aktif di Proxmox.
- API token butuh privilege `VM.Monitor` (PVE 8: `VM.GuestAgent.Unrestricted`) pada VM terkait,
  plus `VM.Audit` jika PROXMOX_NODE tidak diisi (untuk mencari node otomatis).

Format env:
  PROXMOX_API_URL=https://192.168.1.10:8006/api2/json
  PROXMOX_API_TOKEN=root@pam!hyperheal=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
  PROXMOX_NODE=pve            (opsional)
  PROXMOX_VERIFY_SSL=false    (opsional, default false karena sertifikat self-signed)
"""
import asyncio
import logging
import os

import httpx

logger = logging.getLogger("hyperheal.proxmox")

PLACEHOLDERS = {"", "YOUR_PROXMOX_TOKEN", "YOUR_PROXMOX_API_TOKEN"}


class ProxmoxError(Exception):
    pass


def _url() -> str:
    return os.getenv("PROXMOX_API_URL", "").strip().rstrip("/")


def _token() -> str:
    return os.getenv("PROXMOX_API_TOKEN", "").strip()


def is_configured() -> bool:
    url = _url()
    return bool(url) and "example.com" not in url and _token() not in PLACEHOLDERS


def _headers() -> dict:
    token = _token()
    if not token.startswith("PVEAPIToken="):
        token = f"PVEAPIToken={token}"
    return {"Authorization": token}


def _client() -> httpx.AsyncClient:
    verify = os.getenv("PROXMOX_VERIFY_SSL", "false").strip().lower() in ("1", "true", "yes")
    return httpx.AsyncClient(base_url=_url(), headers=_headers(), verify=verify, timeout=20.0)


async def _request(client: httpx.AsyncClient, method: str, path: str, **kwargs) -> dict | list:
    res = await client.request(method, path, **kwargs)
    if res.status_code >= 400:
        raise ProxmoxError(f"Proxmox API {method} {path} -> HTTP {res.status_code}: {res.text[:300]}")
    return res.json().get("data")


async def _find_node(client: httpx.AsyncClient, vmid: str) -> str:
    node = os.getenv("PROXMOX_NODE", "").strip()
    if node:
        return node
    resources = await _request(client, "GET", "/cluster/resources", params={"type": "vm"})
    for r in resources or []:
        if str(r.get("vmid")) == str(vmid) and r.get("type") == "qemu":
            return r["node"]
    raise ProxmoxError(f"VM {vmid} (qemu) tidak ditemukan di cluster Proxmox")


async def execute_script(vmid: str, script: str, wait_seconds: int = 60) -> dict:
    """Jalankan `bash -c <script>` di VM dan tunggu hasilnya."""
    if not str(vmid).isdigit():
        raise ProxmoxError(f"VMID tidak valid: {vmid!r}")
    if not is_configured():
        raise ProxmoxError("Proxmox belum dikonfigurasi (PROXMOX_API_URL / PROXMOX_API_TOKEN)")

    async with _client() as client:
        node = await _find_node(client, vmid)
        base = f"/nodes/{node}/qemu/{vmid}/agent"

        started = await _request(client, "POST", f"{base}/exec",
                                 json={"command": ["/bin/bash", "-c", script]})
        pid = (started or {}).get("pid")
        if pid is None:
            raise ProxmoxError(f"Guest agent tidak mengembalikan PID: {started}")

        status: dict = {}
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait_seconds
        while loop.time() < deadline:
            await asyncio.sleep(1.5)
            status = await _request(client, "GET", f"{base}/exec-status", params={"pid": pid}) or {}
            if status.get("exited"):
                break
        else:
            return {"node": node, "pid": pid, "exited": False, "exitcode": None,
                    "stdout": "", "stderr": f"Belum selesai setelah {wait_seconds} detik (masih berjalan di VM)"}

        return {
            "node": node,
            "pid": pid,
            "exited": True,
            "exitcode": status.get("exitcode"),
            "stdout": (status.get("out-data") or "")[-2000:],
            "stderr": (status.get("err-data") or "")[-2000:],
        }
