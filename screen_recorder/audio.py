"""
Audio detection and device management for ALSA, PulseAudio, and JACK.
"""

from enum import Enum
import os
import re
import shutil
import subprocess
from typing import List, Dict, Optional


class AudioBackend(str, Enum):
    NONE = "none"
    ALSA = "alsa"
    PULSEAUDIO = "pulseaudio"
    JACK = "jack"


class AudioDevice:
    def __init__(self, device_id: str, name: str, backend: AudioBackend):
        self.device_id = device_id
        self.name = name
        self.backend = backend

    def to_dict(self) -> Dict[str, str]:
        return {
            "id": self.device_id,
            "name": self.name,
            "backend": self.backend.value
        }

    def __repr__(self) -> str:
        return f"AudioDevice(id={self.device_id!r}, name={self.name!r}, backend={self.backend.value!r})"


def is_backend_available(backend: AudioBackend) -> bool:
    """Checks if tools or drivers for a given audio backend exist on the system."""
    if backend == AudioBackend.NONE:
        return True
    elif backend == AudioBackend.ALSA:
        # Check for /proc/asound or ffmpeg alsa support or arecord
        return os.path.exists("/proc/asound") or shutil.which("arecord") is not None or shutil.which("ffmpeg") is not None
    elif backend == AudioBackend.PULSEAUDIO:
        # Check pactl, pacmd, or pulseaudio / pipewire
        return shutil.which("pactl") is not None or shutil.which("pacmd") is not None or shutil.which("pulseaudio") is not None or shutil.which("pipewire") is not None
    elif backend == AudioBackend.JACK:
        # Check jackd, jack_lsp, or qjackctl
        return shutil.which("jack_lsp") is not None or shutil.which("jackd") is not None or shutil.which("jackd2") is not None
    return False


def detect_audio_backends() -> List[AudioBackend]:
    """Returns a list of all detected audio backends."""
    backends = [AudioBackend.NONE]
    for backend in [AudioBackend.ALSA, AudioBackend.PULSEAUDIO, AudioBackend.JACK]:
        if is_backend_available(backend):
            backends.append(backend)
    return backends


