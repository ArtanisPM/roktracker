"""Pydantic models for kingdom scanner configuration.

Defines UI region coordinates, tap coordinates, the ranking list
layout, and per-profile UI config. All coordinates are in the 1600x900
reference space of the game client area (see utils/game_window.py).
A region of (0, 0, 0, 0) means "not measured": that stat is skipped.
Supports loading configuration from JSON files via the from_json()
class method."""

from pathlib import Path

from pydantic import BaseModel


class KingdomUIRegions(BaseModel):
    """OCR region coordinates for extracting governor data from screen sections."""

    # first screen
    profile_title: tuple[int, int, int, int] = (0, 0, 0, 0)
    """The "GOVERNOR PROFILE" title bar. It looks the same on every profile skin,
    so it is the most reliable sign that a profile is open."""
    more_info: tuple[int, int, int, int] = (0, 0, 0, 0)
    name_search: tuple[int, int, int, int] = (0, 0, 0, 0)
    """The name row, searched for the copy-name icon."""
    id: tuple[int, int, int, int] = (0, 0, 0, 0)
    power: tuple[int, int, int, int] = (0, 0, 0, 0)
    killpoints: tuple[int, int, int, int] = (0, 0, 0, 0)
    acclaim: tuple[int, int, int, int] = (0, 0, 0, 0)
    acclaim_max: tuple[int, int, int, int] = (0, 0, 0, 0)
    alliance: tuple[int, int, int, int] = (0, 0, 0, 0)

    # second screen
    t1_kills: tuple[int, int, int, int] = (0, 0, 0, 0)
    t2_kills: tuple[int, int, int, int] = (0, 0, 0, 0)
    t3_kills: tuple[int, int, int, int] = (0, 0, 0, 0)
    t4_kills: tuple[int, int, int, int] = (0, 0, 0, 0)
    t5_kills: tuple[int, int, int, int] = (0, 0, 0, 0)
    t1_killpoints: tuple[int, int, int, int] = (0, 0, 0, 0)
    t2_killpoints: tuple[int, int, int, int] = (0, 0, 0, 0)
    t3_killpoints: tuple[int, int, int, int] = (0, 0, 0, 0)
    t4_killpoints: tuple[int, int, int, int] = (0, 0, 0, 0)
    t5_killpoints: tuple[int, int, int, int] = (0, 0, 0, 0)
    ranged_points: tuple[int, int, int, int] = (0, 0, 0, 0)

    # third screen
    deads: tuple[int, int, int, int] = (0, 0, 0, 0)
    gathered: tuple[int, int, int, int] = (0, 0, 0, 0)
    assisted: tuple[int, int, int, int] = (0, 0, 0, 0)
    helps: tuple[int, int, int, int] = (0, 0, 0, 0)


class KingdomTapPositions(BaseModel):
    """Tap coordinates for navigating the governor screens."""

    name: tuple[int, int] = (0, 0)
    """Fallback position of the copy-name icon, only used if the icon is not found."""
    kills: tuple[int, int] = (0, 0)
    dismiss_popup: tuple[int, int] = (0, 0)
    """A harmless spot tapped to close the kill statistics popup. (0, 0) = don't."""
    info: tuple[int, int] = (0, 0)
    close_gov: tuple[int, int] = (0, 0)

    # third screen
    close_info: tuple[int, int] = (0, 0)


class KingdomListLayout(BaseModel):
    """Where the governors are on the individual power/killpoints ranking list."""

    tap_x: int
    """X to click on a row (a blank part of the row)."""
    rows_y: list[int]
    """Y of the first rows. The last entry is used for every governor after that."""
    end_taps: list[tuple[int, int]]
    """Rows below the last slot, clicked at the end of the list when the same
    governor keeps coming up (the last governors are then opened directly)."""
    swipe_next: tuple[int, int, int, int]
    """Drag that moves the list up by one row, used to skip a governor that won't open."""
    row_height: int = 70
    """Height of one row, used to crop screenshots of inactive governors."""


class UIConfig(BaseModel):
    """UI configuration for a governor profile."""

    list_layout: KingdomListLayout
    profile_version: tuple[int, int, int, int] = (0, 0, 0, 0)
    """Region used to detect the pre-acclaim profile layout. (0, 0, 0, 0) = the
    PC client only has one layout, always use regions and taps."""
    regions: KingdomUIRegions
    taps: KingdomTapPositions
    regions_pre_acclaim: KingdomUIRegions | None = None
    taps_pre_acclaim: KingdomTapPositions | None = None


class KingdomConfig(BaseModel):
    """Config options related to a kingdom scan."""

    scan_path: str
    filename_prefix_normal: str
    filename_prefix_continued: str
    ui_config: UIConfig

    @staticmethod
    def from_json(path: str | Path) -> KingdomConfig:
        """Load and validate a KingdomConfig from a JSON file.

        Args:
            path (str | Path): A string or Path object to the json file

        Returns:
            KingdomConfig: The loaded and validated KingdomConfig
        """
        data = Path(path).read_text(encoding="utf-8")
        return KingdomConfig.model_validate_json(data)
