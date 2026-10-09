# google-jules-wayland-screen-recorder

A simple, robust screen recorder utility written in Python with native Wayland support, multi-platform failsafes (X11 and XWayland), MP4/MKV container format exports, and audio capture compatibility with ALSA, PulseAudio, and JACK.

## Features

- **Wayland Compatibility**: Captures Wayland displays using `wf-recorder` or FFmpeg fallbacks.
- **X11 / XWayland Failsafes**: Built-in environment flags (`GDK_BACKEND`, `QT_QPA_PLATFORM`) and selection toggles to ensure graphical compatibility across X11, Wayland, and XWayland environments.
- **Output Formats**: Export recordings to `.mp4` or `.mkv` files with custom framerates and geometries.
- **Audio Detection**: Detects capture sources from ALSA, PulseAudio, and JACK audio servers.
- **Manual GUI Mode**: Interactive Tkinter graphical interface operating in its own window.
- **Semi-Autonomous CLI Mode**: Non-interactive or scripted operation via command-line arguments and optional recording duration timeouts.

---

## Installation & Dependencies

System dependencies (Ubuntu / Debian):
```bash
sudo apt-get update
sudo apt-get install -y python3 ffmpeg wf-recorder pulseaudio-utils jackd2
```

---

## Usage

### 1. Graphical Interface (Manual Mode)

Launch the recorder GUI in its own window:
```bash
python3 -m screen_recorder.gui
```

From the GUI window, you can:
- Specify output filename and choose format (`.mp4` or `.mkv`).
- Select display platform mode (`auto`, `wayland`, `x11`, `xwayland`).
- Choose audio system (`none`, `alsa`, `pulseaudio`, `jack`) and pick detected capture devices.
- Configure framerate and optional crop geometry.
- Click **Start Recording** and **Stop Recording** to manage capture manually.

### 2. Command Line Interface (Semi-Autonomous / Scripted Mode)

Run the recorder directly from the command line:

**List available audio backends and sources:**
```bash
python3 -m screen_recorder.cli --list-audio
```

**Record for 10 seconds automatically:**
```bash
python3 -m screen_recorder.cli -o my_recording.mkv -f mkv -p wayland -a pulseaudio -t 10
```

**Interactive CLI recording (press Ctrl+C to stop):**
```bash
python3 -m screen_recorder.cli -o output.mp4 -r 60
```

---

## Running Tests

Run the test suite:
```bash
xvfb-run -a python3 -m unittest discover -s tests -v
```
