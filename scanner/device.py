"""Control of the Android phone over ADB: screenshots, taps, scrolls, back, and typing
text for `scanner send` (which asks a person to confirm before anything is sent)."""
import os
import re
import subprocess
import time

WECHAT = "com.tencent.mm"


def text_problem(text):
    """Why `text` can't be typed with `adb shell input text`, or "" if it can."""
    if not text.strip():
        return "The message is empty."
    if "\n" in text or "\r" in text:
        return "Only one line per message for now (no line breaks)."
    bad = sorted({c for c in text if not (" " <= c <= "~")})
    if bad:
        return f"Only plain English letters, digits and punctuation can be typed for now; not: {' '.join(bad)}"
    if "%s" in text:
        return "'%s' can't be typed (adb turns it into a space)."
    return ""


def shell_input_arg(chunk):
    """Quote text for the phone's shell: spaces become %s for `input text`, the rest is single-quoted."""
    return "'" + chunk.replace(" ", "%s").replace("'", "'\\''") + "'"


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

    def type_text(self, text):
        """Type into the focused text box. Check text_problem() first."""
        for i in range(0, len(text), 80):
            self.run("shell", "input", "text", shell_input_arg(text[i:i + 80]))

    def delete_chars(self, n):
        self.run("shell", "input", "keyevent", "KEYCODE_MOVE_END")
        for i in range(0, n, 50):
            self.run("shell", "input", "keyevent", *["KEYCODE_DEL"] * min(50, n - i))

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
