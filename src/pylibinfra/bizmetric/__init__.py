"""bizmetric — helper для бизнес-метрик с конвенцией biz_<domain>_<name>_*."""

from .bizmetric import counter, gauge, histogram, must_name

__all__ = ["counter", "gauge", "histogram", "must_name"]
