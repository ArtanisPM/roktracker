"""Kingdom scanner that scans governor data from a rankings screen.

Exports the KingdomScanner class which orchestrates the full
scan workflow: opening governors, reading OCR data, collecting
stats, saving state after each governor, and managing callbacks
for progress and output.

It drives the PC version of the game: screenshots of the game window are
saved as png files in temp_images and the OCR reads them from there."""

import datetime
import logging
import shutil
import time
from pathlib import Path
from typing import Callable, Literal

import copykitten
import cv2
from cv2.typing import MatLike
from PIL import Image
from tesserocr import (  # type: ignore (tesserocr has no type defs)
    OEM,
    PSM,
    PyTessBaseAPI,
)

from dummy_root import get_app_root
from roktracker.common.config import AppConfig
from roktracker.kingdom.config import (
    KingdomConfig,
    KingdomTapPositions,
    KingdomUIRegions,
)
from roktracker.kingdom.governor_data import AdditionalGovernorData, GovernorData
from roktracker.kingdom.governor_data_handler import GovernorDataHandler
from roktracker.kingdom.options import KingdomScanOptions, StatsToScan
from roktracker.utils.exceptions import GovernorNotFoundError, ScanAborted
from roktracker.utils.game_window import GameWindowClient
from roktracker.utils.general import (
    generate_random_id,
    load_cv2_img,
    more_info_present,
    to_int_check,
    write_cv2_img,
)
from roktracker.utils.icon_matching import find_copy_icon
from roktracker.utils.ocr import (
    advancedProcessing,
    cropToRegion,
    ocr_number,
    ocr_text,
    preprocess_and_ocr_number,
    preprocessImage,
)

logger = logging.getLogger(__name__)

PLAUSIBLE_ID_LENGTH = (6, 10)
"""Digit count of a believable governor id, used to recognize an open profile."""

COPY_ATTEMPTS = 3
"""How often copying the governor name is tried."""


