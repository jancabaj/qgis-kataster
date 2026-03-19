# -*- coding: utf-8 -*-
"""
GeoPackage utility functions for the Kataster plugin.
"""

import os
import sqlite3

from osgeo import gdal
from qgis.core import (
    QgsVectorLayer,
    QgsProject,
    QgsMessageLog,
    Qgis,
    QgsVectorFileWriter,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsGeometry,
    QgsPointXY
)

# QgsVectorFileWriter enum compatibility: PyQt5 (QGIS 3.x) vs PyQt6 (QGIS 4.x)
try:
    _VFW_NO_ERROR = QgsVectorFileWriter.WriterError.NoError
    _VFW_CREATE_OR_OVERWRITE_FILE = QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteFile
    _VFW_CREATE_OR_OVERWRITE_LAYER = QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteLayer
except AttributeError:
    _VFW_NO_ERROR = QgsVectorFileWriter.NoError
    _VFW_CREATE_OR_OVERWRITE_FILE = QgsVectorFileWriter.CreateOrOverwriteFile
    _VFW_CREATE_OR_OVERWRITE_LAYER = QgsVectorFileWriter.CreateOrOverwriteLayer

# Slovak diacritics mapping for safe filenames
_DIACRITIC_MAP = {
    'á': 'a', 'ä': 'a', 'č': 'c', 'ď': 'd', 'é': 'e', 'ě': 'e',
    'í': 'i', 'ĺ': 'l', 'ľ': 'l', 'ň': 'n', 'ó': 'o', 'ô': 'o',
    'ŕ': 'r', 'š': 's', 'ť': 't', 'ú': 'u', 'ů': 'u', 'ý': 'y', 'ž': 'z',
    'Á': 'A', 'Ä': 'A', 'Č': 'C', 'Ď': 'D', 'É': 'E', 'Ě': 'E',
    'Í': 'I', 'Ĺ': 'L', 'Ľ': 'L', 'Ň': 'N', 'Ó': 'O', 'Ô': 'O',
    'Ŕ': 'R', 'Š': 'S', 'Ť': 'T', 'Ú': 'U', 'Ů': 'U', 'Ý': 'Y', 'Ž': 'Z'
}

# Layer configuration: filename pattern -> (layer_name, qml_file)
_LAYER_CONFIG = {
    '_parcel_c': ('ParcelC', 'kn_parcelC.qml'),
    '_parcel_e': ('ParcelE', 'kn_parcelE.qml'),
    '_zoning': ('CadastralUnit', 'kn_cadastralunit.qml'),
}

# Official Slovak GKU transformation pipeline for EPSG:4326 -> EPSG:5514
_SK_TRANSFORM_PROJ = (
    "+proj=pipeline "
    "+step +proj=unitconvert +xy_in=deg +xy_out=rad "
    "+step +proj=push +v_3 "
    "+step +proj=cart +ellps=WGS84 "
    "+step +inv +proj=helmert +x=485.021 +y=169.465 +z=483.839 "
    "+rx=-7.786342 +ry=-4.397554 +rz=-4.102655 +s=0 +convention=coordinate_frame "
    "+step +inv +proj=cart +ellps=bessel "
    "+step +proj=pop +v_3 "
    "+step +proj=krovak +lat_0=49.5 +lon_0=24.8333333333333 "
    "+alpha=30.2881397527778 +k=0.9999 +x_0=0 +y_0=0 +ellps=bessel "
    "+step +inv +proj=krovak +lat_0=49.5 +lon_0=24.8333333333333 "
    "+alpha=30.2881397527778 +k=0.9999 +x_0=0 +y_0=0 +ellps=bessel "
    "+step +proj=hgridshift +grids=sk_gku_JTSK03_to_JTSK.tif "
    "+step +proj=krovak +lat_0=49.5 +lon_0=24.8333333333333 "
    "+alpha=30.2881397527778 +k=0.9999 +x_0=0 +y_0=0 +ellps=bessel"
)

