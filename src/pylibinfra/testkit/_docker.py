"""Общее для контейнерных helper'ов testkit: образы с точными тегами и гейт Docker."""

from __future__ import annotations

import logging
import unittest
from functools import cache

POSTGRES_IMAGE = "postgres:17-alpine"
NATS_IMAGE = "nats:2.14.6-alpine"
REDIS_IMAGE = "redis:7-alpine"
MINIO_IMAGE = "minio/minio:RELEASE.2025-01-20T14-49-07Z"


@cache
def docker_available() -> bool:
    """True, если testcontainers дотягивается до Docker-демона; результат кэшируется на процесс."""
    try:
        from testcontainers.core.docker_client import DockerClient  # type: ignore[import-untyped]

        DockerClient().client.ping()
    except Exception:
        return False
    return True


def require_docker() -> None:
    """Пропускает тест (unittest.SkipTest — его понимает и pytest), если Docker недоступен."""
    if not docker_available():
        raise unittest.SkipTest("testkit: docker is not available")


def quiet_testcontainers() -> None:
    """Глушит pull/wait-прогресс testcontainers: он идёт в root-логгер и топит вывод pytest."""
    logging.getLogger("testcontainers").setLevel(logging.WARNING)
