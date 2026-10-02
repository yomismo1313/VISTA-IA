#!/usr/bin/env python3
"""
Servidor STT para VISTA-IA.
Escucha TCP en :5010 y recibe audio PCM 16-bit mono 16kHz.
"""
import socket
import threading
import time
import numpy as np
from faster_whisper import WhisperModel

HOST = "0.0.0.0"
PORT = 5010
SAMPLE_RATE = 16000
CHUNK_SECONDS = 5
CHUNK_BYTES = SAMPLE_RATE * 2 * CHUNK_SECONDS

MODEL_SIZE = "small"
DEVICE = "cuda"
COMPUTE_TYPE = "float16"
LANGUAGE = "es"

print(f"🔄 Cargando faster-whisper '{MODEL_SIZE}' en {DEVICE} ({COMPUTE_TYPE})...")
t0 = time.time()
model = WhisperModel(MODEL_SIZE, device=DEVICE, compute_type=COMPUTE_TYPE)
print(f"✅ Modelo cargado en {time.time()-t0:.2f}s\n")


def pcm_to_float32(pcm_bytes: bytes) -> np.ndarray:
    audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
    return audio / 32768.0


def transcribir(audio_f32: np.ndarray) -> str:
    segments, _ = model.transcribe(
        audio_f32,
        language=LANGUAGE,
        beam_size=5,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
    )
    return " ".join(seg.text.strip() for seg in segments).strip()


def handle_client(conn, addr):
    print(f"🔌 Cliente conectado: {addr}")
    buffer = b""
    try:
        while True:
            data = conn.recv(4096)
            if not data:
                print(f"🔌 Cliente desconectado: {addr}")
                break
            buffer += data
            while len(buffer) >= CHUNK_BYTES:
                chunk = buffer[:CHUNK_BYTES]
                buffer = buffer[CHUNK_BYTES:]
                audio = pcm_to_float32(chunk)
                t0 = time.time()
                texto = transcribir(audio)
                dt = time.time() - t0
                if texto:
                    print(f"📝 [{addr[0]}] ({dt:.2f}s) {texto}")
    except ConnectionResetError:
        print(f"🔌 Cliente reseteado: {addr}")
    finally:
        conn.close()


def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(5)
    print(f"🎙️  STT server en {HOST}:{PORT}")
    print(f"   {SAMPLE_RATE} Hz | mono | 16-bit | chunks de {CHUNK_SECONDS}s\n")
    try:
        while True:
            conn, addr = srv.accept()
            threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()
    except KeyboardInterrupt:
        print("\n👋 Cerrando...")
    finally:
        srv.close()


if __name__ == "__main__":
    main()
