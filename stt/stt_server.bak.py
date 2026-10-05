#!/usr/bin/env python3
"""
VISTA · STT SERVER · v4 (2026-10)

Servidor TCP :5010 → faster-whisper → router de comandos.

Comandos soportados:
  VISTA que ves            → captura del ESP32 + descripción (moondream)
  VISTA dime <pregunta>    → conversación con memoria (llama3.2)
  VISTA cállate            → apaga conversación y limpia historial
  VISTA graba              → graba 2 min de vídeo del ESP32/PC
  VISTA pon la radio       → RADIOLE_ASO_OSUNA con mpv
  VISTA pon música         → canción aleatoria de musica.db con mpv
  VISTA sube el volumen    → nivel 1 → 2 → 3
  VISTA baja el volumen    → nivel 3 → 2 → 1

Variables de entorno (todas opcionales):
  STT_PORT, STT_MODEL, STT_USE_GPU, STT_GAIN_DB, STT_SILENCE_RMS,
  STT_CHUNK_SECONDS, GATEWAY_SPEAK, OLLAMA_URL, OLLAMA_MODEL,
  LLAVA_MODEL, ESP32_IP, RADIO_URL, MUSICA_DB, VOLUME_BACKEND
"""
# ═══════════════════════════════════════════════════════════════
#  PRE-CARGA DE CUDA ANTES DE IMPORTAR ctranslate2
# ═══════════════════════════════════════════════════════════════
import os, sys, site, glob, ctypes

def _preload_all_cuda_libs():
    lib_dirs = []
    search_roots = list(site.getsitepackages())
    if hasattr(site, "getusersitepackages"):
        try: search_roots.append(site.getusersitepackages())
        except Exception: pass
    venv_site = os.path.join(sys.prefix, "lib",
        f"python{sys.version_info.major}.{sys.version_info.minor}",
        "site-packages")
    search_roots.append(venv_site)

    seen = set()
    for root in search_roots:
        for d in glob.glob(os.path.join(root, "nvidia", "*", "lib")):
            if d not in seen:
                seen.add(d); lib_dirs.append(d)

    if not lib_dirs:
        print("⚠️  No hay librerías nvidia/*/lib — ¿pip install nvidia-* hecho?",
              file=sys.stderr); return

    current = os.environ.get("LD_LIBRARY_PATH", "")
    os.environ["LD_LIBRARY_PATH"] = ":".join(lib_dirs) + (":" + current if current else "")

    priority = ["libcudart", "libnvrtc", "libnvJitLink",
                "libcublasLt", "libcublas", "libcufft", "libcurand",
                "libcusolver", "libcusparse", "libcusparselt", "libcudnn"]

    loaded = []
    def _load(p):
        try: ctypes.CDLL(p, mode=ctypes.RTLD_GLOBAL); loaded.append(p)
        except OSError: pass

    for name in priority:
        for d in lib_dirs:
            for so in glob.glob(os.path.join(d, name + "*.so*")):
                _load(so)
    for d in lib_dirs:
        for so in glob.glob(os.path.join(d, "*.so*")):
            _load(so)

    print(f"🔧 CUDA pre-cargada: {len(loaded)} librerías desde {len(lib_dirs)} dirs",
          file=sys.stderr)

_preload_all_cuda_libs()

# ═══════════════════════════════════════════════════════════════
#  IMPORTS
# ═══════════════════════════════════════════════════════════════
import base64, re, socket, sqlite3, subprocess, threading, time
from pathlib import Path
from typing import Optional

import numpy as np
from faster_whisper import WhisperModel
import requests


# ═══════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════
HOST            = "0.0.0.0"
PORT            = int(os.environ.get("STT_PORT", 5010))
SAMPLE_RATE     = 16000
CHUNK_SECONDS   = float(os.environ.get("STT_CHUNK_SECONDS", 4))
CHUNK_BYTES     = int(SAMPLE_RATE * 2 * CHUNK_SECONDS)
MIN_BYTES       = int(SAMPLE_RATE * 2 * 1.0)

MODEL_SIZE      = os.environ.get("STT_MODEL", "small")
USE_GPU         = os.environ.get("STT_USE_GPU", "1") == "1"
DEVICE          = "cuda" if USE_GPU else "cpu"
COMPUTE_TYPE    = "float16" if USE_GPU else "int8"
LANGUAGE        = "es"

GAIN_DB         = float(os.environ.get("STT_GAIN_DB", "20"))
SILENCE_RMS     = float(os.environ.get("STT_SILENCE_RMS", "0.0005"))

