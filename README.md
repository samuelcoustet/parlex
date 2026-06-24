# relais simplex — RPi Zero + AIOC

Clone logiciel 1:1 du relais simplex **relais simplex store-and-forward**,
implémenté en Python sur **Raspberry Pi Zero** avec **AIOC** (AllInOneCable)
comme interface audio/PTT.

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

---

## Matériel requis

| Composant | Détail |
|---|---|
| Raspberry Pi Zero W / 2W | Processeur ARM, ≥512 Mo RAM |
| AIOC (AllInOneCable) | Interface audio + PTT sur port série USB |
| Radio VHF/UHF simplex | Compatible Kenwood 2-pin ou câble adapté |
| Carte micro-SD ≥8 Go | Raspberry Pi OS Lite |

**Connexions AIOC :**
- Audio RX → `aioc_shared` (dsnoop ALSA)
- Audio TX → `plug:aioc_hw` (ALSA exclusif)
- PTT → DTR sur `/dev/ttyACM0`

---

## Installation

```bash
# 1. Cloner le dépôt
git clone <repo> /opt/parlex
cd /opt/parlex

# 2. Installer les dépendances
pip install -r requirements.txt
# sounddevice, pyserial, pyyaml, numpy, textual

# 3. Installer le service systemd
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
# Démarrer le daemon (mode texte)
parlex run

# Avec TUI (Textual)
parlex run --tui

# Avec TUI curses (RPi sans environnement graphique)
parlex run --curses

# Voir l'état
parlex status
```

---

## CLI — référence complète

### Lancer le daemon

```bash
parlex run [--tui] [--curses] [--config PATH]
                     [--log-level DEBUG|INFO|WARNING|ERROR]
                     [--log-file /var/log/sr1.log]
```

### État et statistiques

```bash
parlex status          # config complète + état live si daemon actif
parlex stats           # QSO count, TX total, dernier QSO
```

`status` et `stats` lisent `/run/parlex/status.json`
(écrit toutes les 2s par le daemon). Sans daemon, ils lisent le YAML.

---

### Configuration

```bash
# Lister tous les paramètres
parlex config list

# Lire un paramètre
parlex config get vox_threshold
parlex config get repeater_on

# Modifier un paramètre (typage automatique)
parlex config set vox_threshold 0.015
parlex config set repeater_on true
parlex config set tx_gain 3.5
parlex config set cw_id_text "F4XYZ/R"
parlex config set courtesy_tone 1

# Remettre aux valeurs usine Parlex
parlex config reset
```

Les changements sont immédiatement écrits dans `config.yaml`. Si le daemon
tourne, certains paramètres (VOX threshold, VOX timeout) sont appliqués en
live via la TUI ; pour les autres, un redémarrage du service est nécessaire.

---

### Voicemail

```bash
# Inventaire (numéro, durée, date, taille)
parlex voicemail list

# Effacer le message n°2
parlex voicemail erase 2

# Vider toute la boîte
parlex voicemail erase-all
```

---

### Annonces programmées

```bash
# État des 10 slots (0-9)
parlex announce list

# Configurer slot 0 : toutes les heures (3600s), pas d'offset
parlex announce set 0 3600

# Slot 1 : toutes les 2h, offset +5min (300s) pour décaler par rapport au slot 0
parlex announce set 1 7200 300

# Désactiver le timer d'un slot (interval=0)
parlex announce set 2 0

# Effacer le fichier audio d'un slot
parlex announce erase 3
```

---

## Commandes DTMF

Toutes les commandes sont préfixées par `##` (double dièse).  
Le relais répond par un bip de confirmation (OK / négatif / erreur / verrouillé).

### Accès et sécurité

| Commande | Description |
|---|---|
| `##00` | Verrouille le relais |
| `##00XXX` | Déverrouille avec code (XXX = security_code) |
| `##01` | Ping (répond bip OK) |
| `##08XXXXXXYYY` | Modifie le code sécurité (XXX=nouveau, YYY=confirmation) |
| `##09MM` | Auto-lock timer (MM minutes, 00=désactivé) |

