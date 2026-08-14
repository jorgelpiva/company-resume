import threading
import uuid
from typing import Dict, Optional

_jobs: Dict[str, Dict[str, object]] = {}
_jobs_lock = threading.Lock()


def create_job(company_name: Optional[str] = None, url: Optional[str] = None) -> str:
    job_id = str(uuid.uuid4())
    with _jobs_lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "progress": 0,
            "message": "Aguardando processamento",
            "company_name": company_name,
            "url": url,
            "error": None,
        }
    return job_id


def get_job(job_id: str) -> Optional[Dict[str, object]]:
    return _jobs.get(job_id)


def update_job(job_id: str, **kwargs) -> Dict[str, object]:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return {}
        for key, value in kwargs.items():
            job[key] = value
        return job


def remove_job(job_id: str) -> None:
    with _jobs_lock:
        _jobs.pop(job_id, None)