# ESRI-compatible WKT for EPSG:5514 (ESRI Pro compatibility)
_ESRI_WKT_5514 = (
    'PROJCS["S-JTSK_Krovak_East_North",'
    'GEOGCS["GCS_S_JTSK",'
    'DATUM["D_S_JTSK",'
    'SPHEROID["Bessel_1841",6377397.155,299.1528128]],'
    'PRIMEM["Greenwich",0.0],'
    'UNIT["Degree",0.0174532925199433]],'
    'PROJECTION["Krovak"],'
    'PARAMETER["False_Easting",0.0],'
    'PARAMETER["False_Northing",0.0],'
    'PARAMETER["Pseudo_Standard_Parallel_1",78.5],'
    'PARAMETER["Scale_Factor",0.9999],'
    'PARAMETER["Azimuth",30.28813975277778],'
    'PARAMETER["Longitude_Of_Center",24.83333333333333],'
    'PARAMETER["Latitude_Of_Center",49.5],'
    'PARAMETER["X_Scale",-1.0],'
    'PARAMETER["Y_Scale",1.0],'
    'PARAMETER["XY_Plane_Rotation",90.0],'
    'UNIT["Meter",1.0],'
    'AUTHORITY["EPSG","5514"]]'
)


def remove_diacritics(text):
    """Remove diacritics from Slovak text for safe filenames."""
    for diacritic, replacement in _DIACRITIC_MAP.items():
        text = text.replace(diacritic, replacement)
    return text


def _swap_polygon_coords(polygon):
    """Swap X/Y coordinates in a polygon (list of rings)."""
    return [[QgsPointXY(pt.y(), pt.x()) for pt in ring] for ring in polygon]


def fix_swapped_coordinates(layer, layer_name):
    """
    Fix parcels with swapped coordinates (X and Y reversed).
    Returns a memory layer with fixed geometries.
    """
    source_crs = layer.crs()
    if not source_crs.isValid():
        source_crs = QgsCoordinateReferenceSystem('EPSG:4326')
        QgsMessageLog.logMessage(f"  ⚠ Source CRS not defined, assuming EPSG:4326", "Kataster", Qgis.Warning)

    geom_type_str = "MultiPolygon" if layer.geometryType() == 2 else "Polygon"
    memory_layer = QgsVectorLayer(f"{geom_type_str}?crs={source_crs.authid()}", "temp", "memory")
    memory_layer.dataProvider().addAttributes(layer.fields())
    memory_layer.updateFields()

    fixed_count = 0
    total_count = 0

    for feature in layer.getFeatures():
        total_count += 1
        geom = feature.geometry()

        new_feature = QgsFeature(memory_layer.fields())
        new_feature.setAttributes(feature.attributes())

        if not geom.isNull():
            bbox = geom.boundingBox()
            # Check for swapped coords: Slovak longitude ~17-22°, latitude ~47-49°
            if bbox.xMinimum() > 40 and bbox.yMaximum() < 40:
                fixed_count += 1
                if geom.isMultipart():
                    swapped = [_swap_polygon_coords(poly) for poly in geom.asMultiPolygon()]
                    geom = QgsGeometry.fromMultiPolygonXY(swapped)
                else:
                    geom = QgsGeometry.fromPolygonXY(_swap_polygon_coords(geom.asPolygon()))
            new_feature.setGeometry(geom)

        memory_layer.dataProvider().addFeature(new_feature)

    if fixed_count > 0:
        QgsMessageLog.logMessage(f"  ⚠ Fixed {fixed_count}/{total_count} parcels with swapped coordinates", "Kataster", Qgis.Warning)
    else:
        QgsMessageLog.logMessage(f"  ✓ All {total_count} parcels have correct coordinates", "Kataster", Qgis.Info)

    return memory_layer


def _setup_transform_context(transform_to_5514):
    """Set up CRS and transform context for GPKG conversion."""
    source_crs = QgsCoordinateReferenceSystem('EPSG:4326')
    transform_context = QgsProject.instance().transformContext()

    if transform_to_5514:
        target_crs = QgsCoordinateReferenceSystem('EPSG:5514')
        try:
            transform_context.addCoordinateOperation(source_crs, target_crs, _SK_TRANSFORM_PROJ)
            QgsMessageLog.logMessage("CRS transformation: EPSG:4326 → EPSG:5514 (SK GKU)", "Kataster", Qgis.Info)
        except Exception as e:
            QgsMessageLog.logMessage(f"Using default transformation: {e}", "Kataster", Qgis.Warning)
    else:
        target_crs = source_crs
        QgsMessageLog.logMessage("Keeping original CRS: EPSG:4326", "Kataster", Qgis.Info)

    return source_crs, target_crs, transform_context


