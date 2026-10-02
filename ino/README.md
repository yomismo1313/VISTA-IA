# VISTA-IA · Sketch ESP32-CAM

## Antes de compilar

1. **Arduino IDE**: instala el core "ESP32" (Boards Manager → `esp32` by
   Espressif). Selecciona la placa **AI Thinker ESP32-CAM**.
2. **Partition Scheme**: `Huge APP (3MB No OTA/1MB SPIFFS)` — la cámara
   necesita espacio.
3. **PSRAM**: actívala en el menú Tools si tu placa la tiene (la mayoría de
   ESP32-CAM sí). Si no la tiene, el sketch cae solo a resolución QVGA.

## Qué revisar en el propio `.ino` antes de subirlo

- `WIFI_SSID` / `WIFI_PASSWORD` — ya puestos a `Livebox-BED3` / tu clave.
- `SERVER_IP` — ya puesto a `192.168.1.135` (debe ser igual que
  `SERVIDOR_WEB_IP` en `services.env`).
- `local_IP` — puesta a `192.168.1.141` (igual que `ESP32_IP` en
  `services.env`). Si prefieres que el router le dé la IP por DHCP en vez
  de forzarla, pon `USE_STATIC_IP` a `false` y actualiza `ESP32_IP` a mano
  en `services.env` si cambia.
- **`I2S_BCLK_PIN` / `I2S_LRC_PIN` / `I2S_DOUT_PIN`** — están puestos a
  `2 / 14 / 15` como valores de ejemplo. **Ajusta esto a tu cableado real**
  del amplificador (MAX98357A u otro I2S). Si tu módulo de audio anterior
  usaba otros pines, cámbialos aquí.

## Cómo encaja con el resto del proyecto

| Endpoint/puerto ESP32      | Quién lo llama                                   |
|-----------------------------|---------------------------------------------------|
| `GET :80/capture`           | `gateway/app.py` (proxy web) y `vision/service.py` |
| `GET :80/stream`             | opcional, MJPEG continuo (no usado por defecto)    |
| TCP cliente → `:5002`       | `gateway/app.py` le manda beeps y TTS               |
| UDP escucha `:5003`          | `musica/service.py` le manda la radio               |

La ESP32 es la que **inicia** la conexión TCP hacia el gateway (no al
revés), así que en cuanto flashees y arranque, deberías ver en
`logs/gateway.log`: `🔊 ESP32 conectada desde 192.168.1.141`.

## Prueba rápida tras flashear

```bash
# desde tu PC, en la misma red:
curl -o foto.jpg http://192.168.1.141/capture   # debería descargar una foto
./check_setup.sh                                 # ping + /capture + health de los 4 servicios
```

Si `/capture` no responde: revisa el Monitor Serie (115200 baudios) — el
sketch imprime si la cámara no inicializó (fallo típico: cableado o
`Partition Scheme` mal puesto).

Si no oyes nada por el altavoz al probar "Decir" en la web: casi siempre es
el cableado I2S (`I2S_BCLK_PIN`/`I2S_LRC_PIN`/`I2S_DOUT_PIN`) mal puesto, o
un MAX98357A con el pin `SD` no puesto a nivel alto/flotante para
seleccionar canal mono correctamente.
