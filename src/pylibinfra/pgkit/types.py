"""Тип-алиасы поверх asyncpg — единая точка, чтобы потребитель не тянул
asyncpg напрямую."""

import asyncpg  # type: ignore[import-untyped]

Record = asyncpg.Record
Connection = asyncpg.Connection
Pool = asyncpg.Pool
