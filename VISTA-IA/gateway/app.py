#!/usr/bin/env python3
"""
VISTA · GATEWAY
Punto de entrada web. Proxy de cámara, TCP al altavoz ESP32, delega
describe → vision/, speak → voz/, radio → musica/.

Endpoints:
  GET  /                → web de control
  GET  /web/{fname}     → estáticos (app.js, style.css)
  GET  /stats           → estado (tcp_spk, radio, esp32_ip, server_ip, audio)
  POST /cmd             → {cmd: beep|say|stop|radio_on|radio_off}
  GET  /camera_proxy    → frame de la ESP32 (con caché y fallback)
  GET  /last_frame      → último frame cacheado (sin tocar ESP32)
  POST /describe        → descripción (proxy a vision/)
  POST /speak           → TTS voz clonada (proxy a voz/)
  POST /tts_test        → TTS de prueba (proxy a voz/)
  POST /internal/send_pcm → PCM crudo a reproducir (usado por voz/)

Config (services.env):
  TCP_SPK_PORT, GATEWAY_PORT, SAMPLE_RATE
  ESP32_IP, ESP32_CAPTURE_PATH
  VISION_PORT, VOZ_PORT, MUSICA_PORT
  GATEWAY_URL (para que voz/ encuentre al gateway)
  AUDIO_LOCAL, AUDIO_TCP, AUDIO_LOCAL_DEVICE
"""
import asyncio, logging, socket, struct, sys, tempfile, re, time
from math import gcd
from pathlib import Path

import numpy as np
import scipy.signal as ss
import soundfile as sf
import subprocess
from aiohttp import web, ClientSession, ClientTimeout

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from _vista_config import cfg

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-7s  [gateway] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger("gateway")

# ── CONFIG ──
TCP_SPK_PORT   = int(cfg.get("TCP_SPK_PORT", 5002))
WEB_PORT       = int(cfg.get("GATEWAY_PORT", 8080))
SAMPLE_RATE    = int(cfg.get("SAMPLE_RATE", 16000))
SILENCE_MS     = 800
ESP32_IP       = cfg.get("ESP32_IP", "192.168.1.138")
ESP32_CAPTURE  = cfg.get("ESP32_CAPTURE_PATH", "/capture")
VISION_URL     = f"http://127.0.0.1:{int(cfg.get('VISION_PORT', 8081))}"
VOZ_URL        = f"http://127.0.0.1:{int(cfg.get('VOZ_PORT', 8082))}"
MUSICA_URL     = f"http://127.0.0.1:{int(cfg.get('MUSICA_PORT', 8083))}"

# ── SALIDA DE AUDIO ──
AUDIO_LOCAL        = str(cfg.get("AUDIO_LOCAL", "1")) == "1"
AUDIO_TCP          = str(cfg.get("AUDIO_TCP",   "0")) == "1"
AUDIO_LOCAL_DEVICE = cfg.get("AUDIO_LOCAL_DEVICE", "default")

# ── ESTADO ──
tcp_writer = None
current_audio_task = None
local_audio_proc = None
stop_event = asyncio.Event()
stats = {"tcp_spk": False, "radio": False, "esp32_ip": ESP32_IP, "server_ip": ""}
_http_session: ClientSession | None = None
last_camera_frame: bytes | None = None
last_camera_ts: float = 0.0

# ── UTILS ──
def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()

