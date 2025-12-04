#!/usr/bin/env python3
"""
Download cadastre parcels by cadastre code or name and save to GeoJSON

This module can be used both as a standalone script and as an importable module.
"""

import requests
import json
import sys
import csv
import os
import time
from pathlib import Path

BASE_URL = "https://inspirews.skgeodesy.sk/geoserver/cp/ogc/features/v1"
BASE_URL_E = "https://inspirews.skgeodesy.sk/geoserver/cp_uo/ogc/features/v1"

class DownloadCallback:
    """Callback interface for progress updates"""
    def on_progress(self, message):
        """Called when progress message is available"""
        print(message)

    def on_error(self, message):
        """Called when error occurs"""
        print(f"ERROR: {message}")

def load_cadastre_codes(base_dir=None):
    """Load cadastre name -> code mapping from CSV"""
    cadastre_map = {}
    if base_dir is None:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    # CSV files are in ../data/ directory
    code_file = os.path.join(base_dir, '..', 'data', 'cadastre_code_name.csv')

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

def find_cadastre_code(query):
    """
    Find cadastre code by name or return the query if it's already a code

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

def fetch_parcels_by_cadastre_code(cadastre_code, limit=25000, output_dir=".", callback=None):
    """
    Fetch all parcels for a given cadastre code using CQL filtering

    Args:
        cadastre_code: 6-digit cadastre code (e.g., "815713")
        limit: Max features per request (default 25000, very large to avoid pagination bugs)
        output_dir: Directory to save error files (default current directory)
        callback: Optional DownloadCallback instance for progress updates

    Returns:
        GeoJSON FeatureCollection
    """
    if callback is None:
        callback = DownloadCallback()

    collection = "CP.CadastralParcel"
    url = f"{BASE_URL}/collections/{collection}/items"

    # The nationalCadastralReference format is: cadastre_code + "_" + parcel_number + ".C"
    # Use CQL filter for efficient server-side filtering
    cql_filter = f"nationalCadastralReference LIKE '{cadastre_code}_%'"

    all_features = []
    offset = 0

    callback.on_progress(f"Fetching parcels for cadastre code: {cadastre_code}")
    callback.on_progress(f"Using CQL filter: {cql_filter}")
    callback.on_progress(f"Batch size: {limit} (very large to get everything in one request, avoiding API pagination bugs)\n")

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
                error_file = os.path.join(output_dir, f"error_response_{cadastre_code}_offset{offset}.json")
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

    callback.on_progress(f"\n✓ Total parcels found: {len(all_features)}")
    return feature_collection

def fetch_cadastral_zoning_by_code(cadastre_code, limit=25000, output_dir=".", callback=None):
    """
    Fetch cadastral zoning (boundaries) for a given cadastre code using CQL filtering

    Args:
        cadastre_code: 6-digit cadastre code (e.g., "815713")
        limit: Max features per request (default 25000)
        output_dir: Directory to save error files
        callback: Optional DownloadCallback instance for progress updates

    Returns:
        GeoJSON FeatureCollection
    """
    if callback is None:
        callback = DownloadCallback()

    collection = "CP.CadastralZoning"
    url = f"{BASE_URL}/collections/{collection}/items"

    # The nationalCadastalZoningReference uses the 6-digit code
    # Note: API has typo "Cadastal" instead of "Cadastral"
    cql_filter = f"nationalCadastalZoningReference = '{cadastre_code}'"

    all_features = []
    offset = 0

    callback.on_progress(f"Fetching cadastral zoning for code: {cadastre_code}")
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

            try:
                data = response.json()
            except json.JSONDecodeError as e:
                callback.on_error(f"JSON Error at offset {offset}: {str(e)}")
                error_file = os.path.join(output_dir, f"error_response_zoning_{cadastre_code}_offset{offset}.json")
                try:
                    with open(error_file, 'w', encoding='utf-8') as f:
                        f.write(response.text)
                    callback.on_progress(f"  Saved corrupted response to: {error_file}")
                except Exception as save_error:
                    callback.on_progress(f"  Could not save error file: {save_error}")

                offset += limit
                if offset > 10000:
                    callback.on_progress(f"  Too many errors, stopping")
                    break
                continue

            features = data.get('features', [])

            if not features:
                callback.on_progress("  no more features")
                break

            all_features.extend(features)
            callback.on_progress(f"  got {len(features)} features")

            if len(features) < limit:
                break

            offset += limit

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

    callback.on_progress(f"\n✓ Total zoning features found: {len(all_features)}")
    return feature_collection

def fetch_parcel_e_by_cadastre_code(cadastre_code, limit=25000, output_dir=".", callback=None):
    """
    Fetch E register parcels for a given cadastre code using CQL filtering

    Args:
        cadastre_code: 6-digit cadastre code (e.g., "815713")
        limit: Max features per request (default 25000)
        output_dir: Directory to save error files
        callback: Optional DownloadCallback instance for progress updates

    Returns:
        GeoJSON FeatureCollection
    """
    if callback is None:
        callback = DownloadCallback()

    collection = "CP.CadastralParcelUO"
    url = f"{BASE_URL_E}/collections/{collection}/items"

    # The nationalCadastralReference format is: cadastre_code + "_" + parcel_number + ".E"
    # Use CQL filter for efficient server-side filtering
    cql_filter = f"nationalCadastralReference LIKE '{cadastre_code}_%'"

    all_features = []
    offset = 0

    callback.on_progress(f"Fetching E register parcels for cadastre code: {cadastre_code}")
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

            try:
                data = response.json()
            except json.JSONDecodeError as e:
                callback.on_error(f"JSON Error at offset {offset}: {str(e)}")
                error_file = os.path.join(output_dir, f"error_response_parcel_e_{cadastre_code}_offset{offset}.json")
                try:
                    with open(error_file, 'w', encoding='utf-8') as f:
                        f.write(response.text)
                    callback.on_progress(f"  Saved corrupted response to: {error_file}")
                except Exception as save_error:
                    callback.on_progress(f"  Could not save error file: {save_error}")

                offset += limit
                if offset > 10000:
                    callback.on_progress(f"  Too many errors, stopping")
                    break
                continue

            features = data.get('features', [])

            if not features:
                callback.on_progress("  no more features")
                break

            all_features.extend(features)
            callback.on_progress(f"  got {len(features)} features")

            if len(features) < limit:
                break

            offset += limit

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

    callback.on_progress(f"\n✓ Total E register parcels found: {len(all_features)}")
    return feature_collection

def save_to_geojson(feature_collection, output_file):
    """Save FeatureCollection to GeoJSON file"""
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(feature_collection, f, indent=2, ensure_ascii=False)
    print(f"✓ Saved to GeoJSON: {output_file}")

def main():
    if len(sys.argv) < 2:
        print("Usage: python download_cadastre.py <cadastre_name_or_code> [output_name] [output_dir] [--parcel-c] [--parcel-e] [--zoning]")
        print("Examples:")
        print("  python download_cadastre.py nitra")
        print("  python download_cadastre.py 815713")
        print("  python download_cadastre.py nitra nitra_data /path/to/output --parcel-c --parcel-e --zoning")
        sys.exit(1)

    # Parse arguments
    query = sys.argv[1]
    output_name = None
    output_dir = "."
    fetch_parcel_c = True  # Default to C register for backwards compatibility
    fetch_parcel_e = False
    fetch_zoning = False

    # Parse positional and flag arguments
    positional_args = []
    for arg in sys.argv[2:]:
        if arg == '--parcel-c' or arg == '--parcels':  # Support old --parcels flag
            fetch_parcel_c = True
        elif arg == '--parcel-e':
            fetch_parcel_e = True
        elif arg == '--zoning':
            fetch_zoning = True
        else:
            positional_args.append(arg)

    # If any flags are specified, reset defaults and use only what's specified
    if '--parcel-c' in sys.argv or '--parcels' in sys.argv or '--parcel-e' in sys.argv or '--zoning' in sys.argv:
        # User explicitly specified layers, reset defaults
        fetch_parcel_c = '--parcel-c' in sys.argv or '--parcels' in sys.argv
        fetch_parcel_e = '--parcel-e' in sys.argv
        fetch_zoning = '--zoning' in sys.argv

    if positional_args:
        output_name = positional_args[0]
    if len(positional_args) > 1:
        output_dir = positional_args[1]

    # Find cadastre code from name or validate if it's already a code
    cadastre_code, cadastre_name = find_cadastre_code(query)

    if not cadastre_code:
        print(f"✗ Error: Could not find cadastre for query: '{query}'")
        print("  Try using the full name or 6-digit cadastre code")
        sys.exit(1)

    # Set output name base
    if not output_name:
        output_name = f"cadastre_{cadastre_name.lower() if cadastre_name else cadastre_code}"

    # Ensure output directory exists
    if output_dir != ".":
        os.makedirs(output_dir, exist_ok=True)

    print(f"\n{'='*60}")
    if cadastre_name:
        print(f"Cadastre: {cadastre_name.title()} (code: {cadastre_code})")
    else:
        print(f"Cadastre code: {cadastre_code}")
    print(f"Output directory: {os.path.abspath(output_dir)}")

    layers_text = []
    if fetch_parcel_c:
        layers_text.append("Parcel C")
    if fetch_parcel_e:
        layers_text.append("Parcel E")
    if fetch_zoning:
        layers_text.append("Zoning")
    print(f"Layers to fetch: {', '.join(layers_text)}")
    print(f"{'='*60}\n")

    layers_fetched = []

    # Fetch C register parcels if requested
    if fetch_parcel_c:
        parcel_c_collection = fetch_parcels_by_cadastre_code(cadastre_code, output_dir=output_dir)

        if parcel_c_collection['features']:
            print(f"\nSaving C register parcel data...")
            geojson_file = os.path.join(output_dir, f"{output_name}_parcel_c.geojson")
            save_to_geojson(parcel_c_collection, geojson_file)
            layers_fetched.append(geojson_file)
        else:
            print("\n⚠ No C register parcels found (may be API issue or empty cadastre)")

    # Fetch E register parcels if requested
    if fetch_parcel_e:
        if fetch_parcel_c:
            print("\n")  # Add spacing between layers
        parcel_e_collection = fetch_parcel_e_by_cadastre_code(cadastre_code, output_dir=output_dir)

        if parcel_e_collection['features']:
            print(f"\nSaving E register parcel data...")
            geojson_file = os.path.join(output_dir, f"{output_name}_parcel_e.geojson")
            save_to_geojson(parcel_e_collection, geojson_file)
            layers_fetched.append(geojson_file)
        else:
            print("\n⚠ No E register parcels found (may be API issue or empty cadastre)")

    # Fetch zoning if requested
    if fetch_zoning:
        if fetch_parcel_c or fetch_parcel_e:
            print("\n")  # Add spacing
        zoning_collection = fetch_cadastral_zoning_by_code(cadastre_code, output_dir=output_dir)

        if zoning_collection['features']:
            print(f"\nSaving zoning data...")
            geojson_file = os.path.join(output_dir, f"{output_name}_zoning.geojson")
            save_to_geojson(zoning_collection, geojson_file)
            layers_fetched.append(geojson_file)
        else:
            print("\n⚠ No zoning features found (may be API issue or empty cadastre)")

    print(f"\n{'='*60}")
    if layers_fetched:
        print(f"Done! Fetched {len(layers_fetched)} layer(s)")
        for layer_file in layers_fetched:
            print(f"  - {os.path.basename(layer_file)}")
    else:
        print("Done (no data fetched)")
    print(f"{'='*60}")

if __name__ == "__main__":
    main()