def _build_layer_configs(vsimem_files):
    """Build layer configurations from /vsimem/ paths."""
    configs = []
    for vsimem_path in vsimem_files:
        filename = vsimem_path.split('/')[-1]  # Get filename from /vsimem/path
        for pattern, (layer_name, qml_file) in _LAYER_CONFIG.items():
            if pattern in filename:
                configs.append({
                    'vsimem_path': vsimem_path,
                    'layer_name': layer_name,
                    'qml': qml_file
                })
                break
    return configs


def _write_layer_to_gpkg(layer, output_gpkg, layer_name, target_crs, transform_context, transform_to_5514, is_first):
    """Write a single layer to GPKG."""
    save_options = QgsVectorFileWriter.SaveVectorOptions()
    save_options.driverName = 'GPKG'
    save_options.fileEncoding = 'UTF-8'
    save_options.layerName = layer_name
    save_options.layerOptions = ['SPATIAL_INDEX=YES']

    if transform_to_5514:
        save_options.ct = QgsCoordinateTransform(layer.crs(), target_crs, transform_context)

    if is_first:
        save_options.actionOnExistingFile = _VFW_CREATE_OR_OVERWRITE_FILE
    else:
        save_options.actionOnExistingFile = _VFW_CREATE_OR_OVERWRITE_LAYER

    error = QgsVectorFileWriter.writeAsVectorFormatV3(layer, output_gpkg, transform_context, save_options)

    if error[0] != _VFW_NO_ERROR:
        QgsMessageLog.logMessage(f"ERROR: Failed to write {layer_name}: {error[1]}", "Kataster", Qgis.Critical)
        return False

    QgsMessageLog.logMessage(f"  ✓ {layer_name} written to GPKG", "Kataster", Qgis.Success)
    return True


def apply_style_to_gpkg_layer(gpkg_path, layer_name, qml_file, plugin_dir):
    """Apply QML style to GPKG layer and set as default."""
    layer = None
    try:
        layer = QgsVectorLayer(f"{gpkg_path}|layername={layer_name}", layer_name, "ogr")
        if not layer.isValid():
            QgsMessageLog.logMessage(f"  Warning: Could not load {layer_name} for styling", "Kataster", Qgis.Warning)
            return

        style_path = os.path.join(plugin_dir, 'styles', qml_file)
        if not os.path.exists(style_path):
            QgsMessageLog.logMessage(f"  Warning: Style file not found: {qml_file}", "Kataster", Qgis.Warning)
            return

        msg, success = layer.loadNamedStyle(style_path)
        if not success:
            QgsMessageLog.logMessage(f"  Warning: Could not load style: {msg}", "Kataster", Qgis.Warning)
            return

        error_msg = layer.saveStyleToDatabase("", "", True, "")
        if error_msg:
            QgsMessageLog.logMessage(f"  Warning: Style save warning: {error_msg}", "Kataster", Qgis.Warning)
        else:
            QgsMessageLog.logMessage(f"  ✓ Style saved as default for {layer_name}", "Kataster", Qgis.Info)
    except Exception as e:
        QgsMessageLog.logMessage(f"  Warning: Style error: {e}", "Kataster", Qgis.Warning)
    finally:
        # Explicitly release layer to free file handles (important for Windows)
        if layer is not None:
            del layer


def update_gpkg_crs_for_ESRI(gpkg_path):
    """Update GPKG CRS definition with ESRI-compatible WKT for ESRI Pro."""
    try:
        conn = sqlite3.connect(gpkg_path)
        cursor = conn.cursor()
        cursor.execute('UPDATE gpkg_spatial_ref_sys SET definition = ? WHERE srs_id = 5514', (_ESRI_WKT_5514,))

        if cursor.rowcount > 0:
            conn.commit()
            QgsMessageLog.logMessage("  ✓ Updated CRS for ESRI Pro compatibility", "Kataster", Qgis.Info)
        else:
            QgsMessageLog.logMessage("  Warning: EPSG:5514 not found in GPKG CRS table", "Kataster", Qgis.Warning)
        conn.close()
    except Exception as e:
        QgsMessageLog.logMessage(f"  Warning: Could not update CRS for ESRI: {e}", "Kataster", Qgis.Warning)


