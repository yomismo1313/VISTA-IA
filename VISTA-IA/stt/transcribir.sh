#!/bin/bash
# Transcribe un WAV existente
# Uso: ./transcribir.sh archivo.wav

cd "$(dirname "$0")"
source .venv/bin/activate

if [ -z "$1" ]; then
  echo "❌ Uso: $0 archivo.wav"
  exit 1
fi

python test_stt.py "$1"
