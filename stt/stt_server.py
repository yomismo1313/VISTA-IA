#!/usr/bin/env python3
"""Servidor STT para VISTA-IA. TCP :5010 → faster-whisper."""
import socket, threading, time
import numpy as np
from faster_whisper import WhisperModel

HOST = "0.0.0.0"
PORT = 5010
SAMPLE_RATE = 16000
CHUNK_SECONDS = 5
CHUNK_BYTES = SAMPLE_RATE * 2 * CHUNK_SECONDS

MODEL_SIZE  = "small"
DEVICE      = "cuda"
COMPUTE_TYPE= "float16"
LANGUAGE    = "es"

print(f"🔄 Cargando faster-whisper '{MODEL_SIZE}' en {DEVICE} ({COMPUTE_TYPE})...", flush=True)
t0 = time.time()
model = WhisperModel(MODEL_SIZE, device=DEVICE, compute_type=COMPUTE_TYPE)
print(f"✅ Modelo cargado en {time.time()-t0:.2f}s\n", flush=True)

def pcm_to_f32(b): 
    return np.frombuffer(b, dtype=np.int16).astype(np.float32) / 32768.0

def transcribir(a):
    segs, _ = model.transcribe(a, language=LANGUAGE, beam_size=5, vad_filter=True,
                                vad_parameters=dict(min_silence_duration_ms=500))
    return " ".join(s.text.strip() for s in segs).strip()

def handle(conn, addr):
    print(f"🔌 Cliente: {addr}", flush=True)
    buf = b""
    try:
        while True:
            data = conn.recv(4096)
            if not data:
                print(f"🔌 Desconectado: {addr}", flush=True)
                break
            buf += data
            while len(buf) >= CHUNK_BYTES:
                chunk, buf = buf[:CHUNK_BYTES], buf[CHUNK_BYTES:]
                t0 = time.time()
                txt = transcribir(pcm_to_f32(chunk))
                dt = time.time() - t0
                if txt:
                    print(f"📝 [{addr[0]}] ({dt:.2f}s) {txt}", flush=True)
    except ConnectionResetError:
        print(f"🔌 Reseteado: {addr}", flush=True)
    finally:
        conn.close()

def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(5)
    print(f"🎙️  STT server en {HOST}:{PORT}", flush=True)
    print(f"   {SAMPLE_RATE} Hz | mono | 16-bit | chunks de {CHUNK_SECONDS}s\n", flush=True)
    try:
        while True:
            conn, addr = srv.accept()
            threading.Thread(target=handle, args=(conn, addr), daemon=True).start()
    except KeyboardInterrupt:
        print("\n👋 Cerrando...", flush=True)
    finally:
        srv.close()

if __name__ == "__main__":
    main()
