#!/bin/bash
# Diagnóstico del micrófono
echo "════ DISPOSITIVOS DE CAPTURA ════"
arecord -l

echo
echo "════ NIVEL DEL MIC POR DEFECTO ════"
echo "Habla durante 3 segundos..."
arecord -f S16_LE -r 16000 -c 1 -d 3 /tmp/check.wav
ffmpeg -i /tmp/check.wav -af "volumedetect" -f null /dev/null 2>&1 | grep -E "mean_volume|max_volume"

echo
echo "════ CONFIG ACTUAL ════"
cat /etc/asound.conf 2>/dev/null || echo "(no hay /etc/asound.conf)"

echo
echo "════ INTERPRETACIÓN ════"
echo "  > -20 dB  → excelente"
echo "  -30 a -20 → bueno"
echo "  -40 a -30 → bajo, sube en alsamixer (F4)"
echo "  < -50 dB  → micrófono equivocado o silenciado"
