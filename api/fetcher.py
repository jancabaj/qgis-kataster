# -*- coding: utf-8 -*-
"""
Generic feature fetching from Slovak cadastre API with retry logic.
"""

import requests
import json
import time
import os


class DownloadCallback:
    """Callback interface for progress updates."""

    def on_progress(self, message):
        """Called when progress message is available."""
        print(message)

    def on_error(self, message):
        """Called when error occurs."""
        print(f"ERROR: {message}")


def fetch_features_generic(base_url, collection, cql_filter, cadastre_code, layer_type_name,
                           limit=25000, output_dir=".", callback=None):
    """
    Generic function to fetch features from Slovak cadastre API.

    Args:
        base_url: Base URL of the API
        collection: Collection name (e.g., "CP.CadastralParcel")
        cql_filter: CQL filter expression
        cadastre_code: Cadastre code for error logging
        layer_type_name: Human-readable layer type (e.g., "parcels", "zoning")
        limit: Max features per request
        output_dir: Directory to save error files
        callback: Optional DownloadCallback instance for progress updates

    Returns:
        GeoJSON FeatureCollection
    """
    if callback is None:
        callback = DownloadCallback()

    url = f"{base_url}/collections/{collection}/items"
    all_features = []
    offset = 0

    callback.on_progress(f"Fetching {layer_type_name} for cadastre code: {cadastre_code}")
    callback.on_progress(f"Using CQL filter: {cql_filter}")
    callback.on_progress(f"Batch size: {limit}\n")

    while True:
        params = {
            'limit': limit,
            'offset': offset,
            'filter-lang': 'cql-text',
            'filter': cql_filter
        }

        callback.on_progress(f"  Requesting: offset={offset}, limit={limit}...")

        try:
            response = requests.get(url, params=params, timeout=60)
            response.raise_for_status()

            # Parse JSON with error handling
            try:
                data = response.json()
            except json.JSONDecodeError as e:
                # API sometimes returns malformed JSON for certain cadastres
                callback.on_error(f"JSON Error at offset {offset}: {str(e)}")

                # Save the corrupted response for inspection
                error_file = os.path.join(output_dir, f"error_response_{layer_type_name}_{cadastre_code}_offset{offset}.json")
                try:
                    with open(error_file, 'w', encoding='utf-8') as f:
                        f.write(response.text)
                    callback.on_progress(f"  Saved corrupted response to: {error_file}")
                    callback.on_progress(f"  Response length: {len(response.text)} chars")

                    # Show the error location
                    error_pos = e.pos if hasattr(e, 'pos') else None
                    if error_pos:
                        # Show context around the error
                        start = max(0, error_pos - 100)
                        end = min(len(response.text), error_pos + 100)
                        context = response.text[start:end]
                        callback.on_progress(f"  Error at position {error_pos}:")
                        callback.on_progress(f"  Context: ...{context}...")
                except Exception as save_error:
                    callback.on_progress(f"  Could not save error file: {save_error}")

                callback.on_progress(f"  Skipping this batch and continuing...")
                # Skip this batch and try next one
                offset += limit
                if offset > 10000:  # Don't retry forever
                    callback.on_progress(f"  Too many errors, stopping")
                    break
                continue

            features = data.get('features', [])

            if not features:
                callback.on_progress("  no more features")
                break

            all_features.extend(features)
            callback.on_progress(f"  got {len(features)} features")

            # If we got fewer features than limit, we're at the end
            if len(features) < limit:
                break

            offset += limit

            # Safety limit to avoid infinite loops
            if offset > 50000:
                callback.on_progress("  Warning: Hit safety limit of 50k features")
                break

        except requests.exceptions.Timeout:
            callback.on_error("Timeout - server might be overloaded. Retrying...")
            # Retry with exponential backoff
            retry_successful = False
            for retry in range(3):
                time.sleep(2 ** retry)  # Wait 1s, 2s, 4s
                callback.on_progress(f"  Retry {retry + 1}/3...")
                try:
                    response = requests.get(url, params=params, timeout=15)
                    response.raise_for_status()
                    data = response.json()
                    features = data.get('features', [])
                    all_features.extend(features)
                    callback.on_progress(f"  Retry successful! Got {len(features)} features")
                    retry_successful = True
                    if len(features) < limit:
                        break
                    offset += limit
                    break
                except Exception as retry_error:
                    if retry == 2:  # Last retry
                        callback.on_error(f"All retries failed: {retry_error}")
                    continue
            if not retry_successful:
                break
        except requests.exceptions.RequestException as e:
            callback.on_error(f"Request error: {e}. Retrying...")
            # Retry once for other request errors
            time.sleep(2)
            try:
                response = requests.get(url, params=params, timeout=90)
                response.raise_for_status()
                data = response.json()
                features = data.get('features', [])
                all_features.extend(features)
                callback.on_progress(f"  Retry successful! Got {len(features)} features")
                if len(features) < limit:
                    break
                offset += limit
            except Exception as retry_error:
                callback.on_error(f"Retry failed: {retry_error}")
                break

    feature_collection = {
        "type": "FeatureCollection",
        "features": all_features
    }

    callback.on_progress(f"\n✓ Total {layer_type_name} found: {len(all_features)}")
    return feature_collection


