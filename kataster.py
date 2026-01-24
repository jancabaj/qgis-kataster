# -*- coding: utf-8 -*-
"""
Kataster - A QGIS plugin for Slovak cadastre data.

Copyright (C) 2025 by jancabaj
License: GNU General Public License v2+
"""
import os.path
import math

from qgis.PyQt.QtCore import QSettings, QTranslator, QCoreApplication, QUrl
from qgis.PyQt.QtGui import QIcon, QDesktopServices
from qgis.PyQt.QtWidgets import QAction, QMessageBox, QFileDialog
from qgis.core import (
    QgsVectorLayer,
    QgsProject,
    QgsMessageLog,
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
)

from .resources import *
from .kataster_dialog import katasterDialog
from .api.download_cadastre import find_cadastre_code
from .workers import DownloadWorker
from .gpkg_utils import remove_diacritics, convert_vsimem_to_gpkg

# Layer names used in GPKG
_GPKG_LAYER_NAMES = ['ParcelC', 'ParcelE', 'CadastralUnit']


class kataster:
    """QGIS Plugin Implementation."""

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.actions = []
        self.menu = self.tr(u'&kataster')
        self.toolbar = None
        self.first_start = None
        self.worker = None
        self.current_query = None
        self.current_output_name = None
        self.settings = QSettings()
        self.settings_key = 'kataster/output_path'

        self._init_locale()
        self._init_proj_path()

    def _init_locale(self):
        """Initialize locale and translator."""
        locale = QSettings().value('locale/userLocale')[0:2]
        locale_path = os.path.join(self.plugin_dir, 'i18n', f'kataster_{locale}.qm')
        if os.path.exists(locale_path):
            self.translator = QTranslator()
            self.translator.load(locale_path)
            QCoreApplication.installTranslator(self.translator)

    def _init_proj_path(self):
        """Add plugin's proj directory to PROJ search path for bundled grid file."""
        proj_dir = os.path.join(self.plugin_dir, 'proj')
        if os.path.exists(proj_dir):
            current_proj_data = os.environ.get('PROJ_DATA', '')
            if proj_dir not in current_proj_data:
                os.environ['PROJ_DATA'] = f"{proj_dir}{os.pathsep}{current_proj_data}" if current_proj_data else proj_dir
                QgsMessageLog.logMessage(f"Using bundled grid file from: {proj_dir}", "Kataster", Qgis.Info)

    def tr(self, message):
        """Get the translation for a string using Qt translation API."""
        return QCoreApplication.translate('kataster', message)

    # -------------------------------------------------------------------------
    # Output path management
    # -------------------------------------------------------------------------

    def get_default_output_path(self):
        """Get default output path (KN directory inside plugin folder)."""
        default_path = os.path.join(self.plugin_dir, "KN")
        if not os.path.exists(default_path):
            try:
                os.makedirs(default_path)
                QgsMessageLog.logMessage(f"Created default output directory: {default_path}", "Kataster", Qgis.Info)
            except Exception as e:
                QgsMessageLog.logMessage(f"Failed to create default directory: {e}", "Kataster", Qgis.Warning)
                default_path = self.plugin_dir
        return default_path

    def get_output_path(self):
        """Get configured output path from settings or return default."""
        saved_path = self.settings.value(self.settings_key, None)
        if saved_path and os.path.exists(saved_path):
            return saved_path
        return self.get_default_output_path()

    def set_output_path(self, path):
        """Save output path to settings."""
        if path and os.path.isdir(path):
            self.settings.setValue(self.settings_key, path)
            QgsMessageLog.logMessage(f"Output path set to: {path}", "Kataster", Qgis.Info)
            return True
        return False

    def browse_output_path(self):
        """Open directory browser dialog."""
        current_path = self.dlg.output_path_input.text()
        if not current_path or not os.path.exists(current_path):
            current_path = self.get_output_path()

        folder = QFileDialog.getExistingDirectory(self.dlg, "Select Output Folder", current_path)
        if folder:
            self.dlg.output_path_input.setText(folder)
            self.set_output_path(folder)

    # -------------------------------------------------------------------------
    # GUI setup
    # -------------------------------------------------------------------------

    def add_action(self, icon_path, text, callback, enabled_flag=True, add_to_menu=True,
                   add_to_toolbar=True, status_tip=None, whats_this=None, parent=None,
                   use_custom_toolbar=False):
        """Add a toolbar icon to the toolbar."""
        icon = QIcon(icon_path)
        action = QAction(icon, text, parent)
        action.triggered.connect(callback)
        action.setEnabled(enabled_flag)

        if status_tip:
            action.setStatusTip(status_tip)
        if whats_this:
            action.setWhatsThis(whats_this)

        if add_to_toolbar:
            if use_custom_toolbar:
                if self.toolbar is None:
                    self.toolbar = self.iface.addToolBar(u'Kataster SR')
                self.toolbar.addAction(action)
            else:
                self.iface.addToolBarIcon(action)

        if add_to_menu:
            self.iface.addPluginToMenu(self.menu, action)

        self.actions.append(action)
        return action

    def initGui(self):
        """Create the menu entries and toolbar icons inside the QGIS GUI."""
        self.add_action(
            ':/plugins/kataster/icon.png',
            text=self.tr(u'Kataster SR'),
            callback=self.run,
            parent=self.iface.mainWindow(),
            status_tip=self.tr(u'Load cadastral data from Slovak Cadastre'),
            use_custom_toolbar=True
        )

        self.add_action(
            ':/plugins/kataster/zbgis_icon.png',
            text=self.tr(u'Open ZBGIS'),
            callback=self.open_zbgis,
            parent=self.iface.mainWindow(),
            status_tip=self.tr(u'Open zbgis.skgeodesy.sk at current map extent'),
            add_to_menu=False,
            use_custom_toolbar=True
        )

        self.first_start = True

    def unload(self):
        """Removes the plugin menu item and icon from QGIS GUI."""
        for action in self.actions:
            self.iface.removePluginMenu(self.tr(u'&kataster'), action)
            self.iface.removeToolBarIcon(action)

        if self.toolbar is not None:
            del self.toolbar
            self.toolbar = None

    # -------------------------------------------------------------------------
    # Worker signal handlers
    # -------------------------------------------------------------------------

    def worker_progress(self, message):
        """Handle progress messages from worker."""
        QgsMessageLog.logMessage(message, "Kataster", Qgis.Info)

    def worker_error(self, message):
        """Handle error messages from worker."""
        QgsMessageLog.logMessage(message, "Kataster", Qgis.Critical)

    def _load_gpkg_layers(self, output_gpkg):
        """Load all layers from GPKG into QGIS project. Returns list of (name, count) tuples."""
        loaded_layers = []
        for layer_name in _GPKG_LAYER_NAMES:
            layer = QgsVectorLayer(f"{output_gpkg}|layername={layer_name}", layer_name, "ogr")
            if not layer.isValid():
                continue

            num_features = layer.featureCount()
            if num_features == 0:
                QgsMessageLog.logMessage(f"Warning: {layer_name} has no features", "Kataster", Qgis.Warning)
                continue

            QgsProject.instance().addMapLayer(layer)
            loaded_layers.append((layer_name, num_features))
            QgsMessageLog.logMessage(f"✓ Loaded {num_features} features for {layer_name}", "Kataster", Qgis.Success)

        return loaded_layers

    def worker_finished(self, vsimem_files):
        """Called when download worker finishes."""
        try:
            if not vsimem_files:
                raise Exception("No data was downloaded")

            self.dlg.progress_bar.setValue(70)
            self.dlg.status_label.setText("Converting to GeoPackage...")

            # Convert to GPKG
            output_path = self.dlg.output_path_input.text().strip()
            gpkg_filename = f"{self.current_output_name}.gpkg"
            output_gpkg = os.path.join(output_path, gpkg_filename)
            transform_to_5514 = self.dlg.transform_crs_checkbox.isChecked()

            QgsMessageLog.logMessage(f"\nConverting to GeoPackage: {gpkg_filename}", "Kataster", Qgis.Info)
            if not convert_vsimem_to_gpkg(vsimem_files, output_gpkg, self.plugin_dir, transform_to_5514):
                raise Exception("Failed to convert data to GeoPackage")

            self.dlg.progress_bar.setValue(85)
            self.dlg.status_label.setText("Loading layers into QGIS...")

            # Load layers
            loaded_layers = self._load_gpkg_layers(output_gpkg)
            if not loaded_layers:
                QMessageBox.warning(self.dlg, "No Data",
                    f"No data found for {self.current_query}.\n\n"
                    "This may be due to:\n• API returning invalid data\n• Empty cadastre\n• Try a different cadastre")
                self.dlg.status_label.setText("No data found")
                return

            # Show success
            self.dlg.progress_bar.setValue(100)
            total_features = sum(count for _, count in loaded_layers)

            success_msg = f"Successfully loaded {len(loaded_layers)} layer(s):\n\n"
            for name, count in loaded_layers:
                success_msg += f"• {name}: {count} features\n"
            success_msg += f"\nData saved to: {gpkg_filename}"

            self.dlg.status_label.setText(f"Success! Loaded {total_features} features in {len(loaded_layers)} layer(s)")
            QMessageBox.information(self.dlg, "Success", success_msg)

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

    # -------------------------------------------------------------------------
    # Main actions
    # -------------------------------------------------------------------------

    def _determine_output_name(self, query, custom_filename):
        """Determine output filename from query or custom input."""
        if custom_filename:
            output_name = custom_filename.replace('.gpkg', '').replace('.GPKG', '')
            return remove_diacritics(output_name).replace(' ', '_')

        code, name = find_cadastre_code(query)
        if code and name:
            return f"{code}_{remove_diacritics(name).replace(' ', '_').upper()}"
        elif code:
            return code
        else:
            return remove_diacritics(query).replace(' ', '_')

    def _validate_load_inputs(self):
        """Validate inputs for load_parcels. Returns (query, output_path, layers) or None if invalid."""
        query = self.dlg.cadastre_input.text().strip()
        if not query:
            QMessageBox.warning(self.dlg, "Input Required", "Please enter a cadastre name or code")
            return None

        output_path = self.dlg.output_path_input.text().strip()
        if not output_path or not os.path.exists(output_path):
            QMessageBox.warning(self.dlg, "Invalid Path", "Please select a valid output folder")
            return None

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

        return query, output_path, layers

    def load_parcels(self):
        """Load selected layers by calling standalone download script asynchronously."""
        validated = self._validate_load_inputs()
        if not validated:
            return

        query, output_path, layers = validated
        self.dlg.load_button.setEnabled(False)
        self.dlg.progress_bar.setValue(10)

        layers_text = []
        if layers['parcel_c']:
            layers_text.append("Parcel C")
        if layers['parcel_e']:
            layers_text.append("Parcel E")
        if layers['zoning']:
            layers_text.append("Zoning")
        self.dlg.status_label.setText(f"Downloading {', '.join(layers_text)} for {query}...")

        try:
            output_name = self._determine_output_name(query, self.dlg.filename_input.text().strip())
            self.current_query = query
            self.current_output_name = output_name

            QgsMessageLog.logMessage(f"Query: {query}", "Kataster", Qgis.Info)
            QgsMessageLog.logMessage(f"Output path: {output_path}", "Kataster", Qgis.Info)

            self.dlg.progress_bar.setValue(30)
            self.dlg.status_label.setText("Starting download...")

            self.worker = DownloadWorker(
                query=query,
                output_name=output_name,
                fetch_parcel_c=layers['parcel_c'],
                fetch_parcel_e=layers['parcel_e'],
                fetch_zoning=layers['zoning']
            )
            self.worker.progress.connect(self.worker_progress)
            self.worker.error.connect(self.worker_error)
            self.worker.finished.connect(self.worker_finished)
            self.worker.start()

            self.dlg.status_label.setText(f"Downloading {query}...")
            QgsMessageLog.logMessage("Download started", "Kataster", Qgis.Info)

        except Exception as e:
            import traceback
            QgsMessageLog.logMessage(f"ERROR: {traceback.format_exc()}", "Kataster", Qgis.Critical)
            QMessageBox.critical(self.dlg, "Error", f"Failed to start download:\n{e}")
            self.dlg.status_label.setText(f"Error: {e}")
            self.dlg.load_button.setEnabled(True)
            self.dlg.progress_bar.setValue(0)
            self.worker = None

    def open_zbgis(self):
        """Open zbgis.skgeodesy.sk with current map extent coordinates."""
        try:
            canvas = self.iface.mapCanvas()
            extent = canvas.extent()
            project_crs = QgsProject.instance().crs()
            wgs84_crs = QgsCoordinateReferenceSystem("EPSG:4326")

            transform = QgsCoordinateTransform(project_crs, wgs84_crs, QgsProject.instance())
            center_wgs84 = transform.transform(extent.center())

            lat, lon = center_wgs84.y(), center_wgs84.x()
            scale = canvas.scale()

            # Convert scale to zoom level (web map formula)
            zoom = math.log2(591657550.5 / scale) - 1.5
            zoom = int(max(8, min(21, round(zoom))))

            url = f"https://zbgis.skgeodesy.sk/mapka/sk/kataster?pos={lat:.6f},{lon:.6f},{zoom}"
            QDesktopServices.openUrl(QUrl(url))

            QgsMessageLog.logMessage(f"ZBGIS: scale 1:{scale:.0f}, zoom {zoom}", "Kataster", Qgis.Info)
            self.iface.messageBar().pushMessage("ZBGIS", f"Opening at zoom {zoom}", Qgis.Info, 3)

        except Exception as e:
            QgsMessageLog.logMessage(f"Error opening ZBGIS: {e}", "Kataster", Qgis.Critical)
            self.iface.messageBar().pushMessage("Error", f"Failed to open ZBGIS: {e}", Qgis.Critical, 5)

    def run(self):
        """Run method that performs all the real work."""
        if self.first_start:
            self.first_start = False
            self.dlg = katasterDialog()
            self.dlg.load_button.clicked.connect(self.load_parcels)
            self.dlg.browse_button.clicked.connect(self.browse_output_path)

        self.dlg.cadastre_input.clear()
        self.dlg.filename_input.clear()
        self.dlg.progress_bar.setValue(0)
        self.dlg.status_label.setText("Ready")
        self.dlg.output_path_input.setText(self.get_output_path())
        self.dlg.show()
