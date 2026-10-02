"""
Carga services.env y expone la config como diccionario/objeto simple.
Cada servicio importa esto con: from _vista_config import cfg
"""
import os
from pathlib import Path

def _find_env_file():
    here = Path(__file__).resolve().parent
    for base in [here, here.parent]:
        candidate = base / "services.env"
        if candidate.exists():
            return candidate
    return here.parent / "services.env"

def _load(path):
    data = {}
    if not path.exists():
        return data
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        data[k.strip()] = v.strip()
    return data

_env_path = _find_env_file()
_raw = _load(_env_path)

class Config:
    def __init__(self, d):
        self._d = d
        for k, v in d.items():
            setattr(self, k, self._cast(v))

    @staticmethod
    def _cast(v):
        if v.replace(".", "", 1).isdigit():
            return float(v) if "." in v else int(v)
        return v

    def get(self, key, default=None):
        return getattr(self, key, default)

cfg = Config(_raw)
