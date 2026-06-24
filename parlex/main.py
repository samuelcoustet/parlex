"""
main.py — CLI complète du relais simplex

Commandes :
  simplex-repeater run [--tui|--curses] [--config PATH] [--log-level X] [--log-file PATH]
  simplex-repeater status
  simplex-repeater stats
  simplex-repeater config list
  simplex-repeater config get <param>
  simplex-repeater config set <param> <valeur>
  simplex-repeater config reset
  simplex-repeater voicemail list
  simplex-repeater voicemail erase <n>
  simplex-repeater voicemail erase-all
  simplex-repeater announce list
  simplex-repeater announce set <slot> <interval_s> [<offset_s>]
  simplex-repeater announce erase <slot>
  simplex-repeater remote [--url URL] [--watch]

Le daemon écrit /run/parlex/status.json toutes les 2s.
Les commandes status/stats le lisent si le daemon tourne.
"""
from __future__ import annotations
import argparse
import dataclasses
import json
import logging
import signal
import sys
import time
from pathlib import Path
from typing import Any

from .config import RepeaterConfig, CONFIG_PATH

STATUS_FILE = Path("/run/parlex/status.json")

log = logging.getLogger("main")


# ═════════════════════════════════════════════════════════════════════════════
# Utilitaires
# ═════════════════════════════════════════════════════════════════════════════

def _load_cfg(args) -> tuple[RepeaterConfig, Path]:
    path = Path(getattr(args, "config", str(CONFIG_PATH)))
    return RepeaterConfig.load(path), path


def _cast(key: str, raw: str, cfg: RepeaterConfig) -> Any:
    """Convertit raw en type Python correct d'après la valeur actuelle du champ."""
    current = getattr(cfg, key, None)
    if isinstance(current, bool):
        return raw.lower() in ("true", "1", "yes", "on", "oui")
    if isinstance(current, int):
        return int(raw)
    if isinstance(current, float):
        return float(raw)
    return raw


def _bool_str(v: bool) -> str:
    return "ON" if v else "OFF"


def _read_status() -> dict | None:
    if STATUS_FILE.exists():
        try:
            return json.loads(STATUS_FILE.read_text())
        except Exception:
            pass
    return None


def _hr() -> str:
    return "─" * 52


# ═════════════════════════════════════════════════════════════════════════════
# Commandes
# ═════════════════════════════════════════════════════════════════════════════

