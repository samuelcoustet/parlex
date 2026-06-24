# Parlex — Relais simplex store-and-forward · RPi Zero + AIOC

Relais simplex store-and-forward implémenté en Python sur **Raspberry Pi Zero**
avec **[AIOC (AllInOneCable)](https://github.com/skuep/AIOC)** comme interface audio/PTT USB.

> 🇬🇧 **[Jump to English documentation](#english-documentation)**

---

## Table des matières

1. [Matériel requis](#matériel-requis)
2. [Installation](#installation)
3. [Démarrage rapide](#démarrage-rapide)
4. [CLI — référence complète](#cli--référence-complète)
5. [Commandes DTMF](#commandes-dtmf)
6. [Paramètres de configuration](#paramètres-de-configuration)
7. [Voicemail](#voicemail)
8. [Annonces programmées](#annonces-programmées)
9. [Interface TUI](#interface-tui)
10. [Architecture logicielle](#architecture-logicielle)
11. [Dépendances](#dépendances)

---

## Matériel requis

| Composant | Détail |
|---|---|
| Raspberry Pi Zero W / 2W | Processeur ARM, ≥ 512 Mo RAM |
| [AIOC (AllInOneCable)](https://github.com/skuep/AIOC) | Interface audio + PTT sur port USB — pas de carte son ni de circuit PTT externe |
| Radio VHF/UHF simplex | Compatible Kenwood 2-pin ou câble adapté |
| Carte micro-SD ≥ 8 Go | Raspberry Pi OS Lite |

**Connexions AIOC :**
- Audio RX → `aioc_shared` (dsnoop ALSA)
- Audio TX → `plug:aioc_hw` (ALSA exclusif)
- PTT → signal DTR sur `/dev/ttyACM0`

---

## Installation

```bash
# 1. Cloner le dépôt
git clone https://github.com/samuelcoustet/parlex /opt/parlex
cd /opt/parlex

# 2. Installer les dépendances Python
pip install -r requirements.txt
# sounddevice, pyserial, pyyaml, numpy, textual

# 3. Déployer le service systemd
sudo bash install/install.sh

# 4. Activer et démarrer
sudo systemctl enable parlex
sudo systemctl start parlex
```

### Configuration ALSA requise (`/etc/asound.conf`)

```
pcm.aioc_hw {
    type hw
    card AIOC
    device 0
}
pcm.aioc_shared {
    type dsnoop
    ipc_key 2048
    slave { pcm aioc_hw; rate 48000; channels 1; }
}
```

---

## Démarrage rapide

```bash
parlex run                  # daemon mode texte
parlex run --tui            # interface Textual (recommandé)
parlex run --curses         # interface curses (RPi sans X)
parlex status               # état + configuration complète
```

---

## CLI — référence complète

### Lancer le daemon

```bash
parlex run [--tui] [--curses] [--config PATH]
           [--log-level DEBUG|INFO|WARNING|ERROR]
           [--log-file /var/log/parlex.log]
```

### État et statistiques

```bash
parlex status          # configuration complète + état live si daemon actif
parlex stats           # QSO count, TX total, dernier QSO
```

`status` et `stats` lisent `/run/parlex/status.json`
(écrit toutes les 2 s par le daemon). Sans daemon, ils lisent le YAML.

---

### Configuration

```bash
parlex config list                        # tous les paramètres
parlex config get vox_threshold           # lire un paramètre
parlex config set vox_threshold 0.015     # modifier (typage automatique)
parlex config set repeater_on true
parlex config set tx_gain 3.5
parlex config set cw_id_text "F4XYZ/R"
parlex config reset                       # valeurs usine
```

Les modifications sont écrites immédiatement dans `config.yaml`.
Les paramètres VOX sont appliqués en live ; les autres nécessitent un redémarrage du service.

---

### Voicemail

```bash
parlex voicemail list           # inventaire : numéro, durée, date, taille
parlex voicemail erase 2        # effacer le message n°2
parlex voicemail erase-all      # vider la boîte
```

---

### Annonces programmées

```bash
parlex announce list                    # état des 10 slots (0-9)
parlex announce set 0 3600             # slot 0 : toutes les heures
parlex announce set 1 7200 300         # slot 1 : toutes les 2h, offset +5 min
parlex announce set 2 0                # désactiver le timer du slot 2
parlex announce erase 3                # effacer le fichier audio du slot 3
```

---

## Commandes DTMF

Toutes les commandes sont préfixées par `##` (double dièse).
Le relais répond par un bip de confirmation (OK / négatif / erreur / verrouillé).

### Accès et sécurité

| Commande | Description |
|---|---|
| `##00` | Verrouille le relais |
| `##00XXX` | Déverrouille avec le code sécurité XXX |
| `##01` | Ping — répond bip OK |
| `##08XXXXXXYYY` | Modifie le code sécurité (XXX = nouveau, YYY = confirmation) |
| `##09MM` | Auto-lock timer (MM minutes, 00 = désactivé) |

### Fonctionnement général

| Commande | Description |
|---|---|
| `0` | Say-again — réémet la dernière réception |
| `1` | Enregistrer un message voicemail |
| `##02` | Consulter la boîte voicemail |
| `##06MM` | Auto-off timer (MM minutes) |
| `##14n` | Say-again : 0 = OFF, 1 = ON |
| `##70` | Relais OFF |
| `##71` | Relais ON |
| `##72` | Voicemail OFF |
| `##73` | Voicemail ON |
| `##74` | Effacer tous les messages voicemail |
| `##75n` | Responder mode : 0 = OFF, 1 = ON |
| `##78XXX` | Code pager |
| `##79nn` | Suppression queue de squelch (× 1/75 s) |

### Audio et niveaux

| Commande | Description |
|---|---|
| `##11nn` | Niveau audio TX (00-99) |
| `##12n` | Courtesy tone (0 = aucun … 6 = bip simple) |
| `##13nn` | Timeout VOX (1/10 s, ex : 20 = 2,0 s) |
| `##77n` | Délai courtesy tone : 0 = OFF, 1 = ON |
| `##92MM` | Délai PTT avant audio (MM × 1/10 s) |
| `##107n` | Gain entrée : 0 = 1×, 1 = 2×, 2 = 4× |
| `##109n` | Mode squelch : 0 = VOX, 1 = COR actif-haut, 2 = COR actif-bas |

### Identification CW Morse

| Commande | Description |
|---|---|
| `##80n` | CW ID : 0 = OFF, 1 = ON, 2 = Cleanup OFF, 3 = Cleanup ON |
| `##81nn` | Vitesse CW (00-99 → WPM) |
| `##82...` | Texte CW (paires encodées : A = 21, B = 22 … 0 = 99, fin = 00) |
| `##83MM` | Timer CW ID (MM minutes) |
| `##84MM` | Timer inhibition CW (MM minutes) |
| `##85MM` | Timer inhibition Voice ID (MM minutes) |
| `##15n` | CW responder : 0 = OFF, 1 = ON |

### Identification vocale

| Commande | Description |
|---|---|
| `##160` | Voice ID OFF |
| `##161` | Voice ID ON |
| `##162` | Preamble OFF |
| `##163` | Preamble ON |
| `##168` | Rotation OFF |
| `##169` | Rotation ON |
| `##164` | Stand-by message OFF |
| `##165` | Stand-by beep ON |
| `##165n` | Stand-by message slot n (1-9) |

### Timing

| Commande | Description |
|---|---|
| `##17MM` | Timeout TX max (MM minutes, 00 = désactivé) |
| `##17SSS` | Timeout TX max (SSS secondes) |
| `##18MM` | Cooldown time (MM minutes) |
| `##19nn` | Durée TX minimum (1/10 s) |

### Annonces (slots 0-9)

| Commande | Description |
|---|---|
| `##2n` | Rejouer l'annonce du slot n |
| `##3n` | Enregistrer une annonce dans le slot n |
| `##4n` | Effacer l'annonce du slot n |
| `##5nMM` | Interval d'émission slot n (MM minutes) |
| `##5nSSS` | Interval d'émission slot n (SSS secondes) |
| `##5nHHMM` | Interval d'émission slot n (HH heures MM minutes) |
| `##6nMM` | Offset temporel slot n (MM minutes) |

### Voicemail (mode VM_PLAYBACK)

Une fois en mode écoute (`##02` ou `*` + code), les touches suivantes naviguent :

| Touche | Description |
|---|---|
| `1` | Message précédent |
| `2` | Rejouer le message courant |
| `3` | Message suivant |
| `0` | Effacer le message courant |
| `*` | Quitter la boîte vocale |

### Format des temps

| Format | Interprétation | Exemple |
|---|---|---|
| `MM` (2 chiffres) | Minutes | `05` = 5 min |
| `SSS` (3 chiffres) | Secondes | `090` = 90 s |
| `HHMM` (4 chiffres) | Heures + minutes | `0130` = 1 h 30 |

---

## Paramètres de configuration

Modifiables via `parlex config set` ou via la TUI.

### Relais

| Paramètre | Défaut | Description |
|---|---|---|
| `repeater_on` | `true` | Activation du store-and-forward |
| `say_again_on` | `true` | Fonction say-again (touche 0) |
| `voicemail_on` | `false` | Boîte voicemail |
| `responder_mode` | `false` | Mode répondeur automatique |
| `auto_off_timer` | `0` | Auto-extinction en secondes (0 = désactivé) |
| `standby_msg` | `-1` | Message stand-by (-1 = OFF, 0 = bip, 1-9 = slot annonce) |
| `pager_code` | `""` | Code DTMF pager (vide = désactivé) |
| `usage_count` | `0` | Compteur persistant de transmissions |

### Audio

| Paramètre | Défaut | Description |
|---|---|---|
| `alsa_capture` | `aioc_shared` | Périphérique ALSA entrée (dsnoop) |
| `alsa_playback` | `plug:aioc_hw` | Périphérique ALSA sortie |
| `sample_rate` | `48000` | Fréquence d'échantillonnage Hz |
| `serial_port` | `/dev/ttyACM0` | Port série AIOC (PTT DTR) |
| `tx_gain` | `3.5` | Multiplicateur gain TX (peut dépasser 1,0) |
| `tx_audio_level` | `99` | Niveau TX 0-99 (commande ##11) |
| `input_gain` | `0` | Gain entrée : 0 = 1×, 1 = 2×, 2 = 4× |
| `courtesy_tone` | `1` | Style de bip de courtoisie (0-6) |
| `tx_delay` | `0.5` | Délai PTT → audio en secondes |
| `ctcss_enabled` | `false` | Sous-tonalité CTCSS sur TX |
| `ctcss_freq` | `88.5` | Fréquence CTCSS en Hz |

### VOX / Squelch

| Paramètre | Défaut | Description |
|---|---|---|
| `cor_mode` | `0` | 0 = VOX, 1 = COR actif-haut, 2 = COR actif-bas |
| `cor_gpio` | `null` | Pin GPIO BCM pour COR matériel |
| `vox_threshold` | `0.02` | Seuil RMS VOX (fraction de pleine échelle) |
| `vox_timeout` | `2.0` | Fermeture VOX après N secondes de silence |
| `squelch_tail_supp` | `0.0` | Suppression queue de squelch (secondes) |

### Timing

| Paramètre | Défaut | Description |
|---|---|---|
| `min_tx_time` | `0.2` | Durée TX minimale (secondes) |
| `max_tx_time` | `0.0` | Durée TX maximale (0 = illimitée) |
| `cooldown_time` | `0.0` | Attente entre deux TX (0 = désactivé) |

### Sécurité

| Paramètre | Défaut | Description |
|---|---|---|
| `security_code` | `000` | Code DTMF pour déverrouiller |
| `locked` | `false` | État de verrouillage |
| `auto_lock_timer` | `0` | Auto-lock après N secondes sans commande |

### CW ID Morse

| Paramètre | Défaut | Description |
|---|---|---|
| `cw_id_text` | `""` | Texte CW (12 caractères max) |
| `cw_id_on` | `false` | CW ID automatique |
| `cw_id_timer` | `600` | Période CW ID (secondes) |
| `cw_cleanup_id` | `false` | CW ID en fin de TX |
| `cw_wpm` | `15` | Vitesse en mots par minute |
| `cw_freq` | `800` | Fréquence ton CW (Hz) |

### Voice ID

| Paramètre | Défaut | Description |
|---|---|---|
| `voice_id_on` | `false` | Voice ID automatique |
| `voice_id_inhibit` | `600` | Inhibition Voice ID (secondes) |
| `voice_id_rotate` | `false` | Rotation entre les slots d'annonces |
| `voice_preamble` | `false` | Preamble avant chaque TX |

---

## Voicemail

**Enregistrer :** émettre `1` en attente → mode `VM_RECORD` → fermeture squelch = fin.

**Consulter :** émettre `##02` ou `*` + code → mode `VM_PLAYBACK`.

Stockage : `/var/lib/parlex/voicemail/` (WAV 48 kHz mono S16_LE + JSON).

---

## Annonces programmées

10 slots (0-9). Chaque slot contient un fichier WAV enregistré par DTMF `##3n`
et un timer d'émission automatique.

**Enregistrer le slot 0 :** émettre `##30` → enregistre jusqu'à fermeture du squelch.

**Programmer toutes les heures :** `parlex announce set 0 3600` ou `##500100` (DTMF).

Stockage : `/var/lib/parlex/announce/ann_XX.wav`.

---

## Interface TUI

```bash
parlex run --tui       # Textual (recommandé)
parlex run --curses    # fallback sans dépendances tierces
```

Barre d'état permanente :
```
▶ IDLE              ○ PTT   ████░░░░░░░░  DTMF: ##70____   QSO: 5  TX: 2m30s
```

7 onglets (navigation `Tab`) :

| Onglet | Contenu |
|---|---|
| **Relais** | Activation, say-again, timings, stand-by, pager |
| **Audio** | VOX, gains, courtesy tone, CTCSS |
| **ID / CW** | Morse, Voice ID, preamble |
| **Voicemail** | Inventaire durées/dates + paramètres |
| **Annonces** | 10 slots : état, durée, timer, offset |
| **Sécurité** | Lock, codes, stats QSO session |
| **Journal** | Log événements temps réel |

Raccourcis : `q` quitter · `s` sauvegarder · `r` rafraîchir.

---

## Architecture logicielle

```
parlex/
├── main.py          CLI (argparse subcommands), daemon, status.json
├── config.py        RepeaterConfig dataclass, persistance YAML
├── repeater.py      Machine à états (IDLE→RECORDING→TRANSMITTING→COOLDOWN)
├── audio.py         ALSACapture (sounddevice), VOXDetector, Recorder (pre-buffer 0.5 s)
├── ptt.py           PTTController (DTR série AIOC), CORMonitor (GPIO)
├── dtmf.py          DTMFDecoder (FFT+Hamming, debounce 3-hits, gap 0.5 s)
├── commands.py      CommandParser (commandes ##), dispatch
├── tones.py         Courtesy tones, CW Morse, bips système
├── storage.py       AnnouncementStore, VoicemailStore, SayAgainBuffer
├── announcements.py AnnouncementEngine (threading.Timer, offsets)
└── tui.py           RepeaterTUI (Textual 7 onglets) + fallback curses
```

Chaîne audio :
```
Radio → AIOC → sounddevice float32 48 kHz
                    │
        ┌───────────┼──────────┐
        │           │          │
   pre-buffer   DTMFDecoder  VOXDetector
   (0.5 s)      FFT+Hamming  RMS seuil
        │
        ▼ (VOX open)
     Recorder → float32 × tx_gain + CTCSS → sounddevice → AIOC → Radio
```

---

## Dépendances

```
sounddevice >= 0.4.6    Audio I/O ALSA
pyserial    >= 3.5      PTT DTR série (AIOC)
pyyaml      >= 6.0      Configuration YAML
numpy       >= 1.21     DSP : FFT, RMS, CW, pre-buffer
textual     >= 0.50     TUI (optionnel, fallback curses intégré)
```

---

---

# English documentation

> 🇫🇷 **[Retour à la documentation française](#parlex--relais-simplex-store-and-forward--rpi-zero--aioc)**

---

# Parlex — Simplex Store-and-Forward Repeater · RPi Zero + AIOC

Python simplex store-and-forward repeater running on a **Raspberry Pi Zero**
with **[AIOC (AllInOneCable)](https://github.com/skuep/AIOC)** as the USB audio/PTT interface.
No external sound card or PTT circuit required.

---

## Table of Contents

1. [Hardware](#hardware)
2. [Installation](#installation-1)
3. [Quick Start](#quick-start)
4. [CLI Reference](#cli-reference)
5. [DTMF Commands](#dtmf-commands)
6. [Configuration Parameters](#configuration-parameters)
7. [Voicemail](#voicemail-1)
8. [Timed Announcements](#timed-announcements)
9. [TUI Interface](#tui-interface)
10. [Software Architecture](#software-architecture)
11. [Dependencies](#dependencies)

---

## Hardware

| Component | Details |
|---|---|
| Raspberry Pi Zero W / 2W | ARM processor, ≥ 512 MB RAM |
| [AIOC (AllInOneCable)](https://github.com/skuep/AIOC) | USB audio + PTT interface — no external hardware needed |
| VHF/UHF simplex radio | Kenwood 2-pin compatible or adapted cable |
| micro-SD card ≥ 8 GB | Raspberry Pi OS Lite |

**AIOC connections:**
- Audio RX → `aioc_shared` (ALSA dsnoop)
- Audio TX → `plug:aioc_hw` (ALSA exclusive)
- PTT → DTR signal on `/dev/ttyACM0`

---

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/samuelcoustet/parlex /opt/parlex
cd /opt/parlex

# 2. Install Python dependencies
pip install -r requirements.txt
# sounddevice, pyserial, pyyaml, numpy, textual

# 3. Deploy the systemd service
sudo bash install/install.sh

# 4. Enable and start
sudo systemctl enable parlex
sudo systemctl start parlex
```

### Required ALSA configuration (`/etc/asound.conf`)

```
pcm.aioc_hw {
    type hw
    card AIOC
    device 0
}
pcm.aioc_shared {
    type dsnoop
    ipc_key 2048
    slave { pcm aioc_hw; rate 48000; channels 1; }
}
```

---

## Quick Start

```bash
parlex run                  # daemon, text mode
parlex run --tui            # Textual interface (recommended)
parlex run --curses         # curses interface (headless RPi)
parlex status               # full status + live state if daemon is running
```

---

## CLI Reference

### Start the daemon

```bash
parlex run [--tui] [--curses] [--config PATH]
           [--log-level DEBUG|INFO|WARNING|ERROR]
           [--log-file /var/log/parlex.log]
```

### Status and statistics

```bash
parlex status          # full config + live state if daemon is running
parlex stats           # QSO count, total TX time, last QSO
```

`status` and `stats` read `/run/parlex/status.json`
(written every 2 s by the daemon). Without a running daemon they fall back to the YAML file.

---

### Configuration

```bash
parlex config list                        # list all parameters
parlex config get vox_threshold           # read a parameter
parlex config set vox_threshold 0.015     # set a parameter (auto-typed)
parlex config set repeater_on true
parlex config set tx_gain 3.5
parlex config set cw_id_text "W1XYZ/R"
parlex config reset                       # factory defaults
```

Changes are written to `config.yaml` immediately.
VOX parameters are applied live; others require a service restart.

---

### Voicemail

```bash
parlex voicemail list           # inventory: number, duration, date, size
parlex voicemail erase 2        # erase message #2
parlex voicemail erase-all      # clear the mailbox
```

---

### Timed Announcements

```bash
parlex announce list                    # status of all 10 slots (0-9)
parlex announce set 0 3600             # slot 0: every hour
parlex announce set 1 7200 300         # slot 1: every 2 h, +5 min offset
parlex announce set 2 0                # disable slot 2 timer
parlex announce erase 3                # erase audio file from slot 3
```

---

## DTMF Commands

All commands are prefixed with `##` (double hash).
The repeater responds with a confirmation beep (OK / negative / error / locked).

### Access and security

| Command | Description |
|---|---|
| `##00` | Lock the repeater |
| `##00XXX` | Unlock with security code XXX |
| `##01` | Ping — responds with OK beep |
| `##08XXXXXXYYY` | Change security code (XXX = new, YYY = confirmation) |
| `##09MM` | Auto-lock timer (MM minutes, 00 = disabled) |

### General operation

| Command | Description |
|---|---|
| `0` | Say-again — retransmit last received audio |
| `1` | Record a voicemail message |
| `##02` | Access voicemail mailbox |
| `##06MM` | Auto-off timer (MM minutes) |
| `##14n` | Say-again: 0 = OFF, 1 = ON |
| `##70` | Repeater OFF |
| `##71` | Repeater ON |
| `##72` | Voicemail OFF |
| `##73` | Voicemail ON |
| `##74` | Erase all voicemail messages |
| `##75n` | Responder mode: 0 = OFF, 1 = ON |
| `##78XXX` | Pager code |
| `##79nn` | Squelch tail suppression (× 1/75 s) |

### Audio and levels

| Command | Description |
|---|---|
| `##11nn` | TX audio level (00-99) |
| `##12n` | Courtesy tone style (0 = none … 6 = single beep) |
| `##13nn` | VOX timeout (1/10 s, e.g. 20 = 2.0 s) |
| `##77n` | Courtesy tone delay: 0 = OFF, 1 = ON |
| `##92MM` | PTT-to-audio delay (MM × 1/10 s) |
| `##107n` | Input gain: 0 = 1×, 1 = 2×, 2 = 4× |
| `##109n` | Squelch mode: 0 = VOX, 1 = COR active-high, 2 = COR active-low |

### CW Morse ID

| Command | Description |
|---|---|
| `##80n` | CW ID: 0 = OFF, 1 = ON, 2 = Cleanup OFF, 3 = Cleanup ON |
| `##81nn` | CW speed (00-99 → WPM) |
| `##82...` | CW text (encoded pairs: A = 21, B = 22 … 0 = 99, end = 00) |
| `##83MM` | CW ID timer (MM minutes) |
| `##84MM` | CW inhibit timer (MM minutes) |
| `##85MM` | Voice ID inhibit timer (MM minutes) |
| `##15n` | CW responder: 0 = OFF, 1 = ON |

### Voice ID

| Command | Description |
|---|---|
| `##160` | Voice ID OFF |
| `##161` | Voice ID ON |
| `##162` | Preamble OFF |
| `##163` | Preamble ON |
| `##168` | Rotation OFF |
| `##169` | Rotation ON |
| `##164` | Stand-by message OFF |
| `##165` | Stand-by beep ON |
| `##165n` | Stand-by message slot n (1-9) |

### Timing

| Command | Description |
|---|---|
| `##17MM` | Max TX timeout (MM minutes, 00 = disabled) |
| `##17SSS` | Max TX timeout (SSS seconds) |
| `##18MM` | Cooldown time (MM minutes) |
| `##19nn` | Minimum TX time (1/10 s) |

### Announcements (slots 0-9)

| Command | Description |
|---|---|
| `##2n` | Play announcement slot n |
| `##3n` | Record announcement into slot n |
| `##4n` | Erase announcement slot n |
| `##5nMM` | Broadcast interval slot n (MM minutes) |
| `##5nSSS` | Broadcast interval slot n (SSS seconds) |
| `##5nHHMM` | Broadcast interval slot n (HH hours MM minutes) |
| `##6nMM` | Time offset slot n (MM minutes) |

### Voicemail navigation (VM_PLAYBACK mode)

Enter via `##02` or `*` + voicemail code.

| Key | Description |
|---|---|
| `1` | Previous message |
| `2` | Replay current message |
| `3` | Next message |
| `0` | Erase current message |
| `*` | Exit mailbox |

### Time format

| Format | Interpretation | Example |
|---|---|---|
| `MM` (2 digits) | Minutes | `05` = 5 min |
| `SSS` (3 digits) | Seconds | `090` = 90 s |
| `HHMM` (4 digits) | Hours + minutes | `0130` = 1 h 30 |

---

## Configuration Parameters

Editable via `parlex config set` or through the TUI.

### Repeater

| Parameter | Default | Description |
|---|---|---|
| `repeater_on` | `true` | Enable store-and-forward |
| `say_again_on` | `true` | Say-again function (key 0) |
| `voicemail_on` | `false` | Voicemail mailbox |
| `responder_mode` | `false` | Auto-responder mode |
| `auto_off_timer` | `0` | Auto-off in seconds (0 = disabled) |
| `standby_msg` | `-1` | Stand-by message (-1 = OFF, 0 = beep, 1-9 = announcement slot) |
| `pager_code` | `""` | DTMF pager code (empty = disabled) |
| `usage_count` | `0` | Persistent transmission counter |

### Audio

| Parameter | Default | Description |
|---|---|---|
| `alsa_capture` | `aioc_shared` | ALSA input device (dsnoop) |
| `alsa_playback` | `plug:aioc_hw` | ALSA output device |
| `sample_rate` | `48000` | Sample rate in Hz |
| `serial_port` | `/dev/ttyACM0` | AIOC serial port (PTT DTR) |
| `tx_gain` | `3.5` | TX gain multiplier (can exceed 1.0) |
| `tx_audio_level` | `99` | TX level 0-99 (##11 command) |
| `input_gain` | `0` | Input gain: 0 = 1×, 1 = 2×, 2 = 4× |
| `courtesy_tone` | `1` | Courtesy tone style (0-6) |
| `tx_delay` | `0.5` | PTT-to-audio delay in seconds |
| `ctcss_enabled` | `false` | CTCSS subtone on TX |
| `ctcss_freq` | `88.5` | CTCSS frequency in Hz |

### VOX / Squelch

| Parameter | Default | Description |
|---|---|---|
| `cor_mode` | `0` | 0 = VOX, 1 = COR active-high, 2 = COR active-low |
| `cor_gpio` | `null` | BCM GPIO pin for hardware COR |
| `vox_threshold` | `0.02` | VOX RMS threshold (0–1 fraction of full scale) |
| `vox_timeout` | `2.0` | VOX close delay after silence (seconds) |
| `squelch_tail_supp` | `0.0` | Squelch tail suppression (seconds) |

### Timing

| Parameter | Default | Description |
|---|---|---|
| `min_tx_time` | `0.2` | Minimum TX duration (seconds) |
| `max_tx_time` | `0.0` | Maximum TX duration (0 = unlimited) |
| `cooldown_time` | `0.0` | Mandatory wait between transmissions (0 = disabled) |

### Security

| Parameter | Default | Description |
|---|---|---|
| `security_code` | `000` | DTMF unlock code |
| `locked` | `false` | Lock state |
| `auto_lock_timer` | `0` | Auto-lock after N seconds of inactivity |

### CW ID

| Parameter | Default | Description |
|---|---|---|
| `cw_id_text` | `""` | CW ID text (max 12 characters) |
| `cw_id_on` | `false` | Automatic CW ID |
| `cw_id_timer` | `600` | CW ID period (seconds) |
| `cw_cleanup_id` | `false` | CW ID at end of TX |
| `cw_wpm` | `15` | Speed in words per minute |
| `cw_freq` | `800` | CW tone frequency (Hz) |

### Voice ID

| Parameter | Default | Description |
|---|---|---|
| `voice_id_on` | `false` | Automatic Voice ID |
| `voice_id_inhibit` | `600` | Voice ID inhibit period (seconds) |
| `voice_id_rotate` | `false` | Rotate through announcement slots |
| `voice_preamble` | `false` | Play preamble before each TX |

---

## Voicemail

**Record:** transmit `1` while idle → `VM_RECORD` mode → squelch close ends recording.

**Retrieve:** transmit `##02` or `*` + voicemail code → `VM_PLAYBACK` mode.

Storage: `/var/lib/parlex/voicemail/` (WAV 48 kHz mono S16_LE + JSON metadata).

---

## Timed Announcements

10 slots (0-9). Each slot holds a WAV file recorded via DTMF `##3n`
and a configurable broadcast timer.

**Record slot 0:** transmit `##30` → records until squelch closes.

**Schedule every hour:** `parlex announce set 0 3600` or `##500100` (DTMF).

Storage: `/var/lib/parlex/announce/ann_XX.wav`.

---

## TUI Interface

```bash
parlex run --tui       # Textual (recommended)
parlex run --curses    # built-in fallback, no extra dependencies
```

Persistent status bar:
```
▶ IDLE              ○ PTT   ████░░░░░░░░  DTMF: ##70____   QSO: 5  TX: 2m30s
```

7 tabs (navigate with `Tab`):

| Tab | Content |
|---|---|
| **Repeater** | Enable, say-again, timings, stand-by, pager |
| **Audio** | VOX, gains, courtesy tone, CTCSS |
| **ID / CW** | Morse, Voice ID, preamble |
| **Voicemail** | Message inventory with durations + access settings |
| **Announcements** | 10 slots: state, duration, timer, offset |
| **Security** | Lock, codes, QSO session stats |
| **Log** | Live event log |

Keyboard shortcuts: `q` quit · `s` save · `r` refresh.

---

## Software Architecture

```
parlex/
├── main.py          CLI (argparse subcommands), daemon, status.json writer
├── config.py        RepeaterConfig dataclass, YAML persistence
├── repeater.py      State machine (IDLE→RECORDING→TRANSMITTING→COOLDOWN)
├── audio.py         ALSACapture (sounddevice), VOXDetector, Recorder (0.5 s pre-buffer)
├── ptt.py           PTTController (AIOC DTR), CORMonitor (GPIO)
├── dtmf.py          DTMFDecoder (FFT+Hamming, 3-hit debounce, 0.5 s gap)
├── commands.py      CommandParser (## commands), dispatcher
├── tones.py         Courtesy tones, CW Morse, system beeps
├── storage.py       AnnouncementStore, VoicemailStore, SayAgainBuffer
├── announcements.py AnnouncementEngine (threading.Timer, offsets)
└── tui.py           RepeaterTUI (Textual 7 tabs) + curses fallback
```

Audio chain:
```
Radio → AIOC → sounddevice float32 48 kHz
                    │
        ┌───────────┼──────────┐
        │           │          │
   pre-buffer   DTMFDecoder  VOXDetector
   (0.5 s)      FFT+Hamming  RMS threshold
        │
        ▼ (VOX open)
     Recorder → float32 × tx_gain + CTCSS → sounddevice → AIOC → Radio
```

---

## Dependencies

```
sounddevice >= 0.4.6    ALSA audio I/O
pyserial    >= 3.5      PTT DTR serial (AIOC)
pyyaml      >= 6.0      YAML configuration
numpy       >= 1.21     DSP: FFT, RMS, CW, pre-buffer
textual     >= 0.50     TUI (optional, built-in curses fallback)
```
