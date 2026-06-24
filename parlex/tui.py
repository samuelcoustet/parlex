"""
tui.py — TUI complète pour le relais simplex
Basée sur Textual ≥ 0.50. Fallback curses si textual absent.

Layout :
  Header : titre
  Barre état : état machine | PTT | VU-mètre | DTMF live | stats QSO
  TabbedContent :
    [Relais]     — paramètres store-and-forward
    [Audio]      — VOX, niveaux, timing
    [ID / CW]    — CW ID, Voice ID, CTCSS
    [Voicemail]  — inventaire avec durées + paramètres
    [Annonces]   — 10 slots : interval, offset, état
    [Sécurité]   — lock, codes, auto-lock
    [Castanara]  — surveillance relais Castanara via HTTP
    [Journal]    — log événements temps réel
  Footer : raccourcis clavier
"""
from __future__ import annotations
import time
import threading
from collections import deque
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .repeater import SimplexRepeater, State

# ─── Couleurs état ────────────────────────────────────────────────────────────
STATE_COLORS = {
    "IDLE":         "green",
    "RECORDING":    "yellow",
    "TRANSMITTING": "cyan",
    "COOLDOWN":     "red",
    "VM_RECORD":    "magenta",
    "VM_PLAYBACK":  "magenta",
    "REC_ANN":      "blue",
    "CAL_TONE":     "white",
}

# ─── Textual TUI ──────────────────────────────────────────────────────────────
try:
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical, ScrollableContainer, Container
    from textual.widgets import (
        Header, Footer, Static, Label, Switch, Input, Select,
        DataTable, RichLog, Button, TabbedContent, TabPane,
        ProgressBar, Rule,
    )
    from textual.reactive import reactive
    from textual.message import Message
    TEXTUAL_OK = True
except ImportError:
    TEXTUAL_OK = False


# ═════════════════════════════════════════════════════════════════════════════
# Widgets réutilisables
# ═════════════════════════════════════════════════════════════════════════════

