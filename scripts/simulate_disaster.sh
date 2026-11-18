#!/bin/bash
# ==========================================================
# HyperHeal - Disaster Simulation Script (Bahan Uji Coba/Demo)
# ==========================================================
# Variabel opsional:
#   HYPERHEAL_URL      (default http://127.0.0.1:8000)
#   HYPERHEAL_API_KEY  (wajib jika API key diaktifkan di server)
#   SIM_VMID           (default 104)
#   SIM_VM_NAME        (default cbt-server-ujian)

HYPERHEAL_URL="${HYPERHEAL_URL:-http://127.0.0.1:8000}"
SIM_VMID="${SIM_VMID:-104}"
SIM_VM_NAME="${SIM_VM_NAME:-cbt-server-ujian}"
DURATION="120s"

# Escape string agar aman dimasukkan ke JSON
json_escape() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="${s//$'\t'/\\t}"
  s="${s//$'\n'/\\n}"
  s="${s//$'\r'/}"
  printf '%s' "$s"
}

require_stress_ng() {
  if ! command -v stress-ng >/dev/null 2>&1; then
    echo "❌ stress-ng belum terpasang. Install dulu: sudo apt install -y stress-ng"
    exit 1
  fi
}

trigger_alert() {
  local alert_type cpu ram top_proc log_msg body http_code tmp
  alert_type="$(json_escape "$1")"
  cpu="$2"
  ram="$3"
  top_proc="$(json_escape "$4")"
  log_msg="$(json_escape "$5")"

  body=$(cat <<EOF
{
  "vmid": "$(json_escape "$SIM_VMID")",
  "vm_name": "$(json_escape "$SIM_VM_NAME")",
  "alert_name": "${alert_type}",
  "metrics": {
    "cpu_usage_pct": ${cpu},
    "ram_usage_pct": ${ram},
    "swap_usage_pct": 45.0
  },
  "top_processes": [
    "${top_proc}",
    "mysql     1190  0.4 35.0 /usr/sbin/mysqld"
  ],
  "recent_logs": [
    "${log_msg}"
  ]
}
EOF
)

  echo "📡 [AIOps Auto-Detect] Mengirim alert ${1} ke ${HYPERHEAL_URL} ..."
  tmp="$(mktemp)"
  http_code=$(curl -sS -o "$tmp" -w '%{http_code}' --max-time 60 \
    -X POST "${HYPERHEAL_URL}/webhook/alert" \
    -H "Content-Type: application/json" \
    ${HYPERHEAL_API_KEY:+-H "X-API-Key: ${HYPERHEAL_API_KEY}"} \
    -d "$body" 2>&1)
  local curl_status=$?

  if [ $curl_status -ne 0 ]; then
    echo "❌ Gagal menghubungi HyperHeal (curl exit ${curl_status}). Apakah FastAPI sudah jalan?"
  elif [ "$http_code" != "200" ]; then
    echo "❌ HyperHeal membalas HTTP ${http_code}:"
    cat "$tmp"; echo
  elif grep -q '"telegram_sent":true' "$tmp"; then
    echo "✅ Alert diterima HyperHeal dan terkirim ke Telegram!"
  else
    echo "⚠️  Alert diterima HyperHeal, tapi notifikasi Telegram tidak terkirim. Respons:"
    cat "$tmp"; echo
  fi
  rm -f "$tmp"
}

case "$1" in
  cpu)
    require_stress_ng
    echo "🔥 [SIMULASI] Memulai lonjakan CPU 100% (Durasi: ${DURATION})..."
    echo "Grafik di Grafana sedang melonjak merah..."
    # Tembak webhook di background agar Telegram langsung berbunyi saat beban dimulai
    trigger_alert "HighCPU_Overload" "99.8" "42.0" "stress-ng  9812 99.2  1.2 stress-ng-cpu [runaway worker]" "kernel: [warning] CPU core 0-3 throttled due to excessive runaway thread pid 9812" &
    stress-ng --cpu 4 --cpu-load 100 --timeout "$DURATION" --metrics-brief
    wait
    ;;

  ram)
    require_stress_ng
    echo "🔥 [SIMULASI] Memulai lonjakan konsumsi RAM 90% (Durasi: ${DURATION})..."
    echo "Grafik di Grafana sedang melonjak merah..."
    trigger_alert "HighRAM_MemoryLeak" "35.0" "92.4" "stress-ng  9844 12.0 89.5 stress-ng-vm [malloc leak]" "kernel: [alert] Out of memory: Kill process 9844 (stress-ng-vm) score 895 or sacrifice child" &
    stress-ng --vm 2 --vm-bytes 85% --timeout "$DURATION" --metrics-brief
    wait
    ;;

  cbt)
    require_stress_ng
    echo "🔥 [SIMULASI] Simulasi Ujian CBT Overload (CPU 100% + RAM 80% + Swap)..."
    echo "Grafik di Grafana sedang melonjak merah..."
    trigger_alert "CBT_SystemCrash_Overload" "99.4" "95.8" "www-data  5012 49.8 14.2 php-fpm: pool cbt_exam" "php-fpm[5012]: [WARNING] execution duration exceeded 60s in /var/www/cbt/ujian_submit.php" &
    stress-ng --cpu 2 --vm 2 --vm-bytes 80% --timeout "$DURATION" --metrics-brief
    wait
    ;;

  trigger)
    echo "📡 [SIMULASI] Menembak Webhook Alert HyperHeal secara instan..."
    trigger_alert "HighCPU_Overload" "99.4" "95.8" "www-data  5012 49.8 14.2 php-fpm: pool cbt_exam" "php-fpm[5012]: [WARNING] execution duration exceeded 60s in /var/www/cbt/ujian_submit.php"
    ;;

  stop)
    echo "🛑 Menghentikan seluruh proses simulasi bencana..."
    if pkill -9 stress-ng 2>/dev/null; then
      echo "✅ Semua proses simulasi berhasil dihentikan."
    else
      echo "ℹ️  Tidak ada proses stress-ng yang berjalan."
    fi
    ;;

  *)
    echo "=========================================================="
    echo "   HYPERHEAL - SKRIP SIMULASI BENCANA SERVER (DEMO)       "
    echo "=========================================================="
    echo "Penggunaan: ./simulate_disaster.sh [perintah]"
    echo ""
    echo "Pilihan perintah:"
    echo "  cpu     : CPU spike 100% + Auto Alert Telegram (2 menit)"
    echo "  ram     : RAM spike 85% + Auto Alert Telegram (2 menit)"
    echo "  cbt     : CPU + RAM Spike (Server CBT Macet) + Auto Alert"
    echo "  trigger : Tes Webhook Alert instan tanpa beban hardware"
    echo "  stop    : Hentikan paksa beban hardware seketika"
    echo ""
    echo "Env opsional: HYPERHEAL_URL, HYPERHEAL_API_KEY, SIM_VMID, SIM_VM_NAME"
    echo "=========================================================="
    ;;
esac
