# -*- coding: utf-8 -*-
"""
Generic feature fetching from Slovak cadastre API with retry logic.

The Slovak cadastre API (inspirews.skgeodesy.sk) is experimental and has two known issues:
  1. Server crashes mid-stream after ~680 KB of response (java.lang.ArrayIndexOutOfBoundsException),
     returning truncated JSON. The _salvage_partial_geojson function recovers the complete
     features from such responses.
  2. For large cadastral units, requests with a high `offset` value time out because the server
     must scan the entire result set to reach that offset. This is solved by spatial tiling:
     instead of paginating one large query, the cadastre bbox is split into a grid and each
     tile is fetched independently starting at offset=0.
"""

import requests
import json
import time
import os


_MAX_ERROR_FILES_PER_RUN = 3

# Spatial grid used when fetching parcels. 4×4 = 16 tiles means each tile covers
# ~1/16 of the cadastre area and starts at offset=0, keeping offsets small.
_TILE_GRID_SIZE = 4


class DownloadCallback:
    """Callback interface for progress updates."""

    def on_progress(self, message):
        print(message)

    def on_error(self, message):
        print(f"ERROR: {message}")

    def on_batch_complete(self, total_features):
        """Called after each successful batch with the running feature total."""
        pass

    def on_status(self, message):
        """Called with short status messages suitable for a progress label."""
        pass


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _salvage_partial_geojson(text):
    """
    Try to extract complete features from a truncated/corrupted GeoJSON FeatureCollection.

    The Slovak cadastre API sometimes appends a server error JSON object to a
    mid-stream truncated response, e.g.:
        ...partial_feature_data...{"type":"NoApplicableCode","title":"java.lang..."}

    Returns a GeoJSON FeatureCollection with the recoverable features, or None.
    """
    error_suffix = '{"type":"NoApplicableCode","title":"java.lang.ArrayIndexOutOfBoundsException"}'
    if text.endswith(error_suffix):
        text = text[:-len(error_suffix)]

    marker = '},{"type":"Feature"'
    last_boundary = text.rfind(marker)
    if last_boundary == -1:
        return None

    truncated = text[:last_boundary + 1] + ']}'
    try:
        return json.loads(truncated)
    except json.JSONDecodeError:
        return None


def _bbox_from_geometry(geom):
    """Return [minx, miny, maxx, maxy] from a GeoJSON geometry, or None."""
    if not geom:
        return None
    coords = []

    def collect(g):
        t = g.get('type', '')
        c = g.get('coordinates')
        if not c:
            return
        if t == 'Point':
            coords.append(c)
        elif t in ('LineString', 'MultiPoint'):
            coords.extend(c)
        elif t in ('Polygon', 'MultiLineString'):
            for ring in c:
                coords.extend(ring)
        elif t == 'MultiPolygon':
            for poly in c:
                for ring in poly:
                    coords.extend(ring)
        elif t == 'GeometryCollection':
            for sub in g.get('geometries', []):
                collect(sub)

    collect(geom)
    if not coords:
        return None
    xs = [p[0] for p in coords]
    ys = [p[1] for p in coords]
    return [min(xs), min(ys), max(xs), max(ys)]


def _get_cadastre_bbox(cadastre_code, callback):
    """
    Get the bounding box [minx, miny, maxx, maxy] of a cadastral unit by querying
    the CadastralZoning collection. Returns None if not available.
    """
    url = f"{BASE_URL}/collections/CP.CadastralZoning/items"
    params = {
        'limit': 10,
        'filter-lang': 'cql-text',
        'filter': f"nationalCadastalZoningReference = '{cadastre_code}'"
    }
    try:
        callback.on_progress(f"  Getting cadastre area from zoning API...")
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        bboxes = [_bbox_from_geometry(f.get('geometry')) for f in data.get('features', [])]
        bboxes = [b for b in bboxes if b]
        if bboxes:
            result = [
                min(b[0] for b in bboxes), min(b[1] for b in bboxes),
                max(b[2] for b in bboxes), max(b[3] for b in bboxes),
            ]
            callback.on_progress(
                f"  Cadastre bbox: lon {result[0]:.4f}–{result[2]:.4f}, "
                f"lat {result[1]:.4f}–{result[3]:.4f}"
            )
            return result
        callback.on_progress(f"  No zoning data found for cadastre {cadastre_code}")
    except Exception as e:
        callback.on_progress(f"  Could not get cadastre bbox: {e}")
    return None