if TEXTUAL_OK:

    class StatusBar(Static):
        """Barre supérieure : état, PTT, VU-mètre, DTMF, QSO stats."""

        state_name:  reactive[str]   = reactive("IDLE")
        ptt_on:      reactive[bool]  = reactive(False)
        rms_level:   reactive[float] = reactive(0.0)
        dtmf_seq:    reactive[str]   = reactive("")
        qso_count:   reactive[int]   = reactive(0)
        tx_total:    reactive[float] = reactive(0.0)

        def render(self) -> str:
            color = STATE_COLORS.get(self.state_name, "white")
            ptt   = "[bold red]● PTT[/bold red]" if self.ptt_on else "[dim]○ PTT[/dim]"
            bar_w = 20
            filled = int(self.rms_level * bar_w)
            bar = "[green]" + "█" * filled + "[/green]" + "░" * (bar_w - filled)
            dtmf  = f"[yellow]{self.dtmf_seq}[/yellow]" if self.dtmf_seq else "[dim]—[/dim]"
            tx_s  = int(self.tx_total)
            tx_str = f"{tx_s//60}m{tx_s%60:02d}s"
            return (
                f"[bold {color}]▶ {self.state_name:<14}[/bold {color}]"
                f"{ptt}  {bar}  "
                f"DTMF: {dtmf:<10}  "
                f"QSO: [bold]{self.qso_count}[/bold]  TX: {tx_str}"
            )


    # ─── Helpers formulaire ───────────────────────────────────────────────────

    def _row(label: str, widget) -> Horizontal:
        return Horizontal(
            Label(f"{label:<26}", classes="field-label"),
            widget,
            classes="field-row",
        )

    def _switch(id: str, value: bool) -> Switch:
        return Switch(value=value, id=id, classes="field-switch")

    def _input(id: str, value, width: int = 12) -> Input:
        return Input(value=str(value), id=id, classes="field-input")

    def _select(id: str, options: list[tuple[str, str]], value: str) -> Select:
        return Select([(label, v) for label, v in options], value=value,
                      id=id, classes="field-select")


    # ═════════════════════════════════════════════════════════════════════════
    # Onglets
    # ═════════════════════════════════════════════════════════════════════════

    class TabRepeater(Static):
        """Onglet Relais — paramètres store-and-forward Parlex."""

        def __init__(self, rep: "SimplexRepeater", **kw):
            super().__init__(**kw)
            self.rep = rep

        def compose(self) -> ComposeResult:
            cfg = self.rep.config
            with ScrollableContainer():
                yield Label("── Fonctionnement ──────────────────", classes="section-title")
                yield _row("Repeater ON  (##70/71)",  _switch("rep_on", cfg.repeater_on))
                yield _row("Say-again    (##14)",      _switch("say_again", cfg.say_again_on))
                yield _row("Responder mode (##75)",    _switch("responder", cfg.responder_mode))

                yield Label("── Durées ──────────────────────────", classes="section-title")
                yield _row("VOX timeout s (##13)",     _input("vox_timeout", cfg.vox_timeout))
                yield _row("Min TX time s (##19)",     _input("min_tx_time", cfg.min_tx_time))
                yield _row("Max TX time s (##17)",     _input("max_tx_time", cfg.max_tx_time))
                yield _row("Cooldown s    (##18)",     _input("cooldown_time", cfg.cooldown_time))
                yield _row("Auto-off s    (##06)",     _input("auto_off_timer", cfg.auto_off_timer))

                yield Label("── Stand-by & Pager ────────────────", classes="section-title")
                yield _row("Stand-by msg  (##164/165)",_input("standby_msg", cfg.standby_msg))
                yield _row("Pager code    (##78)",     _input("pager_code", cfg.pager_code))

                yield Button("Sauvegarder", id="save_rep", variant="primary")


    class TabAudio(Static):
        """Onglet Audio — VOX, niveaux, timing, CTCSS."""

        def __init__(self, rep: "SimplexRepeater", **kw):
            super().__init__(**kw)
            self.rep = rep

        def compose(self) -> ComposeResult:
            cfg = self.rep.config
            cor_opts = [("VOX", "0"), ("COR actif-haut", "1"), ("COR actif-bas", "2")]
            gain_opts = [("1×", "0"), ("2×", "1"), ("4×", "2")]
            ctone_opts = [(f"{i} — {n}", str(i)) for i, n in enumerate([
                "Aucun", "Triple montant (défaut)", "HH-LL",
                "Haut-bas", "Bas-haut", "Triple bip", "Bip simple"])]
            with ScrollableContainer():
                yield Label("── Entrée ──────────────────────────", classes="section-title")
                yield _row("Mode squelch  (##109)",    _select("cor_mode", cor_opts, str(cfg.cor_mode)))
                yield _row("VOX seuil RMS",            _input("vox_threshold", cfg.vox_threshold))
                yield _row("Gain entrée   (##107)",    _select("input_gain", gain_opts, str(cfg.input_gain)))
                yield _row("Tail suppression s (##79)",_input("squelch_tail", cfg.squelch_tail_supp))

                yield Label("── Sortie ──────────────────────────", classes="section-title")
                yield _row("TX gain (multiplicateur)", _input("tx_gain", cfg.tx_gain))
                yield _row("TX niveau 0-99 (##11)",    _input("tx_level", cfg.tx_audio_level))
                yield _row("TX délai s    (##92)",     _input("tx_delay", cfg.tx_delay))
                yield _row("Courtesy tone (##12)",     _select("courtesy_tone", ctone_opts, str(cfg.courtesy_tone)))
                yield _row("Delay courtesy (##77)",    _switch("ctone_delay", cfg.courtesy_tone_delay))

                yield Label("── CTCSS ───────────────────────────", classes="section-title")
                yield _row("CTCSS activé",             _switch("ctcss_on", cfg.ctcss_enabled))
                yield _row("CTCSS fréquence Hz",       _input("ctcss_freq", cfg.ctcss_freq))

                yield Button("Sauvegarder", id="save_audio", variant="primary")


    class TabID(Static):
        """Onglet ID / CW — Morse, Voice ID, preamble."""

        def __init__(self, rep: "SimplexRepeater", **kw):
            super().__init__(**kw)
            self.rep = rep

        def compose(self) -> ComposeResult:
            cfg = self.rep.config
            with ScrollableContainer():
                yield Label("── CW ID Morse ─────────────────────", classes="section-title")
                yield _row("Texte CW (12 car.)  (##82)", _input("cw_text", cfg.cw_id_text, 20))
                yield _row("CW ID activé        (##80)", _switch("cw_on", cfg.cw_id_on))
                yield _row("CW timer min        (##83)", _input("cw_timer", cfg.cw_id_timer / 60))
                yield _row("CW Cleanup ID  (##80 2/3)",  _switch("cw_cleanup", cfg.cw_cleanup_id))
                yield _row("CW auto-resp.  (##15)",      _switch("cw_resp", cfg.cw_responder_on))
                yield _row("CW inhibit min (##84)",      _input("cw_inhibit", cfg.cw_inhibit_timer / 60))
                yield _row("CW WPM         (##81)",      _input("cw_wpm", cfg.cw_wpm))
                yield _row("CW fréquence Hz",            _input("cw_freq", cfg.cw_freq))

                yield Label("── Voice ID ────────────────────────", classes="section-title")
                yield _row("Voice ID ON    (##16 0/1)",  _switch("vid_on", cfg.voice_id_on))
                yield _row("Voice inhibit min (##85)",   _input("vid_inhibit", cfg.voice_id_inhibit / 60))
                yield _row("Voice rotation (##16 8/9)",  _switch("vid_rotate", cfg.voice_id_rotate))
                yield _row("Voice preamble (##16 2/3)",  _switch("vid_pre", cfg.voice_preamble))

                yield Button("Sauvegarder", id="save_id", variant="primary")


    class TabVoicemail(Static):
        """Onglet Voicemail — inventaire + paramètres."""

        def __init__(self, rep: "SimplexRepeater", **kw):
            super().__init__(**kw)
            self.rep = rep

        def compose(self) -> ComposeResult:
            cfg = self.rep.config
            with ScrollableContainer():
                yield Label("── Paramètres ──────────────────────", classes="section-title")
                yield _row("Voicemail ON (##72/73)",  _switch("vm_on", cfg.voicemail_on))
                yield _row("Code accès   (##07)",     _input("vm_code", cfg.voicemail_code, 6))
                yield Button("Sauvegarder", id="save_vm", variant="primary")

                yield Rule()
                yield Label("── Inventaire ──────────────────────", classes="section-title")
                table = DataTable(id="vm_table", zebra_stripes=True)
                table.add_columns("#", "Durée", "Date/heure", "Taille")
                yield table

                yield Horizontal(
                    Button("Effacer tout (##74)", id="vm_erase_all", variant="error"),
                    Button("Rafraîchir",           id="vm_refresh",  variant="default"),
                    classes="btn-row",
                )

        def on_mount(self) -> None:
            self._refresh_table()

        def _refresh_table(self) -> None:
            from .storage import VoicemailStore
            table = self.query_one("#vm_table", DataTable)
            table.clear()
            vm = VoicemailStore()
            for i, m in enumerate(vm.meta_list()):
                dur  = m.get("dur", 0.0)
                ts   = m.get("ts", 0.0)
                date = time.strftime("%d/%m %H:%M", time.localtime(ts)) if ts else "—"
                size = f"{int(dur * 48000 * 2 / 1024)} Ko"
                table.add_row(str(i + 1), f"{dur:.1f}s", date, size)

        def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button.id == "vm_refresh":
                self._refresh_table()
            elif event.button.id == "vm_erase_all":
                from .storage import VoicemailStore
                VoicemailStore().erase_all()
                self._refresh_table()


    class TabAnnonces(Static):
        """Onglet Annonces — 10 slots avec interval, offset, état."""

        def __init__(self, rep: "SimplexRepeater", **kw):
            super().__init__(**kw)
            self.rep = rep

        def compose(self) -> ComposeResult:
            from .storage import AnnouncementStore
            cfg = self.rep.config
            ann = AnnouncementStore()
            with ScrollableContainer():
                table = DataTable(id="ann_table", zebra_stripes=True)
                table.add_columns("Slot", "Enregistrée", "Durée", "Interval (s)", "Offset (s)")
                for i in range(10):
                    exists = ann.exists(i)
                    t = cfg.announcement_timers.get(str(i), {})
                    iv  = t.get("interval", 0.0)
                    off = t.get("offset",   0.0)
                    dur = "—"
                    if exists:
                        data = ann.load(i)
                        if data:
                            from .storage import audio_duration
                            dur = f"{audio_duration(data):.1f}s"
                    table.add_row(
                        str(i),
                        "✓" if exists else "—",
                        dur,
                        f"{iv:.0f}",
                        f"{off:.0f}",
                    )
                yield table

                yield Label("── Modifier un slot ────────────────", classes="section-title")
                yield _row("Slot (0-9)",     _input("ann_slot", "0", 4))
                yield _row("Interval s",     _input("ann_iv",   "0", 8))
                yield _row("Offset s",       _input("ann_off",  "0", 8))
                yield Horizontal(
                    Button("Appliquer (##5n/##6n)", id="ann_apply", variant="primary"),
                    Button("Effacer slot (##4n)",   id="ann_erase", variant="error"),
                    Button("Rafraîchir",             id="ann_refresh", variant="default"),
                    classes="btn-row",
                )

        def on_button_pressed(self, event: Button.Pressed) -> None:
            cfg = self.rep.config
            try:
                slot = int(self.query_one("#ann_slot", Input).value)
            except ValueError:
                return

            if event.button.id == "ann_apply":
                try:
                    iv  = float(self.query_one("#ann_iv",  Input).value)
                    off = float(self.query_one("#ann_off", Input).value)
                    cfg.announcement_timers[str(slot)] = {"interval": iv, "offset": off}
                    cfg.save()
                    self.rep.ann_engine.refresh_slot(slot)
                    self._refresh()
                except ValueError:
                    pass

            elif event.button.id == "ann_erase":
                from .storage import AnnouncementStore
                AnnouncementStore().erase(slot)
                self._refresh()

            elif event.button.id == "ann_refresh":
                self._refresh()

        def _refresh(self) -> None:
            from .storage import AnnouncementStore, audio_duration
            cfg = self.rep.config
            ann = AnnouncementStore()
            table = self.query_one("#ann_table", DataTable)
            table.clear()
            for i in range(10):
                exists = ann.exists(i)
                t = cfg.announcement_timers.get(str(i), {})
                iv  = t.get("interval", 0.0)
                off = t.get("offset",   0.0)
                dur = "—"
                if exists:
                    data = ann.load(i)
                    if data:
                        dur = f"{audio_duration(data):.1f}s"
                table.add_row(str(i), "✓" if exists else "—", dur,
                              f"{iv:.0f}", f"{off:.0f}")


    class TabSecurite(Static):
        """Onglet Sécurité — lock, codes, auto-lock."""

        def __init__(self, rep: "SimplexRepeater", **kw):
            super().__init__(**kw)
            self.rep = rep

        def compose(self) -> ComposeResult:
            cfg = self.rep.config
            with ScrollableContainer():
                yield Label("── Verrouillage ────────────────────", classes="section-title")
                yield _row("Verrouillé    (##00)",    _switch("locked", cfg.locked))
                yield _row("Code sécurité (##08)",    _input("sec_code", cfg.security_code, 6))
                yield _row("Auto-lock s   (##09)",    _input("auto_lock", cfg.auto_lock_timer))

                yield Label("── Compteurs ───────────────────────", classes="section-title")
                yield Static(id="stats_display", classes="stats-box")

                yield Horizontal(
                    Button("Sauvegarder", id="save_sec", variant="primary"),
                    Button("Reset usine (##97)", id="factory_reset", variant="error"),
                    classes="btn-row",
                )

        def on_mount(self) -> None:
            self._refresh_stats()
            self.set_interval(5.0, self._refresh_stats)

        def _refresh_stats(self) -> None:
            cfg = self.rep.config
            st  = self.rep.stats
            sd  = st.session_duration()
            last = (time.strftime("%H:%M:%S", time.localtime(st.last_qso_time))
                    if st.last_qso_time else "—")
            tx_s = int(st.tx_total_seconds)
            txt = (
                f"QSO session    : [bold]{st.qso_count}[/bold]\n"
                f"TX total       : [bold]{tx_s//60}m{tx_s%60:02d}s[/bold]\n"
                f"Durée session  : {int(sd//3600)}h{int((sd%3600)//60):02d}m\n"
                f"Dernier QSO    : {last}\n"
                f"Usage total    : [bold]{cfg.usage_count}[/bold] transmissions"
            )
            try:
                self.query_one("#stats_display", Static).update(txt)
            except Exception:
                pass

        def on_button_pressed(self, event: Button.Pressed) -> None:
            cfg = self.rep.config
            if event.button.id == "save_sec":
                cfg.locked = self.query_one("#locked", Switch).value
                cfg.security_code = self.query_one("#sec_code", Input).value.strip()[:3]
                try:
                    cfg.auto_lock_timer = float(self.query_one("#auto_lock", Input).value)
                except ValueError:
                    pass
                cfg.save()
            elif event.button.id == "factory_reset":
                cfg.reset_defaults()
                cfg.save()


    class TabJournal(Static):
        """Onglet Journal — log temps réel."""

        def compose(self) -> ComposeResult:
            yield RichLog(highlight=True, markup=True, id="event_log",
                          wrap=True, auto_scroll=True)

        def append(self, msg: str) -> None:
            ts = time.strftime("%H:%M:%S")
            try:
                self.query_one("#event_log", RichLog).write(
                    f"[dim]{ts}[/dim]  {msg}"
                )
            except Exception:
                pass


    # ═════════════════════════════════════════════════════════════════════════
    # Onglet Castanara — surveillance HTTP
    # ═════════════════════════════════════════════════════════════════════════

    class TabCastanara(Static):
        """Onglet surveillance du relais Castanara via son API HTTP."""

        DEFAULT_CSS = """
        TabCastanara { padding: 1 2; }
        TabCastanara .cast-label { color: $text-muted; width: 18; }
        TabCastanara .cast-value { color: $text; }
        TabCastanara .cast-tx    { color: cyan; }
        TabCastanara .cast-ok    { color: green; }
        TabCastanara .cast-warn  { color: yellow; }
        TabCastanara .cast-err   { color: red; }
        TabCastanara .cast-section { color: $accent; margin-top: 1; }
        """

        def __init__(self, url: str, rep=None, **kw):
            super().__init__(**kw)
            self._url  = url.rstrip("/")
            self._rep  = rep
            self._data: dict = {}

        def compose(self) -> ComposeResult:
            yield Static("Castanara — relais distant", classes="cast-section")
            yield Horizontal(
                Input(value=self._url, id="cast_url_input", placeholder="http://host:8080"),
                Button("Connecter", id="cast_connect", variant="primary"),
            )
            yield Rule()
            yield Horizontal(
                Static("État",         classes="cast-label"),
                Static("—",            id="cast_state", classes="cast-value"),
            )
            yield Horizontal(
                Static("PTT",          classes="cast-label"),
                Static("—",            id="cast_ptt",   classes="cast-value"),
            )
            yield Horizontal(
                Static("Niveau RX",    classes="cast-label"),
                ProgressBar(total=100, id="cast_level", show_eta=False),
            )
            yield Horizontal(
                Static("DTMF",         classes="cast-label"),
                Static("—",            id="cast_dtmf",  classes="cast-value"),
            )
            yield Rule()
            yield Static("QSO", classes="cast-section")
            yield Horizontal(
                Static("Compteur",     classes="cast-label"),
                Static("—",            id="cast_qso_count", classes="cast-value"),
            )
            yield Horizontal(
                Static("TX total",     classes="cast-label"),
                Static("—",            id="cast_tx_total",  classes="cast-value"),
            )
            yield Horizontal(
                Static("Dernier QSO",  classes="cast-label"),
                Static("—",            id="cast_last_qso",  classes="cast-value"),
            )
            yield Rule()
            yield Static("Système", classes="cast-section")
            yield Horizontal(
                Static("CPU",          classes="cast-label"),
                Static("—",            id="cast_cpu",   classes="cast-value"),
            )
            yield Horizontal(
                Static("RAM",          classes="cast-label"),
                Static("—",            id="cast_ram",   classes="cast-value"),
            )
            yield Horizontal(
                Static("Température",  classes="cast-label"),
                Static("—",            id="cast_temp",  classes="cast-value"),
            )
            yield Horizontal(
                Static("Uptime",       classes="cast-label"),
                Static("—",            id="cast_uptime", classes="cast-value"),
            )
            yield Rule()
            yield Static("", id="cast_error", classes="cast-err")

        def on_mount(self) -> None:
            self.set_interval(3.0, self._refresh)

        def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button.id == "cast_connect":
                new_url = self.query_one("#cast_url_input", Input).value.strip()
                if new_url:
                    self._url = new_url.rstrip("/")
                    if self._rep is not None:
                        self._rep.config.castanara_url = self._url
                        try:
                            self._rep.config.save()
                        except Exception:
                            pass
                    self._refresh()

        def _refresh(self) -> None:
            import urllib.request
            import json as _json

            def _fetch(path):
                try:
                    with urllib.request.urlopen(
                        f"{self._url}{path}", timeout=2
                    ) as r:
                        return _json.loads(r.read().decode())
                except Exception:
                    return None

            status  = _fetch("/api/status")
            stats   = _fetch("/api/stats")
            sysinfo = _fetch("/api/sysinfo")
            self.call_from_thread(self._apply, status, stats, sysinfo)

        def _apply(self, status, stats, sysinfo) -> None:
            try:
                err = self.query_one("#cast_error", Static)
                if status is None:
                    err.update(f"Impossible de joindre {self._url}")
                    return
                err.update("")

                # État / PTT / niveau
                state  = status.get("state", "?")
                is_tx  = status.get("is_tx", False)
                level  = max(0.0, min(1.0, float(status.get("level", 0.0))))
                dtmf   = status.get("dtmf", "") or "—"

                tx_cls  = "cast-tx" if is_tx else "cast-ok"
                tx_str  = "[cyan]TX[/cyan]" if is_tx else "RX"

                self.query_one("#cast_state", Static).update(state)
                self.query_one("#cast_ptt",   Static).update(tx_str)
                self.query_one("#cast_level", ProgressBar).update(progress=int(level * 100))
                self.query_one("#cast_dtmf",  Static).update(dtmf)

                # Stats QSO
                if stats:
                    qso = stats.get("qso_count", 0)
                    tx_s = int(stats.get("tx_total_seconds", 0))
                    last = stats.get("last_qso_time")
                    last_str = (time.strftime("%d/%m %H:%M:%S", time.localtime(last))
                                if last else "—")
                    self.query_one("#cast_qso_count", Static).update(str(qso))
                    self.query_one("#cast_tx_total",  Static).update(
                        f"{tx_s // 60}m{tx_s % 60:02d}s"
                    )
                    self.query_one("#cast_last_qso",  Static).update(last_str)

                # Sysinfo
                if sysinfo:
                    cpu  = sysinfo.get("cpu_percent", "?")
                    ram  = sysinfo.get("ram_percent", "?")
                    temp = sysinfo.get("cpu_temp", "?")
                    up   = int(sysinfo.get("uptime_s", 0))
                    self.query_one("#cast_cpu",    Static).update(f"{cpu}%")
                    self.query_one("#cast_ram",    Static).update(f"{ram}%")
                    self.query_one("#cast_temp",   Static).update(f"{temp}°C")
                    self.query_one("#cast_uptime", Static).update(
                        f"{up // 3600}h{(up % 3600) // 60:02d}m{up % 60:02d}s"
                    )
            except Exception:
                pass


    # ═════════════════════════════════════════════════════════════════════════
    # Application principale
    # ═════════════════════════════════════════════════════════════════════════

    class RepeaterTUI(App):
        TITLE = "relais simplex — RPi Zero + AIOC"

        CSS = """
        Screen { layout: vertical; background: $background; }

        StatusBar {
            height: 1;
            padding: 0 1;
            background: $surface;
            color: $text;
        }

        TabbedContent { height: 1fr; }

        .section-title {
            color: $accent;
            text-style: bold;
            margin-top: 1;
            margin-bottom: 0;
        }

        .field-row {
            height: 3;
            align: left middle;
            margin-bottom: 0;
        }

        .field-label {
            width: 30;
            color: $text-muted;
        }

        .field-input  { width: 14; }
        .field-switch { width: 8; }
        .field-select { width: 22; }

        .btn-row {
            margin-top: 1;
            height: 3;
        }

        .btn-row Button { margin-right: 1; }

        Button.primary { background: $primary; }
        Button.error   { background: $error; }

        .stats-box {
            border: solid $surface-lighten-2;
            padding: 1;
            margin: 1 0;
        }

        DataTable { height: auto; max-height: 20; }
        """

        BINDINGS = [
            ("q",   "quit",    "Quitter"),
            ("s",   "save",    "Sauvegarder"),
            ("r",   "refresh", "Rafraîchir"),
        ]

        def __init__(self, repeater: "SimplexRepeater"):
            super().__init__()
            self.rep = repeater
            self._status_bar: Optional[StatusBar] = None
            self._tab_journal: Optional[TabJournal] = None
            self._dtmf_live: str = ""

        def compose(self) -> ComposeResult:
            yield Header()
            self._status_bar = StatusBar(id="status_bar")
            yield self._status_bar

            with TabbedContent():
                with TabPane("Relais",    id="tab_rep"):
                    yield TabRepeater(self.rep)
                with TabPane("Audio",     id="tab_audio"):
                    yield TabAudio(self.rep)
                with TabPane("ID / CW",   id="tab_id"):
                    yield TabID(self.rep)
                with TabPane("Voicemail", id="tab_vm"):
                    yield TabVoicemail(self.rep)
                with TabPane("Annonces",  id="tab_ann"):
                    yield TabAnnonces(self.rep)
                with TabPane("Sécurité",  id="tab_sec"):
                    yield TabSecurite(self.rep)
                with TabPane("Castanara", id="tab_cast"):
                    yield TabCastanara(self.rep.config.castanara_url, rep=self.rep)
                with TabPane("Journal",   id="tab_log"):
                    self._tab_journal = TabJournal()
                    yield self._tab_journal

            yield Footer()

        def on_mount(self) -> None:
            self.rep.add_state_observer(self._on_state)
            self.rep.add_log_observer(self._on_log)
            self.set_interval(0.5, self._tick)

        # ── Observers repeater ────────────────────────────────────────────────

        def _on_state(self, state: "State") -> None:
            if self._status_bar:
                self._status_bar.state_name = state.name
                self._status_bar.ptt_on = self.rep.ptt.is_active()

        def _on_log(self, msg: str) -> None:
            # DTMF live : extrait la séquence en cours depuis le log
            if msg.startswith("DTMF:"):
                digit = msg.split(":")[-1].strip()
                self._dtmf_live += digit
            else:
                self._dtmf_live = ""

            if self._tab_journal:
                self.call_from_thread(self._tab_journal.append, msg)

        def _tick(self) -> None:
            if not self._status_bar:
                return
            self._status_bar.ptt_on    = self.rep.ptt.is_active()
            self._status_bar.state_name = self.rep.get_state().name
            self._status_bar.dtmf_seq  = self._dtmf_live
            # Stats
            st = self.rep.stats
            self._status_bar.qso_count = st.qso_count
            self._status_bar.tx_total  = st.tx_total_seconds

        # ── Sauvegarde générique (boutons de chaque onglet) ───────────────────

        def on_button_pressed(self, event: Button.Pressed) -> None:
            bid = event.button.id
            cfg = self.rep.config

            if bid == "save_rep":
                self._save_rep()
            elif bid == "save_audio":
                self._save_audio()
            elif bid == "save_id":
                self._save_id()
            elif bid == "save_vm":
                self._save_vm()

        def _save_rep(self) -> None:
            cfg = self.rep.config
            try:
                cfg.repeater_on    = self.query_one("#rep_on",     Switch).value
                cfg.say_again_on   = self.query_one("#say_again",  Switch).value
                cfg.responder_mode = self.query_one("#responder",  Switch).value
                cfg.vox_timeout    = float(self.query_one("#vox_timeout",  Input).value)
                cfg.min_tx_time    = float(self.query_one("#min_tx_time",  Input).value)
                cfg.max_tx_time    = float(self.query_one("#max_tx_time",  Input).value)
                cfg.cooldown_time  = float(self.query_one("#cooldown_time",Input).value)
                cfg.auto_off_timer = float(self.query_one("#auto_off_timer",Input).value)
                cfg.standby_msg    = int(self.query_one("#standby_msg", Input).value)
                cfg.pager_code     = self.query_one("#pager_code", Input).value.strip()
                self.rep.audio_vox.timeout = cfg.vox_timeout
                cfg.save()
                self.notify("Paramètres relais sauvegardés.", title="OK")
            except Exception as e:
                self.notify(str(e), title="Erreur", severity="error")

        def _save_audio(self) -> None:
            cfg = self.rep.config
            try:
                cfg.cor_mode          = int(self.query_one("#cor_mode",     Select).value)
                cfg.vox_threshold     = float(self.query_one("#vox_threshold", Input).value)
                cfg.input_gain        = int(self.query_one("#input_gain",   Select).value)
                cfg.squelch_tail_supp = float(self.query_one("#squelch_tail", Input).value)
                cfg.tx_gain           = float(self.query_one("#tx_gain",    Input).value)
                cfg.tx_audio_level    = int(self.query_one("#tx_level",     Input).value)
                cfg.tx_delay          = float(self.query_one("#tx_delay",   Input).value)
                cfg.courtesy_tone     = int(self.query_one("#courtesy_tone",Select).value)
                cfg.courtesy_tone_delay = self.query_one("#ctone_delay",    Switch).value
                cfg.ctcss_enabled     = self.query_one("#ctcss_on",        Switch).value
                cfg.ctcss_freq        = float(self.query_one("#ctcss_freq", Input).value)
                self.rep.audio_vox.threshold = cfg.vox_threshold
                cfg.save()
                self.notify("Paramètres audio sauvegardés.", title="OK")
            except Exception as e:
                self.notify(str(e), title="Erreur", severity="error")

        def _save_id(self) -> None:
            cfg = self.rep.config
            try:
                cfg.cw_id_text       = self.query_one("#cw_text",     Input).value.strip()[:12]
                cfg.cw_id_on         = self.query_one("#cw_on",       Switch).value
                cfg.cw_id_timer      = float(self.query_one("#cw_timer",   Input).value) * 60
                cfg.cw_cleanup_id    = self.query_one("#cw_cleanup",  Switch).value
                cfg.cw_responder_on  = self.query_one("#cw_resp",     Switch).value
                cfg.cw_inhibit_timer = float(self.query_one("#cw_inhibit", Input).value) * 60
                cfg.cw_wpm           = int(self.query_one("#cw_wpm",  Input).value)
                cfg.cw_freq          = int(self.query_one("#cw_freq", Input).value)
                cfg.voice_id_on      = self.query_one("#vid_on",      Switch).value
                cfg.voice_id_inhibit = float(self.query_one("#vid_inhibit", Input).value) * 60
                cfg.voice_id_rotate  = self.query_one("#vid_rotate",  Switch).value
                cfg.voice_preamble   = self.query_one("#vid_pre",     Switch).value
                cfg.save()
                self.notify("Paramètres ID/CW sauvegardés.", title="OK")
            except Exception as e:
                self.notify(str(e), title="Erreur", severity="error")

        def _save_vm(self) -> None:
            cfg = self.rep.config
            try:
                cfg.voicemail_on   = self.query_one("#vm_on",   Switch).value
                cfg.voicemail_code = self.query_one("#vm_code", Input).value.strip()[:3]
                cfg.save()
                self.notify("Paramètres voicemail sauvegardés.", title="OK")
            except Exception as e:
                self.notify(str(e), title="Erreur", severity="error")

        def action_save(self) -> None:
            self.rep.config.save()
            self.notify("Configuration sauvegardée.", title="OK")

        def action_refresh(self) -> None:
            self._tick()


    def run_tui(repeater: "SimplexRepeater") -> None:
        if not TEXTUAL_OK:
            print("Erreur: 'textual' non installé — pip install textual")
            return
        RepeaterTUI(repeater).run()


