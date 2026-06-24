"""
repeater.py — Machine d'états principale du relais simplex ADS-SR1

États:
  IDLE        : écoute, détection VOX/COR, décodage DTMF
  RECORDING   : enregistrement de la transmission entrante
  STANDBY_TX  : stand-by tone/message avant retransmission
  TRANSMITTING: retransmission du message enregistré
  COOLDOWN    : pause après timeout de transmission
  VM_RECORD   : enregistrement d'un message voicemail
  VM_PLAYBACK : lecture voicemail (navigation DTMF)
  REC_ANN     : enregistrement d'une annonce
  CAL_TONE    : émission du ton de calibration
"""
from __future__ import annotations
import logging
import threading
import time
from enum import Enum, auto
from typing import Optional

from .config import RepeaterConfig, SQ_VOX
from .audio import ALSACapture, ALSAPlayback, VOXDetector, Recorder, QSOStats, rms_level
from .dtmf import DTMFDecoder
from .ptt import PTTController, CORMonitor
from .storage import AnnouncementStore, VoicemailStore, SayAgainBuffer
from .tones import (
    courtesy_tone, beep_ok, beep_negative, beep_error, beep_locked,
    beep_standby, calibration_tone, generate_cw, pcm_to_bytes, bytes_to_float32,
    sr1_decode_cwtext,
)
from .commands import CommandParser, CMD_OK, CMD_NEG, CMD_ERR, CMD_LOCKED
from .announcements import AnnouncementEngine

log = logging.getLogger("repeater")

FW_REVISION = "Mk II rev A"


class State(Enum):
    IDLE        = auto()
    RECORDING   = auto()
    TRANSMITTING = auto()
    COOLDOWN    = auto()
    VM_RECORD   = auto()
    VM_PLAYBACK = auto()
    REC_ANN     = auto()
    CAL_TONE    = auto()


