#!/usr/bin/env python3
"""
VISTA · CHAT POR VOZ (local)
Graba del micro → STT (faster-whisper) → Ollama → TTS (vía gateway /speak)

Requisitos:
  - gateway/app.py corriendo en :8080
  - voz/app.py corriendo en :8082 (con XTTS o espeak)
  - Ollama corriendo en :11434
  - faster-whisper instalado en este venv
"""
import asyncio
import subprocess
import tempfile
import os
import sys
from pathlib import Path

import requests
from faster_whisper import WhisperModel

# ═══════════════════════════════════════════════════
#  CONFIG — AJUSTA AQUÍ
# ═══════════════════════════════════════════════════
MIC_DEVICE    = "plughw:1,0"          # tu micro webcam
SAMPLE_RATE   = 16000
DURACION      = 6                     # segundos por turno

WHISPER_MODEL = "small"
WHISPER_DEVICE= "cuda"
WHISPER_CTYPE = "float16"

OLLAMA_URL    = "http://localhost:11434"
OLLAMA_MODEL  = "llama3.2"            # ajusta si usas otro

GATEWAY_URL   = "http://127.0.0.1:8080"

SYSTEM_PROMPT = (
    "Eres VISTA, un asistente de voz cercano y útil. "
    "Responde SIEMPRE en español, de forma natural y conversacional. "
    "Sé breve: 1-3 frases como máximo, porque tu respuesta se leerá en voz alta."
)
# ═══════════════════════════════════════════════════

# ── Cargar Whisper ──
print(f"🔄 Cargando faster-whisper '{WHISPER_MODEL}'...")
whisper = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE, compute_type=WHISPER_CTYPE)
print("✅ Whisper listo\n")

# ── Historial de conversación ──
historial = [{"role": "system", "content": SYSTEM_PROMPT}]


# ═══════════════════════════════════════════════════
#  GRABAR
# ═══════════════════════════════════════════════════
def grabar(segundos=DURACION) -> str:
    """Graba del micro y devuelve la ruta del WAV."""
    wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
    subprocess.run(
        ["arecord", "-D", MIC_DEVICE, "-f", "S16_LE",
         "-r", str(SAMPLE_RATE), "-c", "1", "-d", str(segundos), wav],
        check=True, capture_output=True,
    )
    return wav


# ═══════════════════════════════════════════════════
#  TRANSCRIBIR
# ═══════════════════════════════════════════════════
def transcribir(wav_path: str) -> str:
    segments, _ = whisper.transcribe(
        wav_path,
        language="es",
        beam_size=5,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
    )
    return " ".join(seg.text.strip() for seg in segments).strip()


# ═══════════════════════════════════════════════════
#  OLLAMA
# ═══════════════════════════════════════════════════
def preguntar_ollama(texto: str) -> str:
    historial.append({"role": "user", "content": texto})
    try:
        r = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": historial,
                "stream": False,
                "options": {"temperature": 0.7, "num_predict": 200},
            },
            timeout=120,
        )
        r.raise_for_status()
        respuesta = r.json()["message"]["content"].strip()
        historial.append({"role": "assistant", "content": respuesta})
        return respuesta
    except requests.exceptions.Timeout:
        return "(Ollama tardó demasiado)"
    except Exception as e:
        return f"(Error Ollama: {e})"


# ═══════════════════════════════════════════════════
#  HABLAR — reutiliza el /speak del gateway
# ═══════════════════════════════════════════════════
def hablar(texto: str) -> bool:
    """
    Envía el texto a /speak del gateway.
    El gateway lo mandará a voz/ que hará XTTS y reproducirá
    en la ESP32 (si está conectada) o en el altavoz local.
    """
    try:
        r = requests.post(
            f"{GATEWAY_URL}/speak",
            json={"text": texto},
            timeout=300,   # XTTS puede tardar
        )
        if r.ok:
            data = r.json()
            motor = data.get("motor", "?")
            n = data.get("n_frases", "?")
            print(f"   → TTS ({motor}, {n} frases) enviado al gateway")
            return True
        else:
            print(f"   ⚠️  /speak respondió {r.status_code}: {r.text[:200]}")
            return False
    except requests.exceptions.Timeout:
        print("   ⚠️  Timeout esperando al gateway /speak")
        return False
    except Exception as e:
        print(f"   ⚠️  Error hablando: {e}")
        return False


# ═══════════════════════════════════════════════════
#  BUCLE PRINCIPAL
# ═══════════════════════════════════════════════════
def main():
    print("=" * 60)
    print("🎙️  VISTA · chat por voz")
    print(f"   Micro:     {MIC_DEVICE}")
    print(f"   Whisper:   {WHISPER_MODEL} ({WHISPER_DEVICE})")
    print(f"   Ollama:    {OLLAMA_MODEL}")
    print(f"   Gateway:   {GATEWAY_URL}/speak")
    print(f"   Turno:     {DURACION}s de grabación")
    print("=" * 60)
    print("Habla cuando diga 'GRABANDO'. Ctrl+C para salir.\n")

    try:
        while True:
            input("⏎ Pulsa ENTER para hablar...")

            print("🎤 GRABANDO... habla ahora")
            wav = grabar(DURACION)

            print("📝 Transcribiendo...")
            texto = transcribir(wav)
            os.unlink(wav)

            if not texto or len(texto) < 2:
                print("   (silencio o ininteligible, vuelvo a escuchar)\n")
                continue

            print(f"🗣️  Tú: {texto}")

            print("🧠 Pensando con Ollama...")
            respuesta = preguntar_ollama(texto)
            print(f"🤖 VISTA: {respuesta}")

            print("🔊 Generando voz...")
            hablar(respuesta)
            print()

    except KeyboardInterrupt:
        print("\n👋 Adiós.")


if __name__ == "__main__":
    main()
