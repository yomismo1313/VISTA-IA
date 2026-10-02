#!/usr/bin/env bash
# migrar_voz_a_gpu.sh — Cambia el venv de voz de CPU a GPU CUDA

set -e

CUDA_TAG="cu124"   # ← cámbialo según tu nvidia-smi
VOZ_DIR="$HOME/Escritorio/v1/VISTA-IA/voz"

echo "═══════════════════════════════════════════════"
echo "  Migrando voz/ a GPU (CUDA=$CUDA_TAG)"
echo "═══════════════════════════════════════════════"

# 0. Comprobar que hay GPU
if ! nvidia-smi &>/dev/null; then
    echo "❌ nvidia-smi no funciona. ¿Tienes driver NVIDIA instalado?"
    exit 1
fi

echo ""
echo "── Estado actual del driver ──"
nvidia-smi --query-gpu=name,driver_version,cuda_version --format=csv

# 1. Activar venv
cd "$VOZ_DIR"
# shellcheck disable=SC1091
source .venv/bin/activate

# 2. Desinstalar versión CPU
echo ""
echo "── Desinstalando torch/torchaudio/torchcodec de CPU ──"
pip uninstall -y torch torchaudio torchcodec

# 3. Instalar versión CUDA
echo ""
echo "── Instalando torch 2.9.0 + torchaudio 2.9.0 + torchcodec 0.8.1 ($CUDA_TAG) ──"
pip install \
    torch==2.9.0 \
    torchaudio==2.9.0 \
    torchcodec==0.8.1 \
    --index-url "https://download.pytorch.org/whl/$CUDA_TAG"

# 4. Verificar
echo ""
echo "── Verificando ──"
python3 - <<'PYEOF'
import torch, torchaudio, torchcodec
print(f"torch      : {torch.__version__}")
print(f"torchaudio : {torchaudio.__version__}")
print(f"torchcodec : {torchcodec.__version__}")
print(f"CUDA disponible : {torch.cuda.is_available()}")
if torch.cuda.is_available():
    print(f"GPU        : {torch.cuda.get_device_name(0)}")
    print(f"VRAM       : {torch.cuda.get_device_properties(0).total_memory / 1e9:.1f} GB")
PYEOF

echo ""
echo "✅ Listo. Ahora edita voz/app.py para usar .to(device):"
echo "   busca 'tts_engine = TTS(XTTS_MODEL_NAME)' y añade '.to(\"cuda\")'"