def _delete_cadastre_features(gpkg_path, cadastre_codes):
    """
    Delete existing features for the given cadastre codes from all layers.

    This prevents duplicates when re-downloading the same cadastre.
    """
    try:
        conn = sqlite3.connect(gpkg_path)
        cursor = conn.cursor()

        deleted_counts = {'ParcelC': 0, 'ParcelE': 0, 'CadastralUnit': 0}

        for code, name in cadastre_codes:
            # Delete from ParcelC (nationalCadastralReference LIKE 'code_%')
            try:
                cursor.execute("DELETE FROM ParcelC WHERE nationalCadastralReference LIKE ?", (f'{code}_%',))
                deleted_counts['ParcelC'] += cursor.rowcount
            except sqlite3.OperationalError:
                pass  # Table doesn't exist yet

            # Delete from ParcelE (nationalCadastralReference LIKE 'code_%')
            try:
                cursor.execute("DELETE FROM ParcelE WHERE nationalCadastralReference LIKE ?", (f'{code}_%',))
                deleted_counts['ParcelE'] += cursor.rowcount
            except sqlite3.OperationalError:
                pass  # Table doesn't exist yet

            # Delete from CadastralUnit (nationalCadastalZoningReference = 'code')
            # Note: API has typo "Cadastal" instead of "Cadastral"
            try:
                cursor.execute("DELETE FROM CadastralUnit WHERE nationalCadastalZoningReference = ?", (code,))
                deleted_counts['CadastralUnit'] += cursor.rowcount
            except sqlite3.OperationalError:
                pass  # Table doesn't exist yet

        conn.commit()
        conn.close()

        # Log deletions
        total_deleted = sum(deleted_counts.values())
        if total_deleted > 0:
            QgsMessageLog.logMessage(f"Removed existing features for re-download:", "Kataster", Qgis.Info)
            for layer_name, count in deleted_counts.items():
                if count > 0:
                    QgsMessageLog.logMessage(f"  - {layer_name}: {count} features", "Kataster", Qgis.Info)

        return True
    except Exception as e:
        QgsMessageLog.logMessage(f"Warning: Could not delete existing features: {e}", "Kataster", Qgis.Warning)
        return False


def _ensure_metadata_table(gpkg_path):
    """Create metadata table for tracking downloaded cadastres if it doesn't exist."""
    try:
        conn = sqlite3.connect(gpkg_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS _downloaded_cadastres (
                code TEXT PRIMARY KEY,
                name TEXT,
                downloaded_at TEXT,
                parcel_c_count INTEGER DEFAULT 0,
                parcel_e_count INTEGER DEFAULT 0,
                zoning_count INTEGER DEFAULT 0
            )
        ''')
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        QgsMessageLog.logMessage(f"Warning: Could not create metadata table: {e}", "Kataster", Qgis.Warning)
        return False


def _update_metadata(gpkg_path, cadastre_codes, layer_counts):
    """Update metadata table with downloaded cadastres."""
    try:
        from datetime import datetime
        conn = sqlite3.connect(gpkg_path)
        cursor = conn.cursor()

        for code, name in cadastre_codes:
            cursor.execute('''
                INSERT OR REPLACE INTO _downloaded_cadastres
                (code, name, downloaded_at, parcel_c_count, parcel_e_count, zoning_count)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                code,
                name or '',
                datetime.now().isoformat(),
                layer_counts.get('parcel_c', {}).get(code, 0),
                layer_counts.get('parcel_e', {}).get(code, 0),
                layer_counts.get('zoning', {}).get(code, 0)
            ))

        conn.commit()
        conn.close()
        return True
    except Exception as e:
        QgsMessageLog.logMessage(f"Warning: Could not update metadata: {e}", "Kataster", Qgis.Warning)
        return False


def get_gpkg_cadastre_summary(gpkg_path):
    """Get summary of cadastres in a GPKG file."""
    try:
        if not os.path.exists(gpkg_path):
            return None

        conn = sqlite3.connect(gpkg_path)
        cursor = conn.cursor()

        # Check if metadata table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='_downloaded_cadastres'")
        if not cursor.fetchone():
            conn.close()
            return None

        cursor.execute('SELECT COUNT(*) FROM _downloaded_cadastres')
        cadastre_count = cursor.fetchone()[0]

        cursor.execute('SELECT SUM(parcel_c_count + parcel_e_count + zoning_count) FROM _downloaded_cadastres')
        total_features = cursor.fetchone()[0] or 0

        conn.close()
        return {'cadastres': cadastre_count, 'features': total_features}
    except Exception:
        return None


