"""auditkit — append-only audit-log: core-абстракция (Entry, Recorder, Store)
отделена от pgstore-реализации. Фиксирует кто/когда/что сделал над каким
ресурсом для compliance и forensics."""

from .audit import Entry, Recorder, Store, StoreRecorder
from .module import AuditProvider

__all__ = [
    "AuditProvider",
    "Entry",
    "Recorder",
    "Store",
    "StoreRecorder",
]