GATEWAY_SPEAK   = os.environ.get("GATEWAY_SPEAK", "http://127.0.0.1:8080/speak")
OLLAMA_URL      = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
OLLAMA_MODEL    = os.environ.get("OLLAMA_MODEL", "llama3.2")
LLAVA_MODEL     = os.environ.get("LLAVA_MODEL", "moondream")   # moondream describe mejor

ESP32_IP        = os.environ.get("ESP32_IP", "192.168.1.10")
RADIO_URL       = os.environ.get(
    "RADIO_URL",
    "https://playerservices.streamtheworld.com/api/livestream-redirect/RADIOLE_ASO_OSUNA.mp3",
)
MUSICA_DB       = os.environ.get(
    "MUSICA_DB",
    os.path.expanduser("~/Escritorio/VISTA-IA/data/db/musica.db"),
)
VIDEO_DEVICE_ESP = f"http://{ESP32_IP}/capture"

# Volumen: 'pipewire' (wpctl), 'alsa' (amixer), 'esp32' (envía al ESP32), 'none'
VOLUME_BACKEND  = os.environ.get("VOLUME_BACKEND", "pipewire").lower()
VOLUME_SINK     = os.environ.get("VOLUME_SINK", "@DEFAULT_AUDIO_SINK@")
VOLUME_ALSA_CTRL= os.environ.get("VOLUME_ALSA_CTRL", "Master")

# 3 niveles de volumen (en %)
VOL_LEVELS      = [30, 60, 100]      # bajo, medio, alto
_vol_idx_global = {"nivel": 1}        # 1 = medio por defecto

# Wake words y sinónimos
WAKE_WORDS = ("vista", "bista", "pista", "vista,", "bista,",
              "vis ta", "bis ta", "jarvis", "jervis", "hey jarvis")

RX_QUE_VES   = re.compile(
    r"\b(que|qué|q)\s+(ves|veo|hay|pasa|miras|estas\s+viendo|estás\s+viendo)"
    r"|describe|descripcion|descripción|mira\s+(esto|eso|aqu[ií])"
    r"|dime\s+(que|qué)\s+ves|dime\s+que\s+se\s+ve|qué\s+se\s+ve"
)
RX_DIME      = re.compile(r"\b(dime|cu[eé]ntame|expl[ií]came|responde)\b")
RX_GRABA     = re.compile(r"\b(graba|gr[aá]bame|grabar|haz\s+una\s+grabaci[oó]n|empieza\s+a\s+grabar)\b")
RX_RADIO     = re.compile(r"\b(pon|reproduce|toca|play)\b.*\b(radio|emisora)\b")
RX_MUSICA    = re.compile(r"\b(pon|reproduce|toca|play)\b.*\b(m[uú]sica|canci[oó]n|cancion)\b")
RX_SUBE_VOL  = re.compile(r"\b(sube|sube\s+el|aumenta|subir)\b.*\b(volumen|audio)\b")
RX_BAJA_VOL  = re.compile(r"\b(baja|baja\s+el|bajar|reduce|bajo)\b.*\b(volumen|audio)\b")
RX_CALLATE   = re.compile(r"\b(c[aá]llate|callate|silencio|para|stop|basta|adi[oó]s|adios)\b")


# ═══════════════════════════════════════════════════════════════
#  CARGA DEL MODELO
# ═══════════════════════════════════════════════════════════════
print(f"🔄 Cargando faster-whisper '{MODEL_SIZE}' en {DEVICE} ({COMPUTE_TYPE})...",
      flush=True)
t0 = time.time()
try:
    model = WhisperModel(MODEL_SIZE, device=DEVICE, compute_type=COMPUTE_TYPE)
    print(f"✅ Modelo cargado en {time.time() - t0:.2f}s", flush=True)
except Exception as e:
    print(f"❌ Falló en {DEVICE} ({type(e).__name__}: {e}); reintento en CPU",
          flush=True)
    DEVICE, COMPUTE_TYPE = "cpu", "int8"
    model = WhisperModel(MODEL_SIZE, device=DEVICE, compute_type=COMPUTE_TYPE)
    print(f"✅ Modelo cargado en CPU en {time.time() - t0:.2f}s", flush=True)


