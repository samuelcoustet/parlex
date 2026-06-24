"""
ptt.py — Contrôle PTT via AIOC (DTR série) + lecture COR/squelch (GPIO optionnel)
Flags série identiques au projet Castanara : dsrdtr=False, rtscts=False
"""
from __future__ import annotations
import logging
import threading
import time
from typing import Optional

log = logging.getLogger("ptt")


class PTTController:
    """
    Contrôle PTT radio via DTR du port série AIOC.
    Identique au PTTController Castanara (dsrdtr=False, rtscts=False, baudrate=115200).
    Thread-safe.
    """

    def __init__(self, serial_port: str = "/dev/ttyACM0", baudrate: int = 115200):
        self.serial_port = serial_port
        self.baudrate = baudrate
        self._ser = None
        self._lock = threading.Lock()
        self._active = False

    def open(self) -> bool:
        try:
            import serial
            self._ser = serial.Serial(
                self.serial_port,
                self.baudrate,
                timeout=1,
                dsrdtr=False,    # identique Castanara
                rtscts=False,    # identique Castanara
            )
            self._ser.dtr = False
            self._ser.rts = False
            log.info("PTT serial ouvert : %s", self.serial_port)
            return True
        except Exception as e:
            log.error("PTT serial erreur : %s", e)
            return False

    def close(self) -> None:
        with self._lock:
            if self._ser:
                try:
                    self._ser.dtr = False
                    self._ser.close()
                except Exception:
                    pass
                self._ser = None
                self._active = False

    def set_ptt(self, state: bool) -> None:
        with self._lock:
            if self._ser and self._ser.is_open:
                self._ser.dtr = state
                self._active = state
                log.debug("PTT %s", "ON" if state else "OFF")
            else:
                log.warning("PTT non disponible (port fermé)")

    def is_active(self) -> bool:
        return self._active

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *_):
        self.close()


class CORMonitor:
    """
    Lecture COR/squelch via GPIO BCM (RPi), ou VOX (mode sans GPIO).
    """

    def __init__(self, mode: int = 0, gpio_pin: Optional[int] = None):
        self.mode = mode       # 0=VOX, 1=COR_HI, 2=COR_LO
        self.gpio_pin = gpio_pin
        self._gpio_ok = False

    def open(self) -> bool:
        if self.mode == 0 or self.gpio_pin is None:
            return True
        try:
            import RPi.GPIO as GPIO
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.gpio_pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
            self._gpio_ok = True
            log.info("COR GPIO pin %d (mode %d)", self.gpio_pin, self.mode)
            return True
        except Exception as e:
            log.warning("GPIO non disponible : %s", e)
            return False

    def read(self) -> bool:
        """Retourne True si squelch ouvert (signal présent)."""
        if not self._gpio_ok or self.gpio_pin is None:
            return False
        try:
            import RPi.GPIO as GPIO
            raw = GPIO.input(self.gpio_pin)
            return bool(raw) if self.mode == 1 else not bool(raw)
        except Exception:
            return False

    def close(self) -> None:
        if self._gpio_ok:
            try:
                import RPi.GPIO as GPIO
                GPIO.cleanup(self.gpio_pin)
            except Exception:
                pass
