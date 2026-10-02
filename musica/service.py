#!/usr/bin/env python3
"""
VISTA · MUSICA SERVICE
  GET  /health
  POST /radio/on    {"url": "...", "esp32_ip": "..."}
  POST /radio/off   {"esp32_ip": "..."}
  GET  /library      -> canciones en la biblioteca (SQLite, de momento vacío/manual)
  POST /library       {"titulo","artista","ruta"} -> añade una canción

El bug real de tu radio: en web3cam.py se lanzaba el proceso "ffm" en vez de
"ffmpeg" (línea suelta, probablemente un recorte accidental). Aquí ya está
como "ffmpeg" y además comprobamos con shutil.which que existe antes de
arrancar, devolviendo un error claro en vez de morir en silencio.
"""
import asyncio, logging, shutil, socket, sqlite3, sys
from pathlib import Path

from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from _vista_config import cfg

logging.basicConfig(level=logging.INFO, format='%(asctime)s  %(levelname)-7s  [musica] %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger("musica")

PORT           = int(cfg.get("MUSICA_PORT", 8083))
UDP_RADIO_PORT = int(cfg.get("UDP_RADIO_PORT", 5003))
SAMPLE_RATE    = int(cfg.get("SAMPLE_RATE", 16000))
RADIO_DEFAULT  = cfg.get("RADIO_DEFAULT", "")
DATA_DIR       = Path(cfg.get("DATA_DIR", "../data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH        = DATA_DIR / "musica.db"

# ── DB (esqueleto para más adelante) ──
def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS canciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            titulo TEXT NOT NULL,
            artista TEXT,
            ruta TEXT NOT NULL UNIQUE,
            anadida_en TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    con.commit()
    con.close()

init_db()

# ── ESTADO RADIO ──
radio_task = None
radio_on_flag = False

async def _radio_stream(url, target_ip):
    global radio_on_flag
    log.info(f"📻 Radio: {url} → {target_ip}:{UDP_RADIO_PORT}")
    if not shutil.which("ffmpeg"):
        log.error("❌ ffmpeg no está instalado (sudo apt install ffmpeg)")
        radio_on_flag = False
        return
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65536)
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-loglevel", "quiet",
            "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
            "-i", url, "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", "1", "pipe:1",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        CHUNK_BYTES = 640  # ~20ms de audio mono a 16kHz
        CHUNK_TIME = CHUNK_BYTES / (SAMPLE_RATE * 2)

        while radio_on_flag:
            data = await proc.stdout.read(CHUNK_BYTES)
            if not data:
                break
            sock.sendto(data, (target_ip, UDP_RADIO_PORT))
            await asyncio.sleep(CHUNK_TIME)

        proc.terminate()
        await proc.wait()
    except asyncio.CancelledError:
        pass
    except Exception as e:
        log.error(f"❌ Radio: {e}")
    finally:
        sock.close()
        radio_on_flag = False
        log.info("📻 Radio detenida")


async def handle_health(request):
    return web.json_response({"ok": True, "service": "musica", "radio": radio_on_flag})


async def handle_radio_on(request):
    global radio_task, radio_on_flag
    try:
        body = await request.json()
    except Exception:
        body = {}
    url = body.get("url") or RADIO_DEFAULT
    target_ip = body.get("esp32_ip") or cfg.get("ESP32_IP")

    if not target_ip:
        return web.json_response({"ok": False, "err": "no hay esp32_ip"}, status=400)
    if not shutil.which("ffmpeg"):
        return web.json_response({"ok": False, "err": "ffmpeg no instalado en este entorno"}, status=500)

    if radio_task and not radio_task.done():
        radio_task.cancel()
        try:
            await radio_task
        except asyncio.CancelledError:
            pass

    radio_on_flag = True
    radio_task = asyncio.create_task(_radio_stream(url, target_ip))
    return web.json_response({"ok": True})


async def handle_radio_off(request):
    global radio_task, radio_on_flag
    radio_on_flag = False
    if radio_task and not radio_task.done():
        radio_task.cancel()
        try:
            await radio_task
        except asyncio.CancelledError:
            pass
    radio_task = None
    return web.json_response({"ok": True})


async def handle_library_get(request):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT id, titulo, artista, ruta, anadida_en FROM canciones ORDER BY anadida_en DESC").fetchall()
    con.close()
    return web.json_response([dict(r) for r in rows])


async def handle_library_post(request):
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "err": "bad json"}, status=400)
    titulo, artista, ruta = body.get("titulo"), body.get("artista", ""), body.get("ruta")
    if not titulo or not ruta:
        return web.json_response({"ok": False, "err": "titulo y ruta son obligatorios"}, status=400)
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute("INSERT INTO canciones (titulo, artista, ruta) VALUES (?,?,?)", (titulo, artista, ruta))
        con.commit()
        con.close()
        return web.json_response({"ok": True})
    except sqlite3.IntegrityError:
        return web.json_response({"ok": False, "err": "esa ruta ya está en la biblioteca"}, status=409)


def main():
    app = web.Application()
    app.router.add_get("/health", handle_health)
    app.router.add_post("/radio/on", handle_radio_on)
    app.router.add_post("/radio/off", handle_radio_off)
    app.router.add_get("/library", handle_library_get)
    app.router.add_post("/library", handle_library_post)
    log.info(f"🎵 musica/ escuchando en :{PORT}  (db: {DB_PATH})")
    web.run_app(app, host="0.0.0.0", port=PORT, print=None)


if __name__ == "__main__":
    main()
