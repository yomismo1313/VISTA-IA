#!/usr/bin/env python3
"""
VISTA · wake word + comandos por voz (versión local, sin ESP32).
Escucha continuamente, detecta 'VISTA' y ejecuta la acción correspondiente.
"""
import os
import re
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

# ── Añade las .so de CUDA al path antes de importar faster-whisper ──
import site, glob
for sp in site.getsitepackages():
    for so in glob.glob(os.path.join(sp, "nvidia", "*", "lib")):
        os.environ["LD_LIBRARY_PATH"] = so + ":" + os.environ.get("LD_LIBRARY_PATH", "")

from faster_whisper import WhisperModel

# ── CONFIG ──
SAMPLE_RATE   = 16000
MIC_DEVICE    = None            # None = default; o "plughw:1,0"
MODEL_SIZE    = "small"
DEVICE        = "cpu"          # "cpu" si no tienes CUDA lista
COMPUTE_TYPE  = "int8" if DEVICE == "cuda" else "int8"
LANGUAGE      = "es"
WAKE_WORDS    = ("vista", "bista", "vista,")   # variantes que Whisper suele devolver
CHUNK_SECONDS = 3               # ventana de escucha continua
GATEWAY_SPEAK = "http://127.0.0.1:8080/speak"

print(f"🔄 Cargando faster-whisper '{MODEL_SIZE}' en {DEVICE} ({COMPUTE_TYPE})...", flush=True)
model = WhisperModel(MODEL_SIZE, device=DEVICE, compute_type=COMPUTE_TYPE)
print("✅ Whisper listo", flush=True)


# ═══════════════════════════════════════════════════════════════
#  GRABACIÓN
# ═══════════════════════════════════════════════════════════════
def grabar(segundos: float) -> np.ndarray:
    """Graba del micro y devuelve float32 mono a 16 kHz."""
    frames = int(segundos * SAMPLE_RATE)
    print(f"🎤 Grabando {segundos:.1f}s...", flush=True)
    audio = sd.rec(frames, samplerate=SAMPLE_RATE, channels=1,
                   dtype="float32", device=MIC_DEVICE)
    sd.wait()
    return audio.flatten()


def transcribir(audio: np.ndarray) -> str:
    segs, _ = model.transcribe(
        audio, language=LANGUAGE, beam_size=5,
        vad_filter=True, vad_parameters=dict(min_silence_duration_ms=400),
    )
    return " ".join(s.text.strip() for s in segs).strip()


# ═══════════════════════════════════════════════════════════════
#  COMANDOS
# ═══════════════════════════════════════════════════════════════
def hablar(texto: str):
    """Envía texto al gateway para que lo diga por voz clonada."""
    try:
        import requests
        requests.post(GATEWAY_SPEAK, json={"text": texto}, timeout=10)
    except Exception as e:
        print(f"⚠️ No se pudo hablar: {e}")


def cmd_que_ves():
    """VISTA que ves / VISTA describe lo que ves → captura cámara + descripción."""
    print("📷 Capturando imagen...", flush=True)
    img = "/tmp/vista_que_ves.jpg"
    subprocess.run(["ffmpeg", "-y", "-f", "v4l2", "-i", "/dev/video0",
                    "-frames:v", "1", img], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not Path(img).exists():
        hablar("No he podido acceder a la cámara.")
        return
    # Aquí conectarás tu módulo de visión (llava, moondream, etc.)
    # Por ahora, un placeholder:
    hablar("Ahora mismo no tengo el módulo de visión cargado, pero la imagen está capturada.")


def cmd_dime_algo():
    """VISTA dime ... → modo conversación con Ollama."""
    print("💬 Modo conversación (habla, di 'cállate' para salir)", flush=True)
    while True:
        audio = grabar(5.0)
        texto = transcribir(audio)
        if not texto:
            continue
        print(f"👤 {texto}", flush=True)
        if "cállate" in texto.lower() or "callate" in texto.lower():
            hablar("Vale, hasta luego.")
            break
        # Enviar a Ollama (ajusta al endpoint de tu gateway)
        try:
            import requests
            r = requests.post("http://127.0.0.1:11434/api/generate",
                              json={"model": "llama3.2", "prompt": texto, "stream": False},
                              timeout=120)
            respuesta = r.json().get("response", "").strip()
            print(f"🤖 {respuesta}", flush=True)
            hablar(respuesta)
        except Exception as e:
            print(f"⚠️ Ollama falló: {e}")


def cmd_graba_video(minutos: float = 2.0):
    """VISTA graba → graba X minutos de vídeo."""
    out = Path.home() / "Escritorio" / f"vista_grabacion_{int(time.time())}.mp4"
    print(f"🎥 Grabando {minutos} min → {out}", flush=True)
    hablar(f"Grabando {minutos} minutos de vídeo.")
    subprocess.run([
        "ffmpeg", "-y", "-f", "v4l2", "-i", "/dev/video0",
        "-f", "alsa", "-i", "default",
        "-t", str(int(minutos * 60)),
        "-c:v", "libx264", "-preset", "ultrafast",
        "-c:a", "aac", str(out)
    ], check=False)
    hablar("Grabación terminada.")


def cmd_pon_musica():
    """VISTA pon música → radio por streaming."""
    url = "http://stream.radioparadise.com/mp3-128"   # ← cambia por tu emisora
    print(f"🎵 Reproduciendo {url}", flush=True)
    hablar("Poniendo la radio.")
    subprocess.Popen(["mpv", "--no-video", url])


# ═══════════════════════════════════════════════════════════════
#  ROUTER
# ═══════════════════════════════════════════════════════════════
def contiene_wake(texto: str) -> bool:
    t = texto.lower()
    return any(w in t for w in WAKE_WORDS)


def enrutar(texto: str):
    """Dada una frase que empieza por 'VISTA ...', decide qué hacer."""
    t = texto.lower().strip()
    print(f"🗣️  Comando detectado: {t!r}", flush=True)

    # VISTA que ves / VISTA qué ves / VISTA describe
    if re.search(r"vista[,\s]+(que|qué)\s+ves", t) or "vista describe" in t:
        cmd_que_ves()
        return

    # VISTA dime ...
    if re.search(r"vista[,\s]+dime", t):
        cmd_dime_algo()
        return

    # VISTA graba (vídeo)
    if re.search(r"vista[,\s]+graba", t):
        cmd_graba_video(2.0)
        return

    # VISTA pon música / VISTA pon la radio
    if re.search(r"vista[,\s]+pon\s+(m[uú]sica|la\s+radio)", t):
        cmd_pon_musica()
        return

    # Por defecto: conversación corta
    hablar("No he entendido el comando.")


# ═══════════════════════════════════════════════════════════════
#  BUCLE PRINCIPAL
# ═══════════════════════════════════════════════════════════════
def main():
    print("=" * 60)
    print("👂 Escuchando wake word 'VISTA'... (Ctrl+C para salir)")
    print("=" * 60)
    try:
        while True:
            audio = grabar(CHUNK_SECONDS)
            texto = transcribir(audio)
            if not texto:
                continue
            print(f"… {texto}", flush=True)
            if contiene_wake(texto):
                enrutar(texto)
    except KeyboardInterrupt:
        print("\n👋 Cerrando...")


if __name__ == "__main__":
    main()
