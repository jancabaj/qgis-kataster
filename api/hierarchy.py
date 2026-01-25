# -*- coding: utf-8 -*-
"""
Cadastre hierarchy and lookup functions.

Provides functions to load and query the cadastre hierarchy from ku.csv.
"""

import csv
import os


def load_cadastre_codes(base_dir=None):
    """Load cadastre name -> code mapping from CSV."""
    cadastre_map = {}
    if base_dir is None:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    # CSV files are in ../data/ directory
    code_file = os.path.join(base_dir, '..', 'data', 'ku.csv')

    try:
        with open(code_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = row['NM5'].strip().lower()
                code = row['IDN5'].strip()
                cadastre_map[name] = code
    except Exception as e:
        print(f"⚠ Warning: Could not load cadastre codes: {e}")

    return cadastre_map


def load_cadastre_hierarchy(base_dir=None):
    """
    Load full hierarchy data from ku.csv.

    Returns:
        list of dicts with all columns from the CSV
    """
    hierarchy = []
    if base_dir is None:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    code_file = os.path.join(base_dir, '..', 'data', 'ku.csv')

    try:
        with open(code_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                hierarchy.append(row)
    except Exception as e:
        print(f"⚠ Warning: Could not load cadastre hierarchy: {e}")

    return hierarchy


def get_cadastre_codes_by_okres(okres_name, base_dir=None):
    """
    Get all cadastral codes (IDN5) for a given okres (NM3).

    Args:
        okres_name: Name of the okres (district), e.g., "Nitra"
        base_dir: Optional base directory for CSV file

    Returns:
        list of (code, name) tuples for all cadastral units in the okres
    """
    hierarchy = load_cadastre_hierarchy(base_dir)
    results = []
    okres_lower = okres_name.lower().strip()

    for row in hierarchy:
        if row['NM3'].lower().strip() == okres_lower:
            results.append((row['IDN5'].strip(), row['NM5'].strip()))

    # Sort by name for consistent ordering
    results.sort(key=lambda x: x[1])
    return results


def get_cadastre_codes_by_kraj(kraj_name, base_dir=None):
    """
    Get all cadastral codes (IDN5) for a given kraj (NM2).

    Args:
        kraj_name: Name of the kraj (region), e.g., "Nitriansky"
        base_dir: Optional base directory for CSV file

    Returns:
        list of (code, name) tuples for all cadastral units in the kraj
    """
    hierarchy = load_cadastre_hierarchy(base_dir)
    results = []
    kraj_lower = kraj_name.lower().strip()

    for row in hierarchy:
        if row['NM2'].lower().strip() == kraj_lower:
            results.append((row['IDN5'].strip(), row['NM5'].strip()))

    # Sort by name for consistent ordering
    results.sort(key=lambda x: x[1])
    return results


def get_unique_okresy(base_dir=None):
    """
    Get list of unique okres names for UI dropdown.

    Returns:
        Sorted list of unique okres (district) names
    """
    hierarchy = load_cadastre_hierarchy(base_dir)
    okresy = set()

    for row in hierarchy:
        okres = row['NM3'].strip()
        if okres:
            okresy.add(okres)

    return sorted(okresy)


def get_unique_kraje(base_dir=None):
    """
    Get list of unique kraj names for UI dropdown.

    Returns:
        Sorted list of unique kraj (region) names
    """
    hierarchy = load_cadastre_hierarchy(base_dir)
    kraje = set()

    for row in hierarchy:
        kraj = row['NM2'].strip()
        if kraj:
            kraje.add(kraj)

    return sorted(kraje)


def find_cadastre_code(query):
    """
    Find cadastre code by name or return the query if it's already a code.

    Returns: (code, name) tuple or (None, None) if not found
    """
    # If it's a 6-digit number, assume it's already a code
    if query.isdigit() and len(query) == 6:
        return query, None

    # Try to find by name
    cadastre_map = load_cadastre_codes()
    query_lower = query.lower()

    # Exact match
    if query_lower in cadastre_map:
        return cadastre_map[query_lower], query

    # Partial match
    for name, code in cadastre_map.items():
        if query_lower in name or name.startswith(query_lower):
            return code, name

    return None, None
