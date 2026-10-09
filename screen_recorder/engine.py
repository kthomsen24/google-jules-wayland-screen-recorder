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
        force_ffmpeg: bool = False,
    ):
        self.output_file = output_file
        self.format = format
        self.platform = platform
        self.audio_backend = audio_backend
        self.audio_device_id = audio_device_id
        self.framerate = framerate
        self.geometry = geometry
        self.codec = codec
        self.force_ffmpeg = force_ffmpeg

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
        self._active_backend_cmd: Optional[str] = None

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
            if shutil.which("wf-recorder"):
                return DisplayPlatform.WAYLAND
            return DisplayPlatform.X11

    def build_command(self, force_ffmpeg_override: bool = False) -> List[str]:
        platform = self.detect_platform()
        cmd: List[str] = []

        use_wf_recorder = (
            (platform == DisplayPlatform.WAYLAND)
            and (shutil.which("wf-recorder") is not None)
            and not self.config.force_ffmpeg
            and not force_ffmpeg_override
        )

        if use_wf_recorder:
            cmd.append("wf-recorder")
            cmd.extend(["-f", self.config.output_file])
            cmd.extend(["-r", str(self.config.framerate)])

            if self.config.geometry:
                cmd.extend(["-g", self.config.geometry])

            if self.config.codec:
                cmd.extend(["-c", self.config.codec])

            if self.config.audio_backend != AudioBackend.NONE:
                if self.config.audio_device_id and self.config.audio_device_id != "default":
                    cmd.append(f"--audio={self.config.audio_device_id}")
                else:
                    cmd.append("-a")
        else:
            # FFmpeg path
            cmd.append("ffmpeg")
            cmd.extend(["-y"])

            # Video input selection
            if platform in (DisplayPlatform.X11, DisplayPlatform.XWAYLAND):
                display_str = os.environ.get("DISPLAY", ":0.0")
                cmd.extend(["-f", "x11grab"])
                cmd.extend(["-r", str(self.config.framerate)])
                if self.config.geometry:
                    try:
                        coords, size = self.config.geometry.split()
                        cmd.extend(["-s", size])
                        cmd.extend(["-i", f"{display_str}+{coords}"])
                    except Exception:
                        cmd.extend(["-i", display_str])
                else:
                    cmd.extend(["-i", display_str])
            elif platform == DisplayPlatform.WAYLAND:
                display_str = os.environ.get("DISPLAY", ":0.0")
                if os.environ.get("DISPLAY"):
                    cmd.extend(["-f", "x11grab", "-r", str(self.config.framerate)])
                    if self.config.geometry:
                        try:
                            coords, size = self.config.geometry.split()
                            cmd.extend(["-s", size])
                            cmd.extend(["-i", f"{display_str}+{coords}"])
                        except Exception:
                            cmd.extend(["-i", display_str])
                    else:
                        cmd.extend(["-i", display_str])
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

    def _spawn_process(self, cmd: List[str]) -> Tuple[bool, Optional[str]]:
        env = os.environ.copy()
        platform = self.detect_platform()
        if platform == DisplayPlatform.WAYLAND:
            env["GDK_BACKEND"] = "wayland,x11"
            env["QT_QPA_PLATFORM"] = "wayland;xcb"
        elif platform in (DisplayPlatform.X11, DisplayPlatform.XWAYLAND):
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
            self._active_backend_cmd = cmd[0]

            time.sleep(0.3)
            if self.process.poll() is not None:
                _, stderr = self.process.communicate()
                self._last_error = stderr
                self.process = None
                return False, stderr.strip() if stderr else "Process exited immediately."

            return True, None
        except Exception as e:
            self.process = None
            self._last_error = str(e)
            return False, str(e)

    def start(self) -> Tuple[bool, Optional[str]]:
        if self.is_recording:
            return False, "Recording is already in progress."

        valid, err = self.config.validate()
        if not valid:
            return False, f"Invalid configuration: {err}"

        # First attempt with primary built command
        cmd = self.build_command()
        success, err_msg = self._spawn_process(cmd)

        if not success:
            # Check if failure was due to wf-recorder screencopy unsupported protocol on non-wlroots Wayland
            is_screencopy_error = err_msg and ("wlr-screencopy-unstable-v1" in err_msg or "screencopy" in err_msg)
            if cmd[0] == "wf-recorder" and (is_screencopy_error or "failed" in err_msg.lower()):
                # Fallback to FFmpeg (XWayland / KMS)
                fallback_cmd = self.build_command(force_ffmpeg_override=True)
                fallback_success, fallback_err = self._spawn_process(fallback_cmd)
                if fallback_success:
                    return True, None
                else:
                    return False, (
                        f"wf-recorder failed ({err_msg}). "
                        f"Fallback FFmpeg recording also failed: {fallback_err}"
                    )

            return False, f"Recorder failed to start: {err_msg}"

        return True, None

    def stop(self) -> Tuple[bool, Optional[str]]:
        if not self.is_recording or self.process is None:
            return False, "No active recording to stop."

        try:
            self.stop_time = time.time()
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
            "backend_used": self._active_backend_cmd,
            "audio_backend": self.config.audio_backend.value,
            "audio_device": self.config.audio_device_id,
            "last_error": self._last_error
        }
