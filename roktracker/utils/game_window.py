"""Native PC game window client.

Replaces the old ADB/emulator layer. Screenshots are taken from the client area
of the real Rise of Kingdoms window (via mss) and input is sent as real mouse
movements/clicks (via pynput).

All coordinates used by the scanners are written for a 1600x900 game client
area. Whatever size the window really has, screenshots are rescaled to that
size and clicks are scaled back, so the configured positions keep working.

Provides the GameWindowClient with the primitives the scanners need:
screencap(), tap(), swipe() and wait(). Pressing F10 at any time aborts a
running scan (the mouse is hijacked while scanning, so the GUI is awkward to
reach).
"""

import ctypes
import logging
import random
import sys
import threading
import time
from typing import Tuple

import cv2
import mss
import numpy as np
import pygetwindow as gw
from PIL import Image
from pynput import keyboard
from pynput.mouse import Button
from pynput.mouse import Controller as MouseController

from roktracker.utils.exceptions import GameWindowError, ScanAborted

logger = logging.getLogger(__name__)

REF_WIDTH = 1600
REF_HEIGHT = 900
"""The size of the game client area all configured coordinates refer to."""

EMERGENCY_STOP_KEY = keyboard.Key.f10
"""Press this key at any time to abort a running scan."""


def enable_dpi_awareness() -> None:
    """Make the process DPI aware (Windows only).

    Without this, window rectangles, screenshots and mouse coordinates can use
    different pixel scales when Windows display scaling is not 100%.
    The call is harmless if the process is already DPI aware.
    """
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per monitor
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            logger.debug("Could not set DPI awareness", exc_info=True)


