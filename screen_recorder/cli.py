#!/usr/bin/env python3
"""
Command-line interface for semi-autonomous / scripted screen recording.
"""

import argparse
import os
import sys
import time
import signal

from .audio import AudioBackend, detect_audio_backends, get_audio_devices
from .engine import ScreenRecorderEngine, RecorderConfig, DisplayPlatform, ContainerFormat


def parse_args(args=None):
    parser = argparse.ArgumentParser(
        description="Screen Recorder Utility (Wayland, X11, XWayland) with ALSA/Pulse/JACK audio."
    )
    parser.add_argument(
        "-o", "--output",
        default="recording.mp4",
        help="Output filename (e.g., recording.mp4 or recording.mkv). Default: recording.mp4"
    )
    parser.add_argument(
        "-f", "--format",
        choices=["mp4", "mkv"],
        default=None,
        help="Container format (mp4 or mkv). Inferred from filename extension if omitted."
    )
    parser.add_argument(
        "-p", "--platform",
        choices=["auto", "wayland", "x11", "xwayland"],
        default="auto",
        help="Display platform / mode (auto, wayland, x11, xwayland). Default: auto"
    )
    parser.add_argument(
        "-a", "--audio",
        choices=["none", "alsa", "pulseaudio", "jack"],
        default="none",
        help="Audio backend to use (none, alsa, pulseaudio, jack). Default: none"
    )
    parser.add_argument(
        "-d", "--audio-device",
        default="default",
        help="Audio device or source ID (e.g., hw:0,0, default, system:capture_1)."
    )
    parser.add_argument(
        "-r", "--framerate",
        type=int,
        default=30,
        help="Framerate in FPS. Default: 30"
    )
    parser.add_argument(
        "-g", "--geometry",
        default=None,
        help="Region geometry 'x,y WxH' (e.g., '0,0 1920x1080'). Default: full screen"
    )
    parser.add_argument(
        "-c", "--codec",
        default=None,
        help="Video codec (e.g., libx264, h264, vp9)."
    )
    parser.add_argument(
        "-t", "--duration",
        type=float,
        default=None,
        help="Duration in seconds to record semi-autonomously. If omitted, press Ctrl+C to stop."
    )
    parser.add_argument(
        "--list-audio",
        action="store_true",
        help="List available audio backends and devices, then exit."
    )
    return parser.parse_args(args)


def main(args=None):
    parsed = parse_args(args)

    if parsed.list_audio:
        print("=== Available Audio Backends & Devices ===")
        for backend in detect_audio_backends():
            if backend == AudioBackend.NONE:
                continue
            print(f"\nBackend: {backend.value.upper()}")
            devs = get_audio_devices(backend)
            for d in devs:
                print(f"  - ID: {d.device_id} | Name: {d.name}")
        return 0

    # Determine container format
    output_path = parsed.output
    if parsed.format:
        fmt = ContainerFormat(parsed.format)
    else:
        ext = os.path.splitext(output_path)[1].lower().strip(".")
        if ext == "mkv":
            fmt = ContainerFormat.MKV
        else:
            fmt = ContainerFormat.MP4

    platform = DisplayPlatform(parsed.platform)
    audio_backend = AudioBackend(parsed.audio)

    config = RecorderConfig(
        output_file=output_path,
        format=fmt,
        platform=platform,
        audio_backend=audio_backend,
        audio_device_id=parsed.audio_device,
        framerate=parsed.framerate,
        geometry=parsed.geometry,
        codec=parsed.codec
    )

    engine = ScreenRecorderEngine(config)

    print("=== Starting Screen Recorder ===")
    print(f"Output:       {config.output_file} (format: {config.format.value})")
    print(f"Platform:     {engine.detect_platform().value}")
    print(f"Framerate:    {config.framerate} FPS")
    print(f"Audio:        {config.audio_backend.value} (device: {config.audio_device_id})")
    if config.geometry:
        print(f"Geometry:     {config.geometry}")

    cmd = engine.build_command()
    print(f"Command:      {' '.join(cmd)}")

    success, err = engine.start()
    if not success:
        print(f"Error starting recorder: {err}", file=sys.stderr)
        return 1

    print("\nRecording started... Press Ctrl+C to stop.")

    def signal_handler(sig, frame):
        print("\nStopping recording...")
        engine.stop()

    signal.signal(signal.SIGINT, signal_handler)

    try:
        start_time = time.time()
        while engine.is_recording:
            elapsed = time.time() - start_time
            print(f"\rElapsed time: {elapsed:.1f}s", end="", flush=True)

            if parsed.duration and elapsed >= parsed.duration:
                print(f"\nTarget duration of {parsed.duration}s reached. Stopping...")
                engine.stop()
                break

            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        if engine.is_recording:
            engine.stop()

    status = engine.get_status()
    print(f"\n\n=== Recording Finished ===")
    print(f"Saved file:   {status['output_file']}")
    print(f"Duration:     {status['duration']} seconds")
    print(f"File size:    {status['file_size_bytes']} bytes")

    return 0


if __name__ == "__main__":
    sys.exit(main())