def cmd_status(args) -> None:
    cfg, _ = _load_cfg(args)
    live = _read_status()

    print(_hr())
    print(" relais simplex — RPi Zero + AIOC")
    print(_hr())

    # État live si daemon tourne
    if live:
        state  = live.get("state", "?")
        ptt    = live.get("ptt", False)
        uptime = int(live.get("uptime_s", 0))
        print(f"  Daemon          {'EN MARCHE':>20}  (uptime {uptime//3600}h{(uptime%3600)//60:02d}m)")
        print(f"  État machine    {state:>20}")
        print(f"  PTT             {'ACTIF' if ptt else 'repos':>20}")
    else:
        print("  Daemon          {'arrêté (pas de status.json)':>20}")

    print()
    sections = [
        ("── Relais ─────────────────────────────────────────", [
            ("Repeater",         _bool_str(cfg.repeater_on)),
            ("Say-again",        _bool_str(cfg.say_again_on)),
            ("Voicemail",        _bool_str(cfg.voicemail_on)),
            ("Responder mode",   _bool_str(cfg.responder_mode)),
            ("COR mode",         ["VOX", "COR-HI", "COR-LO"][cfg.cor_mode]),
        ]),
        ("── Timing ─────────────────────────────────────────", [
            ("VOX seuil",        f"{cfg.vox_threshold:.4f}"),
            ("VOX timeout",      f"{cfg.vox_timeout:.1f}s"),
            ("TX delay",         f"{cfg.tx_delay:.1f}s"),
            ("Min TX",           f"{cfg.min_tx_time:.1f}s"),
            ("Max TX",           f"{cfg.max_tx_time or '∞'}s"),
            ("Cooldown",         f"{cfg.cooldown_time:.1f}s"),
            ("Auto-off",         f"{cfg.auto_off_timer:.0f}s" if cfg.auto_off_timer else "OFF"),
            ("Squelch tail",     f"{cfg.squelch_tail_supp:.2f}s"),
        ]),
        ("── Audio ──────────────────────────────────────────", [
            ("TX gain",          f"{cfg.tx_gain:.1f}×"),
            ("TX audio level",   f"{cfg.tx_audio_level}/99"),
            ("Input gain",       f"{[1, 2, 4][cfg.input_gain]}×"),
            ("Courtesy tone",    str(cfg.courtesy_tone)),
            ("CTCSS",            f"{'ON' if cfg.ctcss_enabled else 'OFF'}  {cfg.ctcss_freq} Hz"),
            ("ALSA capture",     cfg.alsa_capture),
            ("ALSA playback",    cfg.alsa_playback),
            ("Port série",       cfg.serial_port),
        ]),
        ("── ID / CW ────────────────────────────────────────", [
            ("CW ID texte",      cfg.cw_id_text or "(vide)"),
            ("CW ID",            _bool_str(cfg.cw_id_on)),
            ("CW timer",         f"{cfg.cw_id_timer/60:.0f} min"),
            ("CW WPM",           str(cfg.cw_wpm)),
            ("CW fréquence",     f"{cfg.cw_freq} Hz"),
            ("CW cleanup",       _bool_str(cfg.cw_cleanup_id)),
            ("CW responder",     _bool_str(cfg.cw_responder_on)),
            ("CW inhibit",       f"{cfg.cw_inhibit_timer/60:.0f} min"),
            ("Voice ID",         _bool_str(cfg.voice_id_on)),
            ("Voice inhibit",    f"{cfg.voice_id_inhibit/60:.0f} min"),
            ("Voice rotate",     _bool_str(cfg.voice_id_rotate)),
            ("Voice preamble",   _bool_str(cfg.voice_preamble)),
        ]),
        ("── Sécurité ───────────────────────────────────────", [
            ("Verrouillé",       "OUI" if cfg.locked else "non"),
            ("Code sécurité",    "***"),
            ("Auto-lock",        f"{cfg.auto_lock_timer:.0f}s" if cfg.auto_lock_timer else "OFF"),
            ("Pager code",       cfg.pager_code or "(désactivé)"),
            ("Standby msg",      str(cfg.standby_msg) if cfg.standby_msg >= 0 else "OFF"),
            ("Usage total",      str(cfg.usage_count)),
        ]),
    ]
    for title, rows in sections:
        print(f"  {title}")
        for k, v in rows:
            print(f"    {k:<24} {v}")
        print()

    # Stockage
    from .storage import AnnouncementStore, VoicemailStore, audio_duration
    ann = AnnouncementStore()
    vm  = VoicemailStore()
    slots = ann.list_slots()
    print("  ── Annonces ────────────────────────────────────")
    if slots:
        for s in slots:
            t   = cfg.announcement_timers.get(str(s), {})
            iv  = t.get("interval", 0.0)
            off = t.get("offset", 0.0)
            data = ann.load(s)
            dur = f"{audio_duration(data):.1f}s" if data else "?"
            print(f"    Slot {s}  {dur:<8}  interval={iv:.0f}s  offset={off:.0f}s")
    else:
        print("    (aucune annonce enregistrée)")

    print()
    print("  ── Voicemail ────────────────────────────────────")
    print(f"    {vm.count()}/{vm.MAX_MESSAGES} messages")
    print(_hr())


def cmd_stats(args) -> None:
    live = _read_status()
    if live and "stats" in live:
        st = live["stats"]
        uptime = int(live.get("uptime_s", 0))
        tx_s   = int(st.get("tx_total_s", 0))
        last   = st.get("last_qso")
        last_str = (time.strftime("%d/%m %H:%M:%S", time.localtime(last))
                    if last else "—")
        print(_hr())
        print(" Stats QSO — session en cours")
        print(_hr())
        print(f"  QSO session       {st.get('qso_count', 0)}")
        print(f"  TX total          {tx_s//60}m{tx_s%60:02d}s")
        print(f"  Durée session     {uptime//3600}h{(uptime%3600)//60:02d}m{uptime%60:02d}s")
        print(f"  Dernier QSO       {last_str}")
        print(_hr())
    else:
        cfg, _ = _load_cfg(args)
        print(f"  Daemon arrêté. Usage total persisté : {cfg.usage_count} transmissions")


