from contextvars import ContextVar

HEADER_REQUEST_ID = "X-Request-ID"

# Источник X-Request-ID для исходящих запросов: httpclient читает его сам, чтобы
# не зависеть от httpkit (тот заполняет этот же contextvar на входящем запросе).
request_id: ContextVar[str] = ContextVar("request_id", default="")
