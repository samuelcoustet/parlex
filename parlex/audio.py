"""
audio.py — Capture et playback audio via sounddevice (ALSA sous le capot)
Pattern identique au projet Castanara (sd.InputStream callback + sd.play)
"""
from __future__ import annotations
import logging
import threading
import time
import numpy as np
from typing import Optional, Callable

try:
    import sounddevice as sd
    SD_OK = True
except ImportError:
    SD_OK = False

log = logging.getLogger("audio")

SAMPLE_RATE  = 48000
CHANNELS     = 1
BLOCK_SIZE   = 0        # laisser sounddevice choisir (comme Castanara)
SAMPLE_WIDTH = 2        # bytes S16_LE

PREBUFFER_SEC = 0.5     # pre-buffer avant déclenchement VOX (identique Castanara)


# ─── Niveau RMS ───────────────────────────────────────────────────────────────

def rms_level(data) -> float:
    """RMS sur ndarray float32 ou bytes PCM S16_LE. Retourne 0.0-1.0."""
    if isinstance(data, bytes):
        arr = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
    else:
        arr = np.asarray(data, dtype=np.float32).flatten()
    if arr.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(arr ** 2)))


# ─── CTCSS ────────────────────────────────────────────────────────────────────

def apply_ctcss(audio: np.ndarray, freq: float = 88.5,
                amplitude: float = 0.05, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Ajoute sous-tonalité CTCSS sur signal float32 (identique Castanara)."""
    t = np.arange(len(audio)) / sr
    tone = amplitude * np.sin(2 * np.pi * freq * t).astype(np.float32)
    return np.clip(audio + tone, -1.0, 1.0).astype(np.float32)


# ─── Capture ALSA via sounddevice ─────────────────────────────────────────────

class ALSACapture:
    """
    Capture audio via sounddevice (callback pattern, identique Castanara).
    device: nom ALSA string ou index numérique.
    callback(indata_flat_float32): appelé pour chaque bloc.
    """

    def __init__(self, device, callback: Callable[[np.ndarray], None],
                 sample_rate: int = SAMPLE_RATE):
        self.device = device
        self.callback = callback
        self.sample_rate = sample_rate
        self._stream = None
        self._running = False

    def start(self) -> None:
        if not SD_OK:
            log.error("sounddevice non disponible — pip install sounddevice")
            return
        self._running = True

        def _cb(indata, frames, time_info, status):
            if not self._running or indata is None:
                return
            self.callback(indata.flatten().astype(np.float32))

        try:
            self._stream = sd.InputStream(
                device=self.device,
                samplerate=self.sample_rate,
                channels=CHANNELS,
                blocksize=BLOCK_SIZE,
                dtype="float32",
                callback=_cb,
            )
            self._stream.start()
            log.info("Capture démarrée : %s", self.device)
        except Exception as e:
            log.error("Capture erreur : %s", e)
            self._running = False

    def stop(self) -> None:
        self._running = False
        try:
            if self._stream:
                self._stream.stop()
                self._stream.close()
                self._stream = None
        except Exception:
            pass


# ─── Playback ALSA via sounddevice ────────────────────────────────────────────

class ALSAPlayback:
    """Lecture bloquante via sd.play() (identique Castanara)."""

    def __init__(self, device, sample_rate: int = SAMPLE_RATE):
        self.device = device
        self.sample_rate = sample_rate
        self._lock = threading.Lock()

    def play(self, data: bytes, gain: float = 1.0) -> None:
        """Joue bytes PCM S16_LE avec gain flottant (peut être > 1.0)."""
        if not data or not SD_OK:
            return
        arr = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
        self.play_numpy(arr, gain=gain)

    def play_numpy(self, audio: np.ndarray, gain: float = 1.0,
                   ctcss: bool = False, ctcss_freq: float = 88.5) -> None:
        """Joue ndarray float32. gain peut être > 1.0 (comme tx_gain=3.5)."""
        if not SD_OK:
            return
        audio = np.clip(audio.flatten().astype(np.float32) * gain, -1.0, 1.0)
        if ctcss:
            audio = apply_ctcss(audio, freq=ctcss_freq)
        with self._lock:
            try:
                sd.play(audio, samplerate=self.sample_rate, device=self.device, blocking=True)
            except Exception as e:
                log.error("Playback erreur : %s", e)


# ─── VOX detector ─────────────────────────────────────────────────────────────

class VOXDetector:
    """
    Détecte présence audio (VOX) sur flux float32.
    Identique à la logique Castanara :
      - rms > threshold → voix détectée, met à jour rx_last_voice
      - rms <= threshold ET (now - rx_last_voice) >= timeout → fermeture VOX
    """

    def __init__(self, threshold: float = 0.005, timeout: float = 2.0,
                 sample_rate: int = SAMPLE_RATE):
        self.threshold = threshold
        self.timeout = timeout
        self._open = False
        self._last_signal_time = 0.0
        self._open_callback:  Optional[Callable] = None
        self._close_callback: Optional[Callable] = None

    def on_open(self, cb: Callable) -> None:
        self._open_callback = cb

    def on_close(self, cb: Callable) -> None:
        self._close_callback = cb

    def feed(self, audio: np.ndarray) -> bool:
        """audio: float32 mono. Retourne True si VOX ouvert."""
        rms = float(np.sqrt(np.mean(audio ** 2)))
        now = time.monotonic()

        if rms >= self.threshold:
            self._last_signal_time = now
            if not self._open:
                self._open = True
                if self._open_callback:
                    self._open_callback()
        else:
            if self._open and (now - self._last_signal_time) >= self.timeout:
                self._open = False
                if self._close_callback:
                    self._close_callback()

        return self._open

    def is_open(self) -> bool:
        return self._open

    def force_close(self) -> None:
        if self._open:
            self._open = False
            if self._close_callback:
                self._close_callback()


# ─── Recorder avec pre-buffer ─────────────────────────────────────────────────

class Recorder:
    """
    Enregistre des chunks float32 en mémoire.
    Intègre un pre-buffer roulant (comme Castanara) pour ne pas perdre
    le début de la transmission avant le déclenchement VOX.
    """

    def __init__(self, max_seconds: float = 300.0,
                 prebuffer_sec: float = PREBUFFER_SEC,
                 sample_rate: int = SAMPLE_RATE):
        self.max_samples = int(max_seconds * sample_rate)
        self.prebuffer_size = int(prebuffer_sec * sample_rate)
        self.sample_rate = sample_rate
        # Pre-buffer roulant (identique Castanara : np.roll)
        self._prebuf = np.zeros(self.prebuffer_size, dtype=np.float32)
        self._chunks: list[np.ndarray] = []
        self._total_samples = 0
        self._recording = False
        self._lock = threading.Lock()

    def update_prebuffer(self, audio: np.ndarray) -> None:
        """Met à jour le pre-buffer roulant — appeler en continu même à l'arrêt."""
        n = len(audio)
        self._prebuf = np.roll(self._prebuf, -n)
        self._prebuf[-n:] = audio[:n] if n <= self.prebuffer_size else audio[-self.prebuffer_size:]

    def start(self) -> None:
        """Démarre l'enregistrement en incluant le pre-buffer."""
        with self._lock:
            # Commence par le contenu du pre-buffer (début de la transmission)
            self._chunks = [self._prebuf.copy()]
            self._total_samples = self.prebuffer_size
            self._recording = True

    def feed(self, audio: np.ndarray) -> bool:
        """Ajoute un chunk. Retourne False si mémoire pleine."""
        with self._lock:
            if not self._recording:
                return True
            self._chunks.append(audio.copy())
            self._total_samples += len(audio)
            if self._total_samples >= self.max_samples:
                self._recording = False
                return False
        return True

    def stop(self) -> np.ndarray:
        """Arrête et retourne le signal enregistré en float32."""
        with self._lock:
            self._recording = False
            if not self._chunks:
                return np.array([], dtype=np.float32)
            return np.concatenate(self._chunks).astype(np.float32)

    def is_recording(self) -> bool:
        return self._recording

    def duration(self) -> float:
        return self._total_samples / self.sample_rate

    def is_full(self) -> bool:
        return self._total_samples >= self.max_samples


# ─── Stats QSO ────────────────────────────────────────────────────────────────

class QSOStats:
    """Statistiques de session (comme Castanara qso_stats)."""

    def __init__(self):
        self.qso_count = 0
        self.tx_total_seconds = 0.0
        self.session_start = time.time()
        self.last_qso_time: Optional[float] = None

    def record_tx(self, duration_s: float) -> None:
        self.qso_count += 1
        self.tx_total_seconds += duration_s
        self.last_qso_time = time.time()

    def session_duration(self) -> float:
        return time.time() - self.session_start

    def to_dict(self) -> dict:
        return {
            "qso_count": self.qso_count,
            "tx_total_s": round(self.tx_total_seconds, 1),
            "session_h":  round(self.session_duration() / 3600, 2),
            "last_qso":   self.last_qso_time,
        }
