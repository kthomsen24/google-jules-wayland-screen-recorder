"""
Screen Recorder Engine for Wayland, X11, and XWayland with ALSA/PulseAudio/JACK support.
"""

from enum import Enum
import os
import shutil
import signal
import subprocess
import time
from typing import Dict, List, Optional, Tuple, Any

from .audio import AudioBackend, AudioDevice, get_audio_devices


class DisplayPlatform(str, Enum):
    AUTO = "auto"
    WAYLAND = "wayland"
    X11 = "x11"
    XWAYLAND = "xwayland"


class ContainerFormat(str, Enum):
    MP4 = "mp4"
    MKV = "mkv"


class RecorderConfig:
    def __init__(
        self,
        output_file: str = "recording.mp4",
        format: ContainerFormat = ContainerFormat.MP4,
        platform: DisplayPlatform = DisplayPlatform.AUTO,
        audio_backend: AudioBackend = AudioBackend.NONE,
        audio_device_id: Optional[str] = None,
        framerate: int = 30,
        geometry: Optional[str] = None,  # e.g. "0,0 1920x1080"
        codec: Optional[str] = None,
    ):
        self.output_file = output_file
        self.format = format
        self.platform = platform
        self.audio_backend = audio_backend
        self.audio_device_id = audio_device_id
        self.framerate = framerate
        self.geometry = geometry
        self.codec = codec

    def validate(self) -> Tuple[bool, Optional[str]]:
        if not self.output_file:
            return False, "Output file path cannot be empty."

        ext = os.path.splitext(self.output_file)[1].lower().strip(".")
        if ext and ext not in ["mp4", "mkv"]:
            return False, f"Unsupported file extension '.{ext}'. Supported formats are mp4 and mkv."

        if self.framerate <= 0 or self.framerate > 144:
            return False, f"Invalid framerate: {self.framerate}. Must be between 1 and 144."

        return True, None


