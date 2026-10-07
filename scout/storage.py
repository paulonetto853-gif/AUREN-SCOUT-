from threading import Lock

from scout.models import Lead


class LeadStore:
    """Thread-safe in-memory store; data is cleared when the process exits."""

    def __init__(self) -> None:
        self._leads: dict[str, Lead] = {}
        self._lock = Lock()

    def save(self, lead: Lead) -> None:
        with self._lock:
            self._leads[lead.id] = lead

    def get(self, lead_id: str) -> Lead | None:
        with self._lock:
            return self._leads.get(lead_id)