# ═════════════════════════════════════════════════════════════════════════════
# Fallback TUI curses (sans dépendances tierces)
# ═════════════════════════════════════════════════════════════════════════════

def run_curses_tui(repeater: "SimplexRepeater") -> None:
    """
    TUI curses enrichie :
      - État machine + PTT + VU-mètre ASCII
      - Séquence DTMF en cours
      - Stats QSO (count, TX total, dernière heure)
      - Configuration active (tous paramètres clés)
      - Inventaire voicemail (count + durée totale)
      - Journal événements
    """
    import curses
    from .storage import VoicemailStore, AnnouncementStore

    log_buf: deque[str] = deque(maxlen=60)
    dtmf_live: list[str] = [""]

    def on_log(msg: str) -> None:
        if msg.startswith("DTMF:"):
            digit = msg.split(":")[-1].strip()
            dtmf_live[0] += digit
        else:
            dtmf_live[0] = ""
        log_buf.append(f"{time.strftime('%H:%M:%S')}  {msg}")

    repeater.add_log_observer(on_log)

    def main(stdscr) -> None:
        curses.curs_set(0)
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_GREEN,   -1)  # IDLE
        curses.init_pair(2, curses.COLOR_YELLOW,  -1)  # RECORDING
        curses.init_pair(3, curses.COLOR_CYAN,    -1)  # TX
        curses.init_pair(4, curses.COLOR_RED,     -1)  # COOLDOWN/PTT
        curses.init_pair(5, curses.COLOR_MAGENTA, -1)  # VM
        curses.init_pair(6, curses.COLOR_WHITE,   -1)  # neutre
        curses.init_pair(7, curses.COLOR_BLUE,    -1)  # titres sections

        STATE_CP = {
            "IDLE": 1, "RECORDING": 2, "TRANSMITTING": 3,
            "COOLDOWN": 4, "VM_RECORD": 5, "VM_PLAYBACK": 5,
            "REC_ANN": 3, "CAL_TONE": 6,
        }
        stdscr.timeout(400)

        while True:
            try:
                stdscr.erase()
                h, w = stdscr.getmaxyx()
                cfg   = repeater.config
                state = repeater.get_state().name
                st    = repeater.stats
                cp    = curses.color_pair(STATE_CP.get(state, 6))

                # ── Ligne 0 : titre ──────────────────────────────────────
                title = " relais simplex — RPi Zero + AIOC "
                stdscr.addstr(0, max(0, (w - len(title)) // 2), title[:w], curses.A_BOLD)

                # ── Ligne 1 : état + PTT + VU ────────────────────────────
                ptt_str = "● PTT ON" if repeater.ptt.is_active() else "○ PTT OFF"
                ptt_cp  = curses.color_pair(4) if repeater.ptt.is_active() else curses.A_DIM
                stdscr.addstr(1, 2,  f"État : {state:<16}", cp | curses.A_BOLD)
                stdscr.addstr(1, 26, ptt_str[:w - 26], ptt_cp)

                # ── Ligne 2 : DTMF live ──────────────────────────────────
                dtmf_str = dtmf_live[0] if dtmf_live[0] else "—"
                stdscr.addstr(2, 2,  f"DTMF  : {dtmf_str:<20}"[:w - 2], curses.color_pair(2))

                # ── Ligne 3 : stats QSO ──────────────────────────────────
                tx_s  = int(st.tx_total_seconds)
                last  = (time.strftime("%H:%M:%S", time.localtime(st.last_qso_time))
                         if st.last_qso_time else "—")
                stdscr.addstr(3, 2,
                    f"QSO   : {st.qso_count}  TX total : {tx_s//60}m{tx_s%60:02d}s  Dernier : {last}"[:w - 2])

                # ── Séparateur ───────────────────────────────────────────
                stdscr.addstr(4, 0, "─" * w)

                # ── Colonnes config (gauche) + inventaires (droite) ──────
                col_w  = min(35, w // 2)
                left_params = [
                    ("── Relais ──────────────────────", None, 7),
                    ("Repeater",     "ON" if cfg.repeater_on else "OFF",  1),
                    ("Say-again",    "ON" if cfg.say_again_on else "OFF", 1),
                    ("Voicemail",    "ON" if cfg.voicemail_on else "OFF", 1),
                    ("Responder",    "ON" if cfg.responder_mode else "OFF", 6),
                    ("── Audio ───────────────────────", None, 7),
                    ("VOX seuil",    f"{cfg.vox_threshold:.4f}", 6),
                    ("VOX timeout",  f"{cfg.vox_timeout:.1f}s",  6),
                    ("TX gain",      f"{cfg.tx_gain:.1f}",        6),
                    ("TX delay",     f"{cfg.tx_delay:.1f}s",      6),
                    ("Courtesy",     str(cfg.courtesy_tone),       6),
                    ("COR mode",     ["VOX","COR-HI","COR-LO"][cfg.cor_mode], 6),
                    ("CTCSS",        f"{'ON' if cfg.ctcss_enabled else 'OFF'} {cfg.ctcss_freq}Hz", 6),
                    ("── ID / CW ─────────────────────", None, 7),
                    ("CW ID",        cfg.cw_id_text or "(vide)",  6),
                    ("CW ON",        "OUI" if cfg.cw_id_on else "non", 6),
                    ("CW WPM",       str(cfg.cw_wpm),              6),
                    ("Voice ID",     "ON" if cfg.voice_id_on else "OFF", 6),
                    ("── Sécurité ────────────────────", None, 7),
                    ("Verrouillé",   "OUI" if cfg.locked else "non", 4 if cfg.locked else 6),
                    ("Auto-lock",    f"{cfg.auto_lock_timer:.0f}s" if cfg.auto_lock_timer else "OFF", 6),
                    ("Usage total",  str(cfg.usage_count), 6),
                ]

                row = 5
                for item in left_params:
                    if row >= h - 1:
                        break
                    k, v, cp_idx = item
                    cp_i = curses.color_pair(cp_idx)
                    if v is None:
                        stdscr.addstr(row, 2, k[:col_w - 2], cp_i | curses.A_BOLD)
                    else:
                        stdscr.addstr(row, 2,  f"{k:<16}"[:col_w//2], curses.A_DIM)
                        stdscr.addstr(row, 18, str(v)[:col_w - 18], cp_i)
                    row += 1

                # ── Colonne droite : inventaires ──────────────────────────
                if w > col_w + 20:
                    rx = col_w + 4
                    rrow = 5
                    # Voicemail
                    vm = VoicemailStore()
                    ann = AnnouncementStore()
                    vmlist = vm.meta_list()
                    stdscr.addstr(rrow, rx, "── Voicemail ──────────────", curses.color_pair(7) | curses.A_BOLD)
                    rrow += 1
                    if vmlist:
                        for i, m in enumerate(vmlist[:8]):
                            if rrow >= h - 12:
                                break
                            dur  = m.get("dur", 0.0)
                            ts   = m.get("ts", 0.0)
                            date = time.strftime("%d/%m %H:%M", time.localtime(ts)) if ts else "—"
                            stdscr.addstr(rrow, rx, f"  #{i+1:<2} {dur:>5.1f}s  {date}"[:w - rx - 1])
                            rrow += 1
                    else:
                        stdscr.addstr(rrow, rx, "  (aucun message)", curses.A_DIM)
                        rrow += 1

                    stdscr.addstr(rrow, rx, f"  Total : {vm.count()}/{vm.MAX_MESSAGES}")
                    rrow += 2

                    # Annonces
                    stdscr.addstr(rrow, rx, "── Annonces ───────────────", curses.color_pair(7) | curses.A_BOLD)
                    rrow += 1
                    slots = ann.list_slots()
                    if slots:
                        for s in slots[:8]:
                            if rrow >= h - 6:
                                break
                            t  = cfg.announcement_timers.get(str(s), {})
                            iv = t.get("interval", 0.0)
                            stdscr.addstr(rrow, rx,
                                f"  Slot {s}  iv={iv:.0f}s"[:w - rx - 1])
                            rrow += 1
                    else:
                        stdscr.addstr(rrow, rx, "  (aucune)", curses.A_DIM)
                        rrow += 1

                # ── Séparateur log ────────────────────────────────────────
                log_start = row + 1
                if log_start < h - 3:
                    stdscr.addstr(log_start, 0, "─" * w)
                    stdscr.addstr(log_start + 1, 2, "Journal :", curses.A_BOLD)
                    log_row = log_start + 2
                    visible = list(log_buf)[-(h - log_row - 1):]
                    for i, line in enumerate(visible):
                        if log_row + i >= h - 1:
                            break
                        stdscr.addstr(log_row + i, 2, line[:w - 3])

                # ── Footer ────────────────────────────────────────────────
                stdscr.addstr(h - 1, 0, " q: quitter", curses.A_DIM)

                stdscr.refresh()
            except curses.error:
                pass

            key = stdscr.getch()
            if key in (ord('q'), ord('Q')):
                break

    curses.wrapper(main)
