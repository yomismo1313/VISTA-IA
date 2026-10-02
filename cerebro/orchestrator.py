#!/usr/bin/env python3
"""
VISTA · CEREBRO
No carga ningún modelo pesado. Solo:
  1. Graba del micrófono local (arecord)
  2. Manda el audio a voz/ para transcribir
  3. Decide qué comando es ("vista que ves", "vista explicame", "vista musica", "vista salir")
  4. Llama a vision/ o musica/ según toque
  5. Pide a voz/ que hable la respuesta (voz/ ya reenvía el audio al altavoz ESP32
     a través del gateway)

Así puedes correr esto en una Raspberry Pi barata sin GPU, mientras vision/ y
voz/ corren en la máquina con GPU.
"""
import logging, os, re, subprocess, sys, tempfile, time
from pathlib import Path

import numpy as np
import requests
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from _vista_config import cfg

logging.basicConfig(level=logging.INFO, format='%(asctime)s  %(levelname)-7s  [cerebro] %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger("cerebro")

AUDIO_DEVICE     = cfg.get("AUDIO_DEVICE", "plughw:2,0")
UMBRAL_SILENCIO  = float(cfg.get("UMBRAL_SILENCIO", 0.008))
VOZ_URL          = f"http://localhost:{int(cfg.get('VOZ_PORT', 8082))}"
VISION_URL       = f"http://localhost:{int(cfg.get('VISION_PORT', 8081))}"
MUSICA_URL       = f"http://localhost:{int(cfg.get('MUSICA_PORT', 8083))}"
ESP32_IP         = cfg.get("ESP32_IP", "192.168.1.147")

CORRECCIONES = {
    "bista": "vista", "queves": "que ves", "keves": "que ves",
    "qué ves": "que ves",
}


def hablar(texto):
    log.info(f"🗣️ Vista: {texto}")
    try:
        requests.post(f"{VOZ_URL}/speak", json={"text": texto, "forward": True}, timeout=20)
    except Exception as e:
        log.error(f"No se pudo contactar con voz/: {e}")


def escuchar(tiempo=2.5):
    temp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
    duracion = max(1, int(round(tiempo)))
    try:
        cmd = ['arecord', '-D', AUDIO_DEVICE, '-d', str(duracion), '-f', 'cd', '-t', 'wav', temp]
        subprocess.run(cmd, capture_output=True, timeout=duracion + 2, check=False)
        if not os.path.exists(temp) or os.path.getsize(temp) < 3000:
            return None
        audio, _ = sf.read(temp)
        rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
        if rms < UMBRAL_SILENCIO:
            return None

        with open(temp, "rb") as f:
            resp = requests.post(f"{VOZ_URL}/transcribe", data=f.read(), timeout=15)
        if resp.status_code != 200:
            return None
        texto = resp.json().get("text", "").strip().lower()
        if not texto or len(texto) < 2:
            return None
        for k, v in CORRECCIONES.items():
            texto = texto.replace(k, v)
        texto = re.sub(r'[^\w\sáéíóúñ]', '', texto)
        return re.sub(r'\s+', ' ', texto).strip()
    except Exception as e:
        log.warning(f"Error escucha: {e}")
        return None
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def procesar_comando(texto):
    if "vista" not in texto:
        return False

    if "salir" in texto:
        hablar("¡Hasta luego!")
        sys.exit(0)

    if "que ves" in texto or "que hay" in texto:
        hablar("Vale, miro la cámara.")
        try:
            r = requests.post(f"{VISION_URL}/describe", json={"razonar": False}, timeout=30)
            desc = r.json().get("descripcion", "No pude analizar la imagen.")
        except Exception as e:
            desc = "No pude conectar con el servicio de visión."
            log.error(e)
        hablar(desc)
        return True

    if "explicame" in texto or "explícame" in texto:
        hablar("Analizando la imagen con detalle.")
        try:
            r = requests.post(f"{VISION_URL}/describe", json={"razonar": True}, timeout=30)
            desc = r.json().get("descripcion", "No pude analizar la imagen.")
        except Exception as e:
            desc = "No pude conectar con el servicio de visión."
            log.error(e)
        hablar(desc)
        return True

    if "musica" in texto or "radio" in texto:
        try:
            r = requests.post(f"{MUSICA_URL}/radio/on", json={"esp32_ip": ESP32_IP}, timeout=5)
            if r.json().get("ok"):
                hablar("Encendiendo la radio.")
            else:
                hablar("No pude iniciar la radio.")
        except Exception as e:
            hablar("No pude conectar con el servicio de música.")
            log.error(e)
        return True

    hablar("No entendí. Di 'vista que ves' o 'vista explicame esto'.")
    return True


def calibrar_silencio():
    global UMBRAL_SILENCIO
    log.info("Calibrando ruido ambiente (3s)...")
    temp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
    try:
        subprocess.run(['arecord', '-D', AUDIO_DEVICE, '-d', '3', '-f', 'cd', '-t', 'wav', temp],
                        capture_output=True, timeout=5)
        audio, _ = sf.read(temp)
        rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
        UMBRAL_SILENCIO = max(rms * 2.5, 0.0015)
        log.info(f"Umbral de silencio: {UMBRAL_SILENCIO:.5f}")
    except Exception:
        log.warning("No se pudo calibrar, usando umbral por defecto")
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def main():
    calibrar_silencio()
    hablar("Hola, soy Vista. ¿Qué necesitas?")
    while True:
        try:
            texto = escuchar(2.5)
            if texto:
                log.info(f"Escuchado: {texto}")
                procesar_comando(texto)
        except KeyboardInterrupt:
            print("\n👋 Cerrando...")
            break
        except Exception as e:
            log.error(f"Error en bucle principal: {e}")


if __name__ == "__main__":
    main()
