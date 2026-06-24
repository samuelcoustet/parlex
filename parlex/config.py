"""
config.py — Settings Parlex, valeurs défaut fidèles au manuel (rev Mk II)
Persistance YAML. Toutes les unités sont en secondes sauf mention contraire.
"""
from __future__ import annotations
import yaml
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

CONFIG_PATH = Path("/etc/parlex/config.yaml")
DATA_DIR    = Path("/var/lib/parlex")

# ─── Courtesy tones ───────────────────────────────────────────────────────────
COURTESY_TONES = {
    0: "none",
    1: "rising_triple",   # défaut Parlex
    2: "high_high_low_low",
    3: "high_low",
    4: "low_high",
    5: "triple_beep",
    6: "single_beep",
}

# ─── Squelch modes ────────────────────────────────────────────────────────────
SQ_VOX    = 0
SQ_COR_HI = 1
SQ_COR_LO = 2


@dataclass
class RepeaterConfig:
    # ── Audio hardware ──────────────────────────────────────────────────────
    alsa_capture:  str = "aioc_shared"     # entrée radio (dsnoop)
    alsa_playback: str = "plug:aioc_hw"    # sortie radio (exclusive)
    sample_rate:   int = 48000
    channels:      int = 1
    sample_width:  int = 2                 # bytes (16-bit)

    # ── PTT / COR ──────────────────────────────────────────────────────────
    serial_port: str  = "/dev/ttyACM0"     # AIOC
    cor_mode:    int  = SQ_VOX             # ##109
    cor_gpio:    Optional[int] = None      # GPIO BCM pin (si COR actif)

    # ── VOX ────────────────────────────────────────────────────────────────
    vox_threshold:      float = 0.02       # RMS fraction de full-scale (0-1)
    vox_timeout:        float = 2.0        # ##13 défaut 2s
    squelch_tail_supp:  float = 0.0        # ##79 (×1/75 s dans Parlex)

    # ── Repeater logic ─────────────────────────────────────────────────────
    repeater_on:       bool  = True        # ##70/##71
    say_again_on:      bool  = True        # ##14
    min_tx_time:       float = 0.2         # ##19 (1/10 s units dans Parlex)
    max_tx_time:       float = 0.0         # ##17 (0=disabled)
    cooldown_time:     float = 0.0         # ##18 (0=disabled)
    tx_delay:          float = 0.5         # ##92 délai PTT→audio
    tx_audio_level:    int   = 99          # ##11 (0-99, mappé sur tx_gain)
    tx_gain:           float = 3.5         # multiplicateur réel (peut être > 1.0)
    input_gain:        int   = 0           # ##107 (0=1x, 1=2x, 2=4x)
    # ── CTCSS ──────────────────────────────────────────────────────────────
    ctcss_enabled:     bool  = False       # sous-tonalité CTCSS sur TX
    ctcss_freq:        float = 88.5        # Hz
    courtesy_tone:     int   = 1           # ##12
    courtesy_tone_delay: bool = False      # ##77
    auto_off_timer:    float = 0.0         # ##06 (0=disabled)
    standby_msg:       int   = -1          # ##164/-1=off, ##165=beep (0), ##1651..9=msg n
    pager_code:        str   = ""          # ##78
    responder_mode:    bool  = False       # ##75

    # ── Security ───────────────────────────────────────────────────────────
    security_code:    str   = "000"        # ##08
    locked:           bool  = False        # ##00
    auto_lock_timer:  float = 0.0          # ##09 (0=disabled)

    # ── Voice ID ───────────────────────────────────────────────────────────
    voice_id_on:      bool  = False        # ##16 0/1
    voice_id_inhibit: float = 600.0        # ##85 (10 min défaut)
    voice_id_rotate:  bool  = False        # ##16 8/9
    voice_preamble:   bool  = False        # ##16 2/3

    # ── CW ID ──────────────────────────────────────────────────────────────
    cw_id_text:       str   = ""           # ##82 (12 chars max)
    cw_id_on:         bool  = False        # ##80 0/1
    cw_id_timer:      float = 600.0        # ##83 (10 min défaut)
    cw_cleanup_id:    bool  = False        # ##80 2/3
    cw_responder_on:  bool  = False        # ##15
    cw_inhibit_timer: float = 600.0        # ##84 (10 min défaut)
    cw_speed:         int   = 80           # ##81 (0-99, mappé sur cw_wpm)
    cw_wpm:           int   = 15           # WPM direct
    cw_freq:          int   = 800          # Hz ton CW

    # ── Voicemail ──────────────────────────────────────────────────────────
    voicemail_on:     bool  = False        # ##72/##73
    voicemail_code:   str   = "000"        # ##07
    voicemail_max:    int   = 20

    # ── Announcements (10 slots 0-9) ───────────────────────────────────────
    # Stockés comme dict: {slot: {interval: float, offset: float}}
    announcement_timers: dict = field(default_factory=lambda: {
        str(i): {"interval": 0.0, "offset": 0.0} for i in range(10)
    })

    # ── Usage counter ──────────────────────────────────────────────────────
    usage_count: int = 0

    # ── Output pins ────────────────────────────────────────────────────────
    output_pins: dict = field(default_factory=lambda: {"0": False, "1": False})

    # ── Surveillance relais distant (HTTP) ─────────────────────────────────
    remote_url:     str  = "http://localhost:8080"
    remote_enabled: bool = False

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "RepeaterConfig":
        if path.exists():
            with open(path) as f:
                data = yaml.safe_load(f) or {}
            obj = cls()
            for k, v in data.items():
                if hasattr(obj, k):
                    setattr(obj, k, v)
            return obj
        return cls()

    def save(self, path: Path = CONFIG_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            yaml.dump(asdict(self), f, default_flow_style=False, allow_unicode=True)

    def reset_defaults(self) -> None:
        defaults = RepeaterConfig()
        for k, v in asdict(defaults).items():
            if k not in ("usage_count",):
                setattr(self, k, v)
