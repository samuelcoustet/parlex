"""
dtmf.py — Détection DTMF temps-réel
Combine FFT+Hamming (approche ) avec debounce 3-hits consécutifs
et gap de 0.5s entre répétitions du même digit.
"""
from __future__ import annotations
import time
import numpy as np
from typing import Optional

# Fréquences DTMF standard
ROW_FREQS = [697, 770, 852, 941]
COL_FREQS = [1209, 1336, 1477, 1633]

DTMF_PAD = [
    ['1', '2', '3', 'A'],
    ['4', '5', '6', 'B'],
    ['7', '8', '9', 'C'],
    ['*', '0', '#', 'D'],
]

# Paramètres debounce
MIN_LEVEL    = 0.05      # amplitude minimale (abs max)
SPEC_RATIO   = 15        # les pics DTMF doivent dominer ×15 la moyenne spectrale
TWIST_MIN    = 0.5       # ratio row/col minimum 
TWIST_MAX    = 2.0       # ratio row/col maximum
CONSEC_HITS  = 3         # hits consécutifs requis (~150ms à 50ms/cycle)
PROCESS_INT  = 0.05      # intervalle entre analyses (s) — 
SAME_GAP     = 0.5       # délai min entre deux détections du même digit
SEQ_TIMEOUT  = 4.0       # effacement séquence si silence > 4s


class DTMFDecoder:
    """
    Décodeur DTMF incrémental (FFT+Hamming + debounce).
    Appeler feed() avec des chunks float32.
    pop_digits() retourne et vide le buffer de digits validés.
    """

    def __init__(self, sample_rate: int = 48000):
        self.sr = sample_rate
        self._rolling = np.zeros(4096, dtype=np.float32)

        # Debounce state 
        self._last_detected:    Optional[str] = None
        self._consec_hits:      int = 0
        self._last_char:        Optional[str] = None
        self._last_char_time:   float = 0.0
        self._last_process:     float = 0.0
        self._last_digit_time:  float = 0.0   # pour SEQ_TIMEOUT

        self.buffer: list[str] = []    # digits validés

    def feed(self, audio: np.ndarray) -> None:
        """audio: float32 mono."""
        n = len(audio)
        self._rolling = np.roll(self._rolling, -n)
        self._rolling[-n:] = audio[:n] if n <= len(self._rolling) else audio[-len(self._rolling):]

        now = time.monotonic()
        if now - self._last_process < PROCESS_INT:
            return
        self._last_process = now

        # Effacement séquence sur silence prolongé
        if self.buffer and (now - self._last_digit_time) > SEQ_TIMEOUT:
            self.buffer.clear()

        digit = self._detect(self._rolling)
        self._debounce(digit, now)

    def _detect(self, block: np.ndarray) -> Optional[str]:
        """Détection FFT+Hamming ."""
        if np.max(np.abs(block)) < MIN_LEVEL:
            return None

        freqs = np.fft.rfftfreq(len(block), 1 / self.sr)
        spectrum = np.abs(np.fft.rfft(block * np.hamming(len(block))))
        avg = float(np.mean(spectrum))

        def mag(f: float) -> float:
            idx = int(np.argmin(np.abs(freqs - f)))
            return float(np.max(spectrum[max(0, idx - 1):min(len(spectrum), idx + 2)]))

        row_mags = [mag(f) for f in ROW_FREQS]
        col_mags = [mag(f) for f in COL_FREQS]
        max_r, max_c = max(row_mags), max(col_mags)

        if max_r <= avg * SPEC_RATIO or max_c <= avg * SPEC_RATIO:
            return None

        # Twist check 
        ratio = max_r / max_c if max_c > 0 else 0.0
        if not (TWIST_MIN < ratio < TWIST_MAX):
            return None

        r_idx = int(np.argmax(row_mags))
        c_idx = int(np.argmax(col_mags))
        return DTMF_PAD[r_idx][c_idx]

    def _debounce(self, digit: Optional[str], now: float) -> None:
        """Debounce 3-hits consécutifs + gap 0.5s ."""
        if digit:
            if digit == self._last_detected:
                self._consec_hits += 1
            else:
                self._last_detected = digit
                self._consec_hits = 1

            if self._consec_hits == CONSEC_HITS:
                # Valide si digit différent du précédent OU gap >= 0.5s
                if digit != self._last_char or (now - self._last_char_time >= SAME_GAP):
                    self.buffer.append(digit)
                    self._last_char = digit
                    self._last_char_time = now
                    self._last_digit_time = now
        else:
            self._consec_hits = 0
            self._last_detected = None
            # Reset last_char si pause longue
            if now - self._last_char_time > 0.2:
                self._last_char = None

    def pop_digits(self) -> str:
        """Retourne et vide le buffer de digits détectés."""
        digits = "".join(self.buffer)
        self.buffer.clear()
        return digits
