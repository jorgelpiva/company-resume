from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import DATA_DIR
from app.storage.base import CompanyStorage


class LocalCompanyStorage(CompanyStorage):
    def __init__(self, root: Path | str | None = None):
        self.root = Path(root) if root is not None else DATA_DIR
        self.root.mkdir(parents=True, exist_ok=True)

    def _company_dir(self, slug: str) -> Path:
        if not re.fullmatch(r"[a-zA-Z0-9-]+", slug or ""):
            raise ValueError("invalid company slug")
        return self.root / slug

    def _metadata_path(self, slug: str) -> Path:
        return self._company_dir(slug) / "metadata.json"

    def list_companies(self) -> List[Dict[str, Any]]:
        companies: List[Dict[str, Any]] = []
        if not self.root.exists():
            return companies
        for company_dir in sorted(self.root.iterdir(), key=lambda p: p.name):
            if not company_dir.is_dir() or company_dir.name.startswith("."):
                continue
            metadata_file = company_dir / "metadata.json"
            if not metadata_file.exists():
                continue
            try:
                with metadata_file.open("r", encoding="utf-8") as fh:
                    metadata = json.load(fh)
                companies.append(metadata)
            except Exception:
                continue
        return companies

    def get_company(self, slug: str) -> Optional[Dict[str, Any]]:
        metadata_file = self._metadata_path(slug)
        if not metadata_file.exists():
            return None
        with metadata_file.open("r", encoding="utf-8") as fh:
            return json.load(fh)

    def save_company(self, company: Dict[str, Any]) -> None:
        slug = company["slug"]
        company_dir = self._company_dir(slug)
        company_dir.mkdir(parents=True, exist_ok=True)
        metadata_file = company_dir / "metadata.json"
        with metadata_file.open("w", encoding="utf-8") as fh:
            json.dump(company, fh, ensure_ascii=False, indent=2)

    def delete_company(self, slug: str) -> bool:
        company_dir = self._company_dir(slug)
        if not company_dir.exists():
            return False
        for child in sorted(company_dir.iterdir(), reverse=True):
            if child.is_file() or child.is_symlink():
                child.unlink()
            elif child.is_dir():
                for nested in sorted(child.rglob("*"), reverse=True):
                    if nested.is_file() or nested.is_symlink():
                        nested.unlink()
                    elif nested.is_dir():
                        nested.rmdir()
                child.rmdir()
        company_dir.rmdir()
        return True

    def exists(self, slug: str) -> bool:
        return self._company_dir(slug).exists()
