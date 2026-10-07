"""Pydantic models for ranking scanner configuration.

Defines OCR preprocessing settings, the scroll drag, per-batch
region-of-interest coordinates, and the ranking config model. All
coordinates are in the 1600x900 reference space of the game client
area. Supports loading from JSON via the from_json() class method."""

from pathlib import Path

from pydantic import BaseModel


class RankingMisc(BaseModel):
    """Additional info how to handle the scan.

    Attributes:
        threshold (int): The threshold value to use when preprocessing the image
        invert (bool): Whether to invert the image before OCR
        scroll (tuple[int, int, int, int]): The drag (x1, y1, x2, y2) that scrolls the list
            by exactly one screen of governors after each batch
        scroll_duration (float): Seconds the scroll drag takes
    """

    threshold: int
    invert: bool
    scroll: tuple[int, int, int, int] = (0, 0, 0, 0)
    scroll_duration: float = 0.6


class UIConfig(BaseModel):
    """UI configuration for a batch on the ranking screen.

    Attributes:
        name_normal (list[tuple[int, int, int, int]]): The ROI where the name is located normally
        score_normal (list[tuple[int, int, int, int]]): The ROI where the score is located normally
        name_last (list[tuple[int, int, int, int]]): The ROI where the name is located when at the end of the rankings
        score_last (list[tuple[int, int, int, int]]): The ROI where the score is located when at the end of the rankings
    """

    name_normal: list[tuple[int, int, int, int]]
    score_normal: list[tuple[int, int, int, int]]

    name_last: list[tuple[int, int, int, int]]
    score_last: list[tuple[int, int, int, int]]


class RankingConfig(BaseModel):
    """Config options related to a ranking scan."""

    calibrated: bool = False
    """True once the positions below were measured for the PC client.
    The scan refuses to start before that, instead of reading garbage."""
    scan_path: str  # e.g. "scans_alliance"
    filename_prefix: str  # e.g. "Alliance"
    govs_per_screen: int  # e.g. 6
    last_different: bool  # True for Alliance/Seed (normal+last ROIs), False for Honor
    ui_config: UIConfig  # Any UI class with .name, .score, .name_last, etc.
    misc: RankingMisc

    @classmethod
    def from_json(cls, path: str | Path) -> RankingConfig:
        """Load and validate a RankingConfig from a JSON file.

        Args:
            path (str | Path): A string or Path object to the json file

        Returns:
            RankingConfig: The loaded and validated RankingConfig
        """
        data = Path(path).read_text(encoding="utf-8")
        return cls.model_validate_json(data)