def get_gpkg_cadastre_list(gpkg_path):
    """Get list of cadastres in a GPKG file for filtering.

    Returns:
        List of (code, name) tuples sorted by name, or empty list if not available.
    """
    try:
        if not os.path.exists(gpkg_path):
            return []

        conn = sqlite3.connect(gpkg_path)
        cursor = conn.cursor()

        # Check if metadata table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='_downloaded_cadastres'")
        if not cursor.fetchone():
            conn.close()
            return []

        cursor.execute('SELECT code, name FROM _downloaded_cadastres ORDER BY name')
        results = cursor.fetchall()
        conn.close()

        return [(row[0], row[1]) for row in results]
    except Exception:
        return []


def _append_layer_to_gpkg(layer, gpkg_path, layer_name, target_crs, transform_context, transform_to_5514):
    """Append features from a layer to an existing GPKG layer (or create if doesn't exist)."""
    save_options = QgsVectorFileWriter.SaveVectorOptions()
    save_options.driverName = 'GPKG'
    save_options.fileEncoding = 'UTF-8'
    save_options.layerName = layer_name
    save_options.layerOptions = ['SPATIAL_INDEX=YES']

    if transform_to_5514:
        save_options.ct = QgsCoordinateTransform(layer.crs(), target_crs, transform_context)

    # Check if GPKG exists
    if os.path.exists(gpkg_path):
        # Check if layer exists in GPKG
        ds = gdal.OpenEx(gpkg_path, gdal.OF_VECTOR)
        layer_exists = False
        if ds:
            for i in range(ds.GetLayerCount()):
                if ds.GetLayerByIndex(i).GetName() == layer_name:
                    layer_exists = True
                    break
            ds = None

        if layer_exists:
            # Append to existing layer
            save_options.actionOnExistingFile = QgsVectorFileWriter.AppendToLayerNoNewFields
        else:
            # Create new layer in existing GPKG
            save_options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer
    else:
        # Create new GPKG
        save_options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile

    error = QgsVectorFileWriter.writeAsVectorFormatV3(layer, gpkg_path, transform_context, save_options)

    if error[0] != QgsVectorFileWriter.NoError:
        QgsMessageLog.logMessage(f"ERROR: Failed to append {layer_name}: {error[1]}", "Kataster", Qgis.Critical)
        return False

    return True


