"""healthkit — именованная health-проба.

Киты с внешней зависимостью (pgkit, natskit, cachekit, storagekit) отдают
`Probe`; httpkit (`/ready`) и grpckit (health-loop) прогоняют кортеж `Probes`
из DI-контейнера консьюмера. Кит ничего не эмитит.
"""

from .probe import Check, Probe, Probes, ProbeTimeoutError, new, with_timeout

__all__ = ["Check", "Probe", "ProbeTimeoutError", "Probes", "new", "with_timeout"]
