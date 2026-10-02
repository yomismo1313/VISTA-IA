#!/bin/bash
# Exportar las libs de CUDA al LD_LIBRARY_PATH
if [ -n "$VIRTUAL_ENV" ]; then
  NV="$VIRTUAL_ENV/lib/python3.12/site-packages/nvidia"
  export LD_LIBRARY_PATH="$NV/cuda_nvrtc/lib:$NV/cublas/lib:$NV/cudnn/lib:$LD_LIBRARY_PATH"
  echo "✅ CUDA libs añadidas al LD_LIBRARY_PATH"
else
  echo "⚠️  Activa el venv primero: source .venv/bin/activate"
fi
