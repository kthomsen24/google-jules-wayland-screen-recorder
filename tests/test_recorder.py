import unittest
import os
import sys
import shutil
import tempfile
import time
from unittest.mock import patch, MagicMock

from screen_recorder.audio import (
    AudioBackend, AudioDevice, is_backend_available,
    detect_audio_backends, get_alsa_devices, get_pulseaudio_devices,
    get_jack_devices, get_audio_devices
)
from screen_recorder.engine import (
    ScreenRecorderEngine, RecorderConfig, DisplayPlatform, ContainerFormat, sanitize_recorder_error
)
from screen_recorder.cli import parse_args, main as cli_main


class TestAudioDetection(unittest.TestCase):
    def test_backend_availability(self):
        self.assertTrue(is_backend_available(AudioBackend.NONE))
        self.assertIsInstance(is_backend_available(AudioBackend.ALSA), bool)
        self.assertIsInstance(is_backend_available(AudioBackend.PULSEAUDIO), bool)
        self.assertIsInstance(is_backend_available(AudioBackend.JACK), bool)

    def test_detect_audio_backends(self):
        backends = detect_audio_backends()
        self.assertIn(AudioBackend.NONE, backends)

    def test_get_alsa_devices(self):
        devs = get_alsa_devices()
        self.assertTrue(len(devs) > 0)
        self.assertIsInstance(devs[0], AudioDevice)
        self.assertEqual(devs[0].backend, AudioBackend.ALSA)

    def test_get_pulseaudio_devices(self):
        devs = get_pulseaudio_devices()
        self.assertTrue(len(devs) > 0)
        self.assertEqual(devs[0].backend, AudioBackend.PULSEAUDIO)

    def test_get_jack_devices(self):
        devs = get_jack_devices()
        self.assertTrue(len(devs) > 0)
        self.assertEqual(devs[0].backend, AudioBackend.JACK)

    def test_get_audio_devices_by_enum(self):
        self.assertEqual(get_audio_devices(AudioBackend.NONE), [])
        self.assertTrue(len(get_audio_devices(AudioBackend.ALSA)) > 0)


class TestRecorderConfig(unittest.TestCase):
    def test_valid_config(self):
        cfg = RecorderConfig(output_file="test.mp4", format=ContainerFormat.MP4)
        valid, err = cfg.validate()
        self.assertTrue(valid)
        self.assertIsNone(err)

    def test_valid_mkv_config(self):
        cfg = RecorderConfig(output_file="test.mkv", format=ContainerFormat.MKV)
        valid, err = cfg.validate()
        self.assertTrue(valid)

    def test_invalid_extension(self):
        cfg = RecorderConfig(output_file="test.avi")
        valid, err = cfg.validate()
        self.assertFalse(valid)
        self.assertIn("Unsupported file extension", err)

    def test_invalid_framerate(self):
        cfg = RecorderConfig(output_file="test.mp4", framerate=0)
        valid, err = cfg.validate()
        self.assertFalse(valid)


