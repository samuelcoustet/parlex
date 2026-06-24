"""
tones.py — Génération des sons système ADS-SR1
  Courtesy tones, beeps de commande, ton 1kHz calibration, Morse CW
"""
from __future__ import annotations
import math
import struct
import numpy as np
from typing import List

SAMPLE_RATE = 48000


def _sine(freq: float, duration: float, amplitude: float = 0.6, sr: int = SAMPLE_RATE) -> np.ndarray:
    n = int(sr * duration)
    t = np.linspace(0, duration, n, endpoint=False)
    return (amplitude * np.sin(2 * math.pi * freq * t)).astype(np.float32)


def _silence(duration: float, sr: int = SAMPLE_RATE) -> np.ndarray:
    return np.zeros(int(sr * duration), dtype=np.float32)


def _mix(*arrays) -> np.ndarray:
    return np.concatenate(arrays)


# ─── Beeps système (réponses commandes) ───────────────────────────────────────

def beep_ok() -> np.ndarray:
    """Triple bip haut — commande acceptée."""
    hi = _sine(1200, 0.08)
    gap = _silence(0.04)
    return _mix(hi, gap, hi, gap, hi)


def beep_negative() -> np.ndarray:
    """Haut-haut-bas — résultat négatif."""
    hi = _sine(1200, 0.08)
    lo = _sine(800,  0.12)
    gap = _silence(0.04)
    return _mix(hi, gap, hi, gap, lo)


def beep_error() -> np.ndarray:
    """Haut-bas — commande inconnue."""
    hi = _sine(1200, 0.10)
    lo = _sine(800,  0.15)
    gap = _silence(0.04)
    return _mix(hi, gap, lo)


def beep_locked() -> np.ndarray:
    """Bas-bas — code sécurité requis."""
    lo = _sine(600, 0.15)
    gap = _silence(0.06)
    return _mix(lo, gap, lo)


def beep_standby() -> np.ndarray:
    """Double bip stand-by."""
    b = _sine(1000, 0.08)
    gap = _silence(0.06)
    return _mix(b, gap, b)


def calibration_tone(duration: float = 3.0) -> np.ndarray:
    """Ton 1 kHz pleine amplitude — ##98."""
    return _sine(1000, duration, amplitude=0.95)


# ─── Courtesy tones ───────────────────────────────────────────────────────────

def _rising(f1, f2, f3) -> np.ndarray:
    g = _silence(0.04)
    return _mix(_sine(f1, 0.07), g, _sine(f2, 0.07), g, _sine(f3, 0.07))


def courtesy_tone(style: int) -> np.ndarray:
    if style == 0:
        return np.array([], dtype=np.float32)
    elif style == 1:                         # rising triple (défaut)
        return _rising(880, 1000, 1200)
    elif style == 2:                         # high-high-low-low
        g = _silence(0.04)
        return _mix(_sine(1200, 0.07), g, _sine(1200, 0.07), g,
                    _sine(800, 0.10), g, _sine(800, 0.10))
    elif style == 3:                         # high-low
        return _mix(_sine(1200, 0.08), _silence(0.04), _sine(800, 0.12))
    elif style == 4:                         # low-high
        return _mix(_sine(800, 0.12), _silence(0.04), _sine(1200, 0.08))
    elif style == 5:                         # triple beep
        b = _sine(1000, 0.08)
        g = _silence(0.04)
        return _mix(b, g, b, g, b)
    elif style == 6:                         # single beep
        return _sine(1000, 0.12)
    return np.array([], dtype=np.float32)


# ─── Morse CW ─────────────────────────────────────────────────────────────────

MORSE_TABLE = {
    'A': '.-',   'B': '-...',  'C': '-.-.',  'D': '-..',
    'E': '.',    'F': '..-.',  'G': '--.',   'H': '....',
    'I': '..',   'J': '.---',  'K': '-.-',   'L': '.-..',
    'M': '--',   'N': '-.',    'O': '---',   'P': '.--.',
    'Q': '--.-', 'R': '.-.',   'S': '...',   'T': '-',
    'U': '..-',  'V': '...-',  'W': '.--',   'X': '-..-',
    'Y': '-.--', 'Z': '--..',
    '0': '-----', '1': '.----', '2': '..---', '3': '...--',
    '4': '....-', '5': '.....', '6': '-....', '7': '--...',
    '8': '---..',  '9': '----.',
    '.': '.-.-.-', ',': '--..--', '?': '..--..', '/': '-..-.',
    '-': '-....-', '(': '-.--.', ')': '-.--.-', ';': '-.-.-.',
    ':': '---...', "'": '.----.', '_': '..--.-', ' ': ' ',
}

# Table encodage ADS-SR1 ##82 (00=space, 01=A ... 26=Z, 27=. 28=, 29=? 30=0 ... 39=9 40=; 41=: 42=/ 43=- 44=' 45=( 46=) 47=_)
SR1_CHARS = " ABCDEFGHIJKLMNOPQRSTUVWXYZ.,?0123456789;:/-'()_"


def sr1_decode_cwtext(encoded: str) -> str:
    """Décode la chaîne ##82 (paires de digits) en texte."""
    chars = []
    for i in range(0, len(encoded) - 1, 2):
        try:
            idx = int(encoded[i:i+2])
            if 0 <= idx < len(SR1_CHARS):
                chars.append(SR1_CHARS[idx])
        except ValueError:
            pass
    return "".join(chars).strip()


def generate_cw(text: str, speed: int = 80, freq: float = 700.0, sr: int = SAMPLE_RATE) -> np.ndarray:
    """
    Génère audio CW pour 'text'.
    speed: 0-99 → WPM 5-35
    """
    wpm = 5 + int(speed * 30 / 99)
    dot_dur = 1.2 / wpm              # durée point en secondes
    dash_dur = 3 * dot_dur
    sym_gap  = dot_dur               # espace inter-symbole
    char_gap = 3 * dot_dur           # espace inter-caractère
    word_gap = 7 * dot_dur           # espace mot

    segments: List[np.ndarray] = []
    for ch in text.upper():
        if ch == ' ':
            segments.append(_silence(word_gap))
            continue
        morse = MORSE_TABLE.get(ch)
        if not morse:
            continue
        for i, sym in enumerate(morse):
            if sym == '.':
                segments.append(_sine(freq, dot_dur, 0.7))
            elif sym == '-':
                segments.append(_sine(freq, dash_dur, 0.7))
            if i < len(morse) - 1:
                segments.append(_silence(sym_gap))
        segments.append(_silence(char_gap))

    if not segments:
        return np.array([], dtype=np.float32)
    return np.concatenate(segments).astype(np.float32)


# ─── Utilitaires ──────────────────────────────────────────────────────────────

def pcm_to_bytes(audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Convertit ndarray float32 → bytes PCM S16_LE."""
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    return pcm.tobytes()


def bytes_to_float32(data: bytes) -> np.ndarray:
    """Convertit bytes PCM S16_LE → ndarray float32."""
    arr = np.frombuffer(data, dtype=np.int16).astype(np.float32)
    return arr / 32768.0
