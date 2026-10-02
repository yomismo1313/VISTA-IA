#!/usr/bin/env python3
"""
VISTA · VOZ SERVICE
  GET  /health
  POST /speak     -> {text: "..."} → genera audio (XTTS si está, espeak si no)
                     y lo envía al gateway para que lo reproduzca.
  POST /tts_test  -> {text: "..."} → devuelve info del motor usado y bytes

Config (services.env):
  VOZ_PORT           puerto HTTP (default 8082)
  SAMPLE_RATE        Hz (default 16000)
  VOZ_REFERENCIA     ruta al wav de referencia para clonar voz (opcional)
  GATEWAY_URL        URL del gateway para enviar PCM (default http://127.0.0.1:8080)
  XTTS_MODEL         modelo XTTS (default tts_models/multilingual/multi-dataset/xtts_v2)
"""
import asyncio
import logging
import sys
import tempfile
from pathlib import Path

import numpy as np
import requests
from aiohttp import web
import aiohttp_cors

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from _vista_config import cfg

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-7s  [voz] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger("voz")

# ── CONFIG ──
PORT             = int(cfg.get("VOZ_PORT", 8082))
SAMPLE_RATE      = int(cfg.get("SAMPLE_RATE", 16000))
VOZ_REFERENCIA   = cfg.get("VOZ_REFERENCIA", "")
GATEWAY_URL      = cfg.get("GATEWAY_URL", "http://127.0.0.1:8080")
XTTS_MODEL_NAME  = cfg.get("XTTS_MODEL", "tts_models/multilingual/multi-dataset/xtts_v2")

log.info(f"Sample rate: {SAMPLE_RATE} Hz")
log.info(f"Gateway: {GATEWAY_URL}")
log.info(f"Referencia: {VOZ_REFERENCIA or '(no configurada)'}")

# ── CARGA DE MODELOS ──
whisper_model = None
try:
    log.info("🎤 Cargando Whisper base...")
    import whisper
    whisper_model = whisper.load_model("base")
    log.info("✅ Whisper listo")
except Exception as e:
    log.warning(f"⚠️ Whisper no disponible: {e}")

xtts_engine = None
xtts_ok = False            # se pondrá a False si falla al generar
xtts_last_error = None

try:
    log.info(f"🎙️  Cargando XTTS: {XTTS_MODEL_NAME}...")
    import torch
    # importamos torchaudio aquí para que falle temprano si no está
    import torchaudio  # noqa: F401
    from TTS.api import TTS
    xtts_engine = TTS(XTTS_MODEL_NAME)
    xtts_ok = True
    log.info("✅ XTTS listo (voz clonada)")
except Exception as e:
    log.warning(f"❌ XTTS no se pudo cargar ({e}), se usará espeak")
    xtts_engine = None
    xtts_ok = False
    xtts_last_error = str(e)

if xtts_ok:
    if VOZ_REFERENCIA and Path(VOZ_REFERENCIA).exists():
        log.info(f"✅ Voz de referencia: {VOZ_REFERENCIA}")
    else:
        log.warning(f"⚠️ VOZ_REFERENCIA no encontrada ({VOZ_REFERENCIA}), XTTS usará voz por defecto")


