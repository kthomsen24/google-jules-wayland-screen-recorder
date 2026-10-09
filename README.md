# google-jules-wayland-screen-recorder

A simple, robust screen recorder utility written in Python with native Wayland support (including wlroots and KDE Plasma / KWin / GNOME PipeWire capture), multi-platform failsafes (X11 and XWayland), MP4/MKV container format exports, and audio capture compatibility with ALSA, PulseAudio, and JACK.

## Features

- **Wayland Compatibility**:
  - Supports wlroots-based Wayland compositors (Sway, Wayfire) via `wf-recorder`.
  - Supports KDE Plasma 6 / KWin and GNOME Wayland via PipeWire (`gst-launch-1.0` / PipeWire portal capture).
- **Black Screen Solution for KDE Plasma Wayland**:
  - On KDE Plasma (KWin on Wayland), `x11grab` under XWayland captures only the X11 cursor on an empty root window.
  - Using the **`pipewire`** capture mode (or `--use-pipewire` in CLI) connects directly to PipeWire/Portal streams, capturing full desktop contents cleanly.
- **X11 / XWayland Failsafes**: Built-in environment flags (`GDK_BACKEND`, `QT_QPA_PLATFORM`) and selection toggles ensure graphical compatibility across X11, Wayland, and XWayland environments.
- **Output Formats**: Export recordings to `.mp4` or `.mkv` files with custom framerates and geometries.
- **Audio Detection**: Detects capture sources from ALSA, PulseAudio, and JACK audio servers.
- **Manual GUI Mode**: Interactive Tkinter graphical interface operating in its own window.
- **Semi-Autonomous CLI Mode**: Non-interactive or scripted operation via command-line arguments and optional recording duration timeouts.

---

## Installation & Dependencies

System dependencies (Kubuntu 24.04 / Ubuntu / Debian):
```bash
sudo apt-get update
sudo apt-get install -y python3 ffmpeg wf-recorder pulseaudio-utils jackd2 \
  gstreamer1.0-tools gstreamer1.0-pipewire gstreamer1.0-plugins-good gstreamer1.0-plugins-ugly
```

---

## Usage

### 1. Graphical Interface (Manual Mode)

Launch the recorder GUI in its own window:
```bash
python3 -m screen_recorder.gui
```

**Kubuntu 24.04 / KDE Plasma / GNOME Wayland Note:**
If running on Kubuntu 24.04 under Wayland, set **Capture Method** to `pipewire` in the GUI dropdown to ensure full desktop capturing.

### 2. Command Line Interface (Semi-Autonomous / Scripted Mode)

Run the recorder directly from the command line:

**List available audio backends and sources:**
```bash
python3 -m screen_recorder.cli --list-audio
```

**Record on KDE Plasma Wayland using PipeWire for 10 seconds:**
```bash
python3 -m screen_recorder.cli -o my_recording.mp4 -p pipewire -a pulseaudio -t 10
```

**Record on wlroots Wayland or X11:**
```bash
python3 -m screen_recorder.cli -o output.mkv -f mkv -r 60
```

---

## Running Tests

Run the test suite:
```bash
xvfb-run -a python3 -m unittest discover -s tests -v
```
