"""
commands.py — Parser et dispatcher des commandes DTMF ##XX Parlex
Retourne un tuple (response_tone, message) où response_tone est l'une des
constantes CMD_OK / CMD_NEG / CMD_ERR / CMD_LOCKED.
"""
from __future__ import annotations
import logging
import time
from typing import TYPE_CHECKING, Optional, Tuple

if TYPE_CHECKING:
    from .repeater import SimplexRepeater

log = logging.getLogger("commands")

CMD_OK     = "ok"
CMD_NEG    = "negative"
CMD_ERR    = "error"
CMD_LOCKED = "locked"

# Temps d'entrée de commande (fenêtre)
ENTRY_TIMEOUT = 10.0


class CommandParser:
    """
    Analyse le flux DTMF entrant pour détecter les commandes ##XX.
    Doit être alimenté par feed() pour chaque digit reçu.
    """

    def __init__(self, repeater: "SimplexRepeater"):
        self.rep = repeater
        self._seq: str = ""
        self._last_digit_time: float = 0.0
        self._in_command = False

    def feed(self, digit: str) -> Optional[Tuple[str, str]]:
        """
        Retourne (tone, msg) si une commande complète est détectée, sinon None.
        Peut aussi retourner une action spéciale ("ABORT", "SAY_AGAIN", etc.)
        """
        now = time.monotonic()

        # Timeout de séquence
        if now - self._last_digit_time > ENTRY_TIMEOUT and self._seq:
            self._seq = ""
            self._in_command = False
        self._last_digit_time = now

        cfg = self.rep.config

        # ─── Commandes sans authentification ──────────────────────────────────
        if digit == '*':
            self._seq = ""
            self._in_command = False
            return ("ABORT", "abort")

        if digit == '0' and not self._seq:
            return ("SAY_AGAIN", "say_again")

        if digit == '1' and not self._seq and cfg.voicemail_on:
            return ("VM_RECORD", "record_voicemail")

        # Repeater on/off sans code (seulement si auto-off timer activé)
        if digit == '3' and not self._seq and cfg.auto_off_timer > 0:
            return ("REPEATER_ON", "repeater_on")
        if digit == '6' and not self._seq and cfg.auto_off_timer > 0:
            return ("REPEATER_OFF", "repeater_off")

        # Début commande voicemail : *+code
        if digit == '*' and not self._seq:
            self._seq = "*"
            return None

        if self._seq.startswith("*") and len(self._seq) == 1:
            self._seq += digit
            return None
        if self._seq.startswith("*") and len(self._seq) == 2:
            self._seq += digit
            return None
        if self._seq.startswith("*") and len(self._seq) == 3:
            code = self._seq[1:]
            self._seq = ""
            if code == cfg.voicemail_code:
                return ("VM_RETRIEVE", "voicemail_retrieve")
            return (CMD_ERR, "bad_vm_code")

        # Accumulation ##
        self._seq += digit

        if self._seq == "#":
            return None
        if self._seq == "##":
            self._in_command = True
            return None

        if not self._in_command:
            self._seq = ""
            return None

        # On accumule les digits de commande
        cmd_digits = self._seq[2:]

        # Commandes à longueur fixe connue
        result = self._try_dispatch(cmd_digits)
        if result is not None:
            self._seq = ""
            self._in_command = False
            return result
        return None

    def _try_dispatch(self, digits: str) -> Optional[Tuple[str, str]]:
        """Retourne (tone, msg) si 'digits' forme une commande complète, sinon None."""
        cfg = self.rep.config
        n = len(digits)

        if n < 2:
            return None

        # ── Security check ────────────────────────────────────────────────────
        if cfg.locked and digits[:2] != "00":
            # Tenter déverrouillage ##+code (3 digits après ##)
            if n == 5:
                candidate_code = digits[2:]
                if candidate_code == cfg.security_code:
                    cfg.locked = False
                    cfg.save()
                    self.rep._last_cmd_time = time.monotonic()
                    return (CMD_OK, "unlocked")
                return (CMD_LOCKED, "still_locked")
            if n > 5:
                return (CMD_ERR, "need_unlock_first")
            return None   # encore en cours de saisie

        # ── Commandes (authentifiées) ──────────────────────────────────────────
        prefix = digits[:2]

        # ## 0 0 — Lock
        if digits == "00":
            cfg.locked = True
            cfg.save()
            return (CMD_OK, "locked")

        # ## 0 1 — Ping
        if digits == "01":
            return (CMD_OK, "pong")

        # ## 0 2 — Check voicemail
        if digits == "02":
            return ("VM_RETRIEVE", "voicemail_retrieve")

        # ## 0 6 <time> — Auto-off timer
        if prefix == "06" and n >= 4:
            val = self._parse_time(digits[2:])
            if val is None:
                return None   # encore en saisie
            cfg.auto_off_timer = val
            cfg.save()
            return (CMD_OK, f"auto_off={val}")

        # ## 0 7 <code6> — Set voicemail code
        if prefix == "07" and n == 8:
            c1, c2 = digits[2:5], digits[5:8]
            if c1 == c2:
                cfg.voicemail_code = c1
                cfg.save()
                return (CMD_OK, f"vm_code={c1}")
            return (CMD_NEG, "code_mismatch")

        # ## 0 8 <code6> — Set security code
        if prefix == "08" and n == 8:
            c1, c2 = digits[2:5], digits[5:8]
            if c1 == c2:
                cfg.security_code = c1
                cfg.save()
                return (CMD_OK, f"sec_code={c1}")
            return (CMD_NEG, "code_mismatch")

        # ## 0 9 <time> — Auto-lock timer
        if prefix == "09" and n >= 4:
            val = self._parse_time(digits[2:])
            if val is None:
                return None
            cfg.auto_lock_timer = val
            cfg.save()
            return (CMD_OK, f"auto_lock={val}")

        # ## 1 1 <nn> — TX audio level (0-99)
        if prefix == "11" and n == 4:
            try:
                lvl = int(digits[2:4])
                cfg.tx_audio_level = min(99, max(0, lvl))
                cfg.save()
                return (CMD_OK, f"tx_level={lvl}")
            except ValueError:
                return (CMD_ERR, "bad_value")

        # ## 1 2 <n> — Courtesy tone
        if prefix == "12" and n == 3:
            try:
                t = int(digits[2])
                if 0 <= t <= 6:
                    cfg.courtesy_tone = t
                    cfg.save()
                    return (CMD_OK, f"ctone={t}")
            except ValueError:
                pass
            return (CMD_ERR, "bad_value")

        # ## 1 3 <nn> — VOX timeout (1/10 s)
        if prefix == "13" and n == 4:
            try:
                val = int(digits[2:4]) / 10.0
                cfg.vox_timeout = val
                cfg.save()
                self.rep.audio_vox.timeout = val
                return (CMD_OK, f"vox_timeout={val}")
            except ValueError:
                return (CMD_ERR, "bad_value")

        # ## 1 4 <0/1> — Say-again on/off
        if prefix == "14" and n == 3:
            cfg.say_again_on = (digits[2] == '1')
            cfg.save()
            return (CMD_OK, f"say_again={'on' if cfg.say_again_on else 'off'}")

        # ## 1 5 <0/1> — CW responder on/off
        if prefix == "15" and n == 3:
            cfg.cw_responder_on = (digits[2] == '1')
            cfg.save()
            return (CMD_OK, "cw_responder")

        # ## 1 6 <n> — Voice ID / preamble / rotation
        if prefix == "16" and n == 3:
            sub = digits[2]
            if sub == '0':
                cfg.voice_id_on = False
            elif sub == '1':
                cfg.voice_id_on = True
            elif sub == '2':
                cfg.voice_preamble = False
            elif sub == '3':
                cfg.voice_preamble = True
            elif sub == '8':
                cfg.voice_id_rotate = False
            elif sub == '9':
                cfg.voice_id_rotate = True
            else:
                return (CMD_ERR, "bad_voice_id")
            cfg.save()
            return (CMD_OK, f"voice_id_sub={sub}")

        # ## 1 6 4 — Disable standby message
        if digits == "164":
            cfg.standby_msg = -1
            cfg.save()
            return (CMD_OK, "standby_off")

        # ## 1 6 5 [n] — Enable standby beep (## 165) or message n (##1651..9)
        if digits == "165":
            cfg.standby_msg = 0    # beep
            cfg.save()
            return (CMD_OK, "standby_beep")
        if prefix == "16" and n == 4 and digits[2] == '5':
            try:
                slot = int(digits[3])
                cfg.standby_msg = slot
                cfg.save()
                return (CMD_OK, f"standby_msg={slot}")
            except ValueError:
                return (CMD_ERR, "bad_value")

        # ## 1 7 <time> — Repeater timeout
        if prefix == "17" and n >= 4:
            val = self._parse_time(digits[2:])
            if val is None:
                return None
            cfg.max_tx_time = val
            cfg.save()
            return (CMD_OK, f"max_tx={val}")

        # ## 1 8 <time> — Cooldown time
        if prefix == "18" and n >= 4:
            val = self._parse_time(digits[2:])
            if val is None:
                return None
            cfg.cooldown_time = val
            cfg.save()
            return (CMD_OK, f"cooldown={val}")

        # ## 1 9 <nn> — Min TX time (1/10 s)
        if prefix == "19" and n == 4:
            try:
                val = int(digits[2:4]) / 10.0
                cfg.min_tx_time = val
                cfg.save()
                return (CMD_OK, f"min_tx={val}")
            except ValueError:
                return (CMD_ERR, "bad_value")

        # ## 2 n — Play announcement
        if prefix[0] == '2' and n == 2:
            slot = int(prefix[1])
            return ("PLAY_ANN", str(slot))

        # ## 3 n — Record announcement
        if prefix[0] == '3' and n == 2:
            slot = int(prefix[1])
            return ("REC_ANN", str(slot))

        # ## 4 n — Erase announcement
        if prefix[0] == '4' and n == 2:
            slot = int(prefix[1])
            return ("ERASE_ANN", str(slot))

        # ## 5 n <time> — Announcement interval
        if prefix[0] == '5' and n >= 4:
            slot = int(prefix[1])
            val = self._parse_time(digits[2:])
            if val is None:
                return None
            cfg.announcement_timers[str(slot)]["interval"] = val
            cfg.save()
            return (CMD_OK, f"ann{slot}_interval={val}")

        # ## 6 n <time> — Announcement time offset
        if prefix[0] == '6' and n >= 4:
            slot = int(prefix[1])
            val = self._parse_time(digits[2:])
            if val is None:
                return None
            cfg.announcement_timers[str(slot)]["offset"] = val
            cfg.save()
            return (CMD_OK, f"ann{slot}_offset={val}")

        # ## 7 0 / ## 7 1 — Repeater off/on
        if digits == "70":
            cfg.repeater_on = False
            cfg.save()
            return (CMD_OK, "repeater_off")
        if digits == "71":
            cfg.repeater_on = True
            cfg.save()
            return (CMD_OK, "repeater_on")

        # ## 7 2 / ## 7 3 — Voicemail off/on
        if digits == "72":
            cfg.voicemail_on = False
            cfg.save()
            return (CMD_OK, "vm_off")
        if digits == "73":
            cfg.voicemail_on = True
            cfg.save()
            return (CMD_OK, "vm_on")

        # ## 7 4 — Erase all voicemail
        if digits == "74":
            return ("VM_ERASE_ALL", "vm_erase_all")

        # ## 7 5 <0/1> — Responder mode
        if prefix == "75" and n == 3:
            cfg.responder_mode = (digits[2] == '1')
            cfg.save()
            return (CMD_OK, f"responder={'on' if cfg.responder_mode else 'off'}")

        # ## 7 7 <0/1> — Courtesy tone delay
        if prefix == "77" and n == 3:
            cfg.courtesy_tone_delay = (digits[2] == '1')
            cfg.save()
            return (CMD_OK, "ctone_delay")

        # ## 7 8 <code> — Pager activation code (0-6 chars)
        if prefix == "78" and n >= 2:
            code = digits[2:]
            if len(code) <= 6:
                cfg.pager_code = code
                cfg.save()
                return (CMD_OK, f"pager_code={code}")
            return (CMD_ERR, "code_too_long")

        # ## 7 9 <nn> — Squelch tail suppression (1/75 s)
        if prefix == "79" and n == 4:
            try:
                val = int(digits[2:4]) / 75.0
                cfg.squelch_tail_supp = val
                cfg.save()
                return (CMD_OK, f"tail_supp={val:.3f}s")
            except ValueError:
                return (CMD_ERR, "bad_value")

        # ## 8 0 <n> — CW ID on/off/cleanup
        if prefix == "80" and n == 3:
            sub = digits[2]
            if sub == '0':
                cfg.cw_id_on = False
            elif sub == '1':
                cfg.cw_id_on = True
            elif sub == '2':
                cfg.cw_cleanup_id = False
            elif sub == '3':
                cfg.cw_cleanup_id = True
            else:
                return (CMD_ERR, "bad_value")
            cfg.save()
            return (CMD_OK, f"cw_id_sub={sub}")

        # ## 8 1 <nn> — CW speed
        if prefix == "81" and n == 4:
            try:
                spd = int(digits[2:4])
                cfg.cw_speed = min(99, max(0, spd))
                cfg.save()
                return (CMD_OK, f"cw_speed={spd}")
            except ValueError:
                return (CMD_ERR, "bad_value")

        # ## 8 2 <nn...> — Set CW ID text (paires de 2 digits)
        if prefix == "82" and n >= 4 and (n - 2) % 2 == 0:
            from .tones import sr1_decode_cwtext
            text = sr1_decode_cwtext(digits[2:])
            cfg.cw_id_text = text[:12]
            cfg.save()
            return (CMD_OK, f"cw_id='{text}'")

        # ## 8 3 <time> — CW ID timer
        if prefix == "83" and n >= 4:
            val = self._parse_time(digits[2:])
            if val is None:
                return None
            cfg.cw_id_timer = val
            cfg.save()
            return (CMD_OK, f"cw_timer={val}")

        # ## 8 4 <time> — CW inhibit timer
        if prefix == "84" and n >= 4:
            val = self._parse_time(digits[2:])
            if val is None:
                return None
            cfg.cw_inhibit_timer = val
            cfg.save()
            return (CMD_OK, f"cw_inhibit={val}")

        # ## 8 5 <time> — Voice ID inhibit timer
        if prefix == "85" and n >= 4:
            val = self._parse_time(digits[2:])
            if val is None:
                return None
            cfg.voice_id_inhibit = val
            cfg.save()
            return (CMD_OK, f"voice_inhibit={val}")

        # ## 8 6 — Check CW ID (play it)
        if digits == "86":
            return ("PLAY_CW_ID", "check_cw")

        # ## 9 1 — Identify firmware revision
        if digits == "91":
            return ("FW_ID", "fw_id")

        # ## 9 2 <nn> — TX delay (1/10 s)
        if prefix == "92" and n == 4:
            try:
                val = int(digits[2:4]) / 10.0
                cfg.tx_delay = val
                cfg.save()
                return (CMD_OK, f"tx_delay={val}")
            except ValueError:
                return (CMD_ERR, "bad_value")

        # ## 9 3 <x><y> — Set output pin
        if prefix == "93" and n == 4:
            pin = digits[2]
            state = digits[3] == '1'
            if pin in ("0", "1"):
                cfg.output_pins[pin] = state
                cfg.save()
                self.rep._set_output_pin(int(pin), state)
                return (CMD_OK, f"pin{pin}={'on' if state else 'off'}")
            return (CMD_ERR, "bad_pin")

        # ## 9 5 — Battery (N/A)
        if digits == "95":
            return (CMD_NEG, "no_battery")

        # ## 9 6 — Usage count
        if digits == "96":
            return ("USAGE_COUNT", str(cfg.usage_count))

        # ## 9 7 — Load defaults
        if digits == "97":
            return ("RESET_DEFAULTS", "reset")

        # ## 9 8 — Calibration tone
        if digits == "98":
            return ("CAL_TONE", "cal_tone")

        # ## 9 9 — Reset
        if digits == "99":
            return ("SOFT_RESET", "reset")

        # ## 1 0 7 <n> — Input gain
        if prefix == "10" and n >= 3:
            rest = digits[2:]
            if rest.startswith("7") and len(rest) == 2:
                try:
                    g = int(rest[1])
                    if g in (0, 1, 2):
                        cfg.input_gain = g
                        cfg.save()
                        return (CMD_OK, f"gain={[1,2,4][g]}x")
                except ValueError:
                    pass
                return (CMD_ERR, "bad_gain")
            return None   # encore en saisie

        # ## 1 0 9 <n> — COR/squelch mode
        if prefix == "10" and n >= 3:
            rest = digits[2:]
            if rest.startswith("9") and len(rest) == 2:
                try:
                    m = int(rest[1])
                    if m in (0, 1, 2):
                        cfg.cor_mode = m
                        cfg.save()
                        return (CMD_OK, f"squelch_mode={m}")
                except ValueError:
                    pass
                return (CMD_ERR, "bad_mode")

        # Commandes trop longues sans correspondance
        if n > 8:
            return (CMD_ERR, "unknown_command")

        return None   # encore en saisie

    def _parse_time(self, s: str) -> Optional[float]:
        """
        Interprète le format temps Parlex :
        - 2 digits → minutes
        - 3 digits → secondes
        - 4 digits → HHMM (heures+minutes)
        Retourne None si format invalide ou encore en saisie.
        """
        n = len(s)
        if n < 2:
            return None
        try:
            if n == 2:
                return float(int(s)) * 60.0
            elif n == 3:
                return float(int(s))
            elif n == 4:
                hh = int(s[:2])
                mm = int(s[2:])
                return float(hh * 3600 + mm * 60)
            elif n > 4:
                return None  # invalide
        except ValueError:
            pass
        return None