class GameWindowClient:
    """Screenshots via mss and mouse input via pynput for the PC game window."""

    def __init__(
        self,
        title: str,
        y_offset: int = 0,
        max_random: float = 0.5,
        ref_size: Tuple[int, int] = (REF_WIDTH, REF_HEIGHT),
    ):
        """Creates a client for the game window.

        Nothing is looked up until start() is called.

        Args:
            title (str): The title (or part of it) of the game window
            y_offset (int): Vertical shift in reference pixels applied to all clicks and
                screenshots, for when the game content does not start at the top of the
                client area (Default value = 0)
            max_random (float): Max random extra seconds added to every wait (Default value = 0.5)
            ref_size (Tuple[int, int]): The reference size all coordinates refer to
        """
        self.title = title
        self.y_shift = int(y_offset)
        self.max_random = max_random
        self.ref_w, self.ref_h = ref_size

        self._mouse: MouseController | None = None
        self._local = threading.local()  # mss handles are per thread
        self._window = None
        self._abort = threading.Event()
        self._listener: keyboard.Listener | None = None

    # --- lifecycle
    def start(self, retries: int = 3) -> None:
        """Finds the game window, brings it to the front and starts the F10 abort hotkey.

        Args:
            retries (int): How often to retry the window lookup (Default value = 3)

        Raises:
            GameWindowError: If the window could not be found or is not usable
        """
        enable_dpi_awareness()
        if self._mouse is None:
            self._mouse = MouseController()

        self._abort.clear()
        last_error: Exception | None = None
        for attempt in range(retries):
            try:
                self.refresh()
                self.focus()
                last_error = None
                break
            except GameWindowError as e:
                last_error = e
                logger.warning(
                    "Window lookup attempt %d/%d failed: %s", attempt + 1, retries, e
                )
                if attempt < retries - 1:
                    time.sleep(1)
        if last_error is not None:
            raise last_error

        if self._listener is None:
            self._listener = keyboard.Listener(on_press=self._on_key)
            self._listener.start()
        logger.info("Press F10 at any time to abort the scan")

    def stop(self) -> None:
        """Stops the F10 hotkey listener. Safe to call multiple times."""
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

    def _on_key(self, key) -> None:
        if key == EMERGENCY_STOP_KEY:
            logger.info("F10 pressed - aborting the scan")
            self._abort.set()

    def abort(self) -> None:
        """Aborts the scan: the next action raises ScanAborted."""
        self._abort.set()

    def _check_abort(self) -> None:
        if self._abort.is_set():
            raise ScanAborted("Scan aborted (F10 pressed).")

    # --- window lookup / geometry
    def refresh(self) -> None:
        """Looks the window up again and verifies it is usable.

        Raises:
            GameWindowError: If the window is missing, minimized or has no visible area
        """
        matches = [w for w in gw.getWindowsWithTitle(self.title) if w.title.strip()]
        if not matches:
            raise GameWindowError(
                f'Game window "{self.title}" not found. Is the game running?'
            )
        exact = [w for w in matches if w.title == self.title]
        self._window = (exact or matches)[0]

        left, top, w, h = self._client_rect()
        if w <= 0 or h <= 0:
            raise GameWindowError("Game window is minimized or has no visible area.")
        logger.info(
            "Game window %r found: client area %dx%d at (%d, %d)",
            self._window.title,
            w,
            h,
            left,
            top,
        )
        if abs(w / h - self.ref_w / self.ref_h) > 0.02:
            logger.warning(
                "Game client area is %dx%d, not 16:9. OCR regions and clicks will be misaligned.",
                w,
                h,
            )

    def _client_rect(self) -> Tuple[int, int, int, int]:
        """(left, top, width, height) of the drawable area in screen pixels."""
        win = self._window
        if sys.platform == "win32" and hasattr(win, "_hWnd"):
            from ctypes import wintypes

            hwnd = win._hWnd
            rect = wintypes.RECT()
            ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(rect))
            origin = wintypes.POINT(0, 0)
            ctypes.windll.user32.ClientToScreen(hwnd, ctypes.byref(origin))
            return origin.x, origin.y, rect.right - rect.left, rect.bottom - rect.top
        return win.left, win.top, win.width, win.height  # type: ignore

    def client_size(self) -> Tuple[int, int]:
        """The current size of the game client area in screen pixels."""
        _, _, w, h = self._client_rect()
        return w, h

    def focus(self) -> None:
        """Brings the game to the foreground (it must be visible to be captured)."""
        logger.info("Bringing the game window to the front")
        win = self._window
        try:
            if win.isMinimized:  # type: ignore
                logger.info("Game window was minimized - restoring it")
                win.restore()  # type: ignore
            win.activate()  # type: ignore
        except Exception as e:
            # pygetwindow often raises "Error code 0" even when activation worked
            logger.debug("activate() reported: %s", e)
        time.sleep(0.3)

    def _is_foreground(self) -> bool:
        if sys.platform == "win32" and hasattr(self._window, "_hWnd"):
            return (
                ctypes.windll.user32.GetForegroundWindow()
                == self._window._hWnd  # type: ignore
            )
        return True

    def ensure_foreground(self) -> None:
        """Brings the game to the front again if something else got focus."""
        if not self._is_foreground():
            logger.info("Game window is not in the foreground")
            self.focus()

    # --- screenshots
    def screencap(self) -> Image.Image:
        """Captures the game client area.

        The image is rescaled to the 1600x900 reference size and shifted by the
        configured Y offset, so image[y] matches the configured coordinates.

        Returns:
            Image.Image: The screenshot as RGB image

        Raises:
            ScanAborted: If the scan was aborted with F10
            GameWindowError: If the window was closed or minimized
        """
        self._check_abort()
        left, top, w, h = self._client_rect()
        if w <= 0 or h <= 0:
            raise GameWindowError("Game window was closed or minimized during scan.")

        sct = getattr(self._local, "sct", None)
        if sct is None:
            sct = self._local.sct = mss.mss()

        try:
            shot = np.asarray(
                sct.grab({"left": left, "top": top, "width": w, "height": h})
            )
        except Exception as e:
            logger.exception("Screen capture failed")
            raise GameWindowError(f"Failed to capture the game window: {e}")

        img = cv2.cvtColor(shot, cv2.COLOR_BGRA2BGR)
        if (w, h) != (self.ref_w, self.ref_h):
            interp = cv2.INTER_AREA if w > self.ref_w else cv2.INTER_CUBIC
            img = cv2.resize(img, (self.ref_w, self.ref_h), interpolation=interp)
        img = self._apply_shift(img)
        return Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))

    def _apply_shift(self, img: np.ndarray) -> np.ndarray:
        """Makes img[y] correspond to game content row y (content starts y_shift lower)."""
        s = int(self.y_shift)
        if s == 0:
            return img
        pad = np.zeros((abs(s), img.shape[1], 3), dtype=img.dtype)
        if s > 0:
            return np.vstack([img[s:], pad])
        return np.vstack([pad, img[:s]])

    # --- input
    def _to_screen(self, x: int, y: int, jitter: int = 0) -> Tuple[int, int]:
        left, top, w, h = self._client_rect()
        sx = left + x * w / self.ref_w + random.randint(-jitter, jitter)
        sy = top + (y + self.y_shift) * h / self.ref_h + random.randint(-jitter, jitter)
        return int(sx), int(sy)

    def _move_to(self, sx: int, sy: int, steps: int = 12, step_delay: float = 0.008):
        assert self._mouse is not None
        cx, cy = self._mouse.position
        for s in range(1, steps + 1):
            t = s / steps
            ease = t * t * (3 - 2 * t)  # smoothstep
            self._mouse.position = (
                int(cx + (sx - cx) * ease),
                int(cy + (sy - cy) * ease),
            )
            time.sleep(step_delay)

    def wait(self, min_time: float, max_offset: float | None = None) -> None:
        """Waits for a random amount within the given limits.

        Has a low chance to wait much longer to simulate human behavior (1 in 20 times).
        Aborts immediately when F10 is pressed.

        Args:
            min_time (float): The minimum time to wait
            max_offset (float | None): The max amount added to the minimum time
                (Default value = the configured max_random)

        Raises:
            ScanAborted: If the scan was aborted with F10
        """
        if max_offset is None:
            max_offset = self.max_random
        extra = random.uniform(1.5, 4.0) if random.random() < 0.05 else 0.0
        total = random.uniform(min_time, min_time + max_offset) + extra
        logger.debug("WAIT  %.2fs", total)
        end = time.time() + total
        while True:
            self._check_abort()
            remaining = end - time.time()
            if remaining <= 0:
                return
            time.sleep(min(0.25, remaining))

    def tap(self, position: Tuple[int, int], jitter: int = 1) -> None:
        """Clicks at reference coordinates (1600x900 space).

        Args:
            position (Tuple[int, int]): The position to click in format (x, y)
            jitter (int): The maximum jitter distance in screen pixels (Default value = 1)

        Raises:
            ScanAborted: If the scan was aborted with F10
        """
        self._check_abort()
        assert self._mouse is not None, "start() was not called"
        self.ensure_foreground()
        sx, sy = self._to_screen(position[0], position[1], jitter=jitter)
        logger.debug("CLICK ref=%s -> screen=(%d, %d)", position, sx, sy)
        self._move_to(sx, sy)
        time.sleep(random.uniform(0.05, 0.12))
        self._mouse.press(Button.left)
        time.sleep(random.uniform(0.04, 0.09))
        self._mouse.release(Button.left)

    def swipe(
        self, x1: int, y1: int, x2: int, y2: int, duration: float = 0.4
    ) -> None:
        """Click-and-drag between two reference points.

        Args:
            x1 (int): Start x
            y1 (int): Start y
            x2 (int): End x
            y2 (int): End y
            duration (float): Seconds the drag takes (Default value = 0.4)

        Raises:
            ScanAborted: If the scan was aborted with F10
        """
        self._check_abort()
        assert self._mouse is not None, "start() was not called"
        self.ensure_foreground()
        sx1, sy1 = self._to_screen(x1, y1)
        sx2, sy2 = self._to_screen(x2, y2)
        logger.debug(
            "SWIPE ref=(%d, %d) -> (%d, %d), screen=(%d, %d) -> (%d, %d)",
            x1,
            y1,
            x2,
            y2,
            sx1,
            sy1,
            sx2,
            sy2,
        )
        self._move_to(sx1, sy1)
        time.sleep(0.05)
        self._mouse.press(Button.left)
        try:
            time.sleep(0.05)
            steps = 20
            for s in range(1, steps + 1):
                t = s / steps
                self._mouse.position = (
                    int(sx1 + (sx2 - sx1) * t),
                    int(sy1 + (sy2 - sy1) * t),
                )
                time.sleep(duration / steps)
            time.sleep(0.05)
        finally:
            self._mouse.release(Button.left)