class KingdomScanner:
    """A Scanner for detailed per governor stats.

    It expects a kingdom rankings screen like individual power or individual killpoints as starting point.
    It does that by clicking on each governor until reaching the target amount of people to scan.
    To be more time efficient it also only scans pages, that are needed for the relevant stats.

    To prevent data loss the current state is always saved after completing a governor.
    """

    def __init__(self, config: AppConfig, cfg: KingdomConfig):
        """Creates a kingdom scanner.

        Args:
            config (AppConfig): The app config with general settings to use.
            cfg (KingdomConfig): The kingdom scanner specific config
        """
        self.run_id = generate_random_id(8)
        self.scan_times: list[float] = []
        self.start_date = datetime.date.today()
        self.stop_scan = False

        self.config = config
        self.timings = config.timings
        self.max_random_delay = config.timings.max_random
        self.ocr = cfg.ui_config
        self.cfg = cfg

        self.scan_options = KingdomScanOptions()
        self.stats_to_scan = StatsToScan()
        self.abort = False
        self.inactive_players = 0

        self.root_dir = get_app_root()
        self.tesseract_path = Path(self.root_dir / "deps" / "tessdata")
        self.img_path = Path(self.root_dir / "temp_images")
        self.img_path.mkdir(parents=True, exist_ok=True)
        self.scan_path = Path(self.root_dir / cfg.scan_path)
        self.scan_path.mkdir(parents=True, exist_ok=True)
        self.inactive_path = Path(
            self.root_dir / "inactives" / str(self.start_date) / str(self.run_id)
        )
        self.review_path = Path(
            self.root_dir / "manual_review" / str(self.start_date) / str(self.run_id)
        )
        self.created_review_path = False

        # End of the ranking list: the list stops scrolling, so the same governor
        # keeps coming up. After seeing the same id 3 times the last rows below
        # the usual slot are opened directly (see _end_of_list_step).
        self.id_counter: dict[str, int] = {}
        self.end_detected = False
        self.end_step = 0

        self.gov_callback: Callable[[GovernorData, AdditionalGovernorData], None] = (
            lambda g, e: None
        )
        self.ask_continue: Callable[[str], bool] = lambda m: False
        self.state_callback: Callable[[str], None] = lambda m: None
        self.output_handler: Callable[[str], None] = lambda m: None

        self.client = GameWindowClient(
            config.general.window_title,
            y_offset=config.general.y_offset,
            max_random=config.timings.max_random,
        )

    def set_governor_callback(
        self, cb: Callable[[GovernorData, AdditionalGovernorData], None]
    ) -> None:
        """Sets the callback function that is called after a governor is scanned.

        Args:
            cb (Callable[[GovernorData, AdditionalGovernorData], None]): The function to call
        """
        self.gov_callback = cb

    def set_state_callback(self, cb: Callable[[str], None]):
        """Sets the callback function that is called after the state changes.

        Args:
            cb (Callable[[str], None]): The function to call
        """
        self.state_callback = cb

    def set_continue_handler(self, cb: Callable[[str], bool]):
        """Sets the callback function that is called when input is needed whether to continue the scan or not.

        Args:
            cb (Callable[[str], bool]): The function to call
        """
        self.ask_continue = cb

    def set_output_handler(self, cb: Callable[[str], None]):
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
        return (sum(self.scan_times, start=0) / len(self.scan_times)) * remaining_govs

    def _save_failed(
        self,
        fail_type: Literal["kills", "power"],
        gov_data: GovernorData,
        reconstructed: bool = False,
    ):
        """Save an image of the governor with a filename representing the kind of failure.

        This function saves either the image of the profile or the killpoints in the review folder.

        The filename is determined by the type of failure and if reconstruction was successful.
        It is (R|F|P){gov_id}-(profile|kills).png

        Args:
            fail_type (Literal['kills', 'power']): What failed in the scan
            gov_data (GovernorData): The GovernorData object with the failed data
            reconstructed (bool): Should be set to true of killpoint reconstruction was successful. (Default value = False)
        """
        pre = "Unset"
        if fail_type == "kills":
            if reconstructed:
                pre = "R"
                logging.log(
                    logging.INFO,
                    f"""Kills for {gov_data.name} ({to_int_check(gov_data.id)}) reconstructed, t1 might be off by up to 4 kills.""",
                )
            else:
                pre = "F"
                logging.log(
                    logging.WARNING,
                    f"""Kills for {gov_data.name} ({to_int_check(gov_data.id)}) don't check out, manually need to look at them!""",
                )
        elif fail_type == "power":
            pre = "P"
            logging.log(
                logging.WARNING,
                f"""Power for {gov_data.name} ({to_int_check(gov_data.id)}) is higher then previous governor, manually need to look at it!""",
            )

        if not self.created_review_path:
            self.review_path.mkdir(parents=True, exist_ok=True)
            self.created_review_path = True

        shutil.copy(
            Path(self.img_path / "gov_info.png"),
            Path(self.review_path / f"""{pre}{gov_data.id}-profile.png"""),
        )
        if fail_type == "kills":
            shutil.copy(
                Path(self.img_path / "kills_tier.png"),
                Path(self.review_path / f"""{pre}{gov_data.id}-kills.png"""),
            )

    @staticmethod
    def _region_set(region: tuple[int, int, int, int]) -> bool:
        """Checks if a region was measured. (0, 0, 0, 0) means it was not.

        Args:
            region (tuple[int, int, int, int]): The region in (x, y, w, h)

        Returns:
            bool: True if the region has a size
        """
        return region[2] > 0 and region[3] > 0

    def _warn_uncalibrated(self, options: KingdomScanOptions) -> None:
        """Tells the user which selected stats have no screen position and get skipped.

        Args:
            options (KingdomScanOptions): The options of the scan
        """
        regions = self.ocr.regions
        missing: list[str] = []
        for stat in StatsToScan.model_fields:
            region = getattr(regions, stat, None)
            if region is None or not getattr(options.stats_to_scan, stat):
                continue
            if not self._region_set(region):
                missing.append(stat)

        for stat in missing:
            msg = f"No screen position is configured for '{stat}' (config/internal/kingdom.json), it will be skipped."
            logger.warning(msg)
            self.output_handler(msg)

        if options.validate_kills and options.reconstruct_kills:
            kp_regions = [getattr(regions, f"t{t}_killpoints") for t in range(1, 6)]
            if not all(self._region_set(r) for r in kp_regions):
                msg = "Tier killpoint positions are not configured, wrong kills can't be reconstructed."
                logger.warning(msg)
                self.output_handler(msg)

    def _get_gov_position(self, current_position: int) -> int:
        """Get Y position of governor tap coordinate.

        The first rows are clicked where they first appear. After that the list
        shifts and the same slot (the last configured row) is used every time.

        Args:
            current_position (int): Current position to scan

        Returns:
            int: Y component of coordinate
        """
        rows = self.ocr.list_layout.rows_y
        return rows[min(current_position, len(rows) - 1)]

    def _tap_governor_row(self, current_position: int) -> None:
        """Clicks the row of a governor in the ranking list and waits for the profile.

        Args:
            current_position (int): Current position to scan
        """
        layout = self.ocr.list_layout
        self.client.tap((layout.tap_x, self._get_gov_position(current_position)))
        self.client.wait(self.timings.gov_open, self.max_random_delay)

    def _end_of_list_step(self) -> bool:
        """Advances the end-of-list sequence.

        Opens the rows below the usual slot one after another, because the list can't
        scroll any further there.

        Returns:
            bool: True once the sequence is finished and the scan is complete
        """
        end_taps = self.ocr.list_layout.end_taps
        if self.end_step > len(end_taps):
            return True

        self.client.tap(end_taps[self.end_step - 1])
        self.end_step += 1
        self.client.wait(self.timings.end_of_list, self.max_random_delay)
        return False

    def _open_governor(self, current_position: int) -> bool:
        """Opens the next governor, or advances the end-of-list sequence.

        Args:
            current_position (int): Current position to scan

        Returns:
            bool: False if the end of the list was reached and there is nothing left to open
        """
        if self.end_detected:
            return not self._end_of_list_step()

        self._tap_governor_row(current_position)
        return True

    def _track_governor_id(self, gov_id: str) -> None:
        """Counts how often an id was seen to detect the end of the ranking list.

        Args:
            gov_id (str): The id that was just read
        """
        clean_id = gov_id.strip()
        if not clean_id.isdigit():
            logger.info("Governor id %r is not a number", clean_id)
            return

        self.id_counter[clean_id] = self.id_counter.get(clean_id, 0) + 1
        logger.info("Governor id %s seen %d time(s)", clean_id, self.id_counter[clean_id])
        if self.id_counter[clean_id] == 3 and not self.end_detected:
            self.end_detected = True
            self.end_step = 1
            msg = "Same governor seen 3 times, switching to the last governors of the list."
            logger.info(msg)
            self.output_handler(msg)

    def _profile_open(self) -> bool:
        """Takes a screenshot and checks whether a governor profile is showing.

        Either the "More Info" button text or a plausible governor id counts, so one
        unreliable OCR region can't make the scan think the profile never opened.

        Returns:
            bool: True if the profile is open
        """
        self.client.screencap().save(self.img_path / "check_more_info.png")

        image_gray = load_cv2_img(
            self.img_path / "check_more_info.png", cv2.IMREAD_GRAYSCALE
        )
        im_check_more_info = cropToRegion(image_gray, self.ocr.regions.more_info)
        with PyTessBaseAPI(
            path=str(self.tesseract_path), psm=PSM.SINGLE_LINE
        ) as api:
            api.SetVariable("tessedit_char_whitelist", "MoreInfo")
            api.SetImage(Image.fromarray(im_check_more_info))  # type: ignore (pylance is messed up)
            check_more_info = api.GetUTF8Text()

        if more_info_present(check_more_info):
            return True

        if not self._region_set(self.ocr.regions.id):
            return False

        image_color = load_cv2_img(
            self.img_path / "check_more_info.png", cv2.IMREAD_COLOR_BGR
        )
        im_id = advancedProcessing(
            cropToRegion(image_color, self.ocr.regions.id), 3, "dimmed white"
        )
        with PyTessBaseAPI(
            path=str(self.tesseract_path), psm=PSM.SINGLE_LINE, oem=OEM.LSTM_ONLY
        ) as api:
            id_text = ocr_number(api, im_id, empty_retry=False)
        return PLAUSIBLE_ID_LENGTH[0] <= len(id_text) <= PLAUSIBLE_ID_LENGTH[1]

    def _save_inactive_screenshot(self, current_position: int) -> None:
        """Saves the row of the governor that could not be opened.

        Args:
            current_position (int): Current position to scan
        """
        image = load_cv2_img(
            self.img_path / "check_more_info.png", cv2.IMREAD_UNCHANGED
        )
        row_height = self.ocr.list_layout.row_height
        row_top = max(0, self._get_gov_position(current_position) - row_height // 2)
        roi = (0, row_top, image.shape[1], row_height)
        write_cv2_img(
            cropToRegion(image, roi),
            self.inactive_path / f"inactive {self.inactive_players:03}.png",
            "png",
        )

    def _detect_profile_layout(
        self,
    ) -> tuple[KingdomUIRegions, KingdomTapPositions]:
        """Detects which layout of the governor profile is showing.

        The PC client only has the current layout, so this only does something if
        a pre acclaim layout and a detection region are configured.

        Returns:
            tuple[KingdomUIRegions, KingdomTapPositions]: The regions and taps to use
        """
        cfg = self.ocr
        if not (
            self._region_set(cfg.profile_version)
            and cfg.regions_pre_acclaim is not None
            and cfg.taps_pre_acclaim is not None
        ):
            return cfg.regions, cfg.taps

        image_check = load_cv2_img(
            self.img_path / "check_more_info.png", cv2.IMREAD_COLOR_BGR
        )
        im_check = advancedProcessing(
            cropToRegion(image_check, cfg.profile_version), 3, "dimmed white"
        )
        with PyTessBaseAPI(path=str(self.tesseract_path)) as api:
            api.SetVariable("tessedit_char_whitelist", "Civlzaton")
            api.SetImage(Image.fromarray(im_check))  # type: ignore (pylance is messed up)
            check_profile_version = api.GetUTF8Text()

        if "Civilization" in check_profile_version:
            return cfg.regions, cfg.taps
        return cfg.regions_pre_acclaim, cfg.taps_pre_acclaim

    def _copy_governor_name(
        self,
        image: MatLike,
        regions: KingdomUIRegions,
        taps: KingdomTapPositions,
    ) -> str:
        """Finds the copy-name icon, clicks it and reads the name from the clipboard.

        The icon moves with the length of the governor name, so it is located on the
        screenshot every time. The configured tap position is only a fallback. The
        clipboard is emptied first and then polled, so a failed click can never
        return the name of the previous governor.

        Args:
            image (MatLike): The profile screenshot in BGR format
            regions (KingdomUIRegions): The regions of the profile
            taps (KingdomTapPositions): The tap positions of the profile

        Returns:
            str: The governor name, empty if it could not be copied
        """
        for attempt in range(COPY_ATTEMPTS):
            if attempt > 0:  # retry on a fresh screenshot
                self.client.screencap().save(self.img_path / "gov_info.png")
                image = load_cv2_img(
                    self.img_path / "gov_info.png", cv2.IMREAD_UNCHANGED
                )

            match = find_copy_icon(image[..., :3], regions.name_search)
            if match is not None:
                position = (match.x, match.y)
            else:
                position = taps.name
                logger.warning(
                    "Copy icon not found, clicking the default spot %s instead",
                    position,
                )

            try:
                copykitten.clear()
            except Exception:
                logger.debug("Could not clear the clipboard", exc_info=True)

            self.client.tap(position)
            self.client.wait(self.timings.copy_wait, self.max_random_delay)

            deadline = time.time() + 2.0
            while time.time() < deadline:
                try:
                    text = copykitten.paste().strip()
                except Exception:
                    text = ""
                if text:
                    return text
                time.sleep(0.15)
            logger.info("Name copy failed (attempt %d), retrying", attempt + 1)

        logger.warning("No governor name received from the clipboard")
        return ""

    def _is_page_needed(self, page: int) -> bool:
        """Checks if a page is needed for the scan.

        Args:
            page (int): Page number to check

        Returns:
            bool: True if needed, False otherwise
        """
        match page:
            case 1:
                return (
                    self.stats_to_scan.id
                    or self.stats_to_scan.name
                    or self.stats_to_scan.power
                    or self.stats_to_scan.killpoints
                    or self.stats_to_scan.acclaim
                    or self.stats_to_scan.acclaim_max
                    or self.stats_to_scan.alliance
                )
            case 2:
                return (
                    self.stats_to_scan.t1_kills
                    or self.stats_to_scan.t2_kills
                    or self.stats_to_scan.t3_kills
                    or self.stats_to_scan.t4_kills
                    or self.stats_to_scan.t5_kills
                    or self.stats_to_scan.ranged_points
                )
            case 3:
                return (
                    self.stats_to_scan.deads
                    or self.stats_to_scan.assisted
                    or self.stats_to_scan.gathered
                    or self.stats_to_scan.helps
                )
            case _:
                return False

    def _scan_governor(
        self,
        current_player: int,
        track_inactives: bool,
    ) -> GovernorData | None:
        """Main scanning method to scan all stats of a governor.

        This method has the following flow and calls the state callback multiple times:

        Opening a governor:
            Try to open a governor. To check if the operation was successful
            it is checked if there is a more info text (or a plausible governor id)
            present at the expected position. If not, the governor is skipped by
            dragging the list up one row and clicking the same slot again.

        The actual scan:
            It checks what stats should be scanned and only opens a page if it is actually needed.
            To get the score or text it looks up the expected position in the kingdom scanner config.

        After last screen:
            Cleans up the data for display purpose and calculates additional stats, like eta.

        Args:
            current_player (int): The position of the current governor
            track_inactives (bool): Should inactives be tracked

        Returns:
            GovernorData | None: The processed data for the governor, or None if
                the end of the list was reached

        Raises:
            GovernorNotFoundError: If no governor could be found after all retries
        """
        start_time = time.time()
        governor_data = GovernorData()

        self.state_callback("Opening governor")
        if not self._open_governor(current_player):
            return None

        count = 0
        while True:
            opened = self._profile_open()
            if not opened:
                # the profile may just be slow to open
                self.client.wait(1.0, 0.0)
                opened = self._profile_open()
            if opened:
                break

            # Probably tapped governor is inactive and needs to be skipped
            self.inactive_players += 1
            if track_inactives:
                self._save_inactive_screenshot(current_player)

            self.client.swipe(*self.ocr.list_layout.swipe_next)
            self.client.wait(self.timings.list_swipe, self.max_random_delay)
            if not self._open_governor(current_player):
                return None

            count += 1
            if count == 10:
                cont = self.ask_continue("Could not find user, retry?")
                if cont:
                    count = 0
                else:
                    raise GovernorNotFoundError(
                        "Could not open governor profile. Make sure the game is on the kingdom rankings screen (power or killpoints ranking)."
                    )

        ui_positions, tap_positions = self._detect_profile_layout()

        if self._is_page_needed(1):
            self.state_callback("Scanning general page")

            # take screenshot before copying the name
            self.client.screencap().save(self.img_path / "gov_info.png")
            image = load_cv2_img(self.img_path / "gov_info.png", cv2.IMREAD_UNCHANGED)

            if self.stats_to_scan.name:
                governor_data.name = self._copy_governor_name(
                    image, ui_positions, tap_positions
                )

            # 1st image data (ID, Power, Killpoints, Alliance)
            with PyTessBaseAPI(
                path=str(self.tesseract_path), psm=PSM.SINGLE_LINE, oem=OEM.LSTM_ONLY
            ) as api:
                if self.stats_to_scan.power and self._region_set(ui_positions.power):
                    im_gov_power = cropToRegion(image, ui_positions.power)
                    im_gov_power_bw = advancedProcessing(im_gov_power, 3, "white")

                    governor_data.power = ocr_number(api, im_gov_power_bw)

                if self.stats_to_scan.killpoints and self._region_set(
                    ui_positions.killpoints
                ):
                    im_gov_killpoints = cropToRegion(image, ui_positions.killpoints)
                    im_gov_killpoints_bw = advancedProcessing(
                        im_gov_killpoints, 3, "white"
                    )

                    governor_data.killpoints = ocr_number(api, im_gov_killpoints_bw)

                if self.stats_to_scan.acclaim and self._region_set(
                    ui_positions.acclaim
                ):
                    im_gov_acclaim = cropToRegion(image, ui_positions.acclaim)
                    im_gov_acclaim_bw = advancedProcessing(im_gov_acclaim, 3, "white")

                    governor_data.acclaim = ocr_number(api, im_gov_acclaim_bw)

                if self.stats_to_scan.acclaim_max and self._region_set(
                    ui_positions.acclaim_max
                ):
                    im_gov_acclaim_max = cropToRegion(image, ui_positions.acclaim_max)
                    im_gov_acclaim_max_bw = advancedProcessing(
                        im_gov_acclaim_max, 3, "white"
                    )

                    governor_data.acclaim_max = ocr_number(api, im_gov_acclaim_max_bw)

                api.SetPageSegMode(PSM.SINGLE_LINE)
                if self.stats_to_scan.id and self._region_set(ui_positions.id):
                    im_gov_id = cropToRegion(image, ui_positions.id)
                    im_gov_id_bw = advancedProcessing(im_gov_id, 3, "dimmed white")

                    governor_data.id = ocr_number(api, im_gov_id_bw)
                    self._track_governor_id(governor_data.id)

                if self.stats_to_scan.alliance and self._region_set(
                    ui_positions.alliance
                ):
                    im_alliance_tag = cropToRegion(image, ui_positions.alliance)
                    im_alliance_bw = preprocessImage(im_alliance_tag, 3, 50, 12, True)

                    governor_data.alliance = ocr_text(api, im_alliance_bw)

        if self._is_page_needed(2):
            # kills tier
            self.client.tap(tap_positions.kills)
            self.state_callback("Scanning kills page")
            self.client.wait(self.timings.kills_open, self.max_random_delay)

            self.client.screencap().save(self.img_path / "kills_tier.png")
            image2 = load_cv2_img(
                self.img_path / "kills_tier.png", cv2.IMREAD_UNCHANGED
            )
            image2 = cv2.cvtColor(image2, cv2.COLOR_BGR2RGB)

            with PyTessBaseAPI(
                path=str(self.tesseract_path), psm=PSM.SINGLE_WORD, oem=OEM.LSTM_ONLY
            ) as api:
                for tier in range(1, 6):
                    if not getattr(self.stats_to_scan, f"t{tier}_kills"):
                        continue

                    kills_region = getattr(ui_positions, f"t{tier}_kills")
                    if self._region_set(kills_region):
                        setattr(
                            governor_data,
                            f"t{tier}_kills",
                            preprocess_and_ocr_number(api, image2, kills_region),
                        )

                    kp_region = getattr(ui_positions, f"t{tier}_killpoints")
                    if self._region_set(kp_region):
                        setattr(
                            governor_data,
                            f"t{tier}_kp",
                            preprocess_and_ocr_number(api, image2, kp_region),
                        )

                if self.stats_to_scan.ranged_points and self._region_set(
                    ui_positions.ranged_points
                ):
                    governor_data.ranged_points = preprocess_and_ocr_number(
                        api, image2, ui_positions.ranged_points
                    )

            # Close the popup, otherwise it can swallow the click on More Info
            if any(tap_positions.dismiss_popup):
                self.client.tap(tap_positions.dismiss_popup)
                self.client.wait(self.timings.popup_dismiss, self.max_random_delay)

        if self._is_page_needed(3):
            # More info tab
            self.client.tap(tap_positions.info)
            self.state_callback("Scanning more info page")
            self.client.wait(self.timings.info_open, self.max_random_delay)
            self.client.screencap().save(self.img_path / "more_info.png")
            image3 = load_cv2_img(self.img_path / "more_info.png", cv2.IMREAD_UNCHANGED)

            with PyTessBaseAPI(
                path=str(self.tesseract_path), psm=PSM.SINGLE_WORD, oem=OEM.LSTM_ONLY
            ) as api:
                if self.stats_to_scan.deads and self._region_set(ui_positions.deads):
                    governor_data.dead = preprocess_and_ocr_number(
                        api, image3, ui_positions.deads, True
                    )

                if self.stats_to_scan.assisted and self._region_set(
                    ui_positions.assisted
                ):
                    governor_data.rss_assistance = preprocess_and_ocr_number(
                        api, image3, ui_positions.assisted, True
                    )

                if self.stats_to_scan.gathered and self._region_set(
                    ui_positions.gathered
                ):
                    governor_data.rss_gathered = preprocess_and_ocr_number(
                        api, image3, ui_positions.gathered, True
                    )

                if self.stats_to_scan.helps and self._region_set(ui_positions.helps):
                    governor_data.helps = preprocess_and_ocr_number(
                        api, image3, ui_positions.helps, True
                    )

        # Just to check the progress, printing in cmd the result for each governor
        governor_data.flag_unknown()

        self.state_callback("Closing governor")
        if self._is_page_needed(3):
            self.client.tap(tap_positions.close_info)  # close more info
            self.client.wait(self.timings.info_close, self.max_random_delay)
        self.client.tap(tap_positions.close_gov)  # close governor info
        self.client.wait(self.timings.gov_close, self.max_random_delay)

        end_time = time.time()

        self.output_handler("Time needed for governor: " + str((end_time - start_time)))
        self.scan_times.append(end_time - start_time)

        return governor_data

    def start_scan(self, options: KingdomScanOptions):
        """Start a kingdom scan.

        It is expected that the user has a kingdom ranking screen like individual power/killpoints open
        in the game window.

        Args:
            options (KingdomScanOptions): Scan options to use

        Raises:
            GameWindowError: If the game window can't be found or got closed
        """
        self.state_callback("Initializing")
        self.client.start()
        try:
            self._run_scan(options)
        finally:
            self.client.stop()

    def _run_scan(self, options: KingdomScanOptions):
        """The actual scan loop, see start_scan.

        Args:
            options (KingdomScanOptions): Scan options to use
        """
        amount = options.amount
        if options.track_inactives:
            self.inactive_path.mkdir(parents=True, exist_ok=True)

        ######Excel Formatting
        # Resume Scan options. Refine the loop
        j = 0
        if options.continued:
            j = 4
            amount = amount + j

        if options.continued:
            file_name_prefix = self.cfg.filename_prefix_continued
        else:
            file_name_prefix = self.cfg.filename_prefix_normal

        filename = f"{file_name_prefix}{amount - j}-{self.start_date}-{options.scan_name}-[{self.run_id}]"
        data_handler = GovernorDataHandler(self.scan_path, filename, options.formats)

        self.stats_to_scan = options.stats_to_scan
        self.id_counter = {}
        self.end_detected = False
        self.end_step = 0
        self._warn_uncalibrated(options)

        # The loop in TOP XXX Governors in kingdom - It works both for power and killpoints Rankings
        # MUST have the tab opened to the 1st governor(Power or Killpoints)

        last_gov_power = -1

        for i in range(j, amount):
            if self.stop_scan:
                self.output_handler("Scan Terminated! Saving the current progress...")
                break

            try:
                gov_data = self._scan_governor(i, options.track_inactives)
            except GovernorNotFoundError as e:
                self.output_handler(str(e))
                logging.log(logging.ERROR, str(e))
                self.state_callback("Scan aborted")
                data_handler.save()
                return
            except ScanAborted as e:
                self.output_handler(str(e))
                logging.log(logging.INFO, str(e))
                self.state_callback("Scan aborted")
                data_handler.save()
                return

            if gov_data is None:
                msg = "Reached the final governor on the screen. Scan complete."
                self.output_handler(msg)
                logging.log(logging.INFO, msg)
                break

            # The same governor can show up again at the end of the list
            if data_handler.has_id(gov_data.id):
                logging.log(
                    logging.INFO,
                    f"Governor {gov_data.id} was already scanned, not saving it again.",
                )
                self.gov_callback(
                    gov_data,
                    AdditionalGovernorData(
                        current_governor=i + 1,
                        target_governor=amount,
                        skipped_governors=self.inactive_players,
                        remaining_sec=self.get_remaining_time(amount - i),
                    ),
                )
                continue

            kills_ok = "Not Checked"
            reconstruction_success = "Not Checked"
            if options.validate_kills:
                kills_ok = gov_data.validate_kills()
                if not kills_ok and options.reconstruct_kills:
                    reconstruction_success = gov_data.reconstruct_kills()

                    if reconstruction_success:
                        self._save_failed("kills", gov_data, True)
                    else:
                        self._save_failed("kills", gov_data, False)

            power_ok = "Not Checked"
            if options.validate_power:
                # TODO: Respect threshold here
                gov_power = to_int_check(gov_data.power)
                if gov_power == 0:
                    gov_power = -1

                power_ok = (gov_power != -1) and (
                    (last_gov_power == -1)
                    or (to_int_check(gov_data.power) < last_gov_power)
                )

                if power_ok:
                    last_gov_power = gov_power
                else:
                    self._save_failed("power", gov_data)

            # Write results in excel file
            data_handler.write_governor(gov_data)
            data_handler.save()

            additional_info = AdditionalGovernorData(
                current_governor=i + 1,
                target_governor=amount,
                skipped_governors=self.inactive_players,
                power_ok=str(power_ok),
                kills_ok=str(kills_ok),
                reconstruction_success=str(reconstruction_success),
                remaining_sec=self.get_remaining_time(amount - i),
            )

            self.gov_callback(gov_data, additional_info)
        else:
            self.output_handler("Reached the target amount of people. Scan complete.")
            logging.log(
                logging.INFO, "Reached the target amount of people. Scan complete."
            )

        data_handler.save()
        self.state_callback("Scan finished")
        return

    def end_scan(self):
        """Ends the scan after the current governor."""
        self.stop_scan = True

    def abort_scan(self):
        """Aborts the scan immediately, without waiting for the current governor."""
        self.stop_scan = True
        self.client.abort()
