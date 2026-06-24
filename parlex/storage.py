"""
storage.py — Gestion persistante des messages audio (WAV)
Voicemail (20 max) + Announcements (slots 0-9) + Say-again buffer
"""
from __future__ import annotations
import wave
import struct
import json
import time
import logging
import numpy as np
from pathlib import Path
from typing import Optional, List

log = logging.getLogger("storage")

SAMPLE_RATE = 48000
CHANNELS    = 1
SAMPWIDTH   = 2      # 16-bit


def _data_dir() -> Path:
    d = Path("/var/lib/parlex")
    d.mkdir(parents=True, exist_ok=True)
    return d


# ─── Utilitaires WAV ──────────────────────────────────────────────────────────

def save_wav(path: Path, audio: bytes) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(SAMPWIDTH)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio)


def load_wav(path: Path) -> Optional[bytes]:
    if not path.exists():
        return None
    try:
        with wave.open(str(path), "rb") as wf:
            return wf.readframes(wf.getnframes())
    except Exception as e:
        log.error("load_wav %s : %s", path, e)
        return None


def audio_duration(data: bytes) -> float:
    return len(data) / (SAMPLE_RATE * CHANNELS * SAMPWIDTH)


# ─── Announcements ────────────────────────────────────────────────────────────

class AnnouncementStore:
    """10 slots (0-9) stockés dans DATA_DIR/announce/."""

    def __init__(self):
        self.base = _data_dir() / "announce"
        self.base.mkdir(exist_ok=True)

    def _path(self, slot: int) -> Path:
        return self.base / f"ann_{slot:02d}.wav"

    def save(self, slot: int, audio: bytes) -> None:
        save_wav(self._path(slot), audio)
        log.info("Announcement %d sauvegardé (%ds)", slot, int(audio_duration(audio)))

    def load(self, slot: int) -> Optional[bytes]:
        return load_wav(self._path(slot))

    def exists(self, slot: int) -> bool:
        return self._path(slot).exists()

    def erase(self, slot: int) -> bool:
        p = self._path(slot)
        if p.exists():
            p.unlink()
            return True
        return False

    def list_slots(self) -> List[int]:
        return [int(p.stem.split("_")[1]) for p in sorted(self.base.glob("ann_*.wav"))]


# ─── Voicemail ────────────────────────────────────────────────────────────────

class VoicemailStore:
    """20 messages max, numérotés séquentiellement, avec metadata JSON."""

    MAX_MESSAGES = 20

    def __init__(self):
        self.base = _data_dir() / "voicemail"
        self.base.mkdir(exist_ok=True)
        self._meta_path = self.base / "meta.json"
        self._meta: List[dict] = self._load_meta()

    def _load_meta(self) -> List[dict]:
        if self._meta_path.exists():
            try:
                return json.loads(self._meta_path.read_text())
            except Exception:
                pass
        return []

    def _save_meta(self) -> None:
        self._meta_path.write_text(json.dumps(self._meta, indent=2))

    def _path(self, idx: int) -> Path:
        return self.base / f"vm_{idx:04d}.wav"

    def count(self) -> int:
        return len(self._meta)

    def is_full(self) -> bool:
        return len(self._meta) >= self.MAX_MESSAGES

    def add(self, audio: bytes) -> bool:
        if self.is_full():
            log.warning("Voicemail pleine (%d messages)", self.MAX_MESSAGES)
            return False
        idx = max((m["idx"] for m in self._meta), default=-1) + 1
        save_wav(self._path(idx), audio)
        self._meta.append({
            "idx": idx,
            "ts": time.time(),
            "dur": audio_duration(audio),
        })
        self._save_meta()
        log.info("Voicemail #%d enregistrée (%.1fs)", idx, audio_duration(audio))
        return True

    def get(self, position: int) -> Optional[bytes]:
        """position: 0-based index dans la liste."""
        if not 0 <= position < len(self._meta):
            return None
        return load_wav(self._path(self._meta[position]["idx"]))

    def erase(self, position: int) -> bool:
        if not 0 <= position < len(self._meta):
            return False
        m = self._meta.pop(position)
        p = self._path(m["idx"])
        if p.exists():
            p.unlink()
        self._save_meta()
        return True

    def erase_all(self) -> None:
        for m in self._meta:
            p = self._path(m["idx"])
            if p.exists():
                p.unlink()
        self._meta.clear()
        self._save_meta()
        log.info("Voicemail effacée")

    def meta_list(self) -> List[dict]:
        return list(self._meta)


# ─── Say-again buffer ─────────────────────────────────────────────────────────

class SayAgainBuffer:
    """Stocke la dernière transmission reçue en mémoire (non persistant)."""

    def __init__(self):
        self._audio: Optional[bytes] = None
        self._ts: float = 0.0

    def store(self, audio: bytes) -> None:
        self._audio = audio
        self._ts = time.time()

    def get(self) -> Optional[bytes]:
        return self._audio

    def clear(self) -> None:
        self._audio = None

    def age(self) -> float:
        return time.time() - self._ts if self._audio else float("inf")
