"""
GUI Interface for Screen Recorder Utility using Tkinter.
Runs in its own window with manual controls for recording, stopping, audio source selection, and platform flags.
"""

import os
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import Optional, Dict, List

from .audio import AudioBackend, AudioDevice, detect_audio_backends, get_audio_devices
from .engine import ScreenRecorderEngine, RecorderConfig, DisplayPlatform, ContainerFormat


class ScreenRecorderGUI(tk.Tk):
    def __init__(self, engine: Optional[ScreenRecorderEngine] = None):
        super().__init__()
        self.title("Wayland / Multi-Platform Screen Recorder")
        self.geometry("620x520")
        self.minsize(550, 480)

        self.engine = engine or ScreenRecorderEngine()
        self._update_timer = None
        self._device_map: Dict[str, AudioDevice] = {}

        self.create_widgets()
        self.refresh_audio_devices()

    def create_widgets(self):
        # Header / Title
        header_frame = ttk.Frame(self, padding="10")
        header_frame.pack(fill=tk.X)

        title_lbl = ttk.Label(
            header_frame,
            text="Screen Recorder Utility",
            font=("Helvetica", 16, "bold")
        )
        title_lbl.pack(anchor=tk.W)

        subtitle_lbl = ttk.Label(
            header_frame,
            text="Supports Wayland, X11, XWayland | MP4 & MKV | ALSA, PulseAudio, JACK"
        )
        subtitle_lbl.pack(anchor=tk.W)

        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=10, pady=5)

        # Config Form Frame
        form_frame = ttk.Frame(self, padding="10")
        form_frame.pack(fill=tk.BOTH, expand=True)

        row = 0

        # Output File Selection
        ttk.Label(form_frame, text="Output File:", font=("Helvetica", 10, "bold")).grid(row=row, column=0, sticky=tk.W, pady=5)
        self.output_entry = ttk.Entry(form_frame, width=38)
        self.output_entry.insert(0, "recording.mp4")
        self.output_entry.grid(row=row, column=1, sticky=tk.EW, padx=5, pady=5)

        browse_btn = ttk.Button(form_frame, text="Browse...", command=self.browse_output_file)
        browse_btn.grid(row=row, column=2, sticky=tk.E, pady=5)

        row += 1

        # Container Format Selection
        ttk.Label(form_frame, text="Output Format:", font=("Helvetica", 10, "bold")).grid(row=row, column=0, sticky=tk.W, pady=5)
        self.format_var = tk.StringVar(value="mp4")
        format_combo = ttk.Combobox(
            form_frame,
            textvariable=self.format_var,
            values=["mp4", "mkv"],
            state="readonly",
            width=15
        )
        format_combo.grid(row=row, column=1, sticky=tk.W, padx=5, pady=5)
        format_combo.bind("<<ComboboxSelected>>", self.on_format_changed)

        row += 1

        # Display Platform Failsafe Flag Selection
        ttk.Label(form_frame, text="Display Platform:", font=("Helvetica", 10, "bold")).grid(row=row, column=0, sticky=tk.W, pady=5)
        self.platform_var = tk.StringVar(value="auto")
        platform_combo = ttk.Combobox(
            form_frame,
            textvariable=self.platform_var,
            values=["auto", "wayland", "x11", "xwayland"],
            state="readonly",
            width=15
        )
        platform_combo.grid(row=row, column=1, sticky=tk.W, padx=5, pady=5)

        row += 1

        # Audio Backend Selection
        ttk.Label(form_frame, text="Audio Backend:", font=("Helvetica", 10, "bold")).grid(row=row, column=0, sticky=tk.W, pady=5)
        self.audio_backend_var = tk.StringVar(value="none")
        audio_combo = ttk.Combobox(
            form_frame,
            textvariable=self.audio_backend_var,
            values=["none", "alsa", "pulseaudio", "jack"],
            state="readonly",
            width=15
        )
        audio_combo.grid(row=row, column=1, sticky=tk.W, padx=5, pady=5)
        audio_combo.bind("<<ComboboxSelected>>", lambda e: self.refresh_audio_devices())

        row += 1

        # Audio Device Selection Dropdown
        ttk.Label(form_frame, text="Audio Source/Device:", font=("Helvetica", 10, "bold")).grid(row=row, column=0, sticky=tk.W, pady=5)
        self.audio_device_var = tk.StringVar(value="default")
        self.audio_device_combo = ttk.Combobox(
            form_frame,
            textvariable=self.audio_device_var,
            state="readonly",
            width=38
        )
        self.audio_device_combo.grid(row=row, column=1, sticky=tk.EW, padx=5, pady=5)

        row += 1

        # Framerate Spinner
        ttk.Label(form_frame, text="Framerate (FPS):", font=("Helvetica", 10, "bold")).grid(row=row, column=0, sticky=tk.W, pady=5)
        self.framerate_spin = ttk.Spinbox(form_frame, from_=1, to=144, width=10)
        self.framerate_spin.set(30)
        self.framerate_spin.grid(row=row, column=1, sticky=tk.W, padx=5, pady=5)

        row += 1

        # Region / Geometry Entry
        ttk.Label(form_frame, text="Geometry (x,y WxH):", font=("Helvetica", 10, "bold")).grid(row=row, column=0, sticky=tk.W, pady=5)
        self.geometry_entry = ttk.Entry(form_frame, width=25)
        self.geometry_entry.grid(row=row, column=1, sticky=tk.W, padx=5, pady=5)
        ttk.Label(form_frame, text="(Leave empty for full screen)", font=("Helvetica", 9, "italic")).grid(row=row, column=2, sticky=tk.W, pady=5)

        form_frame.columnconfigure(1, weight=1)

        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=10, pady=5)

        # Control Buttons & Status Panel
        ctrl_frame = ttk.Frame(self, padding="10")
        ctrl_frame.pack(fill=tk.X)

        self.record_btn = ttk.Button(
            ctrl_frame,
            text="⏺ Start Recording",
            command=self.toggle_recording
        )
        self.record_btn.pack(side=tk.LEFT, padx=5)

        self.status_label = ttk.Label(
            ctrl_frame,
            text="Status: Ready",
            font=("Helvetica", 11)
        )
        self.status_label.pack(side=tk.LEFT, padx=20)

        self.time_label = ttk.Label(
            ctrl_frame,
            text="00:00:00",
            font=("Helvetica", 12, "bold"),
            foreground="gray"
        )
        self.time_label.pack(side=tk.RIGHT, padx=5)

        # Information Log Area
        log_frame = ttk.Frame(self, padding="10")
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.log_text = tk.Text(log_frame, height=5, state=tk.DISABLED, wrap=tk.WORD, font=("Monospace", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)

        self.log("Screen Recorder GUI initialized. Platform failsafe options ready.")

    def log(self, message: str):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"[{time.strftime('%H:%M:%S')}] {message}\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def browse_output_file(self):
        fmt = self.format_var.get().lower()
        ext = ".mp4" if fmt == "mp4" else ".mkv"
        filename = filedialog.asksaveasfilename(
            defaultextension=ext,
            filetypes=[("MP4 Video", "*.mp4"), ("Matroska Video", "*.mkv"), ("All Files", "*.*")]
        )
        if filename:
            self.output_entry.delete(0, tk.END)
            self.output_entry.insert(0, filename)
            if filename.lower().endswith(".mkv"):
                self.format_var.set("mkv")
            elif filename.lower().endswith(".mp4"):
                self.format_var.set("mp4")

    def on_format_changed(self, event=None):
        current_path = self.output_entry.get().strip()
        if not current_path:
            return
        fmt = self.format_var.get().lower()
        base, _ = os.path.splitext(current_path)
        self.output_entry.delete(0, tk.END)
        self.output_entry.insert(0, f"{base}.{fmt}")

    def refresh_audio_devices(self):
        backend_str = self.audio_backend_var.get().lower()
        backend = AudioBackend(backend_str)

        if backend == AudioBackend.NONE:
            self.audio_device_combo["values"] = ["None (Disabled)"]
            self.audio_device_var.set("None (Disabled)")
            self.audio_device_combo.config(state="disabled")
            self._device_map.clear()
            return

        self.audio_device_combo.config(state="readonly")
        devices = get_audio_devices(backend)
        self._device_map.clear()

        combo_values = []
        for dev in devices:
            display_str = f"{dev.name} ({dev.device_id})"
            self._device_map[display_str] = dev
            combo_values.append(display_str)

        self.audio_device_combo["values"] = combo_values
        if combo_values:
            self.audio_device_var.set(combo_values[0])
            self.log(f"Detected {len(devices)} device(s) for audio backend '{backend.value}'.")
        else:
            self.audio_device_var.set("default")

    def toggle_recording(self):
        if self.engine.is_recording:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self):
        output_file = self.output_entry.get().strip()
        fmt_str = self.format_var.get().lower()
        platform_str = self.platform_var.get().lower()
        backend_str = self.audio_backend_var.get().lower()
        geometry_str = self.geometry_entry.get().strip() or None

        try:
            fps = int(self.framerate_spin.get())
        except ValueError:
            messagebox.showerror("Error", "Framerate must be a valid integer.")
            return

        audio_backend = AudioBackend(backend_str)
        audio_dev_id = None

        if audio_backend != AudioBackend.NONE:
            selected_str = self.audio_device_var.get()
            if selected_str in self._device_map:
                audio_dev_id = self._device_map[selected_str].device_id
            else:
                audio_dev_id = "default"

        config = RecorderConfig(
            output_file=output_file,
            format=ContainerFormat(fmt_str),
            platform=DisplayPlatform(platform_str),
            audio_backend=audio_backend,
            audio_device_id=audio_dev_id,
            framerate=fps,
            geometry=geometry_str
        )

        self.engine.config = config
        success, err = self.engine.start()

        if not success:
            messagebox.showerror("Recording Failed", f"Failed to start recording:\n{err}")
            self.log(f"Error: {err}")
            return

        self.record_btn.config(text="⏹ Stop Recording")
        self.status_label.config(text="Status: Recording ⏺", foreground="red")
        self.log(f"Recording started -> File: {output_file} | Platform: {self.engine.detect_platform().value}")

        self.start_update_timer()

    def stop_recording(self):
        success, err = self.engine.stop()
        self.stop_update_timer()

        self.record_btn.config(text="⏺ Start Recording")
        self.status_label.config(text="Status: Ready", foreground="black")

        if not success:
            messagebox.showerror("Stop Failed", f"Failed to stop recording cleanly:\n{err}")
            self.log(f"Stop Error: {err}")
            return

        status = self.engine.get_status()
        self.log(f"Recording saved! File: {status['output_file']} ({status['file_size_bytes']} bytes, {status['duration']}s)")
        messagebox.showinfo("Finished", f"Recording saved successfully to:\n{status['output_file']}")

    def start_update_timer(self):
        self.update_status_ui()

    def stop_update_timer(self):
        if self._update_timer:
            self.after_cancel(self._update_timer)
            self._update_timer = None

    def update_status_ui(self):
        if self.engine.is_recording:
            status = self.engine.get_status()
            dur = int(status["duration"])
            mins, secs = divmod(dur, 60)
            hrs, mins = divmod(mins, 60)
            self.time_label.config(text=f"{hrs:02d}:{mins:02d}:{secs:02d}", foreground="red")
            self._update_timer = self.after(500, self.update_status_ui)
        else:
            self.time_label.config(text="00:00:00", foreground="gray")


def main():
    app = ScreenRecorderGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
