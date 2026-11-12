#!/bin/bash
# ==========================================================
# HyperHeal - Disaster Simulation Script (Bahan Uji Coba/Demo)
# ==========================================================

trigger_alert() {
  local alert_type="$1"
  local cpu="$2"
  local ram="$3"
  local top_proc="$4"
  local log_msg="$5"

  echo "📡 [AIOps Auto-Detect] Mengirim alert ${alert_type} ke HyperHeal..."
  curl -s -X POST http://127.0.0.1:8000/webhook/alert \
    -H "Content-Type: application/json" \
    -d "{
      \"vmid\": \"104\",
      \"vm_name\": \"cbt-server-ujian\",
      \"alert_name\": \"${alert_type}\",
      \"metrics\": {
        \"cpu_usage_pct\": ${cpu},
        \"ram_usage_pct\": ${ram},
        \"swap_usage_pct\": 45.0
      },
      \"top_processes\": [
        \"${top_proc}\",
        \"mysql     1190  0.4 35.0 /usr/sbin/mysqld\"
      ],
      \"recent_logs\": [
        \"${log_msg}\"
      ]
    }" > /dev/null 2>&1
  echo "✅ Alert otomatis terkirim ke Telegram!"
}

case "$1" in
  cpu)
    echo "🔥 [SIMULASI] Memulai lonjakan CPU 100% (Durasi: 120 detik)..."
    echo "Grafik di Grafana sedang melonjak merah..."
    # Otomatis tembak webhook alert saat beban dimulai agar Telegram langsung berbunyi
    trigger_alert "HighCPU_Overload" "99.8" "42.0" "stress-ng  9812 99.2  1.2 stress-ng-cpu [runaway worker]" "kernel: [warning] CPU core 0-3 throttled due to excessive runaway thread pid 9812" &
    stress-ng --cpu 4 --cpu-load 100 --timeout 120s --metrics-brief
    ;;

  ram)
    echo "🔥 [SIMULASI] Memulai lonjakan konsumsi RAM 90% (Durasi: 120 detik)..."
    echo "Grafik di Grafana sedang melonjak merah..."
    trigger_alert "HighRAM_MemoryLeak" "35.0" "92.4" "stress-ng  9844 12.0 89.5 stress-ng-vm [malloc leak]" "kernel: [alert] Out of memory: Kill process 9844 (stress-ng-vm) score 895 or sacrifice child" &
    stress-ng --vm 2 --vm-bytes 85% --timeout 120s --metrics-brief
    ;;

  cbt)
    echo "🔥 [SIMULASI] Simulasi Ujian CBT Overload (CPU 100% + RAM 85% + Swap)..."
    echo "Grafik di Grafana sedang melonjak merah..."
    trigger_alert "CBT_SystemCrash_Overload" "99.4" "95.8" "www-data  5012 49.8 14.2 php-fpm: pool cbt_exam" "php-fpm[5012]: [WARNING] execution duration exceeded 60s in /var/www/cbt/ujian_submit.php" &
    stress-ng --cpu 2 --vm 2 --vm-bytes 80% --timeout 120s --metrics-brief
    ;;

  trigger)
    echo "📡 [SIMULASI] Menembak Webhook Alert HyperHeal secara instan ke Telegram..."
    trigger_alert "HighCPU_Overload" "99.4" "95.8" "www-data  5012 49.8 14.2 php-fpm: pool cbt_exam" "php-fpm[5012]: [WARNING] execution duration exceeded 60s in /var/www/cbt/ujian_submit.php"
    ;;

  stop)
    echo "🛑 Menghentikan seluruh proses simulasi bencana..."
    pkill -9 stress-ng 2>/dev/null
    echo "✅ Semua proses simulasi berhasil dihentikan."
    ;;

  *)
    echo "=========================================================="
    echo "   HYPERHEAL - SKRIP SIMULASI BENCANA SERVER (DEMO)       "
    echo "=========================================================="
    echo "Penggunaan: ./simulate_disaster.sh [perintah]"
    echo ""
    echo "Pilihan perintah:"
    echo "  cpu     : CPU spike 100% + Auto Alert Telegram (2 menit)"
    echo "  ram     : RAM spike 90% + Auto Alert Telegram (2 menit)"
    echo "  cbt     : CPU + RAM Spike (Server CBT Macet) + Auto Alert"
    echo "  trigger : Tes Webhook Alert instan tanpa beban hardware"
    echo "  stop    : Hentikan paksa beban hardware seketika"
    echo "=========================================================="
    ;;
esac