def _fetch_one_tile(base_url, collection, cql_filter, bbox_str, limit,
                    seen_ids, all_features, error_files_saved, output_dir,
                    cadastre_code, layer_type_name, callback):
    """
    Fetch all features within one spatial tile (defined by bbox_str), starting at offset=0.
    Updates seen_ids and all_features in place.
    Returns updated error_files_saved count.
    """
    url = f"{base_url}/collections/{collection}/items"
    offset = 0
    tile_limit = limit

    while True:
        params = {
            'limit': tile_limit,
            'offset': offset,
            'filter-lang': 'cql-text',
            'filter': cql_filter,
            'bbox': bbox_str,
        }

        try:
            response = requests.get(url, params=params, timeout=60)
            response.raise_for_status()
        except requests.exceptions.Timeout:
            tile_limit = max(100, tile_limit // 5)
            callback.on_progress(f"    Timeout — reduced tile batch to {tile_limit}")
            time.sleep(3)
            continue
        except requests.exceptions.RequestException as e:
            callback.on_error(f"    Tile request error: {e}")
            break

        try:
            data = response.json()
        except json.JSONDecodeError:
            # Server crash mid-stream: try to salvage and shrink batch
            if error_files_saved < _MAX_ERROR_FILES_PER_RUN:
                path = os.path.join(
                    output_dir,
                    f"error_response_{layer_type_name}_{cadastre_code}_offset{offset}.json"
                )
                try:
                    with open(path, 'w', encoding='utf-8') as f:
                        f.write(response.text)
                    error_files_saved += 1
                except Exception:
                    pass

            salvaged = _salvage_partial_geojson(response.text)
            if salvaged:
                partial = salvaged.get('features', [])
                if partial:
                    new = [f for f in partial if f.get('id') not in seen_ids]
                    seen_ids.update(f.get('id') for f in new)
                    all_features.extend(new)
                    callback.on_batch_complete(len(all_features))
                    offset += len(partial)
                    tile_limit = max(100, tile_limit // 5)
                    continue
            if tile_limit > 100:
                tile_limit = max(100, tile_limit // 5)
                continue
            offset += tile_limit
            continue

        features = data.get('features', [])
        if not features:
            break

        new = [f for f in features if f.get('id') not in seen_ids]
        seen_ids.update(f.get('id') for f in new)
        all_features.extend(new)
        callback.on_batch_complete(len(all_features))

        if len(features) < tile_limit:
            break

        offset += tile_limit

        if offset > 100_000:
            callback.on_progress(f"    Tile offset exceeded 100k, stopping this tile")
            break

    return error_files_saved


def _fetch_with_tiling(base_url, collection, cql_filter, cadastre_code, layer_type_name,
                       bbox, grid_size, limit, output_dir, callback):
    """
    Fetch all features by splitting the cadastre bbox into a grid_size × grid_size grid.
    Each tile is fetched from offset=0, completely avoiding large-offset timeouts.
    Returns a GeoJSON FeatureCollection.
    """
    min_lon, min_lat, max_lon, max_lat = bbox
    lon_step = (max_lon - min_lon) / grid_size
    lat_step = (max_lat - min_lat) / grid_size

    all_features = []
    seen_ids = set()
    error_files_saved = 0
    total_tiles = grid_size * grid_size

    callback.on_progress(
        f"Using {grid_size}×{grid_size} spatial grid ({total_tiles} tiles), "
        f"each starting at offset=0"
    )

    for i in range(grid_size):
        for j in range(grid_size):
            tile_num = i * grid_size + j + 1
            tile_bbox = [
                min_lon + i * lon_step,
                min_lat + j * lat_step,
                min_lon + (i + 1) * lon_step,
                min_lat + (j + 1) * lat_step,
            ]
            bbox_str = f"{tile_bbox[0]},{tile_bbox[1]},{tile_bbox[2]},{tile_bbox[3]}"

            callback.on_progress(f"\n  Tile {tile_num}/{total_tiles}")
            callback.on_status(f"Tile {tile_num}/{total_tiles} — {len(all_features):,} features so far")

            before = len(all_features)
            error_files_saved = _fetch_one_tile(
                base_url, collection, cql_filter, bbox_str, limit,
                seen_ids, all_features, error_files_saved, output_dir,
                cadastre_code, layer_type_name, callback
            )
            callback.on_progress(f"  Tile {tile_num}/{total_tiles}: +{len(all_features) - before} features")

    callback.on_progress(f"\n✓ Total {layer_type_name} found: {len(all_features)}")
    return {"type": "FeatureCollection", "features": all_features}


# ---------------------------------------------------------------------------
# Generic fallback (offset-based pagination, used when bbox is unavailable)
# ---------------------------------------------------------------------------

def fetch_features_generic(base_url, collection, cql_filter, cadastre_code, layer_type_name,
                           limit=25000, output_dir=".", callback=None):
    """
    Fetch features using offset-based pagination (fallback when bbox tiling is unavailable).
    For large cadastres the server may time out at high offsets; prefer the tiled functions.
    """
    if callback is None:
        callback = DownloadCallback()

    url = f"{base_url}/collections/{collection}/items"
    all_features = []
    seen_ids = set()
    offset = 0
    error_files_saved = 0
    consecutive_timeouts = 0

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

            try:
                data = response.json()
            except json.JSONDecodeError as e:
                callback.on_error(f"JSON Error at offset {offset}: {str(e)}")

                if error_files_saved < _MAX_ERROR_FILES_PER_RUN:
                    error_file = os.path.join(
                        output_dir,
                        f"error_response_{layer_type_name}_{cadastre_code}_offset{offset}.json"
                    )
                    try:
                        with open(error_file, 'w', encoding='utf-8') as f:
                            f.write(response.text)
                        callback.on_progress(f"  Saved error file: {os.path.basename(error_file)}")
                        error_files_saved += 1
                    except Exception as save_error:
                        callback.on_progress(f"  Could not save error file: {save_error}")
                else:
                    callback.on_progress(f"  (error file limit reached, not saving)")

                salvaged = _salvage_partial_geojson(response.text)
                if salvaged:
                    partial_features = salvaged.get('features', [])
                    if partial_features:
                        new = [f for f in partial_features if f.get('id') not in seen_ids]
                        seen_ids.update(f.get('id') for f in new)
                        all_features.extend(new)
                        callback.on_progress(f"  Salvaged {len(new)} features from partial response")
                        callback.on_batch_complete(len(all_features))
                        offset += len(partial_features)
                        limit = max(100, limit // 5)
                        callback.on_progress(f"  Retrying with smaller batch: limit={limit}, offset={offset}")
                        continue

                if limit > 100:
                    limit = max(100, limit // 5)
                    callback.on_progress(f"  No features salvaged, retrying with smaller batch: limit={limit}, offset={offset}")
                    continue

                callback.on_progress(f"  Skipping offset {offset} (server error at minimum batch size)")
                offset += limit
                continue

            features = data.get('features', [])

            if not features:
                callback.on_progress("  No more features")
                break

            new = [f for f in features if f.get('id') not in seen_ids]
            seen_ids.update(f.get('id') for f in new)
            all_features.extend(new)
            if len(new) < len(features):
                callback.on_progress(f"  Got {len(features)} features ({len(features) - len(new)} duplicates skipped)")
            else:
                callback.on_progress(f"  Got {len(features)} features")

            consecutive_timeouts = 0
            callback.on_batch_complete(len(all_features))

            if len(features) < limit:
                break

            offset += limit

            if offset > 5_000_000:
                callback.on_progress("  Warning: Hit safety limit of 5M features")
                break

        except requests.exceptions.Timeout:
            consecutive_timeouts += 1
            callback.on_error(f"Timeout at offset {offset} (consecutive: {consecutive_timeouts})")

            if consecutive_timeouts >= 5:
                callback.on_error("Too many consecutive timeouts, stopping")
                break

            limit = max(100, limit // 5)
            callback.on_progress(f"  Reduced batch to {limit}, retrying offset {offset}")
            callback.on_status(f"Timeout — reduced to {limit} features/batch, retrying...")
            time.sleep(3)
            continue

        except requests.exceptions.RequestException as e:
            callback.on_error(f"Request error: {e}. Retrying...")
            callback.on_status("Connection error, retrying...")
            time.sleep(2)
            try:
                response = requests.get(url, params=params, timeout=90)
                response.raise_for_status()
                data = response.json()
                features = data.get('features', [])
                new = [f for f in features if f.get('id') not in seen_ids]
                seen_ids.update(f.get('id') for f in new)
                all_features.extend(new)
                callback.on_progress(f"  Retry successful! Got {len(new)} features")
                consecutive_timeouts = 0
                callback.on_batch_complete(len(all_features))
                if len(features) < limit:
                    break
                offset += limit
            except Exception as retry_error:
                callback.on_error(f"Retry failed: {retry_error}")
                if limit > 100:
                    limit = max(100, limit // 5)
                    callback.on_progress(f"  Reduced batch to {limit}, continuing")
                    callback.on_status(f"Error — reduced to {limit} features/batch...")
                    continue
                break

    callback.on_progress(f"\n✓ Total {layer_type_name} found: {len(all_features)}")
    return {"type": "FeatureCollection", "features": all_features}


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

BASE_URL = "https://inspirews.skgeodesy.sk/geoserver/cp/ogc/features/v1"
BASE_URL_E = "https://inspirews.skgeodesy.sk/geoserver/cp_uo/ogc/features/v1"


def fetch_parcels_by_cadastre_code(cadastre_code, limit=25000, output_dir=".", callback=None):
    """
    Fetch all C register parcels for a given cadastre code.

    Uses spatial bbox tiling (4×4 grid) so that each tile starts at offset=0,
    avoiding the server timeouts that occur at large offsets for big cadastral units.
    Falls back to offset-based pagination if the cadastre bbox cannot be determined.
    """
    if callback is None:
        callback = DownloadCallback()

    callback.on_progress(f"Fetching C register parcels for cadastre: {cadastre_code}")

    cql_filter = f"nationalCadastralReference LIKE '{cadastre_code}_%'"
    bbox = _get_cadastre_bbox(cadastre_code, callback)

    if bbox:
        return _fetch_with_tiling(
            BASE_URL, "CP.CadastralParcel", cql_filter, cadastre_code,
            "C register parcels", bbox, _TILE_GRID_SIZE, limit, output_dir, callback
        )

    callback.on_progress("  Bbox unavailable, falling back to offset-based pagination")
    return fetch_features_generic(
        BASE_URL, "CP.CadastralParcel", cql_filter, cadastre_code,
        "C register parcels", limit, output_dir, callback
    )


def fetch_parcel_e_by_cadastre_code(cadastre_code, limit=25000, output_dir=".", callback=None):
    """
    Fetch E register parcels for a given cadastre code.

    Uses spatial bbox tiling (4×4 grid) — same strategy as C register parcels.
    Falls back to offset-based pagination if the cadastre bbox cannot be determined.
    """
    if callback is None:
        callback = DownloadCallback()

    callback.on_progress(f"Fetching E register parcels for cadastre: {cadastre_code}")

    cql_filter = f"nationalCadastralReference LIKE '{cadastre_code}_%'"
    bbox = _get_cadastre_bbox(cadastre_code, callback)

    if bbox:
        return _fetch_with_tiling(
            BASE_URL_E, "CP.CadastralParcelUO", cql_filter, cadastre_code,
            "E register parcels", bbox, _TILE_GRID_SIZE, limit, output_dir, callback
        )

    callback.on_progress("  Bbox unavailable, falling back to offset-based pagination")
    return fetch_features_generic(
        BASE_URL_E, "CP.CadastralParcelUO", cql_filter, cadastre_code,
        "E register parcels", limit, output_dir, callback
    )


def fetch_cadastral_zoning_by_code(cadastre_code, limit=25000, output_dir=".", callback=None):
    """
    Fetch cadastral zoning (boundaries) for a given cadastre code.

    Zoning has at most a few features per cadastre so offset-based pagination is fine.
    """
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