def cmd_config_list(args) -> None:
    cfg, _ = _load_cfg(args)
    skip = {"announcement_timers", "output_pins"}
    print(_hr())
    print(" Paramètres configurables")
    print(_hr())
    for f in dataclasses.fields(cfg):
        if f.name in skip:
            continue
        v = getattr(cfg, f.name)
        if f.name == "security_code":
            v = "***"
        print(f"  {f.name:<30} {v}")
    print(_hr())


def cmd_config_get(args) -> None:
    cfg, _ = _load_cfg(args)
    key = args.param
    if not hasattr(cfg, key):
        print(f"Paramètre inconnu : {key}", file=sys.stderr)
        sys.exit(1)
    if key == "security_code":
        print("***")
    else:
        print(getattr(cfg, key))


def cmd_config_set(args) -> None:
    cfg, path = _load_cfg(args)
    key = args.param
    if not hasattr(cfg, key):
        print(f"Paramètre inconnu : {key}", file=sys.stderr)
        sys.exit(1)
    if key in ("security_code", "voicemail_code"):
        raw = args.value
    else:
        try:
            raw = _cast(key, args.value, cfg)
        except (ValueError, TypeError) as e:
            print(f"Valeur invalide : {e}", file=sys.stderr)
            sys.exit(1)
    setattr(cfg, key, raw)
    cfg.save(path)
    print(f"  {key} = {raw}")
    print(f"  Sauvegardé dans {path}")


def cmd_config_reset(args) -> None:
    cfg, path = _load_cfg(args)
    cfg.reset_defaults()
    cfg.save(path)
    print(f"Configuration remise aux valeurs usine → {path}")


def cmd_vm_list(args) -> None:
    from .storage import VoicemailStore
    vm = VoicemailStore()
    metas = vm.meta_list()
    print(_hr())
    print(f" Voicemail  ({vm.count()}/{vm.MAX_MESSAGES} messages)")
    print(_hr())
    if not metas:
        print("  (boîte vide)")
    else:
        print(f"  {'#':<4} {'Durée':<8} {'Date/heure':<18} Taille")
        print("  " + "─" * 46)
        for i, m in enumerate(metas):
            dur  = m.get("dur", 0.0)
            ts   = m.get("ts", 0.0)
            date = time.strftime("%d/%m/%Y %H:%M:%S", time.localtime(ts)) if ts else "—"
            size = f"{int(dur * 48000 * 2 / 1024)} Ko"
            print(f"  {i+1:<4} {dur:<8.1f} {date:<18} {size}")
    print(_hr())


def cmd_vm_erase(args) -> None:
    from .storage import VoicemailStore
    vm = VoicemailStore()
    n = args.n - 1  # 1-based → 0-based
    if vm.erase(n):
        print(f"Message #{args.n} effacé. ({vm.count()} restants)")
    else:
        print(f"Message #{args.n} introuvable.", file=sys.stderr)
        sys.exit(1)


def cmd_vm_erase_all(args) -> None:
    from .storage import VoicemailStore
    vm = VoicemailStore()
    count = vm.count()
    vm.erase_all()
    print(f"{count} messages effacés.")


def cmd_ann_list(args) -> None:
    cfg, _ = _load_cfg(args)
    from .storage import AnnouncementStore, audio_duration
    ann = AnnouncementStore()
    print(_hr())
    print(" Annonces (slots 0-9)")
    print(_hr())
    print(f"  {'Slot':<6} {'Enreg.':<8} {'Durée':<8} {'Interval':<12} {'Offset'}")
    print("  " + "─" * 46)
    for i in range(10):
        exists = ann.exists(i)
        t   = cfg.announcement_timers.get(str(i), {})
        iv  = t.get("interval", 0.0)
        off = t.get("offset", 0.0)
        dur = "—"
        if exists:
            data = ann.load(i)
            if data:
                dur = f"{audio_duration(data):.1f}s"
        mark = "✓" if exists else " "
        print(f"  {i:<6} {mark:<8} {dur:<8} {iv:<12.0f} {off:.0f}")
    print(_hr())