class ScreenRecorderEngine:
    def __init__(self, config: Optional[RecorderConfig] = None):
        self.config = config or RecorderConfig()
        self.process: Optional[subprocess.Popen] = None
        self.start_time: Optional[float] = None
        self.stop_time: Optional[float] = None
        self._last_error: Optional[str] = None

    @property
    def is_recording(self) -> bool:
        if self.process is None:
            return False
        return self.process.poll() is None

    def detect_platform(self) -> DisplayPlatform:
        if self.config.platform != DisplayPlatform.AUTO:
            return self.config.platform

        # Check environment variables
        wayland_display = os.environ.get("WAYLAND_DISPLAY")
        xdg_session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()

        if wayland_display or xdg_session_type == "wayland":
            return DisplayPlatform.WAYLAND
        elif os.environ.get("DISPLAY"):
            return DisplayPlatform.X11
        else:
            # Default to Wayland if wf-recorder exists, otherwise X11
            if shutil.which("wf-recorder"):
                return DisplayPlatform.WAYLAND
            return DisplayPlatform.X11

    def build_command(self) -> List[str]:
        platform = self.detect_platform()
        cmd: List[str] = []

        # Determine if we can use wf-recorder for Wayland
        use_wf_recorder = (platform == DisplayPlatform.WAYLAND) and (shutil.which("wf-recorder") is not None)

        if use_wf_recorder:
            cmd.append("wf-recorder")
            cmd.extend(["-f", self.config.output_file])
            cmd.extend(["-r", str(self.config.framerate)])

            if self.config.geometry:
                cmd.extend(["-g", self.config.geometry])

            if self.config.codec:
                cmd.extend(["-c", self.config.codec])

            # Audio setup for wf-recorder
            if self.config.audio_backend != AudioBackend.NONE:
                if self.config.audio_device_id and self.config.audio_device_id != "default":
                    cmd.append(f"--audio={self.config.audio_device_id}")
                else:
                    cmd.append("-a")
        else:
            # FFmpeg fallback path (for X11, XWayland, or Wayland KMS/Pipewire fallback)
            cmd.append("ffmpeg")
            cmd.extend(["-y"])  # Overwrite output file if exists

            # Video input selection
            if platform in (DisplayPlatform.X11, DisplayPlatform.XWAYLAND):
                display_str = os.environ.get("DISPLAY", ":0.0")
                cmd.extend(["-f", "x11grab"])
                cmd.extend(["-r", str(self.config.framerate)])
                if self.config.geometry:
                    # Geometry format x,y WxH -> WxH -i :0.0+x,y
                    try:
                        coords, size = self.config.geometry.split()
                        cmd.extend(["-s", size])
                        cmd.extend(["-i", f"{display_str}+{coords}"])
                    except Exception:
                        cmd.extend(["-i", display_str])
                else:
                    cmd.extend(["-i", display_str])
            elif platform == DisplayPlatform.WAYLAND:
                # Fallback for Wayland without wf-recorder using KMS/DRM or x11grab via Xwayland
                display_str = os.environ.get("DISPLAY", ":0.0")
                if os.environ.get("DISPLAY"):
                    cmd.extend(["-f", "x11grab", "-r", str(self.config.framerate), "-i", display_str])
                elif os.path.exists("/dev/dri/card0"):
                    cmd.extend(["-f", "kmsgrab", "-r", str(self.config.framerate), "-i", "-"])
                else:
                    cmd.extend(["-f", "x11grab", "-r", str(self.config.framerate), "-i", ":0.0"])

            # Audio input selection
            if self.config.audio_backend == AudioBackend.ALSA:
                device = self.config.audio_device_id or "default"
                cmd.extend(["-f", "alsa", "-i", device])
            elif self.config.audio_backend == AudioBackend.PULSEAUDIO:
                device = self.config.audio_device_id or "default"
                cmd.extend(["-f", "pulse", "-i", device])
            elif self.config.audio_backend == AudioBackend.JACK:
                device = self.config.audio_device_id or "default"
                cmd.extend(["-f", "jack", "-i", device])

            # Codec settings
            if self.config.codec:
                cmd.extend(["-c:v", self.config.codec])
            else:
                if self.config.format == ContainerFormat.MP4:
                    cmd.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p"])
                else:
                    cmd.extend(["-c:v", "libx264"])

            cmd.append(self.config.output_file)

        return cmd

    def start(self) -> Tuple[bool, Optional[str]]:
        if self.is_recording:
            return False, "Recording is already in progress."

        valid, err = self.config.validate()
        if not valid:
            return False, f"Invalid configuration: {err}"

        cmd = self.build_command()

        # Set environment flags based on target platform for GDK/Qt failsafe
        env = os.environ.copy()
        platform = self.detect_platform()
        if platform == DisplayPlatform.WAYLAND:
            env["GDK_BACKEND"] = "wayland,x11"
            env["QT_QPA_PLATFORM"] = "wayland;xcb"
        elif platform == DisplayPlatform.X11:
            env["GDK_BACKEND"] = "x11"
            env["QT_QPA_PLATFORM"] = "xcb"
        elif platform == DisplayPlatform.XWAYLAND:
            env["GDK_BACKEND"] = "x11"
            env["QT_QPA_PLATFORM"] = "xcb"

        try:
            self.process = subprocess.Popen(
                cmd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            self.start_time = time.time()
            self._last_error = None

            # Short sleep to check if command immediately failed
            time.sleep(0.3)
            if self.process.poll() is not None:
                _, stderr = self.process.communicate()
                self._last_error = stderr
                self.process = None
                return False, f"Recorder failed to start: {stderr.strip()}"

            return True, None
        except Exception as e:
            self.process = None
            self._last_error = str(e)
            return False, f"Failed to execute recorder command ({cmd[0]}): {e}"

    def stop(self) -> Tuple[bool, Optional[str]]:
        if not self.is_recording or self.process is None:
            return False, "No active recording to stop."

        try:
            self.stop_time = time.time()
            # Send SIGINT to gracefully terminate recorder and finalize container
            self.process.send_signal(signal.SIGINT)

            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(timeout=3)

            self.process = None
            return True, None
        except Exception as e:
            self.process = None
            return False, f"Error stopping recorder process: {e}"

    def get_status(self) -> Dict[str, Any]:
        duration = 0.0
        if self.start_time:
            if self.is_recording:
                duration = time.time() - self.start_time
            elif self.stop_time:
                duration = self.stop_time - self.start_time

        file_exists = os.path.exists(self.config.output_file)
        file_size = os.path.getsize(self.config.output_file) if file_exists else 0

        return {
            "is_recording": self.is_recording,
            "duration": round(duration, 2),
            "output_file": self.config.output_file,
            "file_size_bytes": file_size,
            "platform": self.detect_platform().value,
            "audio_backend": self.config.audio_backend.value,
            "audio_device": self.config.audio_device_id,
            "last_error": self._last_error
        }
