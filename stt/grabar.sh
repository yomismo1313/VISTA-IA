#!/bin/bash
# Graba desde el micro y transcribe con faster-whisper
# Uso: ./grabar.sh [segundos]

cd "$(dirname "$0")"
source .venv/bin/activate

DURACION=${1:-8}
ARCHIVO="/tmp/grabacion_$(date +%s).wav"

echo "🎙️  Grabando ${DURACION}s... Habla ahora"
arecord -D plughw:1,0 -f S16_LE -r 16000 -c 1 -d "$DURACION" "$ARCHIVO"

echo "📝 Transcribiendo..."
python3 - <<EOF
from faster_whisper import WhisperModel
model = WhisperModel("small", device="cuda", compute_type="float16")
segments, info = model.transcribe("$ARCHIVO", language="es", beam_size=5, vad_filter=False)
for seg in segments:
    print(f"[{seg.start:.2f}s → {seg.end:.2f}s] {seg.text.strip()}")
EOF

echo
echo "💾 Audio: $ARCHIVO"
