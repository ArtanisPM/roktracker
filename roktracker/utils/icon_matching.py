"""Template matching for the copy-name icon on the governor profile.

On the PC client the copy icon sits right behind the governor name, so its x
position depends on the length of the name. It is found by matching the icon
pictures in the assets folder inside the name row. The icon can be blue or
white, so every variant is tried and the best match wins.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from cv2.typing import MatLike

from dummy_root import get_app_root
from roktracker.utils.general import load_cv2_img

logger = logging.getLogger(__name__)

COPY_ICON_FILES = ("copy_icon.png", "copy_icon_white.png")
"""Icon variants in the assets folder: blue, white."""

COPY_ICON_MIN_SCORE = 0.80
"""A real icon scores 0.88-0.95, anything else is lower."""

COPY_ICON_SCALES = (0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.15)
"""Sizes the icon is tried at."""

_icon_cache: dict[str, list[tuple[str, MatLike, MatLike]]] = {}


@dataclass
class IconMatch:
    """A found icon.

    Attributes:
        x (int): X of the icon center in reference coordinates
        y (int): Y of the icon center in reference coordinates
        score (float): Match score between 0 and 1
    """

    x: int
    y: int
    score: float


def get_asset_dir() -> Path:
    """The folder the icon images are loaded from."""
    return get_app_root() / "assets"


def _load_icons() -> list[tuple[str, MatLike, MatLike]]:
    """Loads all icon variants as a list of (file name, bgr float32, alpha uint8).

    A missing variant is skipped with a warning, so matching still works with only
    one of them.
    """
    if "icons" in _icon_cache:
        return _icon_cache["icons"]

    icons: list[tuple[str, MatLike, MatLike]] = []
    for name in COPY_ICON_FILES:
        path = get_asset_dir() / name
        raw = load_cv2_img(path, cv2.IMREAD_UNCHANGED) if path.is_file() else None
        if raw is None or raw.ndim != 3 or raw.shape[:2] == (10, 10):
            logger.warning("Copy icon image assets/%s not found or unreadable", name)
            continue
        if raw.shape[2] == 4:
            icons.append((name, raw[..., :3].astype(np.float32), raw[..., 3]))
        else:
            alpha = np.full(raw.shape[:2], 255, dtype=np.uint8)
            icons.append((name, raw.astype(np.float32), alpha))

    logger.info("Copy icon variants loaded: %s", [i[0] for i in icons] or "none")
    _icon_cache["icons"] = icons
    return icons


def find_copy_icon(
    img: MatLike, search_roi: tuple[int, int, int, int]
) -> IconMatch | None:
    """Locates the copy-name icon (blue or white) on a governor profile screenshot.

    Each icon image is transparent around its shape, so it is first painted onto
    the panel color of the name row and then compared by structure (mean-subtracted
    correlation) at a few sizes. The best match over all variants and sizes is used.

    Args:
        img (MatLike): The profile screenshot in BGR format
        search_roi (tuple[int, int, int, int]): The name row in (x, y, w, h)

    Returns:
        IconMatch | None: The match, or None if nothing scores at least COPY_ICON_MIN_SCORE
    """
    icons = _load_icons()
    if not icons or search_roi[2] <= 0 or search_roi[3] <= 0:
        return None

    x0, y0, w, h = search_roi
    roi = img[y0 : y0 + h, x0 : x0 + w]
    if roi.size == 0:
        return None
    panel = np.median(roi.reshape(-1, 3), axis=0).astype(np.float32)
    gray_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)

    best = None
    for name, bgr, alpha in icons:
        for s in COPY_ICON_SCALES:
            tw = max(6, round(bgr.shape[1] * s))
            th = max(6, round(bgr.shape[0] * s))
            if tw > roi.shape[1] or th > roi.shape[0]:
                continue
            a = (
                cv2.resize(alpha, (tw, th), interpolation=cv2.INTER_AREA).astype(
                    np.float32
                )[..., None]
                / 255.0
            )
            b = cv2.resize(bgr, (tw, th), interpolation=cv2.INTER_AREA)
            tpl = cv2.cvtColor(
                (a * b + (1 - a) * panel).astype(np.uint8), cv2.COLOR_BGR2GRAY
            )
            res = cv2.matchTemplate(gray_roi, tpl, cv2.TM_CCOEFF_NORMED)
            _, score, _, loc = cv2.minMaxLoc(res)
            if best is None or score > best[0]:
                best = (
                    float(score),
                    x0 + loc[0] + tw // 2,
                    y0 + loc[1] + th // 2,
                    s,
                    name,
                )

    if best is None:
        return None
    score, x, y, scale, name = best
    logger.info(
        "COPY ICON best match %s at (%d, %d), score %.3f, scale %.2f (needs >= %.2f)",
        name,
        x,
        y,
        score,
        scale,
        COPY_ICON_MIN_SCORE,
    )
    if score < COPY_ICON_MIN_SCORE:
        return None
    return IconMatch(x, y, score)
