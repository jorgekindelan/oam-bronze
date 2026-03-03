from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Mapping, Optional


class ObjectStore(ABC):
    @abstractmethod
    def exists(self, storage_key: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def put_bytes(
        self,
        storage_key: str,
        data: bytes,
        *,
        metadata: Optional[Mapping[str, str]] = None,
        overwrite: bool = False,
    ) -> None:
        raise NotImplementedError