# API endpoints
BASE_URL = "https://inspirews.skgeodesy.sk/geoserver/cp/ogc/features/v1"
BASE_URL_E = "https://inspirews.skgeodesy.sk/geoserver/cp_uo/ogc/features/v1"


def fetch_parcels_by_cadastre_code(cadastre_code, limit=25000, output_dir=".", callback=None):
    """
    Fetch all C register parcels for a given cadastre code using CQL filtering.

    Args:
        cadastre_code: 6-digit cadastre code (e.g., "815713")
        limit: Max features per request (default 25000)
        output_dir: Directory to save error files (default current directory)
        callback: Optional DownloadCallback instance for progress updates

    Returns:
        GeoJSON FeatureCollection
    """
    # The nationalCadastralReference format is: cadastre_code + "_" + parcel_number + ".C"
    cql_filter = f"nationalCadastralReference LIKE '{cadastre_code}_%'"

    return fetch_features_generic(
        BASE_URL,
        "CP.CadastralParcel",
        cql_filter,
        cadastre_code,
        "C register parcels",
        limit,
        output_dir,
        callback
    )


def fetch_parcel_e_by_cadastre_code(cadastre_code, limit=25000, output_dir=".", callback=None):
    """
    Fetch E register parcels for a given cadastre code using CQL filtering.

    Args:
        cadastre_code: 6-digit cadastre code (e.g., "815713")
        limit: Max features per request (default 25000)
        output_dir: Directory to save error files
        callback: Optional DownloadCallback instance for progress updates

    Returns:
        GeoJSON FeatureCollection
    """
    # The nationalCadastralReference format is: cadastre_code + "_" + parcel_number + ".E"
    cql_filter = f"nationalCadastralReference LIKE '{cadastre_code}_%'"

    return fetch_features_generic(
        BASE_URL_E,
        "CP.CadastralParcelUO",
        cql_filter,
        cadastre_code,
        "E register parcels",
        limit,
        output_dir,
        callback
    )


def fetch_cadastral_zoning_by_code(cadastre_code, limit=25000, output_dir=".", callback=None):
    """
    Fetch cadastral zoning (boundaries) for a given cadastre code using CQL filtering.

    Args:
        cadastre_code: 6-digit cadastre code (e.g., "815713")
        limit: Max features per request (default 25000)
        output_dir: Directory to save error files
        callback: Optional DownloadCallback instance for progress updates

    Returns:
        GeoJSON FeatureCollection
    """
    # The nationalCadastalZoningReference uses the 6-digit code
    # Note: API has typo "Cadastal" instead of "Cadastral"
    cql_filter = f"nationalCadastalZoningReference = '{cadastre_code}'"

    return fetch_features_generic(
        BASE_URL,
        "CP.CadastralZoning",
        cql_filter,
        cadastre_code,
        "cadastral zoning",
        limit,
        output_dir,
        callback
    )
