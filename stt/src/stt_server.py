"""
Servidor STT para VISTA-IA.
Recibe audio PCM 16kHz mono por TCP y devuelve texto.
Usa faster-whisper (más rápido que openai-whisper).
"""
import socket
import numpy as np
from faster_whisper import WhisperModel

HOST = "0.0.0.0"
PORT = 5010
SAMPLE_RATE = 16000
CHUNK_SECONDS = 5
CHUNK_BYTES = SAMPLE_RATE * 2 * CHUNK_SECONDS  # 16-bit mono

print("Cargando modelo Whisper...")
model = WhisperModel("small", device="cuda", compute_type="float16")
# Si no tienes GPU: device="cpu", compute_type="int8"

print(f"Escuchando en {HOST}:{PORT}")
srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind((HOST, PORT))
srv.listen(1)

while True:
    conn, addr = srv.accept()
    print(f"Cliente: {addr}")
    buffer = b""
    while True:
        data = conn.recv(4096)
        if not data:
            break
        buffer += data
        if len(buffer) >= CHUNK_BYTES:
            audio = np.frombuffer(buffer[:CHUNK_BYTES], dtype=np.int16).astype(np.float32) / 32768.0
            buffer = buffer[CHUNK_BYTES:]

            segments, info = model.transcribe(audio, language="es", beam_size=1)
            texto = " ".join(s.text for s in segments).strip()
            if texto:
                print(f"📝 {texto}")
                # Aquí mandarías el texto al gateway/cerebro
    conn.close()
