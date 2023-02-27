import logging
import os
import sys
from typing import *

try:
    from types import EllipsisType
except ImportError:
    EllipsisType = Any  # type: ignore

_T = TypeVar("_T")


def init_logging() -> None:
    handler = logging.StreamHandler(sys.stderr)

    loglevel = os.environ.get("LOGLEVEL", "CRITICAL").upper()
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    root_logger.setLevel(loglevel)

    zeroconf_loglevel = os.environ.get("ZEROCONF_LOGLEVEL", "CRITICAL").upper()
    zeroconf_logger = logging.getLogger("zeroconf")
    zeroconf_logger.addHandler(handler)
    zeroconf_logger.setLevel(zeroconf_loglevel)


def get_show_default(default: Callable[[], Union[_T, EllipsisType]]) -> Union[_T, bool]:
    default_value = default()
    return default_value if default_value is not ... else False
