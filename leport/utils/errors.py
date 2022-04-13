from abc import ABC, abstractmethod
from typing import Dict, Any


class Error(Exception, ABC):
    def __init__(self, context: Dict[str, Any]):
        self.__context = context

    def context(self) -> Dict[str, Any]:
        return self.__context

    @abstractmethod
    def message(self) -> str:
        ...
