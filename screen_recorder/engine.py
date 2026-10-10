"""
Screen Recorder Engine for Wayland, X11, and XWayland with ALSA/PulseAudio/JACK support.
Supports OBS Studio CLI recording with temporary profile configuration overrides, PipeWire portal capture, wf-recorder, and FFmpeg.
"""

import configparser
from enum import Enum
import os
import re
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
    PIPEWIRE = "pipewire"
    OBS = "obs"


class ContainerFormat(str, Enum):
    MP4 = "mp4"
    MKV = "mkv"


def sanitize_recorder_error(raw_stderr: str) -> str:
    """
    Strips out verbose FFmpeg/GStreamer configuration banners and returns
    only the meaningful error summary.
    """
    if not raw_stderr:
        return "Unknown recorder error (no output produced)."

    lines = raw_stderr.splitlines()
    filtered_lines = []

    skip_patterns = [
        r"^ffmpeg version",
        r"^\s*built with",
        r"^\s*configuration:",
        r"^\s*libav",
        r"^\s*libsw",
        r"^\s*libpostproc",
        r"^Hyper Fast Audio and Video encoder",
        r"^Setting pipeline to",
        r"^PREROLLING",
        r"^PREROLLED",
        r"^RUNNING",
    ]

    for line in lines:
        line_str = line.strip()
        if not line_str:
            continue
        if any(re.search(pat, line_str, re.IGNORECASE) for pat in skip_patterns):
            continue
        filtered_lines.append(line_str)

    if filtered_lines:
        return "\n".join(filtered_lines[-5:])

    return lines[-1] if lines else raw_stderr.strip()


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
        use_pipewire_gstreamer: bool = False,
        use_obs: bool = False,
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
        self.use_pipewire_gstreamer = use_pipewire_gstreamer
        self.use_obs = use_obs

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
        self._obs_ini_backup: Optional[Tuple[str, str]] = None

    @property
    def is_recording(self) -> bool:
        if self.process is None:
            return False
        return self.process.poll() is None

    def detect_platform(self) -> DisplayPlatform:
        if self.config.platform != DisplayPlatform.AUTO:
            return self.config.platform

        wayland_display = os.environ.get("WAYLAND_DISPLAY")
        xdg_session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()
        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()

        if ("kde" in desktop or "gnome" in desktop) and (wayland_display or xdg_session_type == "wayland"):
            if shutil.which("obs"):
                return DisplayPlatform.OBS
            elif shutil.which("gst-launch-1.0"):
                return DisplayPlatform.PIPEWIRE
            elif shutil.which("wf-recorder"):
                return DisplayPlatform.WAYLAND
            return DisplayPlatform.PIPEWIRE
        elif wayland_display or xdg_session_type == "wayland":
            if shutil.which("wf-recorder"):
                return DisplayPlatform.WAYLAND
            return DisplayPlatform.PIPEWIRE
        elif os.environ.get("DISPLAY"):
            return DisplayPlatform.X11
        else:
            if shutil.which("wf-recorder"):
                return DisplayPlatform.WAYLAND
            return DisplayPlatform.X11

    def _apply_obs_config_override(self) -> None:
        """
        Temporarily configures OBS Studio's output directory and filename template
        in ~/.config/obs-studio/basic/profiles/Untitled/basic.ini so recording
        outputs directly to config.output_file.
        """
        try:
            target_file = os.path.abspath(self.config.output_file)
            target_dir = os.path.dirname(target_file) or os.getcwd()
            base_filename = os.path.splitext(os.path.basename(target_file))[0]

            obs_profile_dir = os.path.expanduser("~/.config/obs-studio/basic/profiles/Untitled")
            os.makedirs(obs_profile_dir, exist_ok=True)
            ini_path = os.path.join(obs_profile_dir, "basic.ini")

            parser = configparser.ConfigParser(interpolation=None)
            if os.path.exists(ini_path):
                with open(ini_path, "r", encoding="utf-8") as f:
                    content = f.read()
                if self._obs_ini_backup is None:
                    self._obs_ini_backup = (ini_path, content)
                parser.read_string(content)

            if not parser.has_section("SimpleOutput"):
                parser.add_section("SimpleOutput")
            if not parser.has_section("AdvOutput"):
                parser.add_section("AdvOutput")

            parser.set("SimpleOutput", "FilePath", target_dir)
            parser.set("SimpleOutput", "FilenameFormatting", base_filename)
            parser.set("SimpleOutput", "RecFormat", self.config.format.value)

            parser.set("AdvOutput", "FilePath", target_dir)
            parser.set("AdvOutput", "FilenameFormatting", base_filename)
            parser.set("AdvOutput", "RecFormat", self.config.format.value)

            with open(ini_path, "w", encoding="utf-8") as f:
                parser.write(f)
        except Exception:
            pass

    def _restore_obs_config(self) -> None:
        """
        Restores OBS Studio's original basic.ini configuration file if a backup was saved.
        """
        if self._obs_ini_backup:
            try:
                ini_path, content = self._obs_ini_backup
                with open(ini_path, "w", encoding="utf-8") as f:
                    f.write(content)
            except Exception:
                pass
            self._obs_ini_backup = None

    def build_command(self, force_fallback: bool = False) -> List[str]:
        platform = self.detect_platform()
        cmd: List[str] = []

        use_obs = (
            (platform == DisplayPlatform.OBS or self.config.use_obs)
            and shutil.which("obs") is not None
            and not force_fallback
        )

        use_wf_recorder = (
            (platform == DisplayPlatform.WAYLAND)
            and (shutil.which("wf-recorder") is not None)
            and not self.config.force_ffmpeg
            and not self.config.use_pipewire_gstreamer
            and not use_obs
            and not force_fallback
        )

        use_gstreamer_pipewire = (
            (platform == DisplayPlatform.PIPEWIRE or self.config.use_pipewire_gstreamer)
            and shutil.which("gst-launch-1.0") is not None
            and not use_wf_recorder
            and not use_obs
        )

        if use_obs:
            self._apply_obs_config_override()
            cmd.append("obs")
            cmd.extend(["--startrecording", "--minimize-to-tray"])

        elif use_wf_recorder:
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

        elif use_gstreamer_pipewire:
            cmd.append("gst-launch-1.0")
            cmd.extend(["-e"])

            cmd.extend(["pipewiresrc", "do-timestamp=true", "!"])
            cmd.extend(["videoconvert", "!"])
            cmd.extend(["videorate", "!"])
            cmd.extend([f"video/x-raw,framerate={self.config.framerate}/1", "!"])

            if self.config.format == ContainerFormat.MKV:
                cmd.extend(["x264enc", "speed-preset=ultrafast", "tune=zerolatency", "!"])
                cmd.extend(["matroskamux", "name=mux", "!"])
                cmd.extend(["filesink", f"location={self.config.output_file}"])
            else:
                cmd.extend(["x264enc", "speed-preset=ultrafast", "tune=zerolatency", "!"])
                cmd.extend(["mp4mux", "name=mux", "!"])
                cmd.extend(["filesink", f"location={self.config.output_file}"])

            if self.config.audio_backend == AudioBackend.PULSEAUDIO:
                device = self.config.audio_device_id or "default"
                cmd.extend(["pulsesrc", f"device={device}", "!"])
                cmd.extend(["audioconvert", "!"])
                cmd.extend(["lamemp3enc", "!"])
                cmd.extend(["mux."])
            elif self.config.audio_backend == AudioBackend.ALSA:
                device = self.config.audio_device_id or "default"
                cmd.extend(["alsasrc", f"device={device}", "!"])
                cmd.extend(["audioconvert", "!"])
                cmd.extend(["lamemp3enc", "!"])
                cmd.extend(["mux."])
            elif self.config.audio_backend == AudioBackend.JACK:
                cmd.extend(["jackaudiosrc", "!"])
                cmd.extend(["audioconvert", "!"])
                cmd.extend(["lamemp3enc", "!"])
                cmd.extend(["mux."])

        else:
            cmd.append("ffmpeg")
            cmd.extend(["-hide_banner", "-loglevel", "error", "-y"])

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

            if self.config.audio_backend == AudioBackend.ALSA:
                device = self.config.audio_device_id or "default"
                cmd.extend(["-f", "alsa", "-i", device])
            elif self.config.audio_backend == AudioBackend.PULSEAUDIO:
                device = self.config.audio_device_id or "default"
                cmd.extend(["-f", "pulse", "-i", device])
            elif self.config.audio_backend == AudioBackend.JACK:
                device = self.config.audio_device_id or "default"
                cmd.extend(["-f", "jack", "-i", device])

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
        if platform in (DisplayPlatform.WAYLAND, DisplayPlatform.OBS):
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
            ret = self.process.poll()

            if ret is not None and ret != 0:
                _, stderr = self.process.communicate()
                clean_err = sanitize_recorder_error(stderr)
                self._last_error = clean_err
                self.process = None
                self._restore_obs_config()
                return False, clean_err

            return True, None
        except Exception as e:
            self.process = None
            self._last_error = str(e)
            self._restore_obs_config()
            return False, str(e)

    def start(self) -> Tuple[bool, Optional[str]]:
        if self.is_recording:
            return False, "Recording is already in progress."

        valid, err = self.config.validate()
        if not valid:
            return False, f"Invalid configuration: {err}"

        cmd = self.build_command()
        success, err_msg = self._spawn_process(cmd)

        if not success:
            if shutil.which("gst-launch-1.0"):
                self.config.use_pipewire_gstreamer = True
                self.config.use_obs = False
                pipewire_cmd = self.build_command()
                pw_success, pw_err = self._spawn_process(pipewire_cmd)
                if pw_success:
                    return True, None

            self.config.force_ffmpeg = True
            self.config.use_pipewire_gstreamer = False
            self.config.use_obs = False
            ffmpeg_cmd = self.build_command()
            ffmpeg_success, ffmpeg_err = self._spawn_process(ffmpeg_cmd)
            if ffmpeg_success:
                return True, None

            return False, f"Recorder failed to start: {err_msg}"

        return True, None

    def stop(self) -> Tuple[bool, Optional[str]]:
        if self.process is None and not self.is_recording:
            return False, "No active recording to stop."

        try:
            self.stop_time = time.time()
            if self.process is not None:
                if self.process.poll() is None:
                    self.process.send_signal(signal.SIGINT)

                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.terminate()
                    self.process.wait(timeout=3)

            self.process = None
            self._restore_obs_config()
            return True, None
        except Exception as e:
            self.process = None
            self._restore_obs_config()
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