def cmd_ann_set(args) -> None:
    cfg, path = _load_cfg(args)
    slot = args.slot
    if not 0 <= slot <= 9:
        print("Slot invalide (0-9).", file=sys.stderr)
        sys.exit(1)
    iv  = float(args.interval)
    off = float(args.offset) if args.offset is not None else 0.0
    cfg.announcement_timers[str(slot)] = {"interval": iv, "offset": off}
    cfg.save(path)
    print(f"  Slot {slot} : interval={iv:.0f}s  offset={off:.0f}s  → sauvegardé")


def cmd_ann_erase(args) -> None:
    from .storage import AnnouncementStore
    ann = AnnouncementStore()
    if ann.erase(args.slot):
        print(f"Slot {args.slot} effacé.")
    else:
        print(f"Slot {args.slot} vide ou introuvable.", file=sys.stderr)
        sys.exit(1)


# ═════════════════════════════════════════════════════════════════════════════
# Daemon + écriture status.json
# ═════════════════════════════════════════════════════════════════════════════

# ─── Systemd watchdog (sans dépendance externe) ───────────────────────────────

def _sd_notify(msg: str) -> None:
    """Envoie un message sd_notify à systemd (Type=notify / WatchdogSec)."""
    import os, socket
    sock_path = os.environ.get("NOTIFY_SOCKET", "")
    if not sock_path:
        return
    try:
        addr = sock_path.lstrip("@")
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.connect(addr)
            s.send(msg.encode())
    except Exception:
        pass


def _watchdog_loop(interval: float = 10.0) -> None:
    """Thread : envoie WATCHDOG=1 à systemd toutes les `interval` secondes."""
    while True:
        _sd_notify("WATCHDOG=1")
        time.sleep(interval)


# ─── Status JSON ──────────────────────────────────────────────────────────────

def _write_status_loop(rep, interval: float = 2.0) -> None:
    """Thread : écrit /run/parlex/status.json toutes les `interval` secondes."""
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    while True:
        try:
            payload = {
                "state":    rep.get_state().name,
                "ptt":      rep.ptt.is_active(),
                "uptime_s": round(time.time() - t0),
                "stats":    rep.stats.to_dict(),
            }
            STATUS_FILE.write_text(json.dumps(payload))
        except Exception:
            pass
        time.sleep(interval)


def cmd_run(args) -> None:
    import threading
    from .repeater import SimplexRepeater

    setup_logging(args.log_level, getattr(args, "log_file", None))
    cfg, _ = _load_cfg(args)
    rep = SimplexRepeater(config=cfg)

    def _shutdown(sig, frame):
        log.info("Signal %s — arrêt", sig)
        _sd_notify("STOPPING=1")
        rep.stop()
        if STATUS_FILE.exists():
            try:
                STATUS_FILE.unlink()
            except Exception:
                pass
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    rep.start()

    # Notifie systemd que le service est prêt
    _sd_notify("READY=1")

    # Thread status.json
    threading.Thread(target=_write_status_loop, args=(rep,), daemon=True).start()

    # Thread watchdog systemd (envoie WATCHDOG=1 toutes les 10s, WatchdogSec=30)
    threading.Thread(target=_watchdog_loop, args=(10.0,), daemon=True).start()

    if args.tui:
        from .tui import run_tui
        run_tui(rep)
    elif args.curses:
        from .tui import run_curses_tui
        run_curses_tui(rep)
    else:
        log.info("Relais actif — Ctrl+C pour arrêter")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass

    rep.stop()


# ─── Surveillance relais distant ─────────────────────────────────────────────

