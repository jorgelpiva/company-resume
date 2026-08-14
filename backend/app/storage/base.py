from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class CompanyStorage(ABC):
    @abstractmethod
    def list_companies(self) -> List[Dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def get_company(self, slug: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def save_company(self, company: Dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    def delete_company(self, slug: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def exists(self, slug: str) -> bool:
        raise NotImplementedError