# ── UTILS ──
def _float_to_pcm16_mono(samples: np.ndarray, sr: int) -> bytes:
    """Float32 [-1,1] -> PCM16 mono al SAMPLE_RATE de config."""
    import scipy.signal as ss
    from math import gcd

    if samples.ndim > 1:
        samples = samples.mean(axis=1)
    samples = samples.astype(np.float32)

    if sr != SAMPLE_RATE:
        g = gcd(SAMPLE_RATE, sr)
        samples = ss.resample_poly(samples, SAMPLE_RATE // g, sr // g).astype(np.float32)

    peak = np.max(np.abs(samples)) if len(samples) else 0
    if peak > 1e-6:
        samples = samples / peak * 0.95
    samples = np.clip(samples, -1.0, 1.0)
    return (samples * 32767).astype(np.int16).tobytes()


def tts_xtts(texto: str):
    """
    Genera PCM16 mono con XTTS. Devuelve (pcm, None) o (None, error_str).
    Actualiza xtts_ok a False si falla.
    """
    global xtts_ok, xtts_last_error

    if not xtts_engine:
        return None, "XTTS no cargado"

    try:
        kwargs = {
            "text": texto,
            "language": "es",
            "split_sentences": False,   # evita que torchcodec procese audio
        }
        if VOZ_REFERENCIA and Path(VOZ_REFERENCIA).exists():
            kwargs["speaker_wav"] = VOZ_REFERENCIA

        wav = xtts_engine.tts(**kwargs)
        samples = np.array(wav, dtype=np.float32)
        return _float_to_pcm16_mono(samples, 24000), None
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        log.error(f"XTTS error: {msg}")
        xtts_last_error = msg
        # Si el error es de librería nativa, marcamos xtts como KO
        if "libtorchcodec" in msg or "Could not load this library" in msg:
            xtts_ok = False
        return None, msg


def tts_espeak(texto: str):
    """Genera PCM16 mono con espeak-ng. Devuelve bytes o None."""
    import subprocess
    import soundfile as sf
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
    try:
        subprocess.run(
            ["espeak-ng", "-v", "es", "-s", "150", "-w", tmp, texto],
            check=True, capture_output=True,
        )
        samples, sr = sf.read(tmp, dtype="float32")
        return _float_to_pcm16_mono(samples, sr)
    except Exception as e:
        log.error(f"espeak error: {e}")
        return None
    finally:
        Path(tmp).unlink(missing_ok=True)


def sintetizar(texto: str):
    """Devuelve (pcm_bytes, motor). Intenta XTTS, luego espeak."""
    pcm, err = tts_xtts(texto)
    if pcm:
        log.info(f"🎙️  XTTS: {len(pcm):,} bytes PCM")
        return pcm, "xtts"

    pcm = tts_espeak(texto)
    if pcm:
        log.info(f"🗣️  espeak: {len(pcm):,} bytes PCM")
        return pcm, "espeak"

    return None, None


def _pcm_mono_to_stereo(pcm_mono: bytes) -> bytes:
    """PCM16 mono -> PCM16 estéreo intercalado (L=R)."""
    arr = np.frombuffer(pcm_mono, dtype=np.int16)
    st = np.empty(len(arr) * 2, dtype=np.int16)
    st[0::2] = arr
    st[1::2] = arr
    return st.tobytes()


async def enviar_al_gateway(pcm_mono: bytes):
    """Envía PCM16 mono al gateway por /internal/send_pcm."""
    pcm_stereo = _pcm_mono_to_stereo(pcm_mono)
    url = f"{GATEWAY_URL}/internal/send_pcm"
    try:
        loop = asyncio.get_running_loop()
        r = await loop.run_in_executor(
            None,
            lambda: requests.post(
                url,
                data=pcm_stereo,
                headers={"Content-Type": "application/octet-stream"},
                timeout=15,
            ),
        )
        if r.ok:
            log.info(f"✅ Enviado al gateway: {len(pcm_stereo):,} bytes")
        else:
            log.warning(f"Gateway respondió {r.status_code}: {r.text[:200]}")
    except Exception as e:
        log.error(f"No se pudo contactar con gateway ({url}): {e}")


# ── HANDLERS HTTP ──
async def handle_health(request):
    return web.json_response({
        "ok": True,
        "service": "voz",
        "xtts_cargado": xtts_engine is not None,
        "xtts_ok": xtts_ok,
        "xtts_last_error": xtts_last_error,
        "referencia": VOZ_REFERENCIA if (VOZ_REFERENCIA and Path(VOZ_REFERENCIA).exists()) else None,
    })


async def handle_speak(request):
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "err": "JSON inválido"}, status=400)

    texto = (body.get("text") or "").strip()
    if not texto:
        return web.json_response({"ok": False, "err": "texto vacío"}, status=400)

    log.info(f'TTS: "{texto}"')

    loop = asyncio.get_running_loop()
    pcm, motor = await loop.run_in_executor(None, sintetizar, texto)
    if not pcm:
        return web.json_response({"ok": False, "err": "no se pudo sintetizar"}, status=500)

    asyncio.create_task(enviar_al_gateway(pcm))
    return web.json_response({"ok": True, "bytes": len(pcm), "motor": motor})


async def handle_tts_test(request):
    """
    Igual que /speak pero síncrono y devuelve info del motor.
    Útil para diagnosticar qué motor se está usando.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    texto = (body.get("text") or "prueba de voz").strip()

    loop = asyncio.get_running_loop()
    pcm, motor = await loop.run_in_executor(None, sintetizar, texto)
    if not pcm:
        return web.json_response({"ok": False, "err": "no se pudo sintetizar"}, status=500)

    asyncio.create_task(enviar_al_gateway(pcm))
    return web.json_response({
        "ok": True,
        "motor": motor,
        "bytes": len(pcm),
        "xtts_ok": xtts_ok,
        "xtts_last_error": xtts_last_error,
    })


# ── MAIN ──
def main():
    app = web.Application()
    app.router.add_get("/health", handle_health)
    app.router.add_post("/speak", handle_speak)
    app.router.add_post("/tts_test", handle_tts_test)

    # CORS
    cors = aiohttp_cors.setup(app, defaults={
        "*": aiohttp_cors.ResourceOptions(
            allow_credentials=True,
            expose_headers="*",
            allow_headers="*",
            allow_methods="*",
        )
    })
    for route in list(app.router.routes()):
        cors.add(route)

    log.info(f"🗣️  voz/ escuchando en :{PORT}  "
             f"(XTTS={'✅' if xtts_ok else '❌ espeak'})")
    web.run_app(app, host="0.0.0.0", port=PORT, print=None)


if __name__ == "__main__":
    main()
