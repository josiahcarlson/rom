from datetime import datetime, date, time as dtime
from decimal import Decimal as _Decimal

__all__ = ['NULL', '_NUMERIC', 'MODELS']

MODELS = {}
NULL = object()
_NUMERIC = (0, 0.0, _Decimal('0'), datetime(1970, 1, 1), date(1970, 1, 1), dtime(0, 0, 0))
