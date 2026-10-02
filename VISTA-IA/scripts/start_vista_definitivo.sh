#!/usr/bin/env bash
set -u

# BASE = un nivel arriba de este script (VISTA-IA/)
BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOGS="$BASE/logs"

echo "📂 BASE = $BASE"

[ -d "$BASE" ] || { echo "❌ No existe BASE=$BASE"; exit 1; }
mkdir -p "$LOGS"

echo "🛑 Matando procesos previos..."
pkill -9 -f "gateway/app.py"    2>/dev/null
pkill -9 -f "vision/app.py"     2>/dev/null
pkill -9 -f "vision/service.py" 2>/dev/null
pkill -9 -f "voz/app.py"        2>/dev/null
pkill -9 -f "voz/service.py"    2>/dev/null
pkill -9 -f "musica/service.py" 2>/dev/null
sleep 2

for port in 5000 5002 8080 8081 8082 8083; do
    pid=$(lsof -ti :$port 2>/dev/null)
    [ -n "$pid" ] && kill -9 $pid 2>/dev/null
done
sleep 2

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
        exec nohup python3 "$script" > "$log" 2>&1
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
start_svc "musica"  "$BASE/musica"  "service.py" "$LOGS/musica.log"
start_svc "vision"  "$BASE/vision"  "app.py"     "$LOGS/vision.log"
start_svc "voz"     "$BASE/voz"     "app.py"     "$LOGS/voz.log"
start_svc "gateway" "$BASE/gateway" "app.py"     "$LOGS/gateway.log"

echo ""
echo "⏳ Esperando a que los servicios escuchen..."
wait_port 8083 "musica"  30
wait_port 8081 "vision"  120
wait_port 8082 "voz"     180
wait_port 8080 "gateway" 30

echo ""
echo "📡 Puertos en escucha:"
ss -tlnp 2>/dev/null | grep -E ':(5000|5002|8080|8081|8082|8083)' || echo "   (ninguno)"

echo ""
echo "📜 Últimas líneas de cada log:"
for f in musica vision voz gateway; do
    echo "── $f ──"
    tail -n 6 "$LOGS/$f.log" 2>/dev/null || echo "   (sin log)"
done

echo ""
echo "✅ Arranque terminado"
IP=$(hostname -I | awk '{print $1}')
echo "🌐 Web: http://$IP:8080"
