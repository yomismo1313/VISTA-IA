#!/usr/bin/env bash
set -u

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OLD="/home/yomismo13/Escritorio/v1/VISTA-IA"
NEW="$BASE"

echo "🔧 Reparando venvs:"
echo "   ruta vieja: $OLD"
echo "   ruta nueva: $NEW"
echo

for svc in musica vision voz stt gateway cerebro; do
    dir="$NEW/$svc"
    venv="$dir/.venv"
    [ -d "$venv" ] || continue

    echo "── $svc ──"

    # Detectar si tiene la ruta vieja
    if grep -q "$OLD" "$venv/bin/activate" 2>/dev/null; then
        echo "   parcheando $venv/bin/activate"
        sed -i "s|$OLD|$NEW|g" "$venv/bin/activate"
    fi

    if grep -q "$OLD" "$venv/pyvenv.cfg" 2>/dev/null; then
        echo "   parcheando $venv/pyvenv.cfg"
        sed -i "s|$OLD|$NEW|g" "$venv/pyvenv.cfg"
    fi

    # Shebangs de scripts
    if grep -rl "$OLD" "$venv/bin/" 2>/dev/null | grep -q .; then
        echo "   parcheando shebangs en $venv/bin/"
        grep -rl "$OLD" "$venv/bin/" 2>/dev/null | while read -r f; do
            [ -f "$f" ] && sed -i "s|$OLD|$NEW|g" "$f"
        done
    fi

    # Verificar
    if [ -x "$venv/bin/python" ]; then
        ruta=$(grep -m1 "VIRTUAL_ENV=" "$venv/bin/activate" | grep -oP '(?<=VIRTUAL_ENV=).*' | tr -d "'\"")
        echo "   ✅ VIRTUAL_ENV = $ruta"
    fi
    echo
done

echo "✅ Todos los venvs reparados."
echo "Prueba con: source <svc>/.venv/bin/activate && which python"
