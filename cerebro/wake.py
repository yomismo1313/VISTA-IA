#!/usr/bin/env python3
"""
VISTA · Wake Word Detection

DGM20 USB Microphone
48 kHz entrada
        ↓
resampling
        ↓
16 kHz
        ↓
openWakeWord + Silero VAD
        ↓
WakeEvent
"""

from __future__ import annotations

import argparse
import queue
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np
import sounddevice as sd
from scipy.signal import resample_poly

try:
    from openwakeword.model import Model as OWWModel
except ImportError:
    print(
        "❌ Falta openwakeword. Instala con:",
        "pip install openwakeword",
        file=sys.stderr,
    )
    raise

try:
    from silero_vad import load_silero_vad, VADIterator

    _HAS_VAD = True

except ImportError:

    _HAS_VAD = False

    print(
        "⚠️ silero-vad no instalado",
        file=sys.stderr,
    )


# ============================================================
# CONFIG
# ============================================================

MIC_DEVICE = 7

MIC_SAMPLE_RATE = 48000
SAMPLE_RATE = 16000

MIC_CHANNELS = 2

FRAME_SAMPLES = 1280       # 80 ms

VAD_FRAME_SAMPLES = 512

WAKE_THRESHOLD = 0.55

VAD_THRESHOLD = 0.35

POST_WAKE_SILENCE = 0.8

POST_WAKE_WAIT = 4.0

MAX_TURN_SECONDS = 12.0

PRE_ROLL_SECONDS = 1.0

# RMS mínimo considerado voz.
#
# Tu micrófono tiene bastante más señal que esto.
# Lo usamos como respaldo del VAD.
VOICE_RMS_THRESHOLD = 0.008

WAKE_MODELS = ["hey_jarvis"]


# ============================================================
# EVENTO
# ============================================================

@dataclass
class WakeEvent:

    audio: np.ndarray

    duration: float

    timestamp: float = field(
        default_factory=time.time
    )


# ============================================================
# LISTENER
# ============================================================