def _remote_fetch(url: str, path: str, timeout: float = 3.0) -> dict | None:
    """Interroge le dashboard du relais distant via HTTP."""
    import urllib.request, urllib.error
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}{path}", timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def cmd_remote(args) -> None:
    cfg, _ = _load_cfg(args)
    url = getattr(args, "url", None) or cfg.remote_url

    print(_hr())
    print(f" Relais distant — {url}")
    print(_hr())

    status  = _remote_fetch(url, "/api/status")
    stats   = _remote_fetch(url, "/api/stats")
    sysinfo = _remote_fetch(url, "/api/sysinfo")

    if status is None:
        print(f"  Impossible de joindre {url}")
        print("  Vérifiez que le daemon du relais distant tourne et que l'URL est correcte.")
        print(f"  Configurer : parlex config set remote_url {url}")
        sys.exit(1)

    # État
    state   = status.get("state", "?")
    is_tx   = status.get("is_tx", False)
    level   = status.get("level", 0.0)
    dtmf    = status.get("dtmf", "") or "—"
    bar_w   = 20
    bar     = "█" * int(level * bar_w) + "░" * (bar_w - int(level * bar_w))
    tx_str  = "TX EN COURS" if is_tx else "en attente"

    print(f"  État          {state}")
    print(f"  PTT           {tx_str}")
    print(f"  Niveau RX     [{bar}] {level:.3f}")
    print(f"  DTMF          {dtmf}")

    # Stats QSO
    if stats:
        tx_s = int(stats.get("tx_total_seconds", 0))
        last = stats.get("last_qso_time")
        last_str = (time.strftime("%d/%m %H:%M:%S", time.localtime(last))
                    if last else "—")
        print()
        print(f"  QSO session   {stats.get('qso_count', 0)}")
        print(f"  TX total      {tx_s//60}m{tx_s%60:02d}s")
        print(f"  Dernier QSO   {last_str}")

    # Sysinfo
    if sysinfo:
        print()
        cpu  = sysinfo.get("cpu_percent", "?")
        ram  = sysinfo.get("ram_percent", "?")
        temp = sysinfo.get("cpu_temp", "?")
        up   = int(sysinfo.get("uptime_s", 0))
        print(f"  CPU           {cpu}%")
        print(f"  RAM           {ram}%")
        print(f"  Température   {temp}°C")
        print(f"  Uptime        {up//3600}h{(up%3600)//60:02d}m{up%60:02d}s")

    print(_hr())

    # Mode watch
    if getattr(args, "watch", False):
        print("  Mode watch — Ctrl+C pour quitter")
        try:
            while True:
                time.sleep(2)
                st = _remote_fetch(url, "/api/status")
                if st:
                    lvl   = st.get("level", 0.0)
                    b     = "█" * int(lvl * bar_w) + "░" * (bar_w - int(lvl * bar_w))
                    tx    = "TX" if st.get("is_tx") else "RX"
                    state = st.get("state", "?")
                    ts    = time.strftime("%H:%M:%S")
                    print(f"  {ts}  {state:<14} {tx}  [{b}] {lvl:.3f}", end="\r")
        except KeyboardInterrupt:
            print()


# ═════════════════════════════════════════════════════════════════════════════
# Parseur principal
# ═════════════════════════════════════════════════════════════════════════════

def _add_config_arg(p) -> None:
    p.add_argument("--config", default=str(CONFIG_PATH),
                   help=f"Config YAML (défaut: {CONFIG_PATH})")


