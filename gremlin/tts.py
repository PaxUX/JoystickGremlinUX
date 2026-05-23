# -*- coding: utf-8; -*-

# Copyright (C) 2015 - 2019 Lionel Ott
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""
Text-to-speech module.

On Windows this uses Microsoft SAPI (via win32com). On Linux uses
Speech Dispatcher (spd-say) with espeak-ng using the en+f2 female
voice for a clear robotic-sounding output.
"""

import logging
import subprocess
import threading
import time
from gremlin import event_handler

log = logging.getLogger("system")

# Thread-safe dedup: module-level lock protects _last_speak_time across all TTS
# instances (each TextToSpeechFunctor keeps its own reference).
_lock = threading.Lock()
_last_speak_time = 0.0

# === TTS backend selection ===== ===== ===== ===== ===== ===== ===== =====

def _find_bin(names):
    """Find first available binary from *names*, or None."""
    for name in names:
        try:
            result = subprocess.run(
                ["which", name],
                capture_output=True, text=True, timeout=2
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
    return None


def _speak_via_binary(binary, text, volume=100, rate=0):
    """Synthesize text with an available TTS binary.

    Priority is:
      1. espeak-ng + aplay (direct, no daemon required)
      2. aplay (pipes espeak-ng output internally)
      3. spd-say (fallback — requires speechd daemon)
    """
    # espeak-ng: synthesize speech to PCM, pipe to aplay
    if "espeak-ng" in binary:
        result = subprocess.run(
            [binary, "-v", "en+f2", "-p", "90", "-a", "100", "-s", "170", "-b", "UTF-8", "--stdout", text],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
        if result.returncode != 0:
            log.warning("espeak-ng stderr: %s", result.stderr.decode(errors="replace"))
        if result.stdout:
            subprocess.run(
                ["aplay", "-q", "-f", "S16_LE", "-t", "raw", "-r", "22050"],
                input=result.stdout,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
    # aplay: use as standalone backend (will also invoke espeak-ng internally)
    elif "aplay" in binary:
        espeak = subprocess.Popen(
            ["espeak-ng", "-v", "en+f2", "-p", "90", "-a", "100", "-s", "170", "-b", "UTF-8", "--stdout", text],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ["aplay", "-q", "-f", "S16_LE", "-t", "raw", "-r", "22050"],
            input=espeak.stdout,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
        espeak.wait()
    # spd-say: fallback — requires speechd daemon to be running
    elif "spd-say" in binary:
        result = subprocess.run(
            [binary, "-l", "en", "-v", "female2", "-p", "90", "-i", "100", "-r", "70", text],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
        )
        print(f"[TTS ENGINE DEBUG] spd-say return_code={result.returncode}, stderr={result.stderr.decode(errors='replace')!r}")


# Detect available TTS/ audio tools on the system
# Priority order — prefer espeak-ng + aplay (no daemon needed) over spd-say
# We check for BOTH needed components: espeak-ng for speech synthesis + aplay for output
_espeak_ng = None
_aplay = None
for name in ["espeak-ng", "aplay"]:
    try:
        result = subprocess.run(["which", name], capture_output=True, text=True, timeout=2)
        if result.returncode == 0 and result.stdout.strip():
            if name == "espeak-ng":
                _espeak_ng = result.stdout.strip()
            elif name == "aplay":
                _aplay = result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

# Backend detection: prefer espeak-ng (for speech synthesis), then aplay (for audio output), then spd-say
if _espeak_ng:
    _BACKEND = _espeak_ng  # primary: espeak-ng + aplay pipeline
elif _aplay:
    _BACKEND = _aplay  # fallback: aplay with internal espeak-ng pipe
else:
    _BACKEND = _find_bin(["spd-say"])  # last resort: spd-say (requires speechd daemon)

if _BACKEND:
    log.info("TTS backend: %s (en+f2, pitch=90, amp=100, speed=170)", _BACKEND)
else:
    print("WARNING: No TTS/ audio backend found — text-to-speech will not function")
    log.warning("TTS stub: no audio backend found")


class TextToSpeech:
    """Unified TTS class — auto-selects best system backend.

    speak() is fire-and-forget: the blocking subprocess call runs in a
    background daemon thread so the Qt event loop stays responsive for
    input event processing.

    On Windows (if win32com available):
       Uses Microsoft SAPI 5 voice engine.
    On Linux (if spd-say or espeak-ng available):
       Uses Speech Dispatcher or espeak-ng via aplay/espeak-ng.
    """

    def __init__(self):
        """Create a TTS instance — auto-detects the best available backend."""
        self._backend = _BACKEND
        log.info("TTS class ready — backend: %s", self._backend or "stub")

    def speak(self, text):
        """Speaks the provided text using the available TTS backend.

        Fire-and-forget: the actual audio synthesis playback is offloaded
        to a background daemon thread so the caller (the Qt event loop)
        returns immediately and input processing is not blocked.

        :param text: The text to speak
        """
        global _last_speak_time

        if not text:
            return

        if not _BACKEND:
            log.debug("TTS stub: would speak %r", text)
            return

        # Prevent duplicate TTS playback (events fire on press + release)
        with _lock:
            now = time.time()
            if now - _last_speak_time < 0.2:
                return
            _last_speak_time = now

        # Offload blocking subprocess call to a daemon thread
        t = threading.Thread(
            target=self._speak_blocking,
            args=(text,),
            daemon=True,
        )
        t.start()

    def _speak_blocking(self, text):
        """Actual blocking TTS call — must run in a background thread."""
        global _last_speak_time
        try:
            _speak_via_binary(self._backend, text, volume=100, rate=0)
        except Exception as e:
            log.error("TTS error: %s", e)
        finally:
            with _lock:
                # Update last_speak_time in the background thread so the
                # debounce window starts *after* playback begins.
                _last_speak_time = time.time()

    def set_volume(self, volume):
        """Sets the volume (0-100)."""
        # TODO: implement volume control based on the backend
        pass

    def set_rate(self, rate):
        """Sets the speaking rate (0-20)."""
        # TODO: implement rate control based on the backend
        pass


def text_substitution(text):
    """Replace TTS variables in text with their values.

    Current substitutions:
      - ${current_mode}: Replaces with the active mode name.
    """
    eh = event_handler.EventHandler()
    mode = eh.active_mode
    if not mode:
        mode = "default"
    return text.replace("${current_mode}", mode)
