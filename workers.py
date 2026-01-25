# -*- coding: utf-8 -*-
"""
Worker thread classes for the Kataster plugin.
"""

import json
import os
from osgeo import gdal
from qgis.PyQt.QtCore import QThread, pyqtSignal

from .api import download_cadastre


class DownloadWorker(QThread):
    """Worker thread for downloading cadastre data"""

    progress = pyqtSignal(str)
    error = pyqtSignal(str)
    finished = pyqtSignal(list)
    # Signal for multi-cadastre progress: (current_index, total_count, cadastre_name)
    multi_progress = pyqtSignal(int, int, str)

    def __init__(self, cadastre_codes, output_name, fetch_parcel_c, fetch_parcel_e, fetch_zoning, plugin_dir=None):
        """
        Initialize the download worker.

        Args:
            cadastre_codes: list of (code, name) tuples for cadastral units to download
            output_name: base name for output files
            fetch_parcel_c: whether to fetch C register parcels
            fetch_parcel_e: whether to fetch E register parcels
            fetch_zoning: whether to fetch cadastral zoning
            plugin_dir: plugin directory for error logs
        """
        super().__init__()
        self.cadastre_codes = cadastre_codes  # List of (code, name) tuples
        self.output_name = output_name
        self.fetch_parcel_c = fetch_parcel_c
        self.fetch_parcel_e = fetch_parcel_e
        self.fetch_zoning = fetch_zoning
        self.plugin_dir = plugin_dir
        self._is_killed = False

    def _get_error_logs_dir(self):
        """Get or create the error_logs directory."""
        if self.plugin_dir:
            error_dir = os.path.join(self.plugin_dir, 'error_logs')
        else:
            error_dir = 'error_logs'

        if not os.path.exists(error_dir):
            try:
                os.makedirs(error_dir)
            except Exception:
                pass  # Fall back to current directory if creation fails
        return error_dir

    def _fetch_layer_for_cadastre(self, cadastre_code, layer_type, fetch_func, callback, error_logs_dir):
        """
        Fetch a single layer type for a single cadastre.

        Returns:
            list of features if successful, empty list otherwise.
        """
        collection = fetch_func(cadastre_code, output_dir=error_logs_dir, callback=callback)
        return collection.get('features', [])

    def _store_aggregated_layer(self, layer_type, all_features):
        """
        Store aggregated features in VSIMEM.

        Returns:
            /vsimem/ path if successful, None otherwise.
        """
        layer_names = {
            'parcel_c': 'C register parcel',
            'parcel_e': 'E register parcel',
            'zoning': 'zoning'
        }

        if all_features:
            self.progress.emit(f"\nPreparing {layer_names[layer_type]} data...")
            feature_collection = {
                "type": "FeatureCollection",
                "features": all_features
            }
            vsimem_path = f"/vsimem/{self.output_name}_{layer_type}.geojson"
            geojson_bytes = json.dumps(feature_collection, ensure_ascii=False).encode('utf-8')
            gdal.FileFromMemBuffer(vsimem_path, geojson_bytes)
            self.progress.emit(f"✓ {len(all_features)} features ready")
            return vsimem_path
        else:
            self.progress.emit(f"\n⚠ No {layer_names[layer_type]} data found")
            return None

    def run(self):
        """Run the download in a separate thread"""
        try:
            callback = DownloadCallbackQt(self.progress, self.error)
            total_cadastres = len(self.cadastre_codes)
            error_logs_dir = self._get_error_logs_dir()

            # Log header
            self.progress.emit(f"\n{'='*60}")
            if total_cadastres == 1:
                code, name = self.cadastre_codes[0]
                if name:
                    self.progress.emit(f"Cadastre: {name} (code: {code})")
                else:
                    self.progress.emit(f"Cadastre code: {code}")
            else:
                self.progress.emit(f"Downloading {total_cadastres} cadastral units")

            layers_to_fetch = []
            if self.fetch_parcel_c:
                layers_to_fetch.append("Parcel C")
            if self.fetch_parcel_e:
                layers_to_fetch.append("Parcel E")
            if self.fetch_zoning:
                layers_to_fetch.append("Zoning")
            self.progress.emit(f"Layers to fetch: {', '.join(layers_to_fetch)}")
            self.progress.emit(f"{'='*60}\n")

            # Aggregated features for each layer type
            aggregated = {
                'parcel_c': [],
                'parcel_e': [],
                'zoning': []
            }

            # Track cadastres with 0 features per layer
            empty_cadastres = {
                'parcel_c': [],
                'parcel_e': [],
                'zoning': []
            }

            layer_config = [
                (self.fetch_parcel_c, 'parcel_c', download_cadastre.fetch_parcels_by_cadastre_code),
                (self.fetch_parcel_e, 'parcel_e', download_cadastre.fetch_parcel_e_by_cadastre_code),
                (self.fetch_zoning, 'zoning', download_cadastre.fetch_cadastral_zoning_by_code),
            ]

            # Fetch data for each cadastral unit
            for idx, (code, name) in enumerate(self.cadastre_codes, 1):
                if self._is_killed:
                    break

                display_name = f"{name} ({code})" if name else code
                self.progress.emit(f"\n--- Downloading {idx}/{total_cadastres}: {display_name} ---")
                self.multi_progress.emit(idx, total_cadastres, display_name)

                # Fetch each requested layer for this cadastre
                for should_fetch, layer_type, fetch_func in layer_config:
                    if should_fetch and not self._is_killed:
                        features = self._fetch_layer_for_cadastre(code, layer_type, fetch_func, callback, error_logs_dir)
                        if features:
                            aggregated[layer_type].extend(features)
                        else:
                            empty_cadastres[layer_type].append(display_name)

            # Store aggregated results in VSIMEM
            vsimem_files = []
            for should_fetch, layer_type, _ in layer_config:
                if should_fetch and not self._is_killed:
                    result = self._store_aggregated_layer(layer_type, aggregated[layer_type])
                    if result:
                        vsimem_files.append(result)

            # Log summary
            self.progress.emit(f"\n{'='*60}")
            if vsimem_files:
                self.progress.emit(f"Downloaded {len(vsimem_files)} layer(s) from {total_cadastres} cadastral unit(s)")

                # Report empty cadastres per layer
                layer_display_names = {
                    'parcel_c': 'Parcel C',
                    'parcel_e': 'Parcel E',
                    'zoning': 'Zoning'
                }
                for should_fetch, layer_type, _ in layer_config:
                    if should_fetch and empty_cadastres[layer_type]:
                        empty_count = len(empty_cadastres[layer_type])
                        self.progress.emit(f"\n{layer_display_names[layer_type]}: {empty_count} cadastre(s) returned 0 features:")
                        # List them (limit to first 10 if many)
                        if empty_count <= 10:
                            for name in empty_cadastres[layer_type]:
                                self.progress.emit(f"  - {name}")
                        else:
                            for name in empty_cadastres[layer_type][:10]:
                                self.progress.emit(f"  - {name}")
                            self.progress.emit(f"  ... and {empty_count - 10} more")
            else:
                self.progress.emit("No data downloaded")
            self.progress.emit(f"{'='*60}")

            self.finished.emit(vsimem_files)

        except Exception as e:
            import traceback
            self.error.emit(f"Download failed: {traceback.format_exc()}")
            self.finished.emit([])

    def kill(self):
        """Stop the download"""
        self._is_killed = True


class DownloadCallbackQt(download_cadastre.DownloadCallback):
    """Callback that emits Qt signals"""

    def __init__(self, progress_signal, error_signal):
        super().__init__()
        self.progress_signal = progress_signal
        self.error_signal = error_signal

    def on_progress(self, message):
        self.progress_signal.emit(message)

    def on_error(self, message):
        self.error_signal.emit(message)
