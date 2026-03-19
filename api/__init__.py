# -*- coding: utf-8 -*-
"""
API module for cadastre data fetching and hierarchy lookups.
"""

# Import from new modular structure
from .hierarchy import (
    load_cadastre_codes,
    load_cadastre_hierarchy,
    get_cadastre_codes_by_okres,
    get_cadastre_codes_by_kraj,
    get_unique_okresy,
    get_unique_kraje,
    find_cadastre_code,
)

from .fetcher import (
    DownloadCallback,
    fetch_parcels_by_cadastre_code,
    fetch_parcel_e_by_cadastre_code,
    fetch_cadastral_zoning_by_code,
)

__all__ = [
    # Hierarchy functions
    'load_cadastre_codes',
    'load_cadastre_hierarchy',
    'get_cadastre_codes_by_okres',
    'get_cadastre_codes_by_kraj',
    'get_unique_okresy',
    'get_unique_kraje',
    'find_cadastre_code',
    # Fetcher functions and classes
    'DownloadCallback',
    'fetch_parcels_by_cadastre_code',
    'fetch_parcel_e_by_cadastre_code',
    'fetch_cadastral_zoning_by_code',
]
