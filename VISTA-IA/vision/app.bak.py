#!/usr/bin/env python3
"""
VISTA · VISION SERVICE
  GET  /health
  POST /detect     -> objetos detectados por YOLO (JSON)
  POST /describe   -> objetos YOLO + descripción en lenguaje natural (LLaVA vía Ollama)
"""
import base64, logging, sys
from collections import Counter
from pathlib import Path

import requests
from aiohttp import web, ClientSession, ClientTimeout

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from _vista_config import cfg

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-7s  [vision] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger("vision")

# ── CONFIG ──
PORT          = int(cfg.get("VISION_PORT", 8081))
ESP32_IP      = cfg.get("ESP32_IP", "192.168.1.132")          # ✅ default correcto
ESP32_CAPTURE = cfg.get("ESP32_CAPTURE_PATH", "/capture")
OLLAMA_URL    = cfg.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL  = cfg.get("OLLAMA_MODEL", "llava:latest")       # ✅ typo corregido
TMP_IMAGE     = "/tmp/vista_frame.jpg"

log.info("👁️  Cargando YOLO...")
from ultralytics import YOLO
_models_dir = Path(cfg.get("MODELS_DIR", "../models"))
_yolo_path  = _models_dir / "yolov8n.pt"
yolo_model  = YOLO(str(_yolo_path) if _yolo_path.exists() else "yolov8n.pt")
log.info("✅ YOLO listo")

# Sesión HTTP reutilizable (mejor que crear una por request)
_session: ClientSession | None = None

async def get_session() -> ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = ClientSession(timeout=ClientTimeout(total=6))
    return _session

async def close_session(app):
    global _session
    if _session and not _session.closed:
        await _session.close()

async def _capturar_frame(timeout=6):
    url = f"http://{ESP32_IP}{ESP32_CAPTURE}"
    try:
        session = await get_session()
        async with session.get(url) as resp:
            if resp.status != 200:
                log.warning(f"ESP32 devolvió {resp.status} en {url}")
                return False
            data = await resp.read()
            if not data:
                log.warning("Frame vacío")
                return False
            with open(TMP_IMAGE, "wb") as f:
                f.write(data)
            return True
    except Exception as e:
        log.error(f"Error capturando frame de {url}: {e}")
        return False

def _detectar_objetos():
    objetos = []
    try:
        results = yolo_model(TMP_IMAGE, conf=0.35, verbose=False)
        for r in results:
            if r.boxes:
                for box in r.boxes:
                    objetos.append(yolo_model.names[int(box.cls[0])])
    except Exception as e:
        log.warning(f"YOLO falló: {e}")
    return objetos

def _resumen_objetos(objetos):
    if not objetos:
        return "", {}
    c = Counter(objetos)
    partes = [f"{v} {k}s" if v > 1 else f"un {k}" for k, v in c.items()]
    return "Detecto: " + ", ".join(partes) + ". ", dict(c)

def _describir_con_ollama(desc_objetos, razonar=False):
    try:
        with open(TMP_IMAGE, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        log.error(f"No se pudo leer {TMP_IMAGE}: {e}")
        return None

    prompt = (
        "Eres un asistente inteligente. Analiza la imagen y responde en español. "
        f"{'Explica con detalle y razonando sobre el contexto.' if razonar else 'Describe de forma clara y concisa.'} "
        f"{desc_objetos} Responde en 2-3 frases."
    )
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "images": [img_b64],
                "stream": False,
                "options": {"temperature": 0.2, "num_predict": 130},
            },
            timeout=25,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("response", "").strip()[:400] or None
        log.warning(f"Ollama HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        log.error(f"Ollama/LLaVA error: {e}")
    return None

# ── HANDLERS ──
async def handle_health(request):
    return web.json_response({"ok": True, "service": "vision", "esp32_ip": ESP32_IP})

async def handle_detect(request):
    if not await _capturar_frame():
        return web.json_response({"error": "No se pudo capturar imagen de la ESP32"}, status=503)
    objetos = _detectar_objetos()
    _, conteo = _resumen_objetos(objetos)
    return web.json_response({"objetos": conteo})

async def handle_describe(request):
    razonar = False
    try:
        body = await request.json()
        razonar = bool(body.get("razonar", False))
    except Exception:
        pass

    if not await _capturar_frame():
        return web.json_response({"error": "No se pudo capturar imagen de la ESP32"}, status=503)

    objetos = _detectar_objetos()
    desc_objetos, conteo = _resumen_objetos(objetos)
    descripcion = _describir_con_ollama(desc_objetos, razonar=razonar)
    if descripcion is None:
        descripcion = desc_objetos or "No pude analizar la imagen."

    return web.json_response({"descripcion": descripcion, "objetos": conteo})

def main():
    app = web.Application()
    app.router.add_get("/health", handle_health)
    app.router.add_post("/detect", handle_detect)
    app.router.add_post("/describe", handle_describe)
    app.on_cleanup.append(close_session)
    log.info(f"👁️  vision/ escuchando en :{PORT}  (ESP32={ESP32_IP})")
    web.run_app(app, host="0.0.0.0", port=PORT, print=None)

if __name__ == "__main__":
    main()