# ═══════════════════════════════════════════════════════════════
#  AUDIO
# ═══════════════════════════════════════════════════════════════
def pcm_to_f32(b: bytes) -> np.ndarray:
    if len(b) < 2:
        return np.zeros(0, dtype=np.float32)
    if len(b) & 1:
        b = b[:-1]
    return np.frombuffer(b, dtype=np.int16).astype(np.float32) / 32768.0

def aplicar_ganancia(audio: np.ndarray, db: float = GAIN_DB) -> np.ndarray:
    if db == 0 or audio.size == 0:
        return audio
    return np.clip(audio * (10.0 ** (db / 20.0)), -1.0, 1.0)

def rms(audio: np.ndarray) -> float:
    return float(np.sqrt(np.mean(audio ** 2))) if audio.size else 0.0

def es_silencio(audio: np.ndarray, umbral: float = SILENCE_RMS) -> bool:
    return audio.size == 0 or rms(audio) < umbral

def transcribir(audio: np.ndarray) -> str:
    if audio.size == 0:
        return ""
    segs, _ = model.transcribe(
        audio, language=LANGUAGE, beam_size=5,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=400),
    )
    return " ".join(s.text.strip() for s in segs).strip()


# ═══════════════════════════════════════════════════════════════
#  HABLAR (gateway XTTS)
# ═══════════════════════════════════════════════════════════════
def hablar(texto: str):
    if not texto:
        return
    try:
        requests.post(GATEWAY_SPEAK, json={"text": texto}, timeout=10)
    except Exception as e:
        print(f"⚠️ hablar(): {type(e).__name__}: {e}", flush=True)


