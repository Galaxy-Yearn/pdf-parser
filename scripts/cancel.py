from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


class CancelledError(RuntimeError):
    """Raised when a user requests to cancel a running job/tool."""


@dataclass(frozen=True)
class CancelContext:
    cancel: Optional[Callable[[], bool]] = None
    cancel_file: Optional[str] = None

    def requested(self) -> bool:
        # Callable cancel flag (e.g. UI cancel button / threading.Event).
        if self.cancel is not None:
            try:
                if bool(self.cancel()):
                    return True
            except Exception:  # noqa: BLE001
                # Cancellation must never crash the main job.
                pass

        # Optional file-based cancel flag (useful from a second terminal / UI).
        if self.cancel_file:
            try:
                if Path(self.cancel_file).exists():
                    return True
            except OSError:
                return False
        return False


def check_cancel(cancel: Optional[Callable[[], bool]] = None, cancel_file: Optional[str] = None) -> None:
    if CancelContext(cancel=cancel, cancel_file=cancel_file).requested():
        raise CancelledError("canceled")

