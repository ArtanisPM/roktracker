"""Draws the configured screen positions on a live screenshot of the game.

Open the screen you want to check in the game (e.g. a governor profile, the kill
statistics popup, the More Info page or a ranking list), then run:

    uv run calibrate.py                    # kingdom scan positions
    uv run calibrate.py --config alliance  # alliance / honor / seed ranking positions

After a short countdown it saves two pictures into the calibration folder:
  *_raw.png      the exact 1600x900 frame the scanner sees (use it to measure positions)
  *_overlay.png  the same frame with every configured region (green boxes, labelled),
                 click position (red crosses) and list row (blue) drawn on it

A box that does not sit exactly on its text needs to be corrected in
config/internal/<config>.json. Regions of (0, 0, 0, 0) are not set and listed in the console.
"""

import argparse
import datetime
import time

import cv2
import numpy as np

from dummy_root import get_app_root
from roktracker.common.config import AppConfig
from roktracker.kingdom.config import KingdomConfig
from roktracker.ranking.config import RankingConfig
from roktracker.utils.game_window import GameWindowClient

GREEN = (0, 200, 0)
YELLOW = (0, 200, 220)
MAGENTA = (200, 0, 200)
RED = (0, 0, 255)
BLUE = (255, 120, 0)


def box(img: np.ndarray, region, label: str, color=GREEN) -> bool:
    """Draws a labelled box, returns False if the region is not set."""
    x, y, w, h = region
    if w <= 0 or h <= 0:
        return False
    cv2.rectangle(img, (x, y), (x + w, y + h), color, 1)
    cv2.putText(img, label, (x, max(10, y - 3)), cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1, cv2.LINE_AA)
    return True


def cross(img: np.ndarray, pos, label: str, color=RED) -> bool:
    """Draws a labelled cross, returns False if the position is not set."""
    x, y = pos
    if x == 0 and y == 0:
        return False
    cv2.drawMarker(img, (x, y), color, cv2.MARKER_CROSS, 18, 2)
    cv2.putText(img, label, (x + 10, y - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1, cv2.LINE_AA)
    return True


def draw_kingdom(img: np.ndarray, cfg: KingdomConfig) -> list[str]:
    """Draws all kingdom positions and returns the names that are not set."""
    ui = cfg.ui_config
    unset: list[str] = []
    for name, region in ui.regions:
        if not box(img, region, name):
            unset.append(name)
    for name, pos in ui.taps:
        if not cross(img, pos, name):
            unset.append(f"tap {name}")
    lay = ui.list_layout
    for i, y in enumerate(lay.rows_y):
        cross(img, (lay.tap_x, y), f"row {i}", BLUE)
    for i, pos in enumerate(lay.end_taps):
        cross(img, pos, f"end {i + 1}", BLUE)
    x1, y1, x2, y2 = lay.swipe_next
    cv2.arrowedLine(img, (x1, y1), (x2, y2), BLUE, 2, tipLength=0.3)
    return unset


def draw_ranking(img: np.ndarray, cfg: RankingConfig) -> list[str]:
    """Draws all ranking positions and returns the names that are not set."""
    ui = cfg.ui_config
    unset: list[str] = []
    sets = [
        ("name", ui.name_normal, GREEN),
        ("score", ui.score_normal, YELLOW),
        ("name_last", ui.name_last, MAGENTA),
        ("score_last", ui.score_last, MAGENTA),
    ]
    for label, regions, color in sets:
        for i, region in enumerate(regions):
            if not box(img, region, f"{label} {i}", color):
                unset.append(f"{label} {i}")
    x1, y1, x2, y2 = cfg.misc.scroll
    if any(cfg.misc.scroll):
        cv2.arrowedLine(img, (x1, y1), (x2, y2), BLUE, 2, tipLength=0.1)
    else:
        unset.append("scroll")
    return unset


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", choices=["kingdom", "alliance", "honor", "seed"], default="kingdom")
    parser.add_argument("--delay", type=int, default=5, help="seconds before the screenshot is taken")
    parser.add_argument("--title", default=None, help="game window title (default: from config/config.json)")
    args = parser.parse_args()

    root = get_app_root()
    app_config = AppConfig()
    client = GameWindowClient(
        args.title or app_config.general.window_title,
        y_offset=app_config.general.y_offset,
    )
    client.start()
    try:
        w, h = client.client_size()
        print(f"Game client area: {w}x{h} (positions refer to 1600x900, the screenshot is scaled to that)")
        for left in range(args.delay, 0, -1):
            print(f"Taking screenshot in {left}...", end="\r", flush=True)
            time.sleep(1)
        frame = client.screencap()
    finally:
        client.stop()

    raw = cv2.cvtColor(np.asarray(frame), cv2.COLOR_RGB2BGR)
    overlay = raw.copy()
    if args.config == "kingdom":
        unset = draw_kingdom(overlay, KingdomConfig.from_json(root / "config" / "internal" / "kingdom.json"))
    else:
        unset = draw_ranking(overlay, RankingConfig.from_json(root / "config" / "internal" / f"{args.config}.json"))

    out = root / "calibration"
    out.mkdir(exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    raw_path = out / f"{stamp}_{args.config}_raw.png"
    overlay_path = out / f"{stamp}_{args.config}_overlay.png"
    cv2.imwrite(str(raw_path), raw)
    cv2.imwrite(str(overlay_path), overlay)

    print(f"\nSaved {raw_path}\nSaved {overlay_path}")
    if unset:
        print("\nNot set (0, 0, 0, 0) in the config: " + ", ".join(unset))


if __name__ == "__main__":
    main()