# ═══════════════════════════════════════════════════════════════
#  VOLUMEN · 3 NIVELES
# ═══════════════════════════════════════════════════════════════
def _aplicar_volumen(porcentaje: int):
    """Aplica el nivel actual según VOLUME_BACKEND."""
    global _vol_idx_global
    if VOLUME_BACKEND == "pipewire":
        try:
            subprocess.run(
                ["wpctl", "set-volume", VOLUME_SINK, f"{porcentaje}%"],
                check=False, timeout=3,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            print(f"🔊 Volumen (pipewire {VOLUME_SINK}) → {porcentaje}%", flush=True)
        except Exception as e:
            print(f"⚠️ wpctl: {e}", flush=True)
    elif VOLUME_BACKEND == "alsa":
        try:
            subprocess.run(
                ["amixer", "-D", "pulse", "sset", VOLUME_ALSA_CTRL, f"{porcentaje}%"],
                check=False, timeout=3,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            print(f"🔊 Volumen (alsa {VOLUME_ALSA_CTRL}) → {porcentaje}%", flush=True)
        except Exception as e:
            print(f"⚠️ amixer: {e}", flush=True)
    elif VOLUME_BACKEND == "esp32":
        try:
            # Envía un POST al ESP32 con el nivel (necesita endpoint /volume en el .ino)
            r = requests.post(
                f"http://{ESP32_IP}/volume",
                json={"percent": porcentaje},
                timeout=3,
            )
            print(f"🔊 Volumen (ESP32) → {porcentaje}% (HTTP {r.status_code})", flush=True)
        except Exception as e:
            print(f"⚠️ ESP32 volumen: {e}", flush=True)
    else:
        print(f"🔇 VOLUME_BACKEND={VOLUME_BACKEND} → no se hace nada "
              f"(nivel pedido: {porcentaje}%)", flush=True)


def cmd_sube_volumen():
    global _vol_idx_global
    _vol_idx_global["nivel"] = min(3, _vol_idx_global["nivel"] + 1)
    pct = VOL_LEVELS[_vol_idx_global["nivel"] - 1]
    hablar(f"Subo el volumen al nivel {_vol_idx_global['nivel']}.")
    _aplicar_volumen(pct)


def cmd_baja_volumen():
    global _vol_idx_global
    _vol_idx_global["nivel"] = max(1, _vol_idx_global["nivel"] - 1)
    pct = VOL_LEVELS[_vol_idx_global["nivel"] - 1]
    hablar(f"Bajo el volumen al nivel {_vol_idx_global['nivel']}.")
    _aplicar_volumen(pct)


# ═══════════════════════════════════════════════════════════════
#  COMANDOS
# ═══════════════════════════════════════════════════════════════
def cmd_que_ves():
    """Solo ESP32. Prompt tipo 'como si el usuario fuera ciego'."""
    print(f"📷 Capturando del ESP32 ({ESP32_IP})...", flush=True)
    img = "/tmp/vista_que_ves.jpg"

    capturado = False
    for intento in (1, 2, 3):
        try:
            r = requests.get(VIDEO_DEVICE_ESP, timeout=15)
            print(f"   · intento {intento}: HTTP {r.status_code}, "
                  f"{len(r.content)} bytes", flush=True)
            if r.ok and len(r.content) > 2000:
                Path(img).write_bytes(r.content)
                capturado = True
                break
        except requests.exceptions.Timeout:
            print(f"   · intento {intento}: timeout", flush=True)
        except Exception as e:
            print(f"   · intento {intento}: {type(e).__name__}: {e}", flush=True)
        time.sleep(1)

    if not capturado:
        hablar("No he podido acceder a la cámara del ESP32.")
        return

    try:
        b64 = base64.b64encode(Path(img).read_bytes()).decode()
        r = requests.post(
            OLLAMA_URL,
            json={
                "model": LLAVA_MODEL,
                "messages": [{
                    "role": "user",
                    "content": (
                        "Eres los ojos de una persona ciega. Describe la imagen "
                        "con detalle y de forma útil: qué hay, dónde está cada "
                        "cosa (izquierda, derecha, delante, fondo), colores, "
                        "personas, objetos, texto visible y distancias "
                        "aproximadas. Empieza por lo más importante. "
                        "Sé concreto. No inventes lo que no ves."
                    ),
                    "images": [b64],
                }],
                "stream": False,
                "options": {"temperature": 0.2, "top_p": 0.9},
            },
            timeout=180,
        )
        desc = r.json().get("message", {}).get("content", "").strip()
        print(f"👁️  {desc}", flush=True)
        hablar(desc or "No he podido describir la imagen.")
    except Exception as e:
        print(f"⚠️ visión: {type(e).__name__}: {e}", flush=True)
        hablar("No puedo analizar la imagen ahora mismo.")


def cmd_graba_video(minutos: float = 2.0):
    """Graba del ESP32 (MJPEG) + audio por micrófono del PC."""
    out = Path.home() / "Escritorio" / f"vista_grabacion_{int(time.time())}.mp4"
    print(f"🎥 Grabando {minutos} min → {out}", flush=True)
    hablar(f"Grabando {minutos} minutos de vídeo.")
    try:
        # Intenta primero la cámara del ESP32
        cmd = [
            "ffmpeg", "-y",
            "-f", "mjpeg", "-i", f"http://{ESP32_IP}:81/stream",
            "-f", "alsa", "-i", "default",
            "-t", str(int(minutos * 60)),
            "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac", str(out),
        ]
        rc = subprocess.run(cmd, check=False,
                            timeout=int(minutos * 60) + 20).returncode
        if rc != 0 or not out.exists():
            print("   ⚠️ Fallo grabando del ESP32, uso /dev/video0", flush=True)
            cmd = [
                "ffmpeg", "-y", "-f", "v4l2", "-i", "/dev/video0",
                "-f", "alsa", "-i", "default",
                "-t", str(int(minutos * 60)),
                "-c:v", "libx264", "-preset", "ultrafast",
                "-c:a", "aac", str(out),
            ]
            subprocess.run(cmd, check=False, timeout=int(minutos * 60) + 20)
        hablar("Grabación terminada.")
    except Exception as e:
        print(f"⚠️ grabar vídeo: {e}", flush=True)
        hablar("No he podido grabar el vídeo.")


def _reproducir(url: str):
    """Lanza mpv contra una URL o archivo."""
    try:
        subprocess.Popen(
            ["mpv", "--no-video", "--really-quiet", url],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return True
    except FileNotFoundError:
        hablar("No tengo instalado el reproductor de música.")
        return False
    except Exception as e:
        print(f"⚠️ mpv: {e}", flush=True)
        return False


def cmd_pon_radio():
    print(f"📻 Reproduciendo radio → {RADIO_URL}", flush=True)
    hablar("Poniendo la radio.")
    _reproducir(RADIO_URL)


def cmd_pon_musica():
    print(f"🎵 Buscando música en {MUSICA_DB}", flush=True)
    if not Path(MUSICA_DB).exists():
        print(f"   ⚠️ No existe {MUSICA_DB}", flush=True)
        hablar("No encuentro la biblioteca de música todavía.")
        return
    try:
        con = sqlite3.connect(MUSICA_DB)
        cur = con.cursor()
        cur.execute("SELECT ruta, titulo, artista FROM canciones "
                    "ORDER BY RANDOM() LIMIT 1")
        row = cur.fetchone()
        con.close()
    except Exception as e:
        print(f"   ⚠️ Error leyendo DB: {e}", flush=True)
        hablar("No puedo leer la biblioteca de música.")
        return

    if not row:
        hablar("La biblioteca de música está vacía.")
        return

    ruta, titulo, artista = row
    if not Path(ruta).exists():
        print(f"   ⚠️ Archivo no existe: {ruta}", flush=True)
        hablar("No encuentro ese archivo de música.")
        return

    print(f"🎵 Reproduciendo: {artista or '?'} – {titulo or '?'}", flush=True)
    hablar(f"Poniendo {titulo or 'una canción'} de {artista or 'tu biblioteca'}.")
    _reproducir(ruta)


# ═══════════════════════════════════════════════════════════════
#  CONVERSACIÓN CON MEMORIA
# ═══════════════════════════════════════════════════════════════
MAX_HISTORIAL = 20   # mensajes (user+assistant) por cliente

def _responder_ollama(texto: str, addr=None):
    """Manda texto a Ollama con el historial del cliente (memoria)."""
    with estado_lock:
        hist = estado_cliente.get(addr, {}).get("messages", []) \
               if addr is not None else []

    mensajes = hist + [{"role": "user", "content": texto}]

    try:
        r = requests.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "messages": mensajes,
                "stream": False,
                "options": {"temperature": 0.7},
            },
            timeout=180,
        )
        resp = r.json().get("message", {}).get("content", "").strip()
        print(f"🤖 {resp[:200]}{'…' if len(resp) > 200 else ''}", flush=True)
        hablar(resp or "No he sabido qué responder.")

        # Guarda el historial (recortado)
        if addr is not None:
            with estado_lock:
                entry = estado_cliente.setdefault(addr, {"modo": "idle", "messages": []})
                entry["messages"].append({"role": "user", "content": texto})
                entry["messages"].append({"role": "assistant", "content": resp})
                entry["messages"] = entry["messages"][-MAX_HISTORIAL:]
    except Exception as e:
        print(f"⚠️ Ollama: {type(e).__name__}: {e}", flush=True)
        hablar("No puedo conectar con el modelo de lenguaje.")


# ═══════════════════════════════════════════════════════════════
#  ROUTER
# ═══════════════════════════════════════════════════════════════
def contiene_wake(texto: str) -> bool:
    return any(w in texto.lower() for w in WAKE_WORDS)


def quitar_wake(texto: str) -> str:
    t = texto
    for _ in range(2):
        t2 = re.sub(r"^\s*(vista|bista|pista|vis\s*ta|bis\s*ta)[,\s]*",
                    "", t, flags=re.IGNORECASE)
        if t2 == t:
            break
        t = t2
    return t.strip()


def enrutar(texto: str, addr=None):
    t = quitar_wake(texto.lower())
    print(f"🗣️  Comando detectado: {texto!r}  →  {t!r}", flush=True)

    if RX_QUE_VES.search(t) or RX_QUE_VES.search(texto.lower()):
        cmd_que_ves(); return

    if RX_GRABA.search(t):
        cmd_graba_video(2.0); return

    if RX_RADIO.search(t):
        cmd_pon_radio(); return

    if RX_MUSICA.search(t):
        cmd_pon_musica(); return

    if RX_SUBE_VOL.search(t):
        cmd_sube_volumen(); return

    if RX_BAJA_VOL.search(t):
        cmd_baja_volumen(); return

    if RX_CALLATE.search(t):
        with estado_lock:
            if addr is not None and addr in estado_cliente:
                estado_cliente[addr] = {"modo": "idle", "messages": []}
        hablar("Vale, hasta luego.")
        return

    if RX_DIME.search(t) or RX_DIME.search(texto.lower()):
        # Si venía de wake word "VISTA dime …", entra en modo conversación
        if addr is not None:
            with estado_lock:
                entry = estado_cliente.setdefault(addr, {"modo": "idle", "messages": []})
                entry["modo"] = "conversacion"
        _responder_ollama(t or "Hola", addr)
        return

    hablar("No he entendido el comando.")


# ═══════════════════════════════════════════════════════════════
#  ESTADO POR CLIENTE
# ═══════════════════════════════════════════════════════════════
estado_cliente = {}     # addr -> {"modo": "idle"|"conversacion", "messages": [...]}
estado_lock = threading.Lock()


# ═══════════════════════════════════════════════════════════════
#  PROCESADO DE BUFFER
# ═══════════════════════════════════════════════════════════════
def _procesar_buffer(audio: np.ndarray, addr, tag: str):
    audio = aplicar_ganancia(audio)
    if es_silencio(audio):
        print(f"🔇 [{addr[0]}] {tag}: silencio (rms={rms(audio):.5f})", flush=True)
        return False

    t0 = time.time()
    texto = transcribir(audio)
    dt = time.time() - t0
    if not texto:
        return False

    print(f"📝 [{addr[0]}] {tag} ({dt:.2f}s, rms={rms(audio):.4f}) {texto}",
          flush=True)

    with estado_lock:
        modo = estado_cliente.get(addr, {}).get("modo", "idle")

    low = texto.lower()

    # Modo conversación: todo va a Ollama, salvo "cállate"
    if modo == "conversacion":
        if RX_CALLATE.search(low):
            with estado_lock:
                estado_cliente[addr] = {"modo": "idle", "messages": []}
            hablar("Vale, hasta luego.")
            return True
        # ¿Ha dicho otro comando "VISTA ..." dentro de la conversación?
        if contiene_wake(texto):
            threading.Thread(target=enrutar, args=(texto, addr), daemon=True).start()
            return True
        threading.Thread(target=_responder_ollama, args=(texto, addr),
                         daemon=True).start()
        return True

    # Modo normal
    if contiene_wake(texto):
        threading.Thread(target=enrutar, args=(texto, addr), daemon=True).start()
        return True

    return False


# ═══════════════════════════════════════════════════════════════
#  SERVIDOR TCP
# ═══════════════════════════════════════════════════════════════
def handle(conn: socket.socket, addr):
    print(f"🔌 Cliente: {addr}", flush=True)
    with estado_lock:
        estado_cliente[addr] = {"modo": "idle", "messages": []}

    conn.settimeout(30.0)
    buf = bytearray()

    try:
        while True:
            try:
                data = conn.recv(4096)
            except socket.timeout:
                if len(buf) >= MIN_BYTES:
                    _procesar_buffer(pcm_to_f32(bytes(buf)), addr, "timeout")
                break

            if not data:
                break

            buf.extend(data)
            while len(buf) >= CHUNK_BYTES:
                chunk = bytes(buf[:CHUNK_BYTES]); del buf[:CHUNK_BYTES]
                _procesar_buffer(pcm_to_f32(chunk), addr, "chunk")

        if len(buf) >= MIN_BYTES:
            _procesar_buffer(pcm_to_f32(bytes(buf)), addr, "final")

    except ConnectionResetError:
        print(f"🔌 Reseteado: {addr}", flush=True)
        if len(buf) >= MIN_BYTES:
            try: _procesar_buffer(pcm_to_f32(bytes(buf)), addr, "reset")
            except Exception: pass
    except Exception as e:
        print(f"⚠️ Error con {addr}: {type(e).__name__}: {e}", flush=True)
    finally:
        conn.close()
        with estado_lock:
            estado_cliente.pop(addr, None)
        print(f"🔌 Desconectado: {addr} ({len(buf)} bytes sin procesar)", flush=True)


# ═══════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════
def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(5)

    print(f"🎙️  STT server en {HOST}:{PORT}", flush=True)
    print(f"   {SAMPLE_RATE} Hz | mono | 16-bit | chunk={CHUNK_SECONDS}s", flush=True)
    print(f"   Modelo: {MODEL_SIZE} | device={DEVICE} | compute={COMPUTE_TYPE}",
          flush=True)
    print(f"   Ganancia: +{GAIN_DB:.1f} dB | silencio_rms={SILENCE_RMS}", flush=True)
    print(f"   Wake words: {WAKE_WORDS}", flush=True)
    print(f"   Gateway:    {GATEWAY_SPEAK}", flush=True)
    print(f"   Ollama:     {OLLAMA_URL} ({OLLAMA_MODEL}, {LLAVA_MODEL})", flush=True)
    print(f"   ESP32 cám:  {VIDEO_DEVICE_ESP}", flush=True)
    print(f"   Radio:      {RADIO_URL}", flush=True)
    print(f"   Música DB:  {MUSICA_DB} "
          f"({'OK' if Path(MUSICA_DB).exists() else 'no existe'})", flush=True)
    print(f"   Volumen:    backend={VOLUME_BACKEND}, niveles={VOL_LEVELS}%, "
          f"actual={_vol_idx_global['nivel']}", flush=True)
    print(flush=True)

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
