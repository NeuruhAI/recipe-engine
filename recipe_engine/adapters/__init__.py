"""Domain adapter interfaces for Recipe Engine autonomy core."""

from .base import DomainAdapter
from .estate_sales import EstateSaleAdapter

__all__ = ["DomainAdapter", "EstateSaleAdapter"]
