#!/usr/bin/env bash
# Arranca todos los microservicios, cada uno con su propio venv,
# y guarda su salida en logs/<servicio>.log
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$ROOT/logs"

start_service () {
    local name="$1"
    local script="$2"
    local dir="$ROOT/$name"
    if [ ! -f "$dir/.venv/bin/activate" ]; then
        echo "⏭️  $name no tiene venv instalado todavía (./scripts/install_all.sh $name) — saltado"
        return
    fi
    echo "🚀 Arrancando $name..."
    (
        cd "$dir"
        source .venv/bin/activate
        nohup python3 "$script" > "$ROOT/logs/$name.log" 2>&1 &
        echo $! > "$ROOT/logs/$name.pid"
    )
    sleep 1
}

# Orden importa poco (cada uno espera al otro por HTTP cuando lo necesita),
# pero arrancamos primero los que no dependen de nadie.
start_service "musica" "service.py"
start_service "vision" "service.py"
start_service "voz"    "service.py"
start_service "gateway" "app.py"

echo ""
echo "✅ Servicios lanzados (revisa arriba si alguno se saltó por falta de venv)."
echo "   Logs en: $ROOT/logs/*.log"
echo "   Web:      http://$(hostname -I | awk '{print $1}'):8080"
echo ""
echo "Para arrancar también el cerebro (bucle de escucha por wake word):"
echo "   cd cerebro && source .venv/bin/activate && python3 orchestrator.py"
echo ""
echo "Para parar todo: ./scripts/stop_all.sh"