class WakeWordListener:

    def __init__(
        self,
        on_wake: Callable[[WakeEvent], None],
        *,
        mic_device: int | str | None = MIC_DEVICE,
        wake_models: list[str] = WAKE_MODELS,
        threshold: float = WAKE_THRESHOLD,
        use_vad: bool = True,
        max_turn_seconds: float = MAX_TURN_SECONDS,
        post_wake_silence: float = POST_WAKE_SILENCE,
        debug: bool = False,
    ):

        self.on_wake = on_wake

        self.mic_device = mic_device

        self.threshold = threshold

        self.use_vad = (
            use_vad and _HAS_VAD
        )

        self.max_turn_seconds = (
            max_turn_seconds
        )

        self.post_wake_silence = (
            post_wake_silence
        )

        self.debug = debug

        # ----------------------------------------------------
        # openWakeWord
        # ----------------------------------------------------

        print(
            f"🔄 Cargando openWakeWord "
            f"{wake_models}...",
            flush=True,
        )

        self.oww = OWWModel(
            wakeword_models=wake_models,
            inference_framework="onnx",
        )

        print(
            "✅ Wake word lista:",
            list(self.oww.models.keys()),
            flush=True,
        )

        # ----------------------------------------------------
        # VAD
        # ----------------------------------------------------

        self.vad_model = None
        self.vad = None

        if self.use_vad:

            print(
                "🔄 Cargando Silero VAD...",
                flush=True,
            )

            self.vad_model = load_silero_vad(
                onnx=True
            )

            self.vad = VADIterator(
                self.vad_model,
                threshold=VAD_THRESHOLD,
                sampling_rate=SAMPLE_RATE,
                min_silence_duration_ms=int(
                    post_wake_silence * 1000
                ),
            )

            print(
                "✅ VAD listo",
                flush=True,
            )

        # ----------------------------------------------------
        # Cola ÚNICA
        # ----------------------------------------------------

        self._q: queue.Queue[np.ndarray] = (
            queue.Queue()
        )

        self._running = False

        # Buffer principal 16 kHz
        self._buffer = np.zeros(
            0,
            dtype=np.float32,
        )

        # Pre-roll
        self._pre_roll = np.zeros(
            0,
            dtype=np.float32,
        )

        self._pre_roll_samples = int(
            PRE_ROLL_SECONDS * SAMPLE_RATE
        )

    # ========================================================
    # CALLBACK
    # ========================================================

    def _audio_cb(
        self,
        indata,
        frames,
        time_info,
        status,
    ):

        if status:

            print(
                f"⚠️ audio: {status}",
                file=sys.stderr,
                flush=True,
            )

        # IMPORTANTE:
        # El callback solamente copia el audio.
        #
        # Nada de VAD.
        # Nada de openWakeWord.
        # Nada de resampling aquí.

        self._q.put(
            indata[:, 0].copy()
        )

    # ========================================================
    # RESAMPLE
    # ========================================================

    @staticmethod
    def _resample(
        audio48: np.ndarray,
    ) -> np.ndarray:

        if len(audio48) == 0:

            return np.zeros(
                0,
                dtype=np.float32,
            )

        return np.asarray(
            resample_poly(
                audio48,
                1,
                3,
            ),
            dtype=np.float32,
        )

    # ========================================================
    # OPENWAKEWORD
    # ========================================================

    def _score(
        self,
        frame: np.ndarray,
    ) -> dict[str, float]:

        pcm = (
            np.clip(
                frame,
                -1.0,
                1.0,
            )
            * 32767
        ).astype(np.int16)

        return self.oww.predict(
            pcm
        )

    # ========================================================
    # RUN
    # ========================================================

    def run(self):

        self._running = True

        # ----------------------------------------------------
        # Información del dispositivo
        # ----------------------------------------------------

        try:

            info = sd.query_devices(
                self.mic_device
            )

            print(
                f"🎙️ Micrófono: "
                f"{info['name']}",
                flush=True,
            )

            print(
                f"   dispositivo={self.mic_device}",
                flush=True,
            )

            print(
                f"   frecuencia="
                f"{MIC_SAMPLE_RATE} Hz",
                flush=True,
            )

        except Exception as e:

            print(
                f"⚠️ Error consultando micrófono: {e}",
                flush=True,
            )

        # ----------------------------------------------------
        # STREAM
        # ----------------------------------------------------

        with sd.InputStream(
            samplerate=MIC_SAMPLE_RATE,
            channels=MIC_CHANNELS,
            dtype="float32",
            blocksize=1280,
            device=self.mic_device,
            callback=self._audio_cb,
        ):

            print(
                f"👂 Escuchando wake word "
                f"(umbral={self.threshold})...",
                flush=True,
            )

            try:

                while self._running:

                    try:

                        audio48 = self._q.get(
                            timeout=0.5
                        )

                    except queue.Empty:

                        continue

                    # ------------------------------------------------
                    # 48 → 16 kHz
                    # ------------------------------------------------

                    audio16 = self._resample(
                        audio48
                    )

                    if len(audio16) == 0:
                        continue

                    # ------------------------------------------------
                    # Añadir al buffer
                    # ------------------------------------------------

                    self._buffer = np.concatenate(
                        [
                            self._buffer,
                            audio16,
                        ]
                    )

                    # ------------------------------------------------
                    # Procesar frames de 80 ms
                    # ------------------------------------------------

                    while (
                        len(self._buffer)
                        >= FRAME_SAMPLES
                    ):

                        frame = (
                            self._buffer[
                                :FRAME_SAMPLES
                            ]
                        )

                        self._buffer = (
                            self._buffer[
                                FRAME_SAMPLES:
                            ]
                        )

                        # ------------------------------------------------
                        # PRE-ROLL
                        # ------------------------------------------------

                        self._pre_roll = np.concatenate(
                            [
                                self._pre_roll,
                                frame,
                            ]
                        )

                        if (
                            len(self._pre_roll)
                            > self._pre_roll_samples
                        ):

                            self._pre_roll = (
                                self._pre_roll[
                                    -self._pre_roll_samples:
                                ]
                            )

                        # ------------------------------------------------
                        # WAKE
                        # ------------------------------------------------

                        scores = self._score(
                            frame
                        )

                        if not scores:
                            continue

                        name = max(
                            scores,
                            key=scores.get,
                        )

                        score = float(
                            scores[name]
                        )

                        if self.debug:

                            print(
                                f"  oww: "
                                f"{name}="
                                f"{score:.4f}",
                                end="\r",
                                flush=True,
                            )

                        if (
                            name == "hey_jarvis"
                            and score
                            >= self.threshold
                        ):

                            print(
                                f"\n🎯 Wake word detectada: "
                                f"{name} "
                                f"(score={score:.3f})",
                                flush=True,
                            )

                            self._capture_turn()

            except KeyboardInterrupt:

                print(
                    "\n👋 Cerrando...",
                    flush=True,
                )

            finally:

                self._running = False

    # ========================================================
    # CAPTURAR TURNO
    # ========================================================

    def _capture_turn(self):

        # Reset VAD
        if self.vad:

            self.vad.reset_states()

        # ----------------------------------------------------
        # PRE-ROLL
        #
        # Incluimos el segundo anterior.
        # ----------------------------------------------------

        captured = []

        if len(self._pre_roll):

            captured.append(
                self._pre_roll.copy()
            )

        # ----------------------------------------------------
        # Variables
        # ----------------------------------------------------

        start = time.time()

        speech_detected = False

        silence_since: Optional[
            float
        ] = None

        # Buffer específico del turno.
        #
        # Esto es importante:
        # mientras estamos capturando el turno,
        # el RUN principal NO consume audio.
        # ----------------------------------------------------

        print(
            "🎤 Esperando comando...",
            flush=True,
        )

        while True:

            elapsed = (
                time.time()
                - start
            )

            # ------------------------------------------------
            # Timeout absoluto
            # ------------------------------------------------

            if (
                elapsed
                >= self.max_turn_seconds
            ):

                print(
                    f"⏱️ Tope de "
                    f"{self.max_turn_seconds}s "
                    f"alcanzado",
                    flush=True,
                )

                break

            # ------------------------------------------------
            # Esperar audio
            # ------------------------------------------------

            try:

                audio48 = self._q.get(
                    timeout=0.5
                )

            except queue.Empty:

                continue

            # ------------------------------------------------
            # Convertir
            # ------------------------------------------------

            audio16 = self._resample(
                audio48
            )

            if len(audio16) == 0:
                continue

            captured.append(
                audio16.copy()
            )

            # ------------------------------------------------
            # RMS
            # ------------------------------------------------

            rms = float(
                np.sqrt(
                    np.mean(
                        audio16 ** 2
                    )
                )
            )

            # ------------------------------------------------
            # VAD
            # ------------------------------------------------

            vad_detected = False

            if self.vad:

                for i in range(
                    0,
                    len(audio16),
                    VAD_FRAME_SAMPLES,
                ):

                    vad_frame = (
                        audio16[
                            i:i
                            + VAD_FRAME_SAMPLES
                        ]
                    )

                    if (
                        len(vad_frame)
                        < VAD_FRAME_SAMPLES
                    ):
                        break

                    ev = self.vad(
                        vad_frame,
                        return_seconds=True,
                    )

                    if ev:

                        if "start" in ev:

                            vad_detected = True

                        if "end" in ev:

                            silence_since = (
                                time.time()
                            )

            # ------------------------------------------------
            # Voz por VAD
            # O por RMS como respaldo
            # ------------------------------------------------

            if (
                vad_detected
                or rms >= VOICE_RMS_THRESHOLD
            ):

                if not speech_detected:

                    print(
                        f"🗣️ Voz detectada "
                        f"(RMS={rms:.4f})",
                        flush=True,
                    )

                speech_detected = True

                # Mientras haya señal de voz,
                # no estamos en silencio.
                silence_since = None

            else:

                # ------------------------------------------------
                # Solo iniciar contador de silencio si ya hubo voz
                # ------------------------------------------------

                if speech_detected:

                    if silence_since is None:

                        silence_since = (
                            time.time()
                        )

                    elif (
                        time.time()
                        - silence_since
                        >= self.post_wake_silence
                    ):

                        print(
                            "🤫 Fin del comando",
                            flush=True,
                        )

                        break

            # ------------------------------------------------
            # Si no hubo voz todavía
            # ------------------------------------------------

            if (
                not speech_detected
                and elapsed
                >= POST_WAKE_WAIT
            ):

                print(
                    "⚠️ Sin voz tras la wake word, "
                    "abortando turno",
                    flush=True,
                )

                return

        # ====================================================
        # RESULTADO
        # ====================================================

        if not captured:
            return

        audio = np.concatenate(
            captured
        ).astype(np.float32)

        duration = (
            len(audio)
            / SAMPLE_RATE
        )

        rms_final = float(
            np.sqrt(
                np.mean(
                    audio ** 2
                )
            )
        )

        print(
            f"🎤 Turno capturado: "
            f"{duration:.2f}s "
            f"(RMS={rms_final:.4f})",
            flush=True,
        )

        evt = WakeEvent(
            audio=audio,
            duration=duration,
        )

        try:

            self.on_wake(
                evt
            )

        except Exception as e:

            print(
                f"❌ Error en on_wake: "
                f"{type(e).__name__}: {e}",
                flush=True,
            )

    # ========================================================
    # STOP
    # ========================================================

    def stop(self):

        self._running = False


# ============================================================
# LIST DEVICES
# ============================================================

def _list_devices():

    print(
        sd.query_devices()
    )


# ============================================================
# CLI
# ============================================================

def _cli():

    parser = argparse.ArgumentParser(
        description="VISTA wake word tester"
    )

    parser.add_argument(
        "--list",
        action="store_true",
    )

    parser.add_argument(
        "--test",
        action="store_true",
    )

    parser.add_argument(
        "--device",
        type=int,
        default=MIC_DEVICE,
    )

    parser.add_argument(
        "--threshold",
        type=float,
        default=WAKE_THRESHOLD,
    )

    args = parser.parse_args()

    if args.list:

        _list_devices()
        return

    def on_wake(
        evt: WakeEvent,
    ):

        rms = float(
            np.sqrt(
                np.mean(
                    evt.audio ** 2
                )
            )
        )

        print(
            f"✅ on_wake llamado "
            f"({evt.duration:.2f}s) "
            f"RMS={rms:.4f}",
            flush=True,
        )

    listener = WakeWordListener(
        on_wake=on_wake,
        mic_device=args.device,
        threshold=args.threshold,
        debug=True,
    )

    listener.run()


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    _cli()
