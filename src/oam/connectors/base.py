from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, AsyncIterator, Optional

from oam.models.discovery import DiscoveryRecord
from oam.models.document import DocumentRecord


class BaseConnector(ABC):
    """Base interface for a country/source connector.

    Important: discovery and download are strictly separated.
    """

    country_code: str
    source_code: str
    source_name: str

    def __init__(self, *, config: dict[str, Any]):
        self.config = config

    @abstractmethod
    async def discover(
        self,
        *,
        crawl_run_id: str,
        date_from: datetime,
        date_to: datetime,
        checkpoint: Optional[dict[str, Any]],
    ) -> AsyncIterator[DiscoveryRecord]:
        raise NotImplementedError

    @abstractmethod
    async def download_document(
        self, *, crawl_run_id: str, discovery: DiscoveryRecord
    ) -> tuple[DocumentRecord, bytes]:
        raise NotImplementedError

    async def smoke_test(self) -> bool:
        return True

    def checkpoint_save(self, state: dict[str, Any]) -> dict[str, Any]:
        return state

    def checkpoint_load(self, state: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        return state
