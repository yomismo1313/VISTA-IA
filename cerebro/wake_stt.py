#!/usr/bin/env python3
"""
VISTA · Wake word → STT (TCP) → comandos.

Escucha con openWakeWord, captura el turno con Silero VAD,
y envía el PCM crudo a stt:5010 (stt_server.py) que ya tiene
el router de comandos + faster-whisper + respuesta por voz.
"""
from __future__ import annotations

import socket
import sys
import time

import numpy as np

from wake import WakeWordListener, WakeEvent

# ── Config ──
STT_HOST = "127.0.0.1"
STT_PORT = 5010
STT_CONNECT_TIMEOUT = 5.0
SAMPLE_RATE = 16000


def send_to_stt(audio: np.ndarray) -> bool:
    """Envía audio float32 mono a stt:5010 como PCM int16 LE."""
    pcm16 = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    payload = pcm16.tobytes()

    try:
        with socket.create_connection((STT_HOST, STT_PORT),
                                      timeout=STT_CONNECT_TIMEOUT) as s:
            s.sendall(payload)
            s.shutdown(socket.SHUT_WR)  # cierra escritura → stt procesa el buffer
            print(f"📤 Enviado a stt:{STT_PORT} ({len(payload)} bytes, "
                  f"{len(audio)/SAMPLE_RATE:.2f}s)", flush=True)
            # Leer respuesta (si la hay). stt_server no siempre responde,
            # pero leemos por si acaso, con timeout corto.
            s.settimeout(0.5)
            try:
                resp = s.recv(4096)
                if resp:
                    print(f"📥 respuesta: {resp[:200]!r}", flush=True)
            except socket.timeout:
                pass
        return True
    except ConnectionRefusedError:
        print(f"❌ stt:{STT_PORT} rechazó la conexión. ¿Está arrancado?",
              file=sys.stderr, flush=True)
        return False
    except Exception as e:
        print(f"❌ Error enviando a stt: {type(e).__name__}: {e}",
              file=sys.stderr, flush=True)
        return False


def on_wake(evt: WakeEvent):
    t0 = time.time()
    ok = send_to_stt(evt.audio)
    dt = time.time() - t0
    if ok:
        print(f"✅ Turno procesado en {dt:.2f}s\n", flush=True)
    else:
        print("⚠️  Fallo al enviar turno\n", flush=True)


def main():
    print("=" * 60)
    print("🎙️  VISTA · wake word → STT")
    print(f"    Wake: hey_jarvis | STT: {STT_HOST}:{STT_PORT}")
    print("=" * 60)

    listener = WakeWordListener(
        on_wake=on_wake,
        mic_device=7,       # default = DGM20
        threshold=0.55,
        debug=False,           # en producción, sin puntuaciones
    )
    try:
        listener.run()
    except KeyboardInterrupt:
        print("\n👋 Cerrando...")


if __name__ == "__main__":
    main()
