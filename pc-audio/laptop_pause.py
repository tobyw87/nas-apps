#!/usr/bin/env python3
"""Pause this PC's media while the laptop is playing through the line-in, resume when the laptop goes quiet.

Watches the level of a recording device (the USB adapter the laptop's audio cable is plugged into). When sound
stays above a threshold for a moment, every media session on this PC that is playing (browser, Spotify, ...) is paused.
When the line has been quiet for a while, only the sessions this script paused are resumed.
It uses Windows' own media controls, so it pauses (never toggles) and leaves anything you paused yourself alone.
The laptop's audio isn't a media session on this PC, so it is never paused.

Setup (Windows, once):  pip install sounddevice numpy winrt-runtime winrt-Windows.Media.Control winrt-Windows.Foundation winrt-Windows.Foundation.Collections
  python laptop_pause.py --list            show recording devices
  python laptop_pause.py --calibrate       print the line's level for 10 s (play laptop audio, then stay silent)
  python laptop_pause.py --sessions       list the media sessions Windows sees (start something playing first)
  python laptop_pause.py --dry-run         say what it would do, pause nothing
  python laptop_pause.py                   run it (Ctrl+C to stop)
Options: --device "USB Audio Device" (part of the name)  --threshold -45 (dB, from --calibrate)
         --on 1.0 (seconds of sound before pausing)  --off 8.0 (seconds of quiet before resuming)  --no-resume
"""
import argparse
import asyncio
import math
import sys
import time


class Gate:
    """Turns a stream of levels into 'start' / 'stop' events. Pure logic, no audio or Windows calls."""

    def __init__(self, threshold_db, on_sec, off_sec):
        self.threshold_db, self.on_sec, self.off_sec = threshold_db, on_sec, off_sec
        self.active = False
        self._loud_since = None
        self._quiet_since = None

    def update(self, level_db, now):
        if level_db > self.threshold_db:
            self._quiet_since = None
            if self._loud_since is None:
                self._loud_since = now
            if not self.active and now - self._loud_since >= self.on_sec:
                self.active = True
                return "start"
        else:
            self._loud_since = None
            if self.active:
                if self._quiet_since is None:
                    self._quiet_since = now
                if now - self._quiet_since >= self.off_sec:
                    self.active = False
                    self._quiet_since = None
                    return "stop"
        return None


def level_db(block):
    import numpy as np
    rms = float(np.sqrt(np.mean(np.square(block, dtype="float64")))) if len(block) else 0.0
    return 20 * math.log10(rms) if rms > 1e-9 else -120.0


def pick_device(name):
    import sounddevice as sd
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and name.lower() in d["name"].lower():
            return i
    sys.exit(f"No recording device with '{name}' in its name. Run with --list.")


async def media_manager():
    from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager
    return await Manager.request_async()


async def pause_playing(mgr):
    from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionPlaybackStatus as S
    paused = set()
    for s in mgr.get_sessions():
        if s.get_playback_info().playback_status == S.PLAYING and await s.try_pause_async():
            paused.add(s.source_app_user_model_id)
    return paused


async def resume_paused(mgr, ids):
    from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionPlaybackStatus as S
    for s in mgr.get_sessions():
        if s.source_app_user_model_id in ids and s.get_playback_info().playback_status == S.PAUSED:
            await s.try_play_async()


async def list_sessions():
    from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionPlaybackStatus as S
    mgr = await media_manager()
    sessions = list(mgr.get_sessions())
    if not sessions:
        print("Windows reports no media sessions at all.")
    for s in sessions:
        info = s.get_playback_info()
        status = S(info.playback_status).name if info else "unknown"
        print(f"{s.source_app_user_model_id}: {status}")


async def run(a):
    import sounddevice as sd
    dev = pick_device(a.device)
    info = sd.query_devices(dev)
    sr = int(info["default_samplerate"])
    q = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def cb(indata, frames, t, status):
        loop.call_soon_threadsafe(q.put_nowait, level_db(indata[:, 0]))

    gate = Gate(a.threshold, a.on, a.off)
    mgr = None if (a.dry_run or a.calibrate) else await media_manager()
    paused = set()
    t0 = time.monotonic()
    print(f"Listening on: {info['name']}  (threshold {a.threshold} dB). Ctrl+C to stop.")
    with sd.InputStream(device=dev, channels=1, samplerate=sr, blocksize=int(sr * 0.25), callback=cb):
        while True:
            lvl = await q.get()
            now = time.monotonic()
            if a.calibrate:
                print(f"{now - t0:5.1f}s  {lvl:6.1f} dB")
                if now - t0 > 10:
                    return
                continue
            ev = gate.update(lvl, now)
            if ev == "start":
                print(time.strftime("%H:%M:%S"), "laptop audio detected - pausing PC media")
                if mgr:
                    paused = await pause_playing(mgr)
                    print("   paused:", ", ".join(sorted(paused)) or "nothing was playing")
            elif ev == "stop":
                print(time.strftime("%H:%M:%S"), "laptop audio stopped")
                if mgr and paused and not a.no_resume:
                    await resume_paused(mgr, paused)
                    print("   resumed:", ", ".join(sorted(paused)))
                paused = set()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--device", default="USB Audio Device")
    p.add_argument("--threshold", type=float, default=-45.0)
    p.add_argument("--on", type=float, default=1.0)
    p.add_argument("--off", type=float, default=8.0)
    p.add_argument("--no-resume", action="store_true")
    p.add_argument("--list", action="store_true")
    p.add_argument("--sessions", action="store_true")
    p.add_argument("--calibrate", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    if a.list:
        import sounddevice as sd
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                print(i, d["name"])
        return
    if a.sessions:
        asyncio.run(list_sessions())
        return
    try:
        asyncio.run(run(a))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
