# HyperHeal AIOps Orchestrator

**HyperHeal** adalah platform AIOps yang memantau metrik server/VM berbasis Proxmox, mendeteksi anomali (CPU, RAM, swap), lalu menghasilkan skrip remediasi dengan AI yang **baru dijalankan setelah disetujui admin** lewat Telegram atau API.

- **FastAPI** sebagai rule-engine pusat (bukan di tiap node).
- **AI (LLM)** lewat endpoint kompatibel OpenAI (OpenRouter, DeepSeek, dll.) untuk diagnosa + skrip perbaikan, dengan fallback jika AI gagal.
- **Guardrails** memblokir perintah berbahaya sebelum disimpan dan sekali lagi sebelum dieksekusi.
- **Telegram Bot** mengirim alert dan menerima perintah persetujuan (`/eksekusi_<vmid>`, `/abaikan_<vmid>`, `/pending`).
- **Proxmox API + QEMU Guest Agent** untuk menjalankan skrip di dalam VM.
- **Prometheus, Node-Exporter, Loki, Grafana** via Docker Compose untuk monitoring & visualisasi.

## Alur Kerja
```
VM / script  --POST /webhook/alert-->  FastAPI  --> AI (diagnosa + script)
                                          |--> Guardrails (blokir perintah berbahaya)
                                          |--> Antrean pending (1 per VM)
                                          '--> Telegram: alert ke admin
Admin  --/eksekusi_<vmid> (Telegram) atau POST /remediation/execute/<vmid>-->
FastAPI --> Guardrails (cek ulang) --> Proxmox QEMU Guest Agent --> hasil (exit code, stdout/stderr)
```

## Struktur Direktori
```
/opt/hyperheal/
│   docker-compose.monitoring.yml   # monitoring stack
│   .env.example                    # template konfigurasi (salin ke .env)
│   main.py                         # FastAPI: endpoint, auth, rate-limit, antrean
│   ai_agent.py                     # Analisis insiden dengan AI
│   guardrails.py                   # Pemblokir perintah berbahaya
│   telegram_notifier.py            # Notifikasi + listener perintah Telegram
│   proxmox_client.py               # Eksekusi via Proxmox QEMU Guest Agent
│   requirements.txt
│
├─ config/
│    prometheus.yml                 # konfigurasi scrape Prometheus
│    alert_rules.yml                # aturan alert CPU/RAM/swap/target down
│
├─ grafana/provisioning/            # datasource Prometheus & Loki + folder dashboard
│
└─ scripts/
     simulate_disaster.sh           # simulasi beban + kirim alert (demo)
```

## Konfigurasi
```bash
cp .env.example .env
nano .env   # isi token asli, JANGAN di-commit (.env sudah ada di .gitignore)
```

| Variabel | Keterangan |
|---|---|
| `AI_ENDPOINT`, `AI_API_KEY`, `AI_MODEL` | Endpoint LLM kompatibel OpenAI. Tanpa key → mode fallback |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Bot & chat admin. Hanya chat ini yang boleh memberi perintah |
| `TELEGRAM_POLLING` | `true` = bot mendengarkan perintah (long polling) |
| `PROXMOX_API_URL` | Contoh `https://192.168.1.10:8006/api2/json` |
| `PROXMOX_API_TOKEN` | Format `user@realm!tokenid=secret` |
| `PROXMOX_NODE` | Opsional, kosong = dicari otomatis |
| `PROXMOX_VERIFY_SSL` | Default `false` (sertifikat self-signed) |
| `DRY_RUN` | `true` = skrip tidak pernah benar-benar dijalankan |
| `HYPERHEAL_API_KEY` | **Wajib di production.** Dikirim via header `X-API-Key` |
| `RATE_LIMIT_PER_MINUTE` | Default 100 request/menit/IP |
| `CORS_ORIGINS` | Daftar origin dipisah koma, kosong = CORS nonaktif |
| `GF_ADMIN_USER`, `GF_ADMIN_PASSWORD` | Login Grafana (password wajib diisi) |

> Jika Proxmox belum dikonfigurasi, eksekusi akan berstatus `SIMULATED` (tidak menjalankan apa pun) — bukan `EXECUTED`.

### Syarat Proxmox
1. Di dalam VM: `apt install qemu-guest-agent && systemctl enable --now qemu-guest-agent`
2. Di Proxmox: VM → Options → **QEMU Guest Agent: Enabled**
3. Buat API token (Datacenter → Permissions → API Tokens) dengan privilege `VM.Monitor`
   (PVE 8: `VM.GuestAgent.Unrestricted`) pada VM target, plus `VM.Audit` jika `PROXMOX_NODE` dikosongkan.

## Menjalankan Lokal (Development)
```bash
cd /opt/hyperheal
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```
Swagger UI: `http://localhost:8000/docs` (klik **Authorize** lalu isi API key).

> Jalankan uvicorn dengan **1 worker** saja: antrean disimpan di memori dan bot Telegram memakai long polling.

## Monitoring Stack (Docker Compose)
```bash
cd /opt/hyperheal
docker compose -f docker-compose.monitoring.yml up -d
```
| Container | Port |
|---|---|
| `hyperheal-node-exporter` | 9100 |
| `hyperheal-prometheus` | 9090 |
| `hyperheal-loki` | 3100 |
| `hyperheal-grafana` | 3001 |

Alert rules Prometheus bisa dilihat di `http://<server>:9090/alerts`.

## API
Semua endpoint selain `/` butuh header `X-API-Key` (jika `HYPERHEAL_API_KEY` diisi).

| Endpoint | Method | Keterangan |
|---|---|---|
| `/` | GET | Health check |
| `/webhook/alert` | POST | Terima alert (`vmid`, `alert_name`, `metrics`, `top_processes`, `recent_logs`), analisa AI, simpan pending, kirim Telegram |
| `/remediation/pending` | GET | Daftar remediasi yang menunggu persetujuan |
| `/remediation/execute/{vmid}` | POST | Setujui & jalankan skrip via QEMU Guest Agent |
| `/remediation/ignore/{vmid}` | POST | Batalkan remediasi pending |
| `/stats` | GET | Statistik runtime + 20 insiden terakhir |

Contoh:
```bash
curl -X POST http://localhost:8000/webhook/alert \
  -H "Content-Type: application/json" -H "X-API-Key: $HYPERHEAL_API_KEY" \
  -d '{"vmid":"104","vm_name":"cbt-server","alert_name":"HighCPU","metrics":{"cpu_usage_pct":98.2}}'
```

## Simulasi (Demo)
```bash
chmod +x scripts/simulate_disaster.sh
HYPERHEAL_API_KEY=xxx ./scripts/simulate_disaster.sh trigger   # kirim alert tanpa beban
./scripts/simulate_disaster.sh cpu                              # butuh: apt install stress-ng
```

## Catatan Keamanan
- Rate-limit per IP (default 100 req/menit) dan header keamanan dasar (`nosniff`, `X-Frame-Options`).
- Autentikasi API key (`X-API-Key`) — aktif jika `HYPERHEAL_API_KEY` diisi.
- Perintah Telegram hanya diterima dari `TELEGRAM_CHAT_ID`; perintah lama sebelum app start diabaikan.
- Guardrails berbasis denylist **bukan jaminan mutlak** — persetujuan admin tetap lapisan pengaman utama.
- Data log/proses dari VM diperlakukan sebagai data tak tepercaya di prompt AI.
- Antrean & riwayat insiden masih di memori (hilang saat restart).

---

**Penulis:** Adib Rijal (Ijal) – https://adibrijal.my.id
