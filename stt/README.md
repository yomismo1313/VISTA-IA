# STT — Speech-to-Text para VISTA-IA

Servidor STT local con **faster-whisper + CUDA**.

## Requisitos

- Python 3.12
- GPU NVIDIA con CUDA (o CPU, cambiando `device="cpu"` y `compute_type="int8"`)
- `ffmpeg` y `alsa-utils` instalados

## Instalación

\`\`\`bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
\`\`\`

El `activate` ya exporta `LD_LIBRARY_PATH` con las libs CUDA del venv.

## Uso

### Grabar y transcribir

\`\`\`bash
./grabar.sh 8
\`\`\`

### Servidor TCP (para ESP32)

\`\`\`bash
source .venv/bin/activate
python stt_server.py
\`\`\`

Escucha en `0.0.0.0:5010`.

### Formato de audio esperado

- PCM 16-bit little endian
- 16000 Hz
- Mono (1 canal)

## Modelos

| Modelo | VRAM | Velocidad | Calidad |
|--------|------|-----------|---------|
| tiny | ~1 GB | muy rápida | básica |
| base | ~1 GB | muy rápida | buena |
| **small** | **~2 GB** | **rápida** | **muy buena** |
| medium | ~5 GB | media | excelente |
| large-v3 | ~10 GB | lenta | máxima |
