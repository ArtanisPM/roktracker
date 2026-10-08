"""Custom exception classes for the rok tracker.

Defines GameWindowError, ScanAborted and GovernorNotFoundError."""


class GameWindowError(RuntimeError):
    """An error with the game window.

    Most likely the window was not found, is minimized or got closed during the scan.
    """

    pass


class ScanAborted(RuntimeError):
    """The scan was aborted by the user with the emergency hotkey (F10)."""

    pass


class GovernorNotFoundError(RuntimeError):
    """No governor was found on the screen.

    Most likely cause is an inactive governor that can't be opened.
    """

    pass
