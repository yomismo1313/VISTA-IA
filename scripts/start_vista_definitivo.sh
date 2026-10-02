#!/usr/bin/env bash
set -u

# BASE = un nivel arriba de este script (VISTA-IA/)
BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOGS="$BASE/logs"
PIDFILE="$BASE/logs/vista.pids"

echo "📂 BASE = $BASE"

[ -d "$BASE" ] || { echo "❌ No existe BASE=$BASE"; exit 1; }
mkdir -p "$LOGS"

# ═══════════════════════════════════════════════════════════════
#  PARADA SEGURA
#  - Solo mata PIDs que registramos nosotros mismos (PIDFILE)
#  - Si no hay PIDFILE, mata solo procesos cuyo CWD esté en BASE
#  - NUNCA mata por puerto a ciegas (eso mata la red)
# ═══════════════════════════════════════════════════════════════
echo "🛑 Parando instancias previas (solo las nuestras)..."

stop_pids_from_file () {
    [ -f "$PIDFILE" ] || return 0
    while read -r pid; do
        [ -z "$pid" ] && continue
        if kill -0 "$pid" 2>/dev/null; then
            echo "   · matando PID $pid"
            kill -TERM "$pid" 2>/dev/null
        fi
    done < "$PIDFILE"
    sleep 2
    # Los que aún vivan → SIGKILL
    while read -r pid; do
        [ -z "$pid" ] && continue
        if kill -0 "$pid" 2>/dev/null; then
            kill -9 "$pid" 2>/dev/null
        fi
    done < "$PIDFILE"
    rm -f "$PIDFILE"
}

# Matar por CWD (defensivo, por si no había PIDFILE)
stop_by_cwd () {
    local pattern="$1"
    # Buscamos procesos cuyo cwd esté dentro de $BASE y que coincidan con el patrón
    for pid in $(pgrep -f "$pattern" 2>/dev/null); do
        local cwd
        cwd=$(readlink "/proc/$pid/cwd" 2>/dev/null) || continue
        case "$cwd" in
            "$BASE"/*)
                echo "   · (por cwd) matando PID $pid  ($cwd)"
                kill -TERM "$pid" 2>/dev/null
                ;;
        esac
    done
}

stop_pids_from_file
stop_by_cwd "gateway/app.py"
stop_by_cwd "vision/app.py"
stop_by_cwd "voz/app.py"
stop_by_cwd "musica/service.py"
stop_by_cwd "stt/stt_server.py"
sleep 1

: > "$PIDFILE"

# ═══════════════════════════════════════════════════════════════
#  ARRANQUE
# ═══════════════════════════════════════════════════════════════
start_svc () {
    local name="$1" dir="$2" script="$3" log="$4"
    [ -d "$dir" ]                    || { echo "⚠️  $name: no existe $dir"; return; }
    [ -f "$dir/$script" ]            || { echo "⚠️  $name: no existe $dir/$script"; return; }
    [ -f "$dir/.venv/bin/activate" ] || { echo "⚠️  $name: falta $dir/.venv"; return; }
    echo "🚀 Iniciando $name ($script)..."

    (
        cd "$dir" || exit 1
        # shellcheck disable=SC1091
        source .venv/bin/activate
        # Guardamos el PID del proceso python real
        exec nohup python3 "$script" > "$log" 2>&1 &
        echo $! >> "$PIDFILE"
    ) &
    disown
}

wait_port () {
    local port="$1" name="$2" timeout="${3:-90}"
    local t=0
    while [ $t -lt $timeout ]; do
        if ss -tln 2>/dev/null | grep -q ":$port "; then
            echo "   ✅ $name  → :$port"
            return 0
        fi
        sleep 1
        t=$((t+1))
    done
    echo "   ❌ $name  → :$port  (timeout ${timeout}s)"
    return 1
}

# ── Arrancar en paralelo ──
start_svc "musica"  "$BASE/musica"  "service.py"    "$LOGS/musica.log"
start_svc "vision"  "$BASE/vision"  "app.py"        "$LOGS/vision.log"
start_svc "voz"     "$BASE/voz"     "app.py"        "$LOGS/voz.log"
start_svc "stt"     "$BASE/stt"     "stt_server.py" "$LOGS/stt.log"
start_svc "gateway" "$BASE/gateway" "app.py"        "$LOGS/gateway.log"

echo ""
echo "⏳ Esperando a que los servicios escuchen..."
wait_port 8083 "musica"  30
wait_port 8081 "vision"  120
wait_port 8082 "voz"     180
wait_port 5010 "stt"     180
wait_port 8080 "gateway" 30

echo ""
echo "📡 Puertos en escucha (solo los nuestros):"
ss -tlnp 2>/dev/null | grep -E ':(5000|5002|5010|8080|8081|8082|8083)' || echo "   (ninguno)"

echo ""
echo "📜 Últimas líneas de cada log:"
for f in musica vision voz stt gateway; do
    echo "── $f ──"
    tail -n 6 "$LOGS/$f.log" 2>/dev/null || echo "   (sin log)"
done

echo ""
echo "✅ Arranque terminado"
IP=$(hostname -I | awk '{print $1}')
echo "🌐 Web:       http://$IP:8080"
echo "🎙️  STT:       $IP:5010  (para ESP32 y chat_local)"
echo ""
echo "🛑 Para parar:  ./stop_vista.sh   (o borra $PIDFILE y mata a mano)"
