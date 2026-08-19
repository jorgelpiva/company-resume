from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class CompanyMetrics(BaseModel):
    pages_discovered: int = 0
    pages_selected: int = 0
    pages_processed: int = 0
    chunks: int = 0


class CompanyMetadata(BaseModel):
    name: str
    slug: str
    domain: str
    source_url: str
    status: str = "ready"
    mapped_at: str
    pages_discovered: int = 0
    pages_selected: int = 0
    pages_processed: int = 0
    chunks: int = 0


class MappingJob(BaseModel):
    job_id: str
    status: str = "queued"
    progress: int = 0
    message: str = "Aguardando processamento"
    company_name: Optional[str] = None
    company_slug: Optional[str] = None
    url: Optional[str] = None
    error: Optional[str] = None


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    history: List[Dict[str, str]] = Field(default_factory=list)


class BrowserCompanyContext(BaseModel):
    profile: str = ""
    chunks: List[Dict[str, object]] = Field(default_factory=list)
    research_context: Dict[str, object] = Field(default_factory=dict)


class BrowserChatRequest(ChatRequest):
    company: BrowserCompanyContext


class ChatResponse(BaseModel):
    answer: str
    sources: List[str] = []