class TestScreenRecorderEngine(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_sanitize_recorder_error(self):
        raw = """ffmpeg version 6.1.1-3ubuntu5 Copyright (c) 2000-2023 the FFmpeg developers
built with gcc 13 (Ubuntu 13.2.0-23ubuntu3)
configuration: --prefix=/usr --enable-gpl
libavutil      58. 29.100 / 58. 29.100
libavcodec     60. 31.102 / 60. 31.102
[x11grab @ 0x55d1] Cannot open display ':0.0', error 1.
:0.0: Input/output error
"""
        clean = sanitize_recorder_error(raw)
        self.assertNotIn("built with gcc", clean)
        self.assertNotIn("configuration:", clean)
        self.assertIn("Cannot open display", clean)

    def test_command_building_wf_recorder(self):
        out_path = os.path.join(self.tmp_dir, "out.mp4")
        cfg = RecorderConfig(
            output_file=out_path,
            platform=DisplayPlatform.WAYLAND,
            audio_backend=AudioBackend.PULSEAUDIO,
            audio_device_id="alsa_output.pci-0000_00_1b.0.analog-stereo.monitor",
            framerate=60
        )
        engine = ScreenRecorderEngine(cfg)

        with patch("shutil.which", side_effect=lambda cmd: "/usr/bin/wf-recorder" if cmd == "wf-recorder" else None):
            cmd = engine.build_command()
            self.assertEqual(cmd[0], "wf-recorder")
            self.assertIn("-f", cmd)
            self.assertIn(out_path, cmd)
            self.assertIn("-r", cmd)
            self.assertIn("60", cmd)

    def test_command_building_pipewire_gstreamer(self):
        out_path = os.path.join(self.tmp_dir, "kde_out.mp4")
        cfg = RecorderConfig(
            output_file=out_path,
            platform=DisplayPlatform.PIPEWIRE,
            use_pipewire_gstreamer=True,
            framerate=30
        )
        engine = ScreenRecorderEngine(cfg)

        with patch("shutil.which", side_effect=lambda cmd: "/usr/bin/gst-launch-1.0" if cmd == "gst-launch-1.0" else None):
            cmd = engine.build_command()
            self.assertEqual(cmd[0], "gst-launch-1.0")
            self.assertIn("pipewiresrc", cmd)

    def test_command_building_ffmpeg_x11(self):
        out_path = os.path.join(self.tmp_dir, "out.mkv")
        cfg = RecorderConfig(
            output_file=out_path,
            format=ContainerFormat.MKV,
            platform=DisplayPlatform.X11,
            audio_backend=AudioBackend.ALSA,
            audio_device_id="hw:0,0",
            geometry="0,0 1280x720"
        )
        engine = ScreenRecorderEngine(cfg)

        with patch("shutil.which", return_value=None):
            cmd = engine.build_command()
            self.assertEqual(cmd[0], "ffmpeg")
            self.assertIn("-f", cmd)
            self.assertIn("x11grab", cmd)
            self.assertIn("-f", cmd)
            self.assertIn("alsa", cmd)
            self.assertIn("hw:0,0", cmd)

    @patch("subprocess.Popen")
    def test_start_and_stop_mocked(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.wait.return_value = 0
        mock_popen.return_value = mock_proc

        out_path = os.path.join(self.tmp_dir, "test.mp4")
        cfg = RecorderConfig(output_file=out_path)
        engine = ScreenRecorderEngine(cfg)

        success, err = engine.start()
        self.assertTrue(success)
        self.assertIsNone(err)
        self.assertTrue(engine.is_recording)

        status = engine.get_status()
        self.assertTrue(status["is_recording"])

        stop_success, stop_err = engine.stop()
        self.assertTrue(stop_success)
        self.assertIsNone(stop_err)
        self.assertFalse(engine.is_recording)


class TestCLI(unittest.TestCase):
    def test_cli_argument_parsing(self):
        args = parse_args(["-o", "my_rec.mkv", "-p", "pipewire", "-a", "jack", "-t", "5"])
        self.assertEqual(args.output, "my_rec.mkv")
        self.assertEqual(args.platform, "pipewire")
        self.assertEqual(args.audio, "jack")
        self.assertEqual(args.duration, 5.0)

    @patch("screen_recorder.cli.get_audio_devices")
    def test_cli_list_audio(self, mock_get_devices):
        mock_get_devices.return_value = [AudioDevice("hw:0,0", "Test ALSA", AudioBackend.ALSA)]
        ret = cli_main(["--list-audio"])
        self.assertEqual(ret, 0)


class TestGUIInitialization(unittest.TestCase):
    def test_gui_instantiation(self):
        try:
            from screen_recorder.gui import ScreenRecorderGUI
            app = ScreenRecorderGUI()
            app.update_idletasks()
            app.update()
            self.assertEqual(app.title(), "Wayland / Multi-Platform Screen Recorder")
            app.destroy()
        except Exception as e:
            if "no display name" in str(e):
                self.skipTest("Skipping GUI test: No X11/Wayland display server available.")
            else:
                raise e


if __name__ == "__main__":
    unittest.main()