### Fonctionnement général

| Commande | Description |
|---|---|
| `0` | Say-again (réémet la dernière réception) |
| `1` | Enregistrer un message voicemail |
| `##02` | Consulter la boîte voicemail |
| `##06MM` | Auto-off timer (MM minutes) |
| `##14n` | Say-again : 0=OFF, 1=ON |
| `##70` | Relais OFF |
| `##71` | Relais ON |
| `##72` | Voicemail OFF |
| `##73` | Voicemail ON |
| `##74` | Effacer tous les messages voicemail |
| `##75n` | Responder mode : 0=OFF, 1=ON |
| `##78XXX` | Code pager (active la sortie relais sur réception de XXX) |
| `##79nn` | Suppression queue de squelch (1/75 s) |

### Audio et niveaux

| Commande | Description |
|---|---|
| `##11nn` | Niveau audio TX (00-99) |
| `##12n` | Courtesy tone (0=aucun … 6=bip simple) |
| `##13nn` | Timeout VOX (1/10 s, ex: 20 = 2,0s) |
| `##77n` | Délai courtesy tone : 0=OFF, 1=ON |
| `##92MM` | Délai PTT avant audio (MM × 1/10 s) |
| `##107n` | Gain entrée : 0=1×, 1=2×, 2=4× |
| `##109n` | Mode squelch : 0=VOX, 1=COR actif-haut, 2=COR actif-bas |

### Identification (CW Morse)

| Commande | Description |
|---|---|
| `##80n` | CW ID : 0=OFF, 1=ON, 2=Cleanup OFF, 3=Cleanup ON |
| `##81nn` | Vitesse CW (00-99 → WPM) |
| `##82...` | Texte CW (paires encodées A=21, B=22…, 0=99, fin=00) |
| `##83MM` | Timer CW ID (MM minutes) |
| `##84MM` | Timer inhibition CW (MM minutes) |
| `##85MM` | Timer inhibition Voice ID (MM minutes) |
| `##15n` | CW responder : 0=OFF, 1=ON |

### Identification (Voice)

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
| `##17MM` | Timeout TX max (MM minutes, 00=désactivé) |
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

### Voicemail (en mode VM_PLAYBACK)

Une fois en mode écoute (`##02` ou `*+code`), les touches suivantes
naviguent dans les messages :

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
| `HHMM` (4 chiffres) | Heures + minutes | `0130` = 1h30 |

---

## Paramètres de configuration

Tous modifiables via `parlex config set` ou via la TUI.

### Relais

| Paramètre | Défaut | Description |
|---|---|---|
| `repeater_on` | `true` | Activation du relais store-and-forward |
| `say_again_on` | `true` | Fonction say-again (touche 0) |
| `voicemail_on` | `false` | Boîte voicemail |
| `responder_mode` | `false` | Mode répondeur automatique |
| `auto_off_timer` | `0` | Auto-extinction en secondes (0=désactivé) |
| `standby_msg` | `-1` | Message stand-by (-1=OFF, 0=bip, 1-9=slot annonce) |
| `pager_code` | `""` | Code DTMF pager (vide=désactivé) |
| `usage_count` | `0` | Compteur persistant de transmissions |

### Audio

