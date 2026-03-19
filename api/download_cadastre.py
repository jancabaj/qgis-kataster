#!/usr/bin/env python3
"""
Download cadastre parcels by cadastre code or name and save to GeoJSON

This module can be used both as a standalone script and as an importable module.
Internally, this now delegates to the refactored hierarchy and fetcher modules.
"""

import json
import sys
import os

# Import from new modular structure for backward compatibility
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
    fetch_cadastral_zoning_by_code,
    fetch_parcel_e_by_cadastre_code,
)


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
