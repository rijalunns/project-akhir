# HyperHeal AIOps Orchestrator

**HyperHeal** adalah platform AIOps yang memantau metrik Proxmox‑based server, mendeteksi anomali (CPU, RAM, swap), dan secara otomatis menghasilkan skrip remediasi yang dapat dijalankan setelah persetujuan admin. 

- **FastAPI** sebagai rule‑engine pusat (bukan di tiap node).
- **Prometheus** & **Node‑Exporter** untuk scraping metrik.
- **Grafana** (dijalankan di container lain) untuk visualisasi.
- **Telegram Bot** mengirim notifikasi alert ke admin.
- **Guardrails** membersihkan skrip remediasi sebelum dieksekusi.
- **Docker‑Compose** mem‑orchestrasi semua service (Prometheus, Node‑Exporter, Loki, Grafana, dll.)

## Struktur Direktori
```
/opt/hyperheal/
│   docker-compose.monitoring.yml   # compose untuk monitoring stack
│   .env                            # env dengan API keys & tokens
│   main.py                         # FastAPI rule engine
│   ai_agent.py                     # Analisis insiden AI
│   guardrails.py                   # Sanitasi skrip
│   telegram_notifier.py            # Kirim notifikasi ke Telegram
│   requirements.txt                # Python deps
│
├─ config/
│    prometheus.yml                # konfigurasi Prometheus
│
└─ scripts/
     simulate_disaster.sh           # skrip contoh untuk simulasi
```

## Menjalankan Lokal (Development)
```bash
# Clone atau pastikan konten ada di /opt/hyperheal
cd /opt/hyperheal

# Buat virtualenv (opsional) dan install deps
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Siapkan .env (contoh minimal)
cat > .env <<EOF
AI_ENDPOINT=https://api.openrouter.ai/v1
AI_API_KEY=YOUR_KEY
AI_MODEL=gpt-4o-mini
TELEGRAM_BOT_TOKEN=YOUR_BOT_TOKEN
TELEGRAM_CHAT_ID=YOUR_CHAT_ID
PROXMOX_API_URL=https://proxmox.example.com/api2/json
PROXMOX_API_TOKEN=YOUR_PROXMOX_TOKEN
EOF

# Jalankan FastAPI (development)
uvicorn main:app --host 0.0.0.0 --port 8000
```
Akses UI Swagger di `http://localhost:8000/docs`.

## Deploy dengan Docker‑Compose (Production)
```bash
cd /opt/hyperheal
docker compose -f docker-compose.monitoring.yml up -d
```
Komponen yang akan berjalan:
- `hyperheal-node-exporter` → port 9100
- `hyperheal-prometheus` → port 9090
- `hyperheal-grafana` (jika ada konfigurasi) → port 3001‑3002
- `hyperheal-loki` → port 3100
- `hyperheal-fastapi` (bisa ditambah service di compose) → port 8000 (contoh)

## API Utama
| Endpoint | Method | Body | Keterangan |
|----------|--------|------|------------|
| `/alert` | POST | `AlertWebhookPayload` (JSON) | Menerima webhook alert dari Proxmox, menjalankan analisis AI, menyimpan remediasi pending |
| `/remediation/{id}` | POST | `{ "approved": true }` | Admin menyetujui remediasi; skrip dijalankan via QEMU guest agent atau Proxmox API |
| `/stats` | GET | – | Statistik runtime (total request, blokir rate‑limit, dll.) |

## Catatan Keamanan
- Semua endpoint dilindungi oleh **rate‑limit** (default 100 req/min/IP).
- **Helmet** & **CORS** diterapkan pada FastAPI via middleware optional.
- Skrip remediasi dibersihkan dengan `guardrails.py` sebelum dieksekusi.
- Token disimpan di file `.env` yang **tidak** di‑commit (tambahkan ke `.gitignore`).

---

**Penulis:** Adib Rijal (Ijal) – https://adibrijal.my.id
