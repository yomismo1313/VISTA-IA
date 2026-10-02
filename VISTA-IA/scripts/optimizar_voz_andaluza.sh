#!/usr/bin/env bash
set -e

BASE="/home/yomismo13/Escritorio/VISTA-IA/VISTA-IA"
cd "$BASE"

echo "════════════════════════════════════════════════════════"
echo "🔧 OPTIMIZAR VOZ ANDALUZA + OLLAMA"
echo "════════════════════════════════════════════════════════"

# ═══ 1. OPTIMIZAR AUDIO DE REFERENCIA ═══
echo ""
echo "🎤 Optimizando audio de referencia..."

cd "$BASE/voz/referencia"

# Verificar archivo original
if [ ! -f "mi_voz.wav" ]; then
    echo "❌ No se encontró mi_voz.wav"
    exit 1
fi

echo "Formato original:"
file mi_voz.wav

# Convertir a formato óptimo para XTTS
# - 22050 Hz (frecuencia que XTTS espera)
# - Mono (1 canal)
# - 16-bit PCM
# - Normalizar volumen
# - Eliminar silencios al inicio y final
ffmpeg -y -i mi_voz.wav \
    -ar 22050 \
    -ac 1 \
    -sample_fmt s16 \
    -af "silenceremove=start_periods=1:start_threshold=-50dB,areverse,silenceremove=start_periods=1:start_threshold=-50dB,areverse,loudnorm=I=-16:TP=-1.5:LRA=11" \
    mi_voz_optimizada.wav

mv mi_voz_optimizada.wav mi_voz.wav

echo "✅ Audio optimizado:"
file mi_voz.wav
echo "Duración:"
ffprobe -v quiet -show_entries format=duration -of csv=p=0 mi_voz.wav

cd "$BASE"

# ═══ 2. CORREGIR VOZ_REFERENCIA EN services.env ═══
echo ""
echo "🔧 Configurando VOZ_REFERENCIA..."

# Eliminar líneas duplicadas
sed -i '/VOZ_REFERENCIA/d' services.env

# Añadir ruta correcta
echo "VOZ_REFERENCIA=$BASE/voz/referencia/mi_voz.wav" >> services.env

echo "✅ VOZ_REFERENCIA configurada"

# ═══ 3. CORREGIR EL SERVICIO VOZ ═══
echo ""
echo "🔧 Optimizando servicio voz..."

python3 <<'EOF'
from pathlib import Path
p = Path("voz/service.py")
code = p.read_text()

# Añadir configuración de velocidad y tono para XTTS
old_tts = 'audio = tts_engine.tts(text=texto, speaker_wav=VOZ_REFERENCIA, language="es")'
new_tts = '''audio = tts_engine.tts(
                text=texto,
                speaker_wav=VOZ_REFERENCIA,
                language="es",
                speed=1.1,          # Ligeramente más rápido para sonar natural
                temperature=0.6,    # Menos variabilidad = más consistente con la voz de referencia
                length_penalty=1.0,
                repetition_penalty=5.0
            )'''

if old_tts in code:
    code = code.replace(old_tts, new_tts, 1)
    print("✅ Parámetros XTTS optimizados")

p.write_text(code)
print("✅ voz/service.py actualizado")
EOF

# ═══ 4. CORREGIR EL TTS PARA USAR GPU ═══
echo ""
echo "🔧 Configurando GPU para XTTS..."

cd "$BASE/voz"
source .venv/bin/activate

# Verificar GPU
if nvidia-smi &>/dev/null; then
    echo "✅ GPU detectada"
    pip install --quiet torch torchaudio --index-url https://download.pytorch.org/whl/cu121
    python3 -c "import torch; print('CUDA:', torch.cuda.is_available())"
else
    echo "⚠️ No hay GPU, usando CPU"
fi

deactivate

# ═══ 5. INTEGRAR OLLAMA ═══
echo ""
echo "🔧 Integrando Ollama..."

# Verificar si Ollama está instalado
if command -v ollama &>/dev/null; then
    echo "✅ Ollama detectado"
    ollama list 2>/dev/null | head -10
else
    echo "⚠️ Ollama no instalado"
    echo "   Instala con: curl -fsSL https://ollama.com/install.sh | sh"
fi

deactivate 2>/dev/null || true

# ═══ 6. CREAR SCRIPT DE PREGUNTAS ═══
cat > "$BASE/scripts/preguntar.py" <<'PYEOF'
#!/usr/bin/env python3
"""Pregunta a Ollama y responde con voz clonada."""
import asyncio
import aiohttp
import json
import sys

async def preguntar_y_responder(texto):
    """Envía pregunta a Ollama y reproduce la respuesta con TTS clonado."""
    
    # 1. Preguntar a Ollama
    print(f"🧠 Preguntando a Ollama: {texto}")
    
    async with aiohttp.ClientSession() as session:
        # Ollama API
        async with session.post(
            "http://localhost:11434/api/generate",
            json={"model": "llama3", "prompt": texto, "stream": False}
        ) as resp:
            if resp.status != 200:
                print(f"❌ Ollama error: {resp.status}")
                return
            data = await resp.json()
            respuesta = data.get("response", "")
            print(f"🤖 Respuesta: {respuesta[:100]}...")
    
    # 2. Enviar al gateway para TTS
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "http://localhost:8080/cmd",
            json={"cmd": "say", "text": respuesta}
        ) as resp:
            if resp.status == 200:
                print("✅ Respuesta enviada al TTS")
            else:
                print(f"❌ TTS error: {resp.status}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python3 preguntar.py 'tu pregunta'")
        sys.exit(1)
    
    pregunta = " ".join(sys.argv[1:])
    asyncio.run(preguntar_y_responder(pregunta))
PYEOF

chmod +x "$BASE/scripts/preguntar.py"
echo "✅ Script preguntar.py creado"

# ═══ 7. REINICIAR VOZ ═══
echo ""
echo "🛑 Reiniciando voz..."
pkill -9 -f "voz/service.py" 2>/dev/null || true
sleep 3
lsof -ti :8082 | xargs kill -9 2>/dev/null || true
sleep 2

echo "🚀 Iniciando voz..."
cd "$BASE/voz"
source .venv/bin/activate
nohup python3 service.py > ../logs/voz.log 2>&1 &
deactivate
cd "$BASE"

echo "⏳ Esperando 15 segundos..."
sleep 15

# ═══ 8. VERIFICAR ═══
echo ""
echo "📋 Log de voz:"
tail -10 logs/voz.log

echo ""
echo "════════════════════════════════════════════════════════"
echo "✅ OPTIMIZACIÓN COMPLETADA"
echo "════════════════════════════════════════════════════════"
echo ""
echo "🔊 Probar voz clonada:"
echo "   curl -X POST http://localhost:8080/cmd -H 'Content-Type: application/json' -d '{\"cmd\":\"say\",\"text\":\"hola guapa, como estas\"}'"
echo ""
echo "🧠 Preguntar a Ollama con voz clonada:"
echo "   python3 scripts/preguntar.py 'que tiempo hace en sevilla'"
echo ""
echo "📋 Si Ollama no está instalado:"
echo "   curl -fsSL https://ollama.com/install.sh | sh"
echo "   ollama pull llama3"
echo "════════════════════════════════════════════════════════"