def get_alsa_devices() -> List[AudioDevice]:
    """Detect ALSA capture devices."""
    devices: List[AudioDevice] = []

    # Method 1: Check via ffmpeg -sources alsa
    if shutil.which("ffmpeg"):
        try:
            res = subprocess.run(["ffmpeg", "-sources", "alsa"], capture_output=True, text=True, timeout=5)
            out = res.stdout + res.stderr
            for line in out.splitlines():
                m = re.match(r'^\s*([\w:,\.-]+)\s+\[(.*)\]', line)
                if m and "Auto-detected" not in line and "PAGE" not in line:
                    dev_id, dev_name = m.group(1), m.group(2)
                    if not any(d.device_id == dev_id for d in devices):
                        devices.append(AudioDevice(dev_id, dev_name, AudioBackend.ALSA))
        except Exception:
            pass

    # Method 2: Check /proc/asound/cards
    if os.path.exists("/proc/asound/cards"):
        try:
            with open("/proc/asound/cards", "r") as f:
                content = f.read()
                for line in content.splitlines():
                    m = re.search(r'^\s*(\d+)\s+\[(\w+)\s*\]:\s*(.*)', line)
                    if m:
                        card_num = m.group(1)
                        card_name = m.group(3).strip()
                        dev_id = f"hw:{card_num},0"
                        if not any(d.device_id == dev_id for d in devices):
                            devices.append(AudioDevice(dev_id, f"Card {card_num}: {card_name}", AudioBackend.ALSA))
        except Exception:
            pass

    # Method 3: arecord -L
    if shutil.which("arecord"):
        try:
            res = subprocess.run(["arecord", "-L"], capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                current_id = None
                for line in res.stdout.splitlines():
                    if line and not line.startswith(" "):
                        current_id = line.strip()
                        if current_id and not any(d.device_id == current_id for d in devices):
                            devices.append(AudioDevice(current_id, f"ALSA: {current_id}", AudioBackend.ALSA))
        except Exception:
            pass

    # Default fallback
    if not devices:
        devices.append(AudioDevice("default", "Default ALSA Device", AudioBackend.ALSA))

    return devices


def get_pulseaudio_devices() -> List[AudioDevice]:
    """Detect PulseAudio capture sources."""
    devices: List[AudioDevice] = []

    # Method 1: pactl list sources short
    if shutil.which("pactl"):
        try:
            res = subprocess.run(["pactl", "list", "sources", "short"], capture_output=True, text=True, timeout=5)
            if res.returncode == 0 and res.stdout.strip():
                for line in res.stdout.splitlines():
                    parts = line.split()
                    if len(parts) >= 2:
                        dev_id = parts[1]
                        if not any(d.device_id == dev_id for d in devices):
                            devices.append(AudioDevice(dev_id, f"PulseAudio Source: {dev_id}", AudioBackend.PULSEAUDIO))
        except Exception:
            pass

    # Method 2: ffmpeg -sources pulse
    if shutil.which("ffmpeg"):
        try:
            res = subprocess.run(["ffmpeg", "-sources", "pulse"], capture_output=True, text=True, timeout=5)
            out = res.stdout + res.stderr
            for line in out.splitlines():
                m = re.match(r'^\s*([\w:,\.-]+)\s+\[(.*)\]', line)
                if m and "Auto-detected" not in line and "PAGE" not in line:
                    dev_id, dev_name = m.group(1), m.group(2)
                    if not any(d.device_id == dev_id for d in devices):
                        devices.append(AudioDevice(dev_id, dev_name, AudioBackend.PULSEAUDIO))
        except Exception:
            pass

    # Default fallback
    if not devices:
        devices.append(AudioDevice("default", "Default PulseAudio Source", AudioBackend.PULSEAUDIO))

    return devices


def get_jack_devices() -> List[AudioDevice]:
    """Detect JACK capture ports or clients."""
    devices: List[AudioDevice] = []

    if shutil.which("jack_lsp"):
        try:
            res = subprocess.run(["jack_lsp"], capture_output=True, text=True, timeout=5)
            if res.returncode == 0 and res.stdout.strip():
                for line in res.stdout.splitlines():
                    port = line.strip()
                    if port and not any(d.device_id == port for d in devices):
                        devices.append(AudioDevice(port, f"JACK Port: {port}", AudioBackend.JACK))
        except Exception:
            pass

    if shutil.which("ffmpeg"):
        try:
            res = subprocess.run(["ffmpeg", "-sources", "jack"], capture_output=True, text=True, timeout=5)
            out = res.stdout + res.stderr
            for line in out.splitlines():
                m = re.match(r'^\s*([\w:,\.-]+)\s+\[(.*)\]', line)
                if m and "Auto-detected" not in line and "PAGE" not in line:
                    dev_id, dev_name = m.group(1), m.group(2)
                    if not any(d.device_id == dev_id for d in devices):
                        devices.append(AudioDevice(dev_id, dev_name, AudioBackend.JACK))
        except Exception:
            pass

    # Default fallback
    if not devices:
        devices.append(AudioDevice("system:capture_1", "Default JACK Capture Port (system:capture_1)", AudioBackend.JACK))

    return devices


def get_audio_devices(backend: AudioBackend) -> List[AudioDevice]:
    """Retrieves available audio devices for a specified audio backend."""
    if backend == AudioBackend.NONE:
        return []
    elif backend == AudioBackend.ALSA:
        return get_alsa_devices()
    elif backend == AudioBackend.PULSEAUDIO:
        return get_pulseaudio_devices()
    elif backend == AudioBackend.JACK:
        return get_jack_devices()
    else:
        return []
