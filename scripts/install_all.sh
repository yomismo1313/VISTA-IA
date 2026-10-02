#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════
#  VISTA-IA · Instalador de entornos separados
#  Crea un venv independiente dentro de cada carpeta de servicio
#  e instala solo lo que ese servicio necesita.
#
#  Uso:
#    ./scripts/install_all.sh              # instala los 5 servicios
#    ./scripts/install_all.sh vision voz   # instala solo esos
#
#  Si un paquete concreto falla, se avisa y se sigue con el resto
#  (un servicio no bloquea a los demás).
# ══════════════════════════════════════════════════════════
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICES=("gateway" "vision" "voz" "musica" "cerebro")

if [ "$#" -gt 0 ]; then
    SERVICES=("$@")
fi

mkdir -p "$ROOT/logs"
rm -f "$ROOT/logs/.install_fallos"
FALLOS=()

echo "📦 Instalando dependencias del sistema (requiere sudo)..."
sudo apt-get update -y
sudo apt-get install -y python3-venv python3-pip ffmpeg espeak-ng alsa-utils portaudio19-dev

for svc in "${SERVICES[@]}"; do
    DIR="$ROOT/$svc"
    if [ ! -d "$DIR" ]; then
        echo "⚠️  Servicio desconocido: $svc (saltado)"
        continue
    fi
    echo ""
    echo "══════════════════════════════════════════"
    echo "🔧 Instalando entorno de: $svc"
    echo "══════════════════════════════════════════"
    (
        cd "$DIR"
        python3 -m venv .venv
        source .venv/bin/activate
        pip install --upgrade pip -q

        if [ ! -f "requirements.txt" ]; then
            deactivate
            exit 0
        fi

        # Primer intento: todo junto (rápido si no hay problemas)
        if pip install -r requirements.txt; then
            deactivate
            exit 0
        fi

        # Si falla el conjunto, instalamos paquete a paquete para que
        # uno malo no impida instalar los demás.
        echo "⚠️  Fallo instalando requirements.txt de golpe, reintentando paquete a paquete..."
        FAILED_PKGS=""
        while IFS= read -r pkg; do
            [ -z "$pkg" ] && continue
            [[ "$pkg" == \#* ]] && continue
            if ! pip install "$pkg"; then
                echo "❌ No se pudo instalar: $pkg"
                FAILED_PKGS="$FAILED_PKGS $pkg"
            fi
        done < requirements.txt

        deactivate
        if [ -n "$FAILED_PKGS" ]; then
            echo "$svc:$FAILED_PKGS" >> "$ROOT/logs/.install_fallos"
            exit 1
        fi
        exit 0
    )
    if [ $? -ne 0 ]; then
        FALLOS+=("$svc")
        echo "⚠️  $svc quedó con algún paquete sin instalar (revisa el detalle arriba)."
    else
        echo "✅ $svc listo (venv en $svc/.venv)"
    fi
done

echo ""
if [ ${#FALLOS[@]} -eq 0 ]; then
    echo "🎉 Instalación completa, sin fallos."
else
    echo "⚠️  Terminado con avisos en: ${FALLOS[*]}"
    echo "   El resto de servicios SÍ se instalaron bien y puedes arrancarlos."
    echo "   Detalle de paquetes fallidos en: logs/.install_fallos"
fi
echo "Para arrancar todo: ./scripts/start_all.sh"
