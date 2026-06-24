"""
announcements.py — Moteur de diffusion des annonces temporisées (ADS-SR1 §##5n/##6n)
Un timer par slot (0-9). Tourne en daemon thread.
"""
from __future__ import annotations
import logging
import threading
import time
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .repeater import SimplexRepeater

log = logging.getLogger("ann")


class AnnouncementEngine:

    def __init__(self, repeater: "SimplexRepeater"):
        self.rep = repeater
        self._timers: dict[int, threading.Timer] = {}
        self._start_times: dict[int, float] = {}
        self._running = False
        self._lock = threading.Lock()
        self._epoch = time.monotonic()   # référence pour les offsets

    def start(self) -> None:
        self._running = True
        self._reschedule_all()

    def stop(self) -> None:
        self._running = False
        with self._lock:
            for t in self._timers.values():
                t.cancel()
            self._timers.clear()

    def refresh_slot(self, slot: int) -> None:
        """Appelé quand l'interval/offset d'un slot change."""
        with self._lock:
            if slot in self._timers:
                self._timers[slot].cancel()
                del self._timers[slot]
        self._schedule_slot(slot)

    def _reschedule_all(self) -> None:
        for slot in range(10):
            self._schedule_slot(slot)

    def _schedule_slot(self, slot: int) -> None:
        if not self._running:
            return
        cfg = self.rep.config
        ann_cfg = cfg.announcement_timers.get(str(slot), {})
        interval = float(ann_cfg.get("interval", 0.0))
        if interval <= 0:
            return

        offset = float(ann_cfg.get("offset", 0.0))
        now = time.monotonic()
        elapsed = now - self._epoch

        # Calcule le prochain déclenchement en tenant compte de l'offset
        cycle_pos = elapsed % interval
        delay = (offset % interval) - cycle_pos
        if delay <= 0:
            delay += interval

        t = threading.Timer(delay, self._fire, args=(slot,))
        t.daemon = True
        with self._lock:
            self._timers[slot] = t
        t.start()
        log.debug("Slot %d scheduled in %.1fs", slot, delay)

    def _fire(self, slot: int) -> None:
        if not self._running:
            return
        log.info("Annonce slot %d", slot)
        self.rep._play_announcement(slot)
        self._schedule_slot(slot)