| Paramètre | Défaut | Description |
|---|---|---|
| `alsa_capture` | `aioc_shared` | Périphérique ALSA entrée (dsnoop) |
| `alsa_playback` | `plug:aioc_hw` | Périphérique ALSA sortie |
| `sample_rate` | `48000` | Fréquence d'échantillonnage Hz |
| `serial_port` | `/dev/ttyACM0` | Port série AIOC (PTT DTR) |
| `tx_gain` | `3.5` | Multiplicateur gain TX (peut être > 1.0) |
| `tx_audio_level` | `99` | Niveau TX 0-99 (compatibilité commande ##11) |
| `input_gain` | `0` | Gain entrée : 0=1×, 1=2×, 2=4× |
| `courtesy_tone` | `1` | Style de bip de courtoisie (0-6) |
| `courtesy_tone_delay` | `false` | Délai bip de courtoisie |
| `tx_delay` | `0.5` | Délai PTT→audio en secondes |
| `ctcss_enabled` | `false` | Sous-tonalité CTCSS sur TX |
| `ctcss_freq` | `88.5` | Fréquence CTCSS en Hz |

### VOX / Squelch

| Paramètre | Défaut | Description |
|---|---|---|
| `cor_mode` | `0` | 0=VOX, 1=COR actif-haut, 2=COR actif-bas |
| `cor_gpio` | `null` | Numéro pin GPIO BCM pour COR matériel |
| `vox_threshold` | `0.02` | Seuil RMS VOX (fraction de pleine échelle 0-1) |
| `vox_timeout` | `2.0` | Durée fermeture VOX après silence (secondes) |
| `squelch_tail_supp` | `0.0` | Suppression queue de squelch (secondes) |

### Timing

| Paramètre | Défaut | Description |
|---|---|---|
| `min_tx_time` | `0.2` | Durée TX minimale en secondes |
| `max_tx_time` | `0.0` | Durée TX maximale (0=illimitée) |
| `cooldown_time` | `0.0` | Attente obligatoire entre deux TX (0=désactivé) |

### Sécurité

| Paramètre | Défaut | Description |
|---|---|---|
| `security_code` | `000` | Code DTMF pour déverrouiller |
| `locked` | `false` | État de verrouillage |
| `auto_lock_timer` | `0` | Auto-lock après N secondes sans commande (0=désactivé) |

### ID Morse (CW)

| Paramètre | Défaut | Description |
|---|---|---|
| `cw_id_text` | `""` | Texte du CW ID (12 caractères max) |
| `cw_id_on` | `false` | Activation CW ID automatique |
| `cw_id_timer` | `600` | Période CW ID en secondes |
| `cw_cleanup_id` | `false` | CW ID en fin de TX (cleanup) |
| `cw_responder_on` | `false` | CW ID automatique sur chaque transmission reçue |
| `cw_inhibit_timer` | `600` | Inhibition CW après transmission (secondes) |
| `cw_wpm` | `15` | Vitesse CW en mots par minute |
| `cw_freq` | `800` | Fréquence ton CW en Hz |

### Voice ID

| Paramètre | Défaut | Description |
|---|---|---|
| `voice_id_on` | `false` | Activation Voice ID automatique |
| `voice_id_inhibit` | `600` | Période inhibition Voice ID (secondes) |
| `voice_id_rotate` | `false` | Rotation entre les slots d'annonces |
| `voice_preamble` | `false` | Émission preamble avant chaque TX |

### Voicemail

| Paramètre | Défaut | Description |
|---|---|---|
| `voicemail_code` | `000` | Code DTMF d'accès (* + code) |
| `voicemail_max` | `20` | Nombre maximum de messages |

---

## Voicemail

**Enregistrer un message :**  
Émettre `1` pendant que le relais est en attente → le relais passe en
mode `VM_RECORD` et enregistre jusqu'à la fermeture du squelch.

**Consulter les messages :**  
Émettre `##02` ou `*` + code voicemail → mode `VM_PLAYBACK`.

Les fichiers sont stockés dans `/var/lib/parlex/voicemail/`
(WAV 48kHz mono S16_LE + metadata JSON).

---

## Annonces programmées

10 slots (0-9). Chaque slot peut contenir un fichier audio WAV enregistré
via DTMF `##3n` et un timer d'émission automatique configurable.

**Enregistrer l'annonce du slot 0 :**  
Émettre `##30` → le relais enregistre jusqu'à fermeture du squelch.

**Programmer l'émission toutes les heures :**  
`parlex announce set 0 3600` ou `##500100` (DTMF : interval 1h00).

Les fichiers sont dans `/var/lib/parlex/announce/ann_XX.wav`.

---

## Interface TUI

Lancée avec `--tui` (Textual) ou `--curses` (fallback sans dépendances).

### Textual (`--tui`)

Barre d'état permanente :
```
▶ IDLE              ○ PTT   ████░░░░░░░░  DTMF: ##70____   QSO: 5  TX: 2m30s
```

7 onglets (`Tab` pour naviguer) :

| Onglet | Contenu |
|---|---|
| **Relais** | Activation, say-again, timings, stand-by, pager |
| **Audio** | VOX, gains, courtesy tone, CTCSS |
| **ID / CW** | Morse, Voice ID, preamble, paramètres CW |
| **Voicemail** | Inventaire avec durées + paramètres accès |
| **Annonces** | 10 slots : état, durée, timer, offset |
| **Sécurité** | Lock, codes, stats QSO session |
| **Journal** | Log événements temps réel |

Raccourcis : `q` quitter · `s` sauvegarder · `r` rafraîchir.  
Chaque onglet dispose d'un bouton **Sauvegarder** qui écrit `config.yaml`
immédiatement.

---

## Architecture logicielle

```
parlex/
├── main.py          CLI (subcommands argparse), daemon, écriture status.json
├── config.py        RepeaterConfig dataclass, valeurs Parlex, persistance YAML
├── repeater.py      Machine à états principale (IDLE→RECORD→TX→COOLDOWN)
├── audio.py         ALSACapture (sounddevice), ALSAPlayback, VOXDetector,
│                    Recorder (pre-buffer 0.5s), QSOStats, apply_ctcss()
├── ptt.py           PTTController (DTR série), CORMonitor (GPIO)
├── dtmf.py          DTMFDecoder (FFT+Hamming, debounce 3-hits, gap 0.5s)
├── commands.py      CommandParser (## DTMF Parlex), dispatch commandes
├── tones.py         Courtesy tones, CW Morse, bips système, calibration
├── storage.py       AnnouncementStore, VoicemailStore, SayAgainBuffer
├── announcements.py AnnouncementEngine (threading.Timer, offsets)
└── tui.py           RepeaterTUI (Textual 7 onglets), run_curses_tui()
```

### Machine à états

```
             VOX/COR ouvre
IDLE ─────────────────────► RECORDING
  ▲                              │ VOX/COR ferme
  │                              ▼
COOLDOWN ◄────────────── TRANSMITTING
  │  cooldown_time=0             │ (retransmet + bip courtoisie)
  └──────────────────────────────┘
```

États supplémentaires : `VM_RECORD`, `VM_PLAYBACK`, `REC_ANN`, `CAL_TONE`.

### Chaîne audio

```
Antenne → Radio → AIOC → sounddevice (float32 48kHz)
                               │
              ┌────────────────┼────────────────┐
              │                │                │
         pre-buffer        DTMFDecoder      VOXDetector
         (0.5s roulant)    (FFT+Hamming)    (RMS seuil)
              │
              ▼ (sur ouverture VOX)
           Recorder (pre-buffer + chunks en direct)
              │
              ▼ (sur fermeture VOX)
         audio float32 × tx_gain (3.5×) + CTCSS optionnel
              │
              ▼
         sounddevice → AIOC → Radio → Antenne
```

### Détection DTMF

Algorithme FFT+Hamming (identique projet Castanara) :
- Fenêtre glissante 4096 échantillons
- Seuil spectral : `pic_DTMF > moyenne × 15`
- Twist check : `0.5 < ratio_row/col < 2.0`
- Debounce : 3 détections consécutives (~150ms)
- Gap même digit : 0.5s minimum
- Timeout séquence : 4s de silence

### Fichiers de données

```
/etc/parlex/config.yaml          Configuration persistante
/var/lib/parlex/
    announce/ann_00.wav … ann_09.wav       Annonces enregistrées
    voicemail/vm_XXXX.wav + meta.json      Messages voicemail
/run/parlex/status.json          État live (daemon → CLI)
```

---

## Dépendances

```
sounddevice >= 0.4.6    Audio I/O (ALSA via libsoundio/PortAudio)
pyserial    >= 3.5      Contrôle PTT (DTR série AIOC)
pyyaml      >= 6.0      Persistance configuration
numpy       >= 1.21     DSP (FFT, RMS, CW, pré-buffer)
textual     >= 0.50     TUI principale (optionnel, fallback curses)
```