def gen_beep_pcm(freq, ms):
    n = int(SAMPLE_RATE * ms / 1000)
    t = np.linspace(0, ms / 1000, n, endpoint=False)
    wave = (np.sin(2 * np.pi * freq * t) * 28000).astype(np.int16)
    fade = min(int(SAMPLE_RATE * 0.01), n // 4) or 1
    ramp = np.linspace(0, 1, fade)
    wave[:fade] = (wave[:fade] * ramp).astype(np.int16)
    wave[-fade:] = (wave[-fade:] * ramp[::-1]).astype(np.int16)
    st = np.empty(n * 2, dtype=np.int16)
    st[0::2] = wave
    st[1::2] = wave
    return st.tobytes()

def _mono_float_to_pcm16_stereo(samples, sr):
    if sr != SAMPLE_RATE:
        g = gcd(SAMPLE_RATE, sr)
        samples = ss.resample_poly(samples, SAMPLE_RATE // g, sr // g).astype(np.float32)
    peak = np.max(np.abs(samples)) if len(samples) else 0
    if peak > 1e-6:
        samples = samples / peak * 0.95
    samples = np.clip(samples, -1.0, 1.0)
    fade_n = min(int(SAMPLE_RATE * 0.01), len(samples) // 4) or 1
    ramp = np.linspace(0, 1, fade_n)
    samples[:fade_n] *= ramp
    samples[-fade_n:] *= ramp[::-1]
    samples = np.concatenate([samples, np.zeros(int(SAMPLE_RATE * 0.35), dtype=np.float32)])
    mono = (samples * 32767).astype(np.int16)
    stereo = np.empty(len(mono) * 2, dtype=np.int16)
    stereo[0::2] = mono
    stereo[1::2] = mono
    return stereo.tobytes()

def gen_silence_pcm(ms=SILENCE_MS):
    n = int(SAMPLE_RATE * ms / 1000)
    return b'\x00' * (n * 4)

def tts_to_pcm_espeak(texto):
    texto = re.sub(r'\[.*?\]', '', texto).strip()
    if not texto:
        return None
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
    try:
        subprocess.run(["espeak-ng", "-v", "es", "-s", "150", "-w", tmp, texto],
                        check=True, capture_output=True)
        samples, sr = sf.read(tmp, dtype="float32")
        if samples.ndim > 1:
            samples = samples.mean(axis=1)
        return _mono_float_to_pcm16_stereo(samples, sr)
    except Exception as e:
        log.error(f"espeak falló: {e}")
        return None
    finally:
        Path(tmp).unlink(missing_ok=True)

async def get_session() -> ClientSession:
    global _http_session
    if _http_session is None or _http_session.closed:
        # Timeout generoso por defecto. Cada handler lo ajusta si quiere otro.
        _http_session = ClientSession(timeout=ClientTimeout(total=60, connect=10, sock_read=50))
    return _http_session

async def close_session(app):
    global _http_session
    if _http_session and not _http_session.closed:
        await _http_session.close()

# ── PROXY CÁMARA ──
async def camera_proxy(request):
    global last_camera_frame, last_camera_ts
    url = f"http://{ESP32_IP}{ESP32_CAPTURE}"
    timeout = ClientTimeout(total=30, connect=10, sock_read=25)
    try:
        session = await get_session()
        async with session.get(url, timeout=timeout) as resp:
            if resp.status == 200:
                data = await resp.read()
                if data and len(data) > 2000:
                    last_camera_frame = data
                    last_camera_ts = time.time()
                    return web.Response(
                        body=data, content_type="image/jpeg",
                        headers={"X-Cache": "miss"},
                    )
                log.warning("ESP32 devolvió 200 pero frame vacío o diminuto")
                if last_camera_frame:
                    return web.Response(body=last_camera_frame, content_type="image/jpeg",
                                        headers={"X-Cache": "stale"})
                return web.Response(status=502, text="ESP32 devolvió frame vacío")
            log.warning(f"ESP32 devolvió {resp.status} en {url}")
            return web.Response(status=502, text=f"ESP32 devolvió {resp.status}")
    except asyncio.TimeoutError:
        log.warning(f"⏱️ Timeout ({timeout.total}s) esperando a {url}")
        if last_camera_frame and (time.time() - last_camera_ts) < 30:
            return web.Response(body=last_camera_frame, content_type="image/jpeg",
                                headers={"X-Cache": "stale"})
        return web.Response(status=504, text=f"Timeout esperando a ESP32 {ESP32_IP}")
    except Exception as e:
        log.warning(f"Error proxy cámara ({url}): {type(e).__name__}: {e!r}")
        if last_camera_frame and (time.time() - last_camera_ts) < 30:
            return web.Response(body=last_camera_frame, content_type="image/jpeg",
                                headers={"X-Cache": "stale"})
        return web.Response(status=503, text=f"ESP32 no responde en {ESP32_IP}: {type(e).__name__}")

async def last_frame(request):
    """
    Devuelve el último frame cacheado. Si no hay caché reciente, intenta
    capturar uno nuevo vía camera_proxy.
    """
    if last_camera_frame and (time.time() - last_camera_ts) < 30:
        age = time.time() - last_camera_ts
        return web.Response(
            body=last_camera_frame,
            content_type="image/jpeg",
            headers={"X-Cache": "hit", "X-Age": f"{age:.1f}"},
        )
    # Sin caché reciente: capturar uno nuevo
    return await camera_proxy(request)

# ── DESCRIBIR (vision/) ──
async def describe_image(request):
    try:
        body = await request.read()
        session = await get_session()
        async with session.post(
            f"{VISION_URL}/describe",
            data=body or None,
            headers={"Content-Type": "application/json"},
            timeout=ClientTimeout(total=120, connect=10),
        ) as resp:
            data = await resp.read()
            return web.Response(body=data, content_type="application/json", status=resp.status)
    except asyncio.TimeoutError:
        return web.json_response({"error": "timeout esperando a vision/"}, status=504)
    except Exception as e:
        log.error(f"describe: no se pudo contactar con vision/: {type(e).__name__}: {e!r}")
        return web.json_response({"error": f"No se pudo contactar con vision/: {type(e).__name__}"}, status=503)

# ── SPEAK (voz/) ──
async def handle_speak(request):
    try:
        body = await request.read()
    except Exception:
        return web.json_response({"ok": False, "err": "no body"}, status=400)
    if not body:
        return web.json_response({"ok": False, "err": "cuerpo vacío"}, status=400)

    # XTTS con textos largos puede tardar minutos → 10 min de margen
    timeout = ClientTimeout(total=600, connect=10)
    try:
        session = await get_session()
        async with session.post(
            f"{VOZ_URL}/speak",
            data=body,
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        ) as resp:
            data = await resp.read()
            return web.Response(body=data, content_type="application/json", status=resp.status)
    except asyncio.TimeoutError:
        return web.json_response({"ok": False, "err": "timeout esperando a voz/"}, status=504)
    except Exception as e:
        log.error(f"speak: no se pudo contactar con voz/: {type(e).__name__}: {e!r}")
        return web.json_response({"ok": False, "err": f"voz/ no disponible: {type(e).__name__}"}, status=503)

async def handle_tts_test(request):
    """Proxy a voz/tts_test para diagnóstico."""
    try:
        body = await request.read()
        session = await get_session()
        async with session.post(
            f"{VOZ_URL}/tts_test",
            data=body or None,
            headers={"Content-Type": "application/json"},
            timeout=ClientTimeout(total=600, connect=10),
        ) as resp:
            data = await resp.read()
            return web.Response(body=data, content_type="application/json", status=resp.status)
    except Exception as e:
        return web.json_response({"ok": False, "err": f"voz/ no disponible: {type(e).__name__}"}, status=503)

# ── ENVÍO DE AUDIO ──
async def _play_local(pcm):
    global local_audio_proc
    if not AUDIO_LOCAL or not pcm:
        return
    if local_audio_proc and local_audio_proc.returncode is None:
        try:
            local_audio_proc.terminate()
        except Exception:
            pass
    try:
        local_audio_proc = await asyncio.create_subprocess_exec(
            "aplay", "-q",
            "-D", AUDIO_LOCAL_DEVICE,
            "-f", "S16_LE",
            "-r", str(SAMPLE_RATE),
            "-c", "2",
            "-t", "raw",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await local_audio_proc.communicate(input=pcm)
        rc = local_audio_proc.returncode
        if rc not in (0, -15, 143):
            log.warning(f"aplay rc={rc}: {err.decode(errors='ignore')[:200]}")
        else:
            log.info(f"🔈 Local: {len(pcm):,} bytes")
    except FileNotFoundError:
        log.error("❌ aplay no encontrado. Instala: sudo apt install alsa-utils")
    except Exception as e:
        log.error(f"❌ Error audio local: {e}")
    finally:
        local_audio_proc = None

async def send_audio_tcp(pcm, wait_seconds=12.0):
    global current_audio_task, tcp_writer
    if pcm is None:
        return

    if AUDIO_LOCAL:
        asyncio.create_task(_play_local(pcm))

    if not AUDIO_TCP:
        return

    if current_audio_task and not current_audio_task.done():
        current_audio_task.cancel()
        try:
            await current_audio_task
        except asyncio.CancelledError:
            pass
    stop_event.clear()

    async def _send():
        global tcp_writer
        if tcp_writer is None:
            log.info(f"⏳ Esperando ESP32 ({wait_seconds}s)...")
            deadline = asyncio.get_event_loop().time() + wait_seconds
            while tcp_writer is None and asyncio.get_event_loop().time() < deadline:
                if stop_event.is_set():
                    return
                await asyncio.sleep(0.2)
            if tcp_writer is None:
                log.warning("⚠️ ESP32 no conectada por TCP")
                return
        try:
            data = struct.pack("<I", len(pcm)) + pcm
            for i in range(0, len(data), 8192):
                if stop_event.is_set():
                    sil = gen_silence_pcm(SILENCE_MS)
                    tcp_writer.write(struct.pack("<I", len(sil)) + sil)
                    await tcp_writer.drain()
                    return
                tcp_writer.write(data[i:i + 8192])
                await tcp_writer.drain()
            log.info(f"✅ Enviados {len(pcm):,} bytes por TCP")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error(f"❌ Envío TCP: {e}")
            tcp_writer = None
            stats["tcp_spk"] = False

    current_audio_task = asyncio.create_task(_send())
    try:
        await current_audio_task
    except asyncio.CancelledError:
        pass
    finally:
        current_audio_task = None

async def stop_audio():
    global current_audio_task, local_audio_proc
    stop_event.set()
    if current_audio_task and not current_audio_task.done():
        current_audio_task.cancel()
        try:
            await current_audio_task
        except asyncio.CancelledError:
            pass
    current_audio_task = None
    if local_audio_proc and local_audio_proc.returncode is None:
        try:
            local_audio_proc.terminate()
        except Exception:
            pass
    if tcp_writer:
        sil = gen_silence_pcm(SILENCE_MS)
        try:
            tcp_writer.write(struct.pack("<I", len(sil)) + sil)
            await tcp_writer.drain()
        except Exception:
            pass

async def internal_send_pcm(request):
    pcm = await request.read()
    if not pcm:
        return web.json_response({"ok": False, "err": "cuerpo vacío"}, status=400)
    asyncio.create_task(send_audio_tcp(pcm))
    return web.json_response({"ok": True, "bytes": len(pcm)})

# ── TCP SERVER (altavoz ESP32) ──
async def handle_tcp_spk(reader, writer):
    global tcp_writer, ESP32_IP
    if tcp_writer:
        try:
            tcp_writer.close()
            await tcp_writer.wait_closed()
        except Exception:
            pass
    try:
        sock = writer.get_extra_info("socket")
        if sock:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    except Exception:
        pass
    tcp_writer = writer
    stats["tcp_spk"] = True
    addr = writer.get_extra_info("peername")
    if addr:
        ESP32_IP = addr[0]
        stats["esp32_ip"] = ESP32_IP
        log.info(f"🔊 ESP32 conectada desde {ESP32_IP}")
    try:
        while not writer.is_closing():
            await asyncio.sleep(0.3)
    finally:
        if tcp_writer is writer:
            tcp_writer = None
            stats["tcp_spk"] = False
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
        log.info("🔊 ESP32 desconectada")

# ── RADIO (musica/) ──
async def radio_proxy(request, action):
    if action == "on":
        try:
            body = await request.json()
        except Exception:
            body = {}
        payload = {"url": body.get("url"), "esp32_ip": ESP32_IP}
    else:
        payload = {"esp32_ip": ESP32_IP}
    try:
        session = await get_session()
        async with session.post(
            f"{MUSICA_URL}/radio/{action}",
            json=payload,
            timeout=ClientTimeout(total=10, connect=5),
        ) as resp:
            data = await resp.json()
            stats["radio"] = (action == "on") and data.get("ok", False)
            return data
    except Exception as e:
        log.error(f"radio: no se pudo contactar con musica/: {type(e).__name__}: {e!r}")
        return {"ok": False, "err": str(e)}

# ── RUTAS HTTP ──
async def handle_index(request):
    html_path = Path(__file__).resolve().parent / "web" / "index.html"
    if not html_path.exists():
        log.error(f"❌ No existe {html_path}")
        return web.Response(status=500, text=f"Falta {html_path}")
    html = html_path.read_text(encoding="utf-8").replace(
        "RADIO_DEFAULT_PLACEHOLDER", cfg.get("RADIO_DEFAULT", "")
    )
    return web.Response(text=html, content_type="text/html", charset="utf-8")

async def handle_static(request):
    fname = request.match_info["fname"]
    if ".." in fname or fname.startswith("/"):
        raise web.HTTPNotFound()
    path = Path(__file__).resolve().parent / "web" / fname
    if not path.exists() or not path.is_file():
        raise web.HTTPNotFound()
    if fname.endswith(".css"):
        ctype = "text/css"
    elif fname.endswith(".js"):
        ctype = "application/javascript"
    else:
        ctype = "application/octet-stream"
    return web.Response(text=path.read_text(encoding="utf-8"), content_type=ctype, charset="utf-8")

async def handle_stats(request):
    d = dict(stats)
    d["server_ip"] = local_ip()
    d["esp32_ip"] = ESP32_IP
    d["audio_local"] = AUDIO_LOCAL
    d["audio_tcp"] = AUDIO_TCP
    d["last_frame_age"] = (time.time() - last_camera_ts) if last_camera_ts else None
    return web.json_response(d)

async def handle_cmd(request):
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "err": "bad json"}, status=400)

    cmd = data.get("cmd", "")
    loop = asyncio.get_running_loop()

    if cmd == "beep":
        pcm = gen_beep_pcm(int(data.get("freq", 880)), int(data.get("ms", 200)))
        asyncio.create_task(send_audio_tcp(pcm))
    elif cmd == "say":
        t = data.get("text", "").strip()
        if not t:
            return web.json_response({"ok": False, "err": "texto vacío"}, status=400)
        async def _say():
            pcm = await loop.run_in_executor(None, tts_to_pcm_espeak, t)
            await send_audio_tcp(pcm)
        asyncio.create_task(_say())
    elif cmd == "stop":
        asyncio.create_task(stop_audio())
    elif cmd == "radio_on":
        return web.json_response(await radio_proxy(request, "on"))
    elif cmd == "radio_off":
        return web.json_response(await radio_proxy(request, "off"))
    else:
        return web.json_response({"ok": False, "err": "cmd desconocido"}, status=400)
    return web.json_response({"ok": True})

# ── MAIN ──
async def main():
    await asyncio.start_server(handle_tcp_spk, "0.0.0.0", TCP_SPK_PORT)

    app = web.Application(client_max_size=50 * 1024 * 1024)  # 50 MB
    app.router.add_get("/", handle_index)
    app.router.add_get("/web/{fname}", handle_static)
    app.router.add_get("/stats", handle_stats)
    app.router.add_post("/cmd", handle_cmd)
    app.router.add_get("/camera_proxy", camera_proxy)
    app.router.add_get("/last_frame", last_frame)
    app.router.add_post("/describe", describe_image)
    app.router.add_post("/speak", handle_speak)
    app.router.add_post("/tts_test", handle_tts_test)
    app.router.add_post("/internal/send_pcm", internal_send_pcm)
    app.on_cleanup.append(close_session)

    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", WEB_PORT).start()

    ip = local_ip()
    log.info("=" * 60)
    log.info("🔊  VISTA · GATEWAY")
    log.info(f"    TCP altavoz  → :{TCP_SPK_PORT}   (AUDIO_TCP={AUDIO_TCP})")
    log.info(f"    Audio local   {'✅ ON' if AUDIO_LOCAL else '❌ OFF'}  (dev={AUDIO_LOCAL_DEVICE})")
    log.info(f"    Web           http://{ip}:{WEB_PORT}")
    log.info(f"    vision/  →  {VISION_URL}")
    log.info(f"    voz/     →  {VOZ_URL}")
    log.info(f"    musica/  →  {MUSICA_URL}")
    log.info(f"    ESP32_IP  =  {ESP32_IP}")
    log.info("=" * 60)

    await asyncio.Future()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