def append_to_gpkg(vsimem_files, output_gpkg, cadastre_codes, plugin_dir, transform_to_5514=False):
    """
    Append features to an existing GPKG, removing duplicates first.

    Args:
        vsimem_files: List of /vsimem/ paths with GeoJSON data
        output_gpkg: Path to the GPKG file (will be created if doesn't exist)
        cadastre_codes: List of (code, name) tuples being downloaded
        plugin_dir: Plugin directory for styles
        transform_to_5514: Whether to transform to EPSG:5514
    """
    try:
        gpkg_exists = os.path.exists(output_gpkg)

        # Delete existing features for these cadastres (if GPKG exists)
        if gpkg_exists:
            _delete_cadastre_features(output_gpkg, cadastre_codes)

        # Set up CRS transformation
        source_crs, target_crs, transform_context = _setup_transform_context(transform_to_5514)

        # Build layer configs
        layer_configs = _build_layer_configs(vsimem_files)
        if not layer_configs:
            QgsMessageLog.logMessage("No valid data to append", "Kataster", Qgis.Warning)
            return False

        # Track feature counts per cadastre per layer (for metadata)
        layer_counts = {'parcel_c': {}, 'parcel_e': {}, 'zoning': {}}

        # Process each layer
        is_first_layer = not gpkg_exists
        for config in layer_configs:
            QgsMessageLog.logMessage(f"Appending {config['layer_name']}...", "Kataster", Qgis.Info)

            layer = QgsVectorLayer(config['vsimem_path'], config['layer_name'], "ogr")
            if not layer.isValid():
                QgsMessageLog.logMessage(f"ERROR: Could not load {config['vsimem_path']}", "Kataster", Qgis.Critical)
                continue

            feature_count = layer.featureCount()
            QgsMessageLog.logMessage(f"  Loaded {feature_count} features", "Kataster", Qgis.Info)

            # Fix swapped coordinates for parcel layers
            if config['layer_name'] in ['ParcelC', 'ParcelE']:
                layer = fix_swapped_coordinates(layer, config['layer_name'])

            # For first layer on new GPKG, use create mode
            if is_first_layer:
                if not _write_layer_to_gpkg(layer, output_gpkg, config['layer_name'],
                                            target_crs, transform_context, transform_to_5514, True):
                    return False
                is_first_layer = False
            else:
                if not _append_layer_to_gpkg(layer, output_gpkg, config['layer_name'],
                                             target_crs, transform_context, transform_to_5514):
                    return False

            QgsMessageLog.logMessage(f"  ✓ {config['layer_name']} appended ({feature_count} features)", "Kataster", Qgis.Success)

            # Apply style (will create default style if not exists)
            apply_style_to_gpkg_layer(output_gpkg, config['layer_name'], config['qml'], plugin_dir)

            # Track counts per cadastre (approximate - divide evenly if multiple cadastres)
            layer_type = [k for k, v in _LAYER_CONFIG.items() if v[0] == config['layer_name']][0].strip('_')
            per_cadastre = feature_count // len(cadastre_codes) if cadastre_codes else 0
            for code, _ in cadastre_codes:
                layer_counts[layer_type][code] = per_cadastre

        # Update CRS for ESRI compatibility
        if transform_to_5514:
            update_gpkg_crs_for_ESRI(output_gpkg)

        # Update metadata table
        _ensure_metadata_table(output_gpkg)
        _update_metadata(output_gpkg, cadastre_codes, layer_counts)

        return True

    except Exception as e:
        import traceback
        QgsMessageLog.logMessage(f"ERROR during GPKG append: {traceback.format_exc()}", "Kataster", Qgis.Critical)
        return False
    finally:
        # Clean up /vsimem/ files
        for vsimem_path in vsimem_files:
            gdal.Unlink(vsimem_path)


def convert_vsimem_to_gpkg(vsimem_files, output_gpkg, plugin_dir, transform_to_5514=False):
    """Convert /vsimem/ GeoJSON data to a single GeoPackage with multiple layers."""
    try:
        # Remove existing GPKG
        if os.path.exists(output_gpkg):
            os.remove(output_gpkg)
            QgsMessageLog.logMessage("Removed existing GPKG file", "Kataster", Qgis.Info)

        # Set up CRS transformation
        source_crs, target_crs, transform_context = _setup_transform_context(transform_to_5514)

        # Build layer configs
        layer_configs = _build_layer_configs(vsimem_files)
        if not layer_configs:
            QgsMessageLog.logMessage("No valid data to convert", "Kataster", Qgis.Warning)
            return False

        # Convert each layer
        for idx, config in enumerate(layer_configs):
            QgsMessageLog.logMessage(f"Converting {config['layer_name']}...", "Kataster", Qgis.Info)

            layer = QgsVectorLayer(config['vsimem_path'], config['layer_name'], "ogr")
            if not layer.isValid():
                QgsMessageLog.logMessage(f"ERROR: Could not load {config['vsimem_path']}", "Kataster", Qgis.Critical)
                continue

            QgsMessageLog.logMessage(f"  Loaded {layer.featureCount()} features", "Kataster", Qgis.Info)

            # Fix swapped coordinates for parcel layers
            if config['layer_name'] in ['ParcelC', 'ParcelE']:
                layer = fix_swapped_coordinates(layer, config['layer_name'])

            # Write to GPKG
            if not _write_layer_to_gpkg(layer, output_gpkg, config['layer_name'],
                                        target_crs, transform_context, transform_to_5514, idx == 0):
                return False

            # Apply style
            apply_style_to_gpkg_layer(output_gpkg, config['layer_name'], config['qml'], plugin_dir)

        # Update CRS for ESRI compatibility
        if transform_to_5514:
            update_gpkg_crs_for_ESRI(output_gpkg)

        return True

    except Exception as e:
        import traceback
        QgsMessageLog.logMessage(f"ERROR during GPKG conversion: {traceback.format_exc()}", "Kataster", Qgis.Critical)
        return False
    finally:
        # Clean up /vsimem/ files (always works, no file locking issues)
        for vsimem_path in vsimem_files:
            gdal.Unlink(vsimem_path)
