from abc import ABC, abstractmethod
from typing import Dict, Any


class Error(Exception, ABC):
    def __init__(self, **kwargs):
        self._context = kwargs

    @property
    def context(self) -> Dict[str, Any]:
        return self._context

    @abstractmethod
    def display_error(self) -> None:
        """Print error to terminal."""
        ...
