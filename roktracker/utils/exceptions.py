"""Custom exception classes for the rok tracker.

Defines GameWindowError, ScanAborted, NotCalibratedError and GovernorNotFoundError."""


class GameWindowError(RuntimeError):
    """An error with the game window.

    Most likely the window was not found, is minimized or got closed during the scan.
    """

    pass


class ScanAborted(RuntimeError):
    """The scan was aborted by the user with the emergency hotkey (F10)."""

    pass


class NotCalibratedError(RuntimeError):
    """The UI positions for this scan type were not measured for the PC client yet.

    The positions live in config/internal/*.json and can be checked with calibrate.py.
    """

    pass


class GovernorNotFoundError(RuntimeError):
    """No governor was found on the screen.

    Most likely cause is an inactive governor that can't be opened.
    """

    pass
