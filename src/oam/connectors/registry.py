from __future__ import annotations


from typing import Any, Dict, Tuple, Type

from oam.connectors.base import BaseConnector


_CONNECTORS: Dict[Tuple[str, str], Type[BaseConnector]] = {}


def register(connector_cls: Type[BaseConnector]) -> Type[BaseConnector]:
    key = (connector_cls.country_code, connector_cls.source_code)
    _CONNECTORS[key] = connector_cls
    return connector_cls


def get_connector(*, country_code: str, source_code: str, config: dict[str, Any]) -> BaseConnector:
    key = (country_code, source_code)
    if key not in _CONNECTORS:
        raise KeyError(f"No connector registered for {country_code}/{source_code}")
    return _CONNECTORS[key](config=config)


def list_connectors() -> list[tuple[str, str]]:
    return sorted(_CONNECTORS.keys())
