"""pylibinfra — инфраструктурные Python-kit'ы с observability из коробки."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("pylibinfra")
except PackageNotFoundError:  # checkout без установленного дистрибутива
    __version__ = "dev"