class SimplexRepeater:

    def __init__(self, config: Optional[RepeaterConfig] = None):
        self.config = config or RepeaterConfig.load()

        # ── Sous-systèmes ───────────────────────────────────────────────────
        self.ptt       = PTTController(self.config.serial_port)
        self.cor       = CORMonitor(self.config.cor_mode, self.config.cor_gpio)
        self.playback  = ALSAPlayback(self.config.alsa_playback, self.config.sample_rate)
        self.audio_vox = VOXDetector(
            threshold=self.config.vox_threshold,
            timeout=self.config.vox_timeout,
            sample_rate=self.config.sample_rate,
        )
        self.recorder  = Recorder(max_seconds=self.config.max_tx_time or 600.0,
                                   sample_rate=self.config.sample_rate)
        self.dtmf      = DTMFDecoder(self.config.sample_rate)
        self.stats     = QSOStats()
        self.cmd_parser = CommandParser(self)
        self.ann_store  = AnnouncementStore()
        self.vm_store   = VoicemailStore()
        self.say_again  = SayAgainBuffer()
        self.ann_engine = AnnouncementEngine(self)

        # ── Capture ALSA ────────────────────────────────────────────────────
        self.capture = ALSACapture(
            self.config.alsa_capture,
            callback=self._on_audio_chunk,
            sample_rate=self.config.sample_rate,
        )

        # ── État interne ────────────────────────────────────────────────────
        self._state = State.IDLE
        self._state_lock = threading.Lock()
        self._running = False
        self._last_cmd_time: float = 0.0
        self._last_cw_time:  float = 0.0
        self._last_voice_id_time: float = 0.0
        self._auto_off_deadline: float = 0.0
        self._cooldown_until: float = 0.0
        self._recording_start: float = 0.0
        self._pager_armed = False
        self._pager_heard = False

        # Voicemail navigation
        self._vm_position: int = 0

        # Announcement recording
        self._ann_rec_slot: int = 0

        # Observers TUI
        self._state_observers: list = []
        self._log_observers:   list = []

        # CW ID timed thread
        self._cw_timer: Optional[threading.Timer] = None

        log.info("SimplexRepeater initialisé (port=%s)", self.config.serial_port)

    # ─── Public ───────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._running = True
        self.ptt.open()
        self.cor.open()
        self.capture.start()
        self.ann_engine.start()
        self._schedule_cw_timer()
        self._emit_log("Relais simplex démarré")
        log.info("Relais simplex démarré")

    def stop(self) -> None:
        self._running = False
        self.ptt.set_ptt(False)
        self.capture.stop()
        self.ann_engine.stop()
        if self._cw_timer:
            self._cw_timer.cancel()
        self.ptt.close()
        self.cor.close()
        self._emit_log("Relais arrêté")

    def add_state_observer(self, cb) -> None:
        self._state_observers.append(cb)

    def add_log_observer(self, cb) -> None:
        self._log_observers.append(cb)

    # ─── Callback capture audio ───────────────────────────────────────────────

    def _on_audio_chunk(self, audio: "np.ndarray") -> None:
        if not self._running:
            return

        # Pre-buffer roulant (identique Castanara) — mis à jour en permanence
        self.recorder.update_prebuffer(audio)

        # DTMF décodage sur tous les états
        self.dtmf.feed(audio)
        digits = self.dtmf.pop_digits()
        for d in digits:
            self._on_dtmf(d)

        state = self._state

        if state == State.IDLE:
            self._idle_audio(audio)
        elif state == State.RECORDING:
            self._recording_audio(audio)
        elif state == State.VM_RECORD:
            self._vm_record_audio(audio)
        elif state == State.REC_ANN:
            self._ann_record_audio(audio)

    def _idle_audio(self, audio: "np.ndarray") -> None:
        cfg = self.config

        # Auto-off timer check
        if cfg.auto_off_timer > 0 and cfg.repeater_on:
            if time.monotonic() > self._auto_off_deadline:
                cfg.repeater_on = False
                self._emit_log("Auto-off: repeater désactivé")

        # Cooldown actif
        if self._cooldown_until and time.monotonic() < self._cooldown_until:
            return

        # Mode VOX
        if cfg.cor_mode == SQ_VOX:
            vox_open = self.audio_vox.feed(audio)
            if vox_open:
                self._start_recording(audio)
        else:
            # COR mode
            cor_open = self.cor.read()
            if cor_open:
                self._start_recording(audio)

    def _start_recording(self, first_chunk: "np.ndarray") -> None:
        cfg = self.config
        # Stand-by tone AVANT enregistrement (si activé)
        if cfg.standby_msg >= 0:
            threading.Thread(target=self._tx_standby, daemon=True).start()

        self._change_state(State.RECORDING)
        # recorder.start() inclut déjà le pre-buffer (Castanara pattern)
        self.recorder.start()
        self.recorder.feed(first_chunk)
        self._recording_start = time.monotonic()
        self.config.usage_count += 1
        self._emit_log("Enregistrement démarré")
        log.info("Enregistrement démarré")

        # Réarme VOX pour détecter la fin
        self.audio_vox.on_close(self._on_vox_close)

    def _recording_audio(self, audio: "np.ndarray") -> None:
        cfg = self.config
        full = not self.recorder.feed(audio)

        # COR mode : surveiller la fin du signal
        if cfg.cor_mode != SQ_VOX:
            if not self.cor.read():
                self._end_recording()
                return

        # Max TX time (identique Castanara max_rx_duration)
        if cfg.max_tx_time > 0:
            elapsed = time.monotonic() - self._recording_start
            if elapsed >= cfg.max_tx_time:
                self._emit_log(f"Timeout enregistrement ({cfg.max_tx_time}s)")
                self._end_recording(timeout=True)

        if full:
            self._emit_log("Mémoire pleine — retransmission")
            self._end_recording()

    def _on_vox_close(self) -> None:
        if self._state == State.RECORDING:
            self._end_recording()

    def _end_recording(self, timeout: bool = False) -> None:
        audio = self.recorder.stop()   # ndarray float32 avec pre-buffer inclus
        duration = len(audio) / self.config.sample_rate

        # Squelch tail suppression
        tail = self.config.squelch_tail_supp
        if tail > 0 and len(audio) > 0:
            cut = int(tail * self.config.sample_rate)
            audio = audio[:-cut] if len(audio) > cut else audio

        self._emit_log(f"Enregistrement terminé ({duration:.1f}s)")
        log.info("Enregistrement terminé (%.1fs, timeout=%s)", duration, timeout)

        # Min TX time check (identique Castanara min_rx_duration)
        if duration < self.config.min_tx_time:
            self._emit_log(f"Trop court ({duration:.1f}s < {self.config.min_tx_time}s) — ignoré")
            self._change_state(State.IDLE)
            return

        # Say-again buffer
        if self.config.say_again_on:
            self.say_again.store(audio)

        if self.config.responder_mode:
            self._change_state(State.IDLE)
            threading.Thread(target=self._tx_beepback, daemon=True).start()
        else:
            self._change_state(State.TRANSMITTING)
            threading.Thread(
                target=self._retransmit,
                args=(audio, timeout),
                daemon=True
            ).start()

        if timeout and self.config.cooldown_time > 0:
            self._cooldown_until = time.monotonic() + self.config.cooldown_time
            self._emit_log(f"Cooldown {self.config.cooldown_time}s")

    # ─── Retransmission ───────────────────────────────────────────────────────

    def _retransmit(self, audio: "np.ndarray", was_timeout: bool = False) -> None:
        """
        Retransmission avec tx_gain flottant (comme Castanara) + CTCSS optionnel.
        audio: float32 ndarray (avec pre-buffer inclus).
        """
        import numpy as np
        cfg = self.config
        tx_start = time.time()

        # Input gain (##107) : 1x / 2x / 4x
        if cfg.input_gain > 0:
            audio = audio * [1, 2, 4][cfg.input_gain]

        # tx_gain multiplicateur flottant (identique Castanara tx_gain=3.5)
        audio = np.clip(audio * cfg.tx_gain, -1.0, 1.0).astype(np.float32)

        # CTCSS optionnel (hérité Castanara)
        ctcss = cfg.ctcss_enabled
        ctcss_freq = cfg.ctcss_freq

        # Preamble (announcement 1) avant PTT
        if cfg.voice_preamble and self.ann_store.exists(1):
            ann = self.ann_store.load(1)
            if ann is not None:
                ann_f32 = np.frombuffer(ann, dtype=np.int16).astype(np.float32) / 32768.0
                self._key_up()
                self.playback.play_numpy(ann_f32, gain=1.0, ctcss=ctcss, ctcss_freq=ctcss_freq)
                self._key_down()
                time.sleep(0.1)

        # PTT + délai (identique Castanara : ptt.set_ptt(True) + sleep(0.3))
        self._key_up()
        time.sleep(cfg.tx_delay)

        self.playback.play_numpy(audio, gain=1.0, ctcss=ctcss, ctcss_freq=ctcss_freq)

        # Courtesy tone (Roger beep dans Castanara = style 6)
        if cfg.courtesy_tone > 0:
            if cfg.courtesy_tone_delay:
                time.sleep(1.0)
            ct = courtesy_tone(cfg.courtesy_tone)
            self.playback.play_numpy(ct, gain=1.0, ctcss=ctcss, ctcss_freq=ctcss_freq)

        self._key_down()

        # Stats QSO (identique Castanara)
        self.stats.record_tx(time.time() - tx_start)

        # Voice ID auto-response
        self._maybe_voice_id()

        # CW ID auto-response
        if cfg.cw_responder_on and cfg.cw_id_text:
            now = time.monotonic()
            if now - self._last_cw_time >= cfg.cw_inhibit_timer:
                self._last_cw_time = now
                threading.Thread(target=self._tx_cw_id, daemon=True).start()

        self._change_state(State.IDLE)
        self._reset_auto_off()

    def _tx_standby(self) -> None:
        import numpy as np
        cfg = self.config
        self._key_up()
        if cfg.standby_msg == 0:
            self.playback.play_numpy(beep_standby())
        elif cfg.standby_msg > 0 and self.ann_store.exists(cfg.standby_msg):
            ann = self.ann_store.load(cfg.standby_msg)
            if ann is not None:
                f32 = np.frombuffer(ann, dtype=np.int16).astype(np.float32) / 32768.0
                self.playback.play_numpy(f32)
        self._key_down()

    def _tx_beepback(self) -> None:
        self._key_up()
        self.playback.play_numpy(courtesy_tone(self.config.courtesy_tone))
        self._key_down()

    def _tx_cw_id(self) -> None:
        cfg = self.config
        # Utilise cw_wpm direct si disponible, sinon mappe cw_speed (0-99 → 5-35 WPM)
        wpm = cfg.cw_wpm if cfg.cw_wpm > 0 else max(5, 5 + int(cfg.cw_speed * 30 / 99))
        cw_audio = generate_cw(cfg.cw_id_text, speed=cfg.cw_speed,
                                freq=float(cfg.cw_freq))
        self._key_up()
        self.playback.play_numpy(cw_audio)
        self._key_down()
        log.info("CW ID émis : %s", cfg.cw_id_text)

    # ─── PTT helpers ──────────────────────────────────────────────────────────

    def _key_up(self) -> None:
        self.ptt.set_ptt(True)
        self._emit_log("PTT ON")

    def _key_down(self) -> None:
        self.ptt.set_ptt(False)
        self._emit_log("PTT OFF")

    # ─── DTMF handler ─────────────────────────────────────────────────────────

    def _on_dtmf(self, digit: str) -> None:
        log.debug("DTMF: %s (état=%s)", digit, self._state.name)
        self._emit_log(f"DTMF: {digit}")

        result = self.cmd_parser.feed(digit)
        if result is None:
            return

        tone_type, msg = result
        self._dispatch_command(tone_type, msg)

    def _dispatch_command(self, tone_type: str, msg: str) -> None:
        import numpy as np
        cfg = self.config

        def respond(tone_fn):
            self._key_up()
            self.playback.play_numpy(tone_fn())
            self._key_down()

        if tone_type == "ABORT":
            if self._state == State.RECORDING:
                self.recorder.stop()
            self._change_state(State.IDLE)
            return

        elif tone_type == "SAY_AGAIN":
            audio = self.say_again.get()
            if audio:
                self._emit_log("Say-again")
                threading.Thread(
                    target=self._retransmit, args=(audio,), daemon=True
                ).start()
            else:
                respond(beep_negative)

        elif tone_type == "VM_RECORD":
            self._change_state(State.VM_RECORD)
            self.recorder.start()
            respond(beep_ok)

        elif tone_type == "VM_RETRIEVE":
            n = self.vm_store.count()
            if n == 0:
                respond(beep_negative)
            else:
                self._vm_position = 0
                self._change_state(State.VM_PLAYBACK)
                threading.Thread(target=self._play_vm_current, daemon=True).start()

        elif tone_type == "VM_ERASE_ALL":
            self.vm_store.erase_all()
            respond(beep_ok)

        elif tone_type == "PLAY_ANN":
            slot = int(msg)
            ann = self.ann_store.load(slot)
            if ann is not None:
                self._emit_log(f"Play annonce {slot}")
                f32 = np.frombuffer(ann, dtype=np.int16).astype(np.float32) / 32768.0
                self._key_up()
                self.playback.play_numpy(f32)
                self._key_down()
            else:
                respond(beep_negative)

        elif tone_type == "REC_ANN":
            slot = int(msg)
            self._ann_rec_slot = slot
            self._change_state(State.REC_ANN)
            self.recorder.start()
            respond(beep_ok)
            self._emit_log(f"Enregistrement annonce {slot}")

        elif tone_type == "ERASE_ANN":
            slot = int(msg)
            ok = self.ann_store.erase(slot)
            respond(beep_ok if ok else beep_negative)

        elif tone_type == "PLAY_CW_ID":
            threading.Thread(target=self._tx_cw_id, daemon=True).start()

        elif tone_type == "FW_ID":
            cw = generate_cw(f"SR1 {FW_REVISION}", speed=cfg.cw_speed, freq=float(cfg.cw_freq))
            self._key_up()
            self.playback.play_numpy(cw)
            self._key_down()

        elif tone_type == "CAL_TONE":
            self._change_state(State.CAL_TONE)
            self._key_up()
            self.playback.play_numpy(calibration_tone(3.0))
            self._key_down()
            self._change_state(State.IDLE)

        elif tone_type == "USAGE_COUNT":
            cw = generate_cw(str(cfg.usage_count), speed=cfg.cw_speed, freq=float(cfg.cw_freq))
            self._key_up()
            self.playback.play_numpy(cw)
            self._key_down()

        elif tone_type == "RESET_DEFAULTS":
            cfg.reset_defaults()
            cfg.save()
            self._emit_log("Reset usine")
            respond(beep_ok)

        elif tone_type == "SOFT_RESET":
            self._emit_log("Soft reset")
            respond(beep_ok)
            # Redémarre la machine d'états
            self._change_state(State.IDLE)

        elif tone_type == "REPEATER_ON":
            cfg.repeater_on = True
            self._reset_auto_off()
            respond(beep_ok)

        elif tone_type == "REPEATER_OFF":
            cfg.repeater_on = False
            respond(beep_ok)

        elif tone_type in (CMD_OK, CMD_NEG, CMD_ERR, CMD_LOCKED):
            resp_map = {
                CMD_OK:     beep_ok,
                CMD_NEG:    beep_negative,
                CMD_ERR:    beep_error,
                CMD_LOCKED: beep_locked,
            }
            respond(resp_map[tone_type])
            if tone_type == CMD_OK:
                self._reset_auto_off()

        log.info("Commande [%s] %s", tone_type, msg)

    # ─── VM record / playback ────────────────────────────────────────────────

    def _vm_record_audio(self, audio: "np.ndarray") -> None:
        cfg = self.config
        if cfg.cor_mode == SQ_VOX:
            if not self.audio_vox.feed(audio):
                self._end_vm_record()
        else:
            if not self.cor.read():
                self._end_vm_record()
        self.recorder.feed(audio)

    def _end_vm_record(self) -> None:
        import numpy as np
        audio = self.recorder.stop()
        if len(audio) > 0:
            # Stocker en WAV bytes (PCM S16_LE)
            pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
            ok = self.vm_store.add(pcm)
            self._key_up()
            self.playback.play_numpy(beep_ok() if ok else beep_negative())
            if ok:
                self.playback.play_numpy(audio)
            self._key_down()
            self._emit_log(f"Voicemail enregistrée ({self.vm_store.count()} messages)")
        self._change_state(State.IDLE)

    def _play_vm_current(self) -> None:
        import numpy as np
        audio = self.vm_store.get(self._vm_position)
        if audio is not None:
            f32 = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
            self._key_up()
            self.playback.play_numpy(f32)
            self._key_down()
        else:
            self._key_up()
            self.playback.play_numpy(beep_negative())
            self._key_down()
            self._change_state(State.IDLE)

    # DTMF navigation voicemail (appelé par _on_dtmf en état VM_PLAYBACK)
    def _vm_nav(self, digit: str) -> None:
        level = self.config.tx_audio_level / 99.0
        if digit == '2':   # repeat
            threading.Thread(target=self._play_vm_current, daemon=True).start()
        elif digit == '3':  # next
            if self._vm_position < self.vm_store.count() - 1:
                self._vm_position += 1
                threading.Thread(target=self._play_vm_current, daemon=True).start()
            else:
                self._key_up()
                self.playback.play_numpy(beep_negative(), level=level)
                self._key_down()
                self._change_state(State.IDLE)
        elif digit == '1':  # prev
            if self._vm_position > 0:
                self._vm_position -= 1
                threading.Thread(target=self._play_vm_current, daemon=True).start()
        elif digit == '0':  # erase + next
            self.vm_store.erase(self._vm_position)
            if self.vm_store.count() == 0:
                self._change_state(State.IDLE)
            else:
                self._vm_position = min(self._vm_position, self.vm_store.count() - 1)
                threading.Thread(target=self._play_vm_current, daemon=True).start()
        elif digit == '*':  # exit
            self._change_state(State.IDLE)

    # ─── Annonce recording ───────────────────────────────────────────────────

    def _ann_record_audio(self, audio: "np.ndarray") -> None:
        cfg = self.config
        if cfg.cor_mode == SQ_VOX:
            if not self.audio_vox.feed(audio):
                self._end_ann_record()
        else:
            if not self.cor.read():
                self._end_ann_record()
        self.recorder.feed(audio)

    def _end_ann_record(self) -> None:
        import numpy as np
        audio = self.recorder.stop()
        slot = self._ann_rec_slot
        if len(audio) > 0:
            pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
            self.ann_store.save(slot, pcm)
            self._key_up()
            self.playback.play_numpy(beep_ok())
            self.playback.play_numpy(audio)
            self._key_down()
            self._emit_log(f"Annonce {slot} enregistrée")
            self.ann_engine.refresh_slot(slot)
        self._change_state(State.IDLE)

    # ─── Annonce playback (appelé par announcement engine) ───────────────────

    def _play_announcement(self, slot: int) -> None:
        import numpy as np
        if self._state != State.IDLE:
            return
        ann = self.ann_store.load(slot)
        if not ann:
            return
        f32 = np.frombuffer(ann, dtype=np.int16).astype(np.float32) / 32768.0
        self._key_up()
        time.sleep(self.config.tx_delay)
        self.playback.play_numpy(f32)
        self._key_down()
        log.info("Annonce slot %d émise", slot)

    # ─── Voice ID ────────────────────────────────────────────────────────────

    def _maybe_voice_id(self) -> None:
        import numpy as np
        cfg = self.config
        if not cfg.voice_id_on:
            return
        now = time.monotonic()
        if now - self._last_voice_id_time < cfg.voice_id_inhibit:
            return
        self._last_voice_id_time = now

        if cfg.voice_id_rotate:
            slots = self.ann_store.list_slots()
            exclude = [1] if cfg.voice_preamble else []
            slots = [s for s in slots if s not in exclude]
            if not slots:
                return
            slot = slots[int(now / cfg.voice_id_inhibit) % len(slots)]
        else:
            slot = 0

        ann = self.ann_store.load(slot)
        if not ann:
            return
        f32 = np.frombuffer(ann, dtype=np.int16).astype(np.float32) / 32768.0
        self._key_up()
        self.playback.play_numpy(f32)
        self._key_down()

    # ─── CW ID timed ─────────────────────────────────────────────────────────

    def _schedule_cw_timer(self) -> None:
        cfg = self.config
        if not cfg.cw_id_on or not cfg.cw_id_text:
            return
        delay = cfg.cw_id_timer
        self._cw_timer = threading.Timer(delay, self._cw_timer_fired)
        self._cw_timer.daemon = True
        self._cw_timer.start()

    def _cw_timer_fired(self) -> None:
        cfg = self.config
        if not self._running:
            return
        if self._state == State.IDLE:
            threading.Thread(target=self._tx_cw_id, daemon=True).start()
            self._last_cw_time = time.monotonic()
        if not cfg.cw_cleanup_id:
            self._schedule_cw_timer()

    # ─── Auto-off ────────────────────────────────────────────────────────────

    def _reset_auto_off(self) -> None:
        if self.config.auto_off_timer > 0:
            self._auto_off_deadline = time.monotonic() + self.config.auto_off_timer

    # ─── Output pins (GPIO) ──────────────────────────────────────────────────

    def _set_output_pin(self, pin: int, state: bool) -> None:
        try:
            import RPi.GPIO as GPIO
            # Pins JP2 ADS-SR1 : pin 0 → GPIO 23, pin 1 → GPIO 24 (à adapter)
            GPIO_MAP = {0: 23, 1: 24}
            if pin in GPIO_MAP:
                GPIO.setmode(GPIO.BCM)
                GPIO.setup(GPIO_MAP[pin], GPIO.OUT)
                GPIO.output(GPIO_MAP[pin], GPIO.HIGH if state else GPIO.LOW)
        except Exception as e:
            log.warning("Output pin %d : %s", pin, e)

    # ─── State machine ───────────────────────────────────────────────────────

    def _change_state(self, new_state: State) -> None:
        with self._state_lock:
            old = self._state
            self._state = new_state
        if old != new_state:
            log.debug("État: %s → %s", old.name, new_state.name)
            for obs in self._state_observers:
                try:
                    obs(new_state)
                except Exception:
                    pass

    def get_state(self) -> State:
        return self._state

    # ─── Observers ───────────────────────────────────────────────────────────

    def _emit_log(self, msg: str) -> None:
        log.info(msg)
        for obs in self._log_observers:
            try:
                obs(msg)
            except Exception:
                pass
