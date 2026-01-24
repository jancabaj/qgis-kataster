# -*- coding: utf-8 -*-
"""
Worker thread classes for the Kataster plugin.
"""

import json
from osgeo import gdal
from qgis.PyQt.QtCore import QThread, pyqtSignal

from .api import download_cadastre


class DownloadWorker(QThread):
    """Worker thread for downloading cadastre data"""

    progress = pyqtSignal(str)
    error = pyqtSignal(str)
    finished = pyqtSignal(list)

    def __init__(self, query, output_name, fetch_parcel_c, fetch_parcel_e, fetch_zoning):
        super().__init__()
        self.query = query
        self.output_name = output_name
        self.fetch_parcel_c = fetch_parcel_c
        self.fetch_parcel_e = fetch_parcel_e
        self.fetch_zoning = fetch_zoning
        self._is_killed = False

    def _fetch_layer(self, cadastre_code, layer_type, fetch_func, callback):
        """
        Fetch a single layer type and store in VSIMEM (virtual memory).

        Returns the /vsimem/ path if successful, None otherwise.
        """
        layer_names = {
            'parcel_c': 'C register parcel',
            'parcel_e': 'E register parcel',
            'zoning': 'zoning'
        }

        collection = fetch_func(cadastre_code, callback=callback)

        if collection['features']:
            self.progress.emit(f"\nPreparing {layer_names[layer_type]} data...")
            vsimem_path = f"/vsimem/{self.output_name}_{layer_type}.geojson"
            geojson_bytes = json.dumps(collection, ensure_ascii=False).encode('utf-8')
            gdal.FileFromMemBuffer(vsimem_path, geojson_bytes)
            self.progress.emit(f"✓ {len(collection['features'])} features ready")
            return vsimem_path
        else:
            self.progress.emit(f"\n⚠ No {layer_names[layer_type]} data found")
            return None

    def run(self):
        """Run the download in a separate thread"""
        try:
            callback = DownloadCallbackQt(self.progress, self.error)
            cadastre_code, cadastre_name = download_cadastre.find_cadastre_code(self.query)

            if not cadastre_code:
                self.error.emit(f"Could not find cadastre for query: '{self.query}'")
                self.finished.emit([])
                return

            # Log header
            self.progress.emit(f"\n{'='*60}")
            if cadastre_name:
                self.progress.emit(f"Cadastre: {cadastre_name.title()} (code: {cadastre_code})")
            else:
                self.progress.emit(f"Cadastre code: {cadastre_code}")

            layers_to_fetch = []
            if self.fetch_parcel_c:
                layers_to_fetch.append("Parcel C")
            if self.fetch_parcel_e:
                layers_to_fetch.append("Parcel E")
            if self.fetch_zoning:
                layers_to_fetch.append("Zoning")
            self.progress.emit(f"Layers to fetch: {', '.join(layers_to_fetch)}")
            self.progress.emit(f"{'='*60}\n")

            # Fetch each requested layer
            vsimem_files = []
            layer_config = [
                (self.fetch_parcel_c, 'parcel_c', download_cadastre.fetch_parcels_by_cadastre_code),
                (self.fetch_parcel_e, 'parcel_e', download_cadastre.fetch_parcel_e_by_cadastre_code),
                (self.fetch_zoning, 'zoning', download_cadastre.fetch_cadastral_zoning_by_code),
            ]

            for should_fetch, layer_type, fetch_func in layer_config:
                if should_fetch and not self._is_killed:
                    result = self._fetch_layer(cadastre_code, layer_type, fetch_func, callback)
                    if result:
                        vsimem_files.append(result)

            # Log summary
            self.progress.emit(f"\n{'='*60}")
            if vsimem_files:
                self.progress.emit(f"Downloaded {len(vsimem_files)} layer(s)")
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
