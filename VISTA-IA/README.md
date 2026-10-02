# VISTA-IA — Arquitectura de microservicios

Cada carpeta es un servicio independiente con su **propio venv** y sus
**propias dependencias**. Se comunican entre sí por HTTP (JSON), no
comparten proceso ni memoria. Toda la configuración compartida (IPs,
puertos) vive en un único archivo: `services.env`.

```
VISTA-IA/
├── services.env          ← config compartida (edítalo)
├── gateway/               :8080  web + proxy cámara + TCP altavoz ESP32
│   └── web/                html/css/js del panel de control
├── vision/                :8081  YOLO + descripción de imagen (Ollama/LLaVA)
├── voz/                   :8082  Whisper (STT) + XTTS voz clonada (TTS)
├── musica/                :8083  radio (UDP → ESP32) + biblioteca (SQLite)
├── cerebro/                (sin puerto) bucle de escucha, orquesta todo
├── scripts/                install_all.sh / start_all.sh / stop_all.sh
├── models/                  pesos de modelos (yolov8n.pt, etc.)
├── data/                    musica.db y otros datos persistentes
└── logs/                    logs + .pid de cada servicio en marcha
```

## Bugs que tenías y quedan arreglados aquí

1. **Radio muda**: en `web3cam.py` se lanzaba el proceso `"ffm"` en vez de
   `"ffmpeg"` → ahora está en `musica/service.py`, corregido y con
   comprobación previa de que `ffmpeg` existe.
2. **"Explícame" fallaba**: `describe_image()` llamaba a
   `ollama run llava <ruta> <prompt>` por `subprocess`, que no es la sintaxis
   real del CLI de Ollama. Ahora `vision/service.py` usa la API HTTP
   (`POST /api/generate`) igual que ya hacía bien `standalone.py`.
3. **IP de la ESP32 inconsistente** entre scripts (`.141` vs `.147`) →
   ahora una sola variable `ESP32_IP` en `services.env`, y además el gateway
   la actualiza sola en cuanto la ESP32 se conecta por TCP.

## Instalación (uno por uno o todos)

```bash
cd VISTA-IA
./scripts/install_all.sh                  # instala los 5 servicios
# o solo alguno mientras probamos:
./scripts/install_all.sh vision
./scripts/install_all.sh voz
```

Copia `yolov8n.pt` a `models/` (si no, `vision/` lo descargará solo la
primera vez). Pon tu wav de referencia de voz en la ruta que indiques en
`VOZ_REFERENCIA` dentro de `services.env`.

## Arrancar

```bash
./scripts/start_all.sh          # gateway + vision + voz + musica
cd cerebro && source .venv/bin/activate && python3 orchestrator.py   # aparte, es el bucle de escucha
```

Web de control: `http://<esta-ip>:8080`

Parar todo: `./scripts/stop_all.sh`

## Cómo hablan entre sí

- `cerebro/` graba del micro, manda el wav a `voz/ /transcribe`, decide el
  comando, y según el caso llama a `vision/ /describe` o `musica/ /radio/on`.
- `voz/ /speak` genera el audio (clonado o espeak) y lo reenvía solo al
  `gateway/ /internal/send_pcm`, que es quien mantiene la conexión TCP
  persistente con el altavoz de la ESP32.
- El panel web llama directamente a `gateway/`, que a su vez reenvía
  "describir" a `vision/` y "radio" a `musica/`.

## Siguientes pasos (vamos uno por uno cuando quieras)

- [ ] `musica/`: llenar `library` de verdad (endpoint para escanear una
      carpeta de mp3 y añadirlos a la base SQLite, endpoint para reproducir
      una canción local en vez de solo radio).
- [ ] `cerebro/`: mejorar el reconocimiento de wake word (ahora mismo es
      "contiene la palabra 'vista'" tras grabar 2.5s a pelo; se puede
      cambiar a un detector de wake word real tipo Porcupine).
- [ ] `vision/`: cachear frames o pasar a captura por stream MJPEG
      continuo en vez de pedir `/capture` cada vez.
