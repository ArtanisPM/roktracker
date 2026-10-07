"""Global configuration and settings for the rok tracker application.

Defines the AppConfig and related configuration models loaded
from config.json, with default values for the game window title,
click/screenshot offsets and scan timings."""

from pathlib import Path
from typing import override

from pydantic import BaseModel
from pydantic_settings import (
    BaseSettings,
    JsonConfigSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from dummy_root import get_app_root


class ScanTimings(BaseModel):
    """Waiting timings (seconds) between actions during scanning.

    Every wait also gets a random extra of 0..max_random seconds.
    Raise a value if the game is slow to open that screen, lower it to scan faster.
    """

    gov_open: float = 2.0  # after clicking a governor row (profile opens)
    copy_wait: float = 0.2  # after clicking the copy-name icon
    kills_open: float = 1.0  # after opening the kill statistics popup
    info_open: float = 1.0  # after opening the More Info page
    info_close: float = 0.5  # after closing the More Info page
    gov_close: float = 1.0  # after closing the governor profile
    popup_dismiss: float = 0.5  # after dismissing the kill statistics popup
    list_swipe: float = 1.0  # after dragging the list to skip a governor
    end_of_list: float = 2.0  # after the end-of-list row clicks (profile opens)
    max_random: float = 0.5


class GeneralConfig(BaseModel):
    """General application settings (game window, click offset)."""

    window_title: str = "Rise of Kingdoms"
    """Title (or part of it) of the game window."""

    y_offset: int = 0
    """Vertical shift in 1600x900 reference pixels applied to ALL clicks and
    screenshots. Leave at 0 when the window is captured correctly."""


class AppConfig(BaseSettings):
    """Collection of all globally configured values.

    It uses default values or loads from the config.json file.
    """

    model_config = SettingsConfigDict(
        # Load settings from config.json; silently ignore unknown keys.
        json_file=get_app_root() / "config" / "config.json",
        extra="ignore",
    )

    timings: ScanTimings = ScanTimings()
    general: GeneralConfig = GeneralConfig()

    @override
    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Only load settings from the JSON config file, ignoring env vars.

        Args:
            settings_cls (type[BaseSettings]): pydantic internal
            init_settings (PydanticBaseSettingsSource): pydantic internal
            env_settings (PydanticBaseSettingsSource): pydantic internal
            dotenv_settings (PydanticBaseSettingsSource): pydantic internal
            file_secret_settings (PydanticBaseSettingsSource): pydantic internal

        Returns:
            tuple[PydanticBaseSettingsSource, ...]: pydantic internal
        """
        return (JsonConfigSettingsSource(settings_cls),)
