# -*- coding: utf-8 -*-
"""
Download management and coordination for the Kataster plugin.
"""

import os
from qgis.PyQt.QtWidgets import QMessageBox
from qgis.core import QgsProject, QgsMessageLog, Qgis, QgsVectorLayer

from ..api.hierarchy import find_cadastre_code, get_cadastre_codes_by_okres, get_cadastre_codes_by_kraj
from ..workers import DownloadWorker
from ..gpkg_utils import remove_diacritics, convert_vsimem_to_gpkg, append_to_gpkg
from ..kataster_dialog import katasterDialog

# Layer names used in GPKG
_GPKG_LAYER_NAMES = ['ParcelC', 'ParcelE', 'CadastralUnit']


class DownloadManager:
    """Manages download operations, validation, and worker coordination."""

    def __init__(self, plugin_dir, iface, dlg):
        """
        Initialize the download manager.

        Args:
            plugin_dir: Plugin directory path
            iface: QGIS interface
            dlg: Plugin dialog
        """
        self.plugin_dir = plugin_dir
        self.iface = iface
        self.dlg = dlg
        self.worker = None

        # State for current download
        self.current_query = None
        self.current_output_name = None
        self.current_cadastre_codes = None
        self.current_output_mode = None
        self.current_append_path = None

    def validate_inputs(self):
        """
        Validate inputs for load_parcels.

        Returns:
            tuple (cadastre_codes, output_info, layers, selection_info) or None if invalid.
            cadastre_codes: list of (code, name) tuples
            selection_info: dict with 'mode' and 'name' for display purposes
        """
        selection_mode = self.dlg.get_selection_mode()
        cadastre_codes = []
        selection_info = {}

        if selection_mode == katasterDialog.MODE_CADASTRE:
            query = self.dlg.cadastre_input.text().strip()
            if not query:
                QMessageBox.warning(self.dlg, "Input Required", "Please enter a cadastre name or code")
                return None
            # Find the cadastre code
            code, name = find_cadastre_code(query)
            if not code:
                QMessageBox.warning(self.dlg, "Not Found", f"Could not find cadastre for: '{query}'")
                return None
            cadastre_codes = [(code, name)]
            selection_info = {'mode': 'cadastre', 'name': name or code, 'query': query}

        elif selection_mode == katasterDialog.MODE_OKRES:
            okres_name = self.dlg.get_selected_okres()
            if not okres_name:
                QMessageBox.warning(self.dlg, "Input Required", "Please select an okres")
                return None
            cadastre_codes = get_cadastre_codes_by_okres(okres_name, os.path.join(self.plugin_dir, 'api'))
            if not cadastre_codes:
                QMessageBox.warning(self.dlg, "Not Found", f"No cadastral units found for okres: '{okres_name}'")
                return None
            selection_info = {'mode': 'okres', 'name': okres_name, 'count': len(cadastre_codes)}

        elif selection_mode == katasterDialog.MODE_KRAJ:
            kraj_name = self.dlg.get_selected_kraj()
            if not kraj_name:
                QMessageBox.warning(self.dlg, "Input Required", "Please select a kraj")
                return None
            cadastre_codes = get_cadastre_codes_by_kraj(kraj_name, os.path.join(self.plugin_dir, 'api'))
            if not cadastre_codes:
                QMessageBox.warning(self.dlg, "Not Found", f"No cadastral units found for kraj: '{kraj_name}'")
                return None
            selection_info = {'mode': 'kraj', 'name': kraj_name, 'count': len(cadastre_codes)}

        # Validate output settings based on output mode
        output_mode = self.dlg.get_output_mode()
        output_info = {'mode': output_mode}

        if output_mode == katasterDialog.OUTPUT_NEW_FILE:
            output_path = self.dlg.output_path_input.text().strip()
            if not output_path or not os.path.exists(output_path):
                QMessageBox.warning(self.dlg, "Invalid Path", "Please select a valid output folder")
                return None
            output_info['path'] = output_path
            output_info['filename'] = self.dlg.filename_input.text().strip()
        else:  # OUTPUT_APPEND
            append_path = self.dlg.get_append_file_path()
            if not append_path:
                QMessageBox.warning(self.dlg, "No File Selected", "Please select a GeoPackage file to append to")
                return None
            # Ensure parent directory exists
            parent_dir = os.path.dirname(append_path)
            if parent_dir and not os.path.exists(parent_dir):
                QMessageBox.warning(self.dlg, "Invalid Path", f"Directory does not exist: {parent_dir}")
                return None
            output_info['append_path'] = append_path

        layers = {
            'parcel_c': self.dlg.parcel_c_checkbox.isChecked(),
            'parcel_e': self.dlg.parcel_e_checkbox.isChecked(),
            'zoning': self.dlg.zoning_checkbox.isChecked(),
        }
        if not any(layers.values()):
            QMessageBox.warning(self.dlg, "No Layers Selected", "Please select at least one layer to fetch")
            return None

        if self.worker is not None and self.worker.isRunning():
            QMessageBox.warning(self.dlg, "Already Running", "A download is already in progress. Please wait.")
            return None

        return cadastre_codes, output_info, layers, selection_info

    def determine_output_name(self, selection_info, custom_filename):
        """Determine output filename from selection info or custom input."""
        if custom_filename:
            output_name = custom_filename.replace('.gpkg', '').replace('.GPKG', '')
            return remove_diacritics(output_name).replace(' ', '_')

        mode = selection_info.get('mode')
        name = selection_info.get('name', '')

        if mode == 'cadastre':
            query = selection_info.get('query', '')
            code, cadastre_name = find_cadastre_code(query)
            if code and cadastre_name:
                return f"{code}_{remove_diacritics(cadastre_name).replace(' ', '_').upper()}"
            elif code:
                return code
            else:
                return remove_diacritics(query).replace(' ', '_')
        elif mode == 'okres':
            return f"OKRES_{remove_diacritics(name).replace(' ', '_').replace('-', '_').upper()}"
        elif mode == 'kraj':
            return f"KRAJ_{remove_diacritics(name).replace(' ', '_').upper()}"
        else:
            return remove_diacritics(name).replace(' ', '_')

    def worker_progress(self, message):
        """Handle progress messages from worker."""
        QgsMessageLog.logMessage(message, "Kataster", Qgis.Info)

    def worker_error(self, message):
        """Handle error messages from worker."""
        QgsMessageLog.logMessage(message, "Kataster", Qgis.Critical)

    def worker_multi_progress(self, current, total, name):
        """Handle multi-cadastre progress updates."""
        # Update progress bar (10-60% for download phase)
        progress = 10 + int((current / total) * 50)
        self.dlg.progress_bar.setValue(progress)
        self.dlg.status_label.setText(f"Downloading {current}/{total}: {name}")

    def _find_existing_layer(self, gpkg_path, layer_name):
        """
        Find an existing layer in the project that uses the same GPKG source.

        Handles layers with subset filters applied (which may modify the source string).
        """
        for layer in QgsProject.instance().mapLayers().values():
            source = layer.source()
            # Check if source points to same GPKG and layer (handles subset filters in source)
            if gpkg_path in source and f"layername={layer_name}" in source:
                return layer
            # Also check by layer name if it matches and source contains the GPKG path
            if layer.name() == layer_name and gpkg_path in source:
                return layer
        return None

    def _load_gpkg_layers(self, output_gpkg):
        """
        Load all layers from GPKG into QGIS project. Returns list of (name, count, is_refresh) tuples.

        If a layer from the same GPKG is already in the project, it will be refreshed instead of duplicated.

        Args:
            output_gpkg: Path to the GPKG file
        """
        loaded_layers = []
        for layer_name in _GPKG_LAYER_NAMES:
            # Check if layer already exists in project - always refresh instead of duplicating
            existing_layer = self._find_existing_layer(output_gpkg, layer_name)

            if existing_layer:
                # Refresh existing layer
                existing_layer.dataProvider().reloadData()
                existing_layer.triggerRepaint()
                num_features = existing_layer.featureCount()
                if num_features > 0:
                    loaded_layers.append((layer_name, num_features, True))
                    QgsMessageLog.logMessage(f"✓ Refreshed {layer_name} ({num_features} features)", "Kataster", Qgis.Success)
            else:
                # Load as new layer
                layer = QgsVectorLayer(f"{output_gpkg}|layername={layer_name}", layer_name, "ogr")
                if not layer.isValid():
                    continue

                num_features = layer.featureCount()
                if num_features == 0:
                    QgsMessageLog.logMessage(f"Warning: {layer_name} has no features", "Kataster", Qgis.Warning)
                    continue

                QgsProject.instance().addMapLayer(layer)
                loaded_layers.append((layer_name, num_features, False))
                QgsMessageLog.logMessage(f"✓ Loaded {num_features} features for {layer_name}", "Kataster", Qgis.Success)

        return loaded_layers

    def worker_finished(self, vsimem_files, on_refresh_filter=None):
        """
        Called when download worker finishes.

        Args:
            vsimem_files: List of vsimem paths
            on_refresh_filter: Optional callback to refresh filter combo
        """
        try:
            if not vsimem_files:
                raise Exception("No data was downloaded")

            self.dlg.progress_bar.setValue(70)
            transform_to_5514 = self.dlg.transform_crs_checkbox.isChecked()

            # Determine output path based on mode
            if self.current_output_mode == katasterDialog.OUTPUT_APPEND:
                # Append mode
                output_gpkg = self.current_append_path
                gpkg_filename = os.path.basename(output_gpkg)
                self.dlg.status_label.setText("Appending to GeoPackage...")

                QgsMessageLog.logMessage(f"\nAppending to GeoPackage: {gpkg_filename}", "Kataster", Qgis.Info)
                if not append_to_gpkg(vsimem_files, output_gpkg, self.current_cadastre_codes,
                                      self.plugin_dir, transform_to_5514):
                    raise Exception("Failed to append data to GeoPackage")
            else:
                # New file mode
                output_path = self.dlg.output_path_input.text().strip()
                gpkg_filename = f"{self.current_output_name}.gpkg"
                output_gpkg = os.path.join(output_path, gpkg_filename)
                self.dlg.status_label.setText("Converting to GeoPackage...")

                QgsMessageLog.logMessage(f"\nConverting to GeoPackage: {gpkg_filename}", "Kataster", Qgis.Info)
                if not convert_vsimem_to_gpkg(vsimem_files, output_gpkg, self.plugin_dir, transform_to_5514):
                    raise Exception("Failed to convert data to GeoPackage")

            self.dlg.progress_bar.setValue(85)
            self.dlg.status_label.setText("Loading layers into QGIS...")

            # Load layers (automatically refreshes existing layers from same GPKG)
            is_append_mode = (self.current_output_mode == katasterDialog.OUTPUT_APPEND)
            loaded_layers = self._load_gpkg_layers(output_gpkg)
            if not loaded_layers:
                QMessageBox.warning(self.dlg, "No Data",
                    f"No data found for {self.current_query}.\n\n"
                    "This may be due to:\n• API returning invalid data\n• Empty cadastre\n• Try a different cadastre")
                self.dlg.status_label.setText("No data found")
                return

            # Show success
            self.dlg.progress_bar.setValue(100)
            total_features = sum(count for _, count, _ in loaded_layers)
            refreshed_count = sum(1 for _, _, is_refresh in loaded_layers if is_refresh)
            new_count = len(loaded_layers) - refreshed_count

            if is_append_mode:
                if refreshed_count > 0 and new_count == 0:
                    success_msg = f"Successfully refreshed {refreshed_count} layer(s):\n\n"
                elif refreshed_count > 0:
                    success_msg = f"Refreshed {refreshed_count}, loaded {new_count} layer(s):\n\n"
                else:
                    success_msg = f"Successfully loaded {len(loaded_layers)} layer(s):\n\n"
            else:
                success_msg = f"Successfully loaded {len(loaded_layers)} layer(s):\n\n"

            for name, count, is_refresh in loaded_layers:
                status = " (refreshed)" if is_refresh else ""
                success_msg += f"• {name}: {count} features{status}\n"
            success_msg += f"\nData saved to: {gpkg_filename}"

            self.dlg.status_label.setText(f"Success! {total_features} features in {len(loaded_layers)} layer(s)")
            QMessageBox.information(self.dlg, "Success", success_msg)

            # Refresh filter combo to include newly downloaded cadastres
            if is_append_mode and on_refresh_filter:
                on_refresh_filter()

        except Exception as e:
            import traceback
            QgsMessageLog.logMessage(f"ERROR: {traceback.format_exc()}", "Kataster", Qgis.Critical)
            QMessageBox.critical(self.dlg, "Error",
                f"Failed to load layers:\n{e}\n\nCheck View → Panels → Log Messages (Kataster tab)")
            self.dlg.status_label.setText(f"Error: {e}")

        finally:
            self.dlg.load_button.setEnabled(True)
            self.dlg.progress_bar.setValue(0)
            if self.worker:
                self.worker.quit()
                self.worker.wait()
                self.worker = None

    def start_download(self, cadastre_codes, output_info, layers, selection_info, on_refresh_filter=None):
        """
        Start the download process.

        Args:
            cadastre_codes: List of (code, name) tuples
            output_info: Dict with output settings
            layers: Dict with layer selection flags
            selection_info: Dict with selection mode info
            on_refresh_filter: Optional callback to refresh filter combo after download
        """
        self.dlg.load_button.setEnabled(False)
        self.dlg.progress_bar.setValue(10)

        layers_text = []
        if layers['parcel_c']:
            layers_text.append("Parcel C")
        if layers['parcel_e']:
            layers_text.append("Parcel E")
        if layers['zoning']:
            layers_text.append("Zoning")

        # Build display name for status
        mode = selection_info.get('mode')
        if mode == 'cadastre':
            display_name = selection_info.get('name', 'cadastre')
        else:
            count = selection_info.get('count', len(cadastre_codes))
            display_name = f"{selection_info.get('name')} ({count} cadastral units)"

        self.dlg.status_label.setText(f"Downloading {', '.join(layers_text)} for {display_name}...")

        try:
            # Store info for worker_finished
            self.current_cadastre_codes = cadastre_codes
            self.current_output_mode = output_info['mode']

            if output_info['mode'] == katasterDialog.OUTPUT_NEW_FILE:
                output_name = self.determine_output_name(selection_info, output_info.get('filename', ''))
                self.current_output_name = output_name
                self.current_append_path = None
                output_path = output_info['path']
                QgsMessageLog.logMessage(f"Output mode: Create new file", "Kataster", Qgis.Info)
                QgsMessageLog.logMessage(f"Output path: {output_path}", "Kataster", Qgis.Info)
            else:  # OUTPUT_APPEND
                self.current_append_path = output_info['append_path']
                self.current_output_name = os.path.splitext(os.path.basename(self.current_append_path))[0]
                QgsMessageLog.logMessage(f"Output mode: Append to existing file", "Kataster", Qgis.Info)
                QgsMessageLog.logMessage(f"Append to: {self.current_append_path}", "Kataster", Qgis.Info)

            self.current_query = selection_info.get('name', '')

            QgsMessageLog.logMessage(f"Selection mode: {mode}", "Kataster", Qgis.Info)
            QgsMessageLog.logMessage(f"Cadastral units to download: {len(cadastre_codes)}", "Kataster", Qgis.Info)

            self.dlg.progress_bar.setValue(30)
            self.dlg.status_label.setText("Starting download...")

            self.worker = DownloadWorker(
                cadastre_codes=cadastre_codes,
                output_name=self.current_output_name,
                fetch_parcel_c=layers['parcel_c'],
                fetch_parcel_e=layers['parcel_e'],
                fetch_zoning=layers['zoning'],
                plugin_dir=self.plugin_dir
            )
            self.worker.progress.connect(self.worker_progress)
            self.worker.error.connect(self.worker_error)
            self.worker.finished.connect(lambda vsimem: self.worker_finished(vsimem, on_refresh_filter))
            self.worker.multi_progress.connect(self.worker_multi_progress)
            self.worker.start()

            self.dlg.status_label.setText(f"Downloading {display_name}...")
            QgsMessageLog.logMessage("Download started", "Kataster", Qgis.Info)

        except Exception as e:
            import traceback
            QgsMessageLog.logMessage(f"ERROR: {traceback.format_exc()}", "Kataster", Qgis.Critical)
            QMessageBox.critical(self.dlg, "Error", f"Failed to start download:\n{e}")
            self.dlg.status_label.setText(f"Error: {e}")
            self.dlg.load_button.setEnabled(True)
            self.dlg.progress_bar.setValue(0)
            self.worker = None
