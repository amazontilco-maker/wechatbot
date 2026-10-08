"""Read-only control of the Android phone over ADB: screenshots, taps, scrolls, back.
Nothing in this module types text or sends messages."""
import os
import re
import subprocess
import time

WECHAT = "com.tencent.mm"


class Device:
    def __init__(self, adb=None, serial=None):
        self.adb = adb or os.environ.get("WB_ADB") or "adb"
        self.serial = serial or os.environ.get("ANDROID_SERIAL")
        self._size = None

    def run(self, *args, binary=False):
        cmd = [self.adb] + (["-s", self.serial] if self.serial else []) + list(args)
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=60)
        except FileNotFoundError:
            raise SystemExit(f"adb not found at '{self.adb}'. Pass --adb C:\\path\\to\\platform-tools\\adb.exe")
        if p.returncode != 0:
            raise RuntimeError(f"adb {' '.join(args)} failed: {p.stderr.decode(errors='replace').strip()}")
        return p.stdout if binary else p.stdout.decode(errors="replace")

    def size(self):
        if not self._size:
            out = self.run("shell", "wm", "size")
            m = re.findall(r"(\d+)x(\d+)", out)
            if not m:
                raise RuntimeError(f"could not read screen size: {out}")
            w, h = m[-1]  # 'Override size' (if any) is listed last
            self._size = (int(w), int(h))
        return self._size

    def screenshot(self):
        import cv2
        import numpy as np
        data = self.run("exec-out", "screencap", "-p", binary=True)
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise RuntimeError("screenshot failed (is the phone unlocked?)")
        return img

    def tap(self, x, y):
        self.run("shell", "input", "tap", str(int(x)), str(int(y)))

    def swipe(self, x1, y1, x2, y2, ms=400):
        self.run("shell", "input", "swipe", *(str(int(v)) for v in (x1, y1, x2, y2, ms)))

    def back(self):
        self.run("shell", "input", "keyevent", "KEYCODE_BACK")

    def wake(self):
        self.run("shell", "input", "keyevent", "KEYCODE_WAKEUP")

    def foreground(self):
        out = self.run("shell", "dumpsys", "window")
        m = re.search(r"mCurrentFocus=.*?\s([\w.]+)/", out)
        return m.group(1) if m else ""

    def open_wechat(self, wait=3.0):
        self.run("shell", "am", "start", "-n", f"{WECHAT}/.ui.LauncherUI")
        time.sleep(wait)
