"""storagetest — общий контрактный набор для `storagekit.Storage`/`Presigner`.

`run_contract` прогоняет одни и те же кейсы (контракт `size`, отказ источника
тела, `NotFoundError`, идемпотентный `delete`, нормализация content-type,
диапазон TTL presign, экранирование ключа) по in-memory адаптеру и по боевому
`storagekit.s3.Storage`.
"""

from .contract import CASES, Case, Subject, SubjectFactory, run_case, run_contract

__all__ = ["CASES", "Case", "Subject", "SubjectFactory", "run_case", "run_contract"]