def build_parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="simplex-repeater",
        description="relais simplex — RPi Zero + AIOC",
    )
    sub = root.add_subparsers(dest="command", metavar="<commande>")

    # ── run ──────────────────────────────────────────────────────────────────
    p_run = sub.add_parser("run", help="Démarre le relais (défaut si aucune commande)")
    _add_config_arg(p_run)
    p_run.add_argument("--tui",       action="store_true", help="Interface Textual")
    p_run.add_argument("--curses",    action="store_true", help="Interface curses")
    p_run.add_argument("--log-level", default="INFO",
                       choices=["DEBUG","INFO","WARNING","ERROR"])
    p_run.add_argument("--log-file",  default=None)
    p_run.set_defaults(func=cmd_run)

    # ── status ────────────────────────────────────────────────────────────────
    p_st = sub.add_parser("status", help="Affiche config complète + état live si daemon actif")
    _add_config_arg(p_st)
    p_st.set_defaults(func=cmd_status)

    # ── stats ─────────────────────────────────────────────────────────────────
    p_stats = sub.add_parser("stats", help="Stats QSO de la session en cours")
    _add_config_arg(p_stats)
    p_stats.set_defaults(func=cmd_stats)

    # ── config ───────────────────────────────────────────────────────────────
    p_cfg = sub.add_parser("config", help="Lire/modifier la configuration")
    _add_config_arg(p_cfg)
    sub_cfg = p_cfg.add_subparsers(dest="cfg_cmd", metavar="<action>")

    sc_list = sub_cfg.add_parser("list",  help="Liste tous les paramètres")
    sc_list.set_defaults(func=cmd_config_list)

    sc_get = sub_cfg.add_parser("get",   help="Lit un paramètre")
    sc_get.add_argument("param")
    sc_get.set_defaults(func=cmd_config_get)

    sc_set = sub_cfg.add_parser("set",   help="Modifie un paramètre et sauvegarde")
    sc_set.add_argument("param")
    sc_set.add_argument("value")
    sc_set.set_defaults(func=cmd_config_set)

    sc_reset = sub_cfg.add_parser("reset", help="Valeurs usine Parlex")
    sc_reset.set_defaults(func=cmd_config_reset)

    def _cfg_dispatch(args):
        if args.cfg_cmd is None:
            p_cfg.print_help()
        else:
            args.func(args)
    p_cfg.set_defaults(func=_cfg_dispatch)

    # ── voicemail ─────────────────────────────────────────────────────────────
    p_vm = sub.add_parser("voicemail", help="Gestion des messages vocaux")
    sub_vm = p_vm.add_subparsers(dest="vm_cmd", metavar="<action>")

    sv_list = sub_vm.add_parser("list",      help="Inventaire (durée, date, taille)")
    sv_list.set_defaults(func=cmd_vm_list)

    sv_erase = sub_vm.add_parser("erase",    help="Efface le message numéro N (1-based)")
    sv_erase.add_argument("n", type=int, metavar="N")
    sv_erase.set_defaults(func=cmd_vm_erase)

    sv_all = sub_vm.add_parser("erase-all", help="Vide la boîte vocale")
    sv_all.set_defaults(func=cmd_vm_erase_all)

    def _vm_dispatch(args):
        if args.vm_cmd is None:
            p_vm.print_help()
        else:
            args.func(args)
    p_vm.set_defaults(func=_vm_dispatch)

    # ── announce ──────────────────────────────────────────────────────────────
    p_ann = sub.add_parser("announce", help="Gestion des annonces (slots 0-9)")
    _add_config_arg(p_ann)
    sub_ann = p_ann.add_subparsers(dest="ann_cmd", metavar="<action>")

    sa_list = sub_ann.add_parser("list",  help="État des 10 slots")
    sa_list.set_defaults(func=cmd_ann_list)

    sa_set = sub_ann.add_parser("set",   help="Configure timer d'un slot")
    sa_set.add_argument("slot",     type=int, metavar="SLOT",     help="0-9")
    sa_set.add_argument("interval", type=float, metavar="INTERVAL_S")
    sa_set.add_argument("offset",   type=float, metavar="OFFSET_S", nargs="?")
    sa_set.set_defaults(func=cmd_ann_set)

    sa_erase = sub_ann.add_parser("erase", help="Efface une annonce enregistrée")
    sa_erase.add_argument("slot", type=int, metavar="SLOT", help="0-9")
    sa_erase.set_defaults(func=cmd_ann_erase)

    def _ann_dispatch(args):
        if args.ann_cmd is None:
            p_ann.print_help()
        else:
            args.func(args)
    p_ann.set_defaults(func=_ann_dispatch)

    # ── remote ─────────────────────────────────────────────────────────────
    p_cast = sub.add_parser("remote",
                            help="Surveillance du relais distant (HTTP API)")
    _add_config_arg(p_cast)
    p_cast.add_argument("--url",   default=None,
                        help="URL du relais distant (ex: http://relais.local:8080)")
    p_cast.add_argument("--watch", action="store_true",
                        help="Mode watch : rafraîchit en continu")
    p_cast.set_defaults(func=cmd_remote)

    return root


def setup_logging(level: str = "INFO", log_file: str | None = None) -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, level))
    if not root.handlers:
        root.addHandler(logging.StreamHandler(sys.stdout))
    if log_file:
        fh = logging.FileHandler(log_file)
        fh.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s %(message)s"))
        root.addHandler(fh)


def main() -> None:
    parser = build_parser()
    args   = parser.parse_args()

    if args.command is None:
        # Aucune commande → run sans TUI (backward compat)
        args.tui       = False
        args.curses    = False
        args.log_level = "INFO"
        args.log_file  = None
        if not hasattr(args, "config"):
            args.config = str(CONFIG_PATH)
        cmd_run(args)
    elif hasattr(args, "func"):
        # Propager --config sur sous-sous-commandes qui ne l'ont pas
        if not hasattr(args, "config"):
            args.config = str(CONFIG_PATH)
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
