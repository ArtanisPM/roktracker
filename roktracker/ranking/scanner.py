"""Ranking scanner for alliance, honor, and kingdom power leaderboards.

Exports the RankingScanner class which scans all visible
governors on screen, scrolls to subsequent batches, saves
state after each batch, and manages callbacks for batch
progress and output. It drives the PC version of the game."""

import datetime
import math
import re
import time
from pathlib import Path
from typing import Callable, List, Literal

import cv2
from cv2.typing import MatLike
from PIL import Image
from tesserocr import OEM, PSM, PyTessBaseAPI

import roktracker.utils.ocr as ocr
from dummy_root import get_app_root
from roktracker.common.config import AppConfig
from roktracker.common.data import AdditionalScanData
from roktracker.ranking.config import RankingConfig
from roktracker.ranking.options import RankingScanOptions
from roktracker.ranking.ranking_data import RankingData
from roktracker.ranking.ranking_data_handler import RankingDataHandler
from roktracker.utils.exceptions import NotCalibratedError, ScanAborted
from roktracker.utils.game_window import GameWindowClient
from roktracker.utils.general import (
    generate_random_id,
    load_cv2_img,
    write_cv2_img,
)


class RankingScanner:
    """A Scanner for fast scanning of a single stat.

    It expects a ranking screen like alliance help ranking, honor rankings or
    kingdom individual power rankings as a starting point.
    It does that by scanning all visible governors on the screen and then
    scrolling to the next batch.

    To prevent data loss the current state is always saved after completing a governor.
    """

    def __init__(self, config: AppConfig, cfg: RankingConfig):
        """Creates a ranking scanner.

        Args:
            config (AppConfig): The app config with general settings to use.
            cfg (RankingConfig): The ranking scanner specific config
        """
        self.run_id = generate_random_id(8)
        self.start_date = datetime.date.today()
        self.stop_scan = False
        self.scan_times: list[float] = []
        self.reached_bottom: bool = False
        self.govs_per_screen = cfg.govs_per_screen
        self.screens_needed = 0
        self.max_random_delay = config.timings.max_random
        self.cfg = cfg

        self.root_dir = get_app_root()
        self.tesseract_path = Path(self.root_dir / "deps" / "tessdata")
        self.img_path = Path(self.root_dir / "temp_images")
        self.img_path.mkdir(parents=True, exist_ok=True)
        self.scan_path = Path(self.root_dir / cfg.scan_path)
        self.scan_path.mkdir(parents=True, exist_ok=True)

        self.batch_callback: Callable[[List[RankingData], AdditionalScanData], None] = (
            lambda g, e: None
        )
        self.state_callback: Callable[[str], None] = lambda m: None
        self.output_handler: Callable[[str], None] = lambda m: None

        self.client = GameWindowClient(
            config.general.window_title,
            y_offset=config.general.y_offset,
            max_random=config.timings.max_random,
        )

    # -- Callback setters (identical, no override needed) --
    def set_batch_callback(
        self, cb: Callable[[List[RankingData], AdditionalScanData], None]
    ) -> None:
        """Sets the callback function that is called after a batch is scanned.

        Args:
            cb (Callable[[List[RankingData], AdditionalScanData], None]): The function to call
        """
        self.batch_callback = cb

    def set_state_callback(self, cb: Callable[[str], None]) -> None:
        """Sets the callback function that is called after the state changes.

        Args:
            cb (Callable[[str], None]): The function to call
        """
        self.state_callback = cb

    def set_output_handler(self, cb: Callable[[str], None]) -> None:
        """Sets the callback function that is called when outputting information.

        Args:
            cb (Callable[[str], None]): The function to call
        """
        self.output_handler = cb

    def get_remaining_time(self, remaining_govs: int) -> float:
        """Estimates the remaining seconds until the scan is finished.

        Args:
            remaining_govs (int): How many more people to scan

        Returns:
            float: The amount of seconds needed to finish
        """
        avg = (
            sum(self.scan_times, start=0) / len(self.scan_times)
            if self.scan_times
            else 0
        )
        return avg * remaining_govs

    def _get_roi_region(
        self, gov_index: int, last: bool, kind: Literal["name", "score"]
    ) -> tuple[int, int, int, int]:
        """Return a ROI (x, y, w, h) tuple for the given governor position.

        Args:
            gov_index (int): Governor position on the screen
            last (bool): Whether it is the last screen of the ranking
            kind (Literal['name', 'score']): What is the kind of data to scan

        Returns:
            tuple[int, int, int, int]: ROI for the given data and position

        Raises:
            ValueError: If the kind did not match name or score
        """
        if kind == "name":
            return (
                self.cfg.ui_config.name_last
                if (last and self.cfg.last_different)
                else self.cfg.ui_config.name_normal
            )[gov_index]
        if kind == "score":
            return (
                self.cfg.ui_config.score_last
                if (last and self.cfg.last_different)
                else self.cfg.ui_config.score_normal
            )[gov_index]
        raise ValueError(f"Unknown kind: {kind}")

    def _check_for_last_screen(self, image: MatLike) -> None:
        """Check if the image is the last of a ranking.

        Args:
            image (MatLike): The image to check
        """
        roi_score = self._get_roi_region(0, False, "score")
        score_raw = ocr.cropToRegion(image, roi_score)
        score_bw = ocr.preprocessImage(
            score_raw, 3, self.cfg.misc.threshold, 12, self.cfg.misc.invert
        )
        with PyTessBaseAPI(
            path=str(self.tesseract_path), psm=PSM.SINGLE_WORD, oem=OEM.LSTM_ONLY
        ) as api:
            api.SetImage(Image.fromarray(score_bw))  # type: ignore (Incorrectly typed in pytesseract)
            test_score = re.sub("[^0-9]", "", api.GetUTF8Text())
            if test_score == "":
                self.reached_bottom = True

    def _scan_screen(self, screen_number: int) -> List[RankingData]:
        """Main part of the scanner. Scans a single ranking screen.

        Args:
            screen_number (int): The current screen position

        Returns:
            List[RankingData]: A list of RankingData for all governors on the screen
        """
        self.client.screencap().save(self.img_path / "currentState.png")
        image = load_cv2_img(self.img_path / "currentState.png", cv2.IMREAD_UNCHANGED)

        # Detect last screen by checking if first score is empty
        self._check_for_last_screen(image)

        govs: List[RankingData] = []
        with PyTessBaseAPI(
            path=str(self.tesseract_path), psm=PSM.SINGLE_LINE, oem=OEM.LSTM_ONLY
        ) as api:
            for gov_number in range(self.govs_per_screen):
                roi_name = self._get_roi_region(gov_number, self.reached_bottom, "name")
                roi_score = self._get_roi_region(
                    gov_number, self.reached_bottom, "score"
                )

                name_raw = ocr.cropToRegion(image, roi_name)
                score_raw = ocr.cropToRegion(image, roi_score)

                name_bw = ocr.preprocessImage(
                    name_raw, 3, self.cfg.misc.threshold, 12, self.cfg.misc.invert
                )
                name_small_bw = ocr.preprocessImage(
                    name_raw, 1, self.cfg.misc.threshold, 4, self.cfg.misc.invert
                )
                score_bw = ocr.preprocessImage(
                    score_raw, 3, self.cfg.misc.threshold, 12, self.cfg.misc.invert
                )

                api.SetPageSegMode(PSM.SINGLE_LINE)
                gov_name = ocr.ocr_text(api, name_bw)

                api.SetPageSegMode(PSM.SINGLE_WORD)
                gov_score = ocr.ocr_number(api, score_bw)

                gov_img_path = str(
                    self.img_path
                    / f"gov_name_{(self.govs_per_screen * screen_number) + gov_number}.png"
                )
                write_cv2_img(name_small_bw, gov_img_path, "png")

                govs.append(RankingData(gov_img_path, gov_name, gov_score))

        return govs

    def _scroll_action(self) -> None:
        """Perform the scroll after processing a screen."""
        x1, y1, x2, y2 = self.cfg.misc.scroll
        self.client.swipe(x1, y1, x2, y2, duration=self.cfg.misc.scroll_duration)

    def _make_filename(self, amount: int, scan_name: str) -> str:
        """Processes the filename for the scan.

        Args:
            amount (int): Amount of people to scan
            scan_name (str): The name of the scan

        Returns:
            str: Full path to the filename, without extension
        """
        return f"{self.cfg.filename_prefix}{amount}-{self.start_date}-{scan_name}-[{self.run_id}]"

    # -- Main scan loop (identical, no override needed) --
    def start_scan(self, options: RankingScanOptions):
        """Start a ranking scan.

        It is expected that the user has a ranking screen like alliance helps, honor or kingdom individual power
        open in the game window.

        Args:
            options (RankingScanOptions): Scan options to use

        Raises:
            NotCalibratedError: If the positions of this ranking were not measured for the PC client yet
            GameWindowError: If the game window can't be found or got closed
        """
        if not self.cfg.calibrated or not any(self.cfg.misc.scroll):
            raise NotCalibratedError(
                f"The {self.cfg.filename_prefix} scan is not set up for the PC client yet: "
                "its screen positions and scroll drag have not been measured. "
                f"Fill them in config/internal/{self.cfg.filename_prefix.lower()}.json "
                'and set "calibrated" to true. Run calibrate.py to check the positions.'
            )

        self.state_callback("Initializing")
        self.client.start()
        try:
            self._run_scan(options)
        finally:
            self.client.stop()
            for p in self.img_path.glob("gov_name*.png"):
                p.unlink()

    def _run_scan(self, options: RankingScanOptions) -> None:
        """The actual scan loop, see start_scan.

        Args:
            options (RankingScanOptions): Scan options to use
        """
        self.screens_needed = int(math.ceil(options.amount / self.govs_per_screen))

        filename = self._make_filename(options.amount, options.scan_name)
        data_handler = RankingDataHandler(self.scan_path, filename, options.formats)

        self.state_callback("Scanning")

        for i in range(self.screens_needed):
            if self.stop_scan:
                self.output_handler("Scan Terminated! Saving current progress...")
                break

            start_time = time.time()
            try:
                governors = self._scan_screen(i)
            except ScanAborted as e:
                self.output_handler(str(e))
                data_handler.save()
                self.state_callback("Scan aborted")
                return
            end_time = time.time()
            self.scan_times.append(end_time - start_time)

            additional_data = AdditionalScanData(
                current_governor=i * self.govs_per_screen,
                target_governor=self.screens_needed * self.govs_per_screen,
                remaining_sec=self.get_remaining_time(self.screens_needed - i),
            )
            self.batch_callback(governors, additional_data)

            self.reached_bottom = (
                data_handler.write_governors(governors) or self.reached_bottom
            )
            data_handler.save()

            if not self.reached_bottom:
                try:
                    self._scroll_action()
                    self.client.wait(1, self.max_random_delay)
                except ScanAborted as e:
                    self.output_handler(str(e))
                    data_handler.save()
                    self.state_callback("Scan aborted")
                    return

        data_handler.save()
        self.state_callback("Scan finished")

    def end_scan(self) -> None:
        """Ends the scan after the current batch."""
        self.stop_scan = True

    def abort_scan(self) -> None:
        """Aborts the scan immediately, without waiting for the current batch."""
        self.stop_scan = True
        self.client.abort()
