# -*- coding: utf-8 -*-
"""
Layer filtering functionality for the Kataster plugin.
"""

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QComboBox, QPushButton, QLabel
from qgis.core import QgsProject, QgsMessageLog, Qgis

from ..api.hierarchy import find_cadastre_code
from ..gpkg_utils import get_gpkg_cadastre_list


class LayerFilter:
    """Manages the cadastre filter toolbar and layer filtering operations."""

    def __init__(self, iface, toolbar, get_append_file_path_func):
        """
        Initialize the layer filter.

        Args:
            iface: QGIS interface
            toolbar: The toolbar to add filter widgets to
            get_append_file_path_func: Function that returns the working GPKG path
        """
        self.iface = iface
        self.toolbar = toolbar
        self.get_append_file_path = get_append_file_path_func

        # Filter widgets
        self.filter_combo = None
        self.filter_apply_btn = None
        self.filter_clear_btn = None
        self.filter_refresh_btn = None

        # Cache for all cadastres
        self._all_cadastres = []

    def init_toolbar(self):
        """Initialize filter widgets in the toolbar."""
        try:
            # Add separator
            self.toolbar.addSeparator()

            # Filter label
            filter_label = QLabel(" Filter: ")
            self.toolbar.addWidget(filter_label)

            # Editable combo box for cadastre filter (simple, no dropdown)
            self.filter_combo = QComboBox()
            self.filter_combo.setEditable(True)
            self.filter_combo.setInsertPolicy(QComboBox.NoInsert)
            self.filter_combo.setMinimumWidth(200)
            self.filter_combo.lineEdit().setPlaceholderText("Type cadastre name or code...")
            self.filter_combo.setToolTip("Type cadastre name or code, then press Enter or click Apply")

            # Connect Enter key to apply filter
            self.filter_combo.lineEdit().returnPressed.connect(self.apply_filter)

            self.toolbar.addWidget(self.filter_combo)

            # Apply filter button
            self.filter_apply_btn = QPushButton("Apply")
            self.filter_apply_btn.setToolTip("Apply filter to KN layers")
            self.filter_apply_btn.clicked.connect(self.apply_filter)
            self.toolbar.addWidget(self.filter_apply_btn)

            # Clear filter button
            self.filter_clear_btn = QPushButton("Clear")
            self.filter_clear_btn.setToolTip("Clear filter and show all features")
            self.filter_clear_btn.clicked.connect(self.clear_filter)
            self.toolbar.addWidget(self.filter_clear_btn)

            # Refresh button to reload cadastre list
            self.filter_refresh_btn = QPushButton("↻")
            self.filter_refresh_btn.setToolTip("Refresh cadastre list from working GPKG")
            self.filter_refresh_btn.setMaximumWidth(30)
            self.filter_refresh_btn.clicked.connect(self.refresh_combo)
            self.toolbar.addWidget(self.filter_refresh_btn)

            # Initial population of combo box (safe to fail)
            try:
                self.refresh_combo()
            except Exception:
                pass  # No working GPKG yet, that's OK

        except Exception as e:
            QgsMessageLog.logMessage(f"Error initializing filter toolbar: {e}", "Kataster", Qgis.Warning)

    def refresh_combo(self):
        """Refresh the cadastre cache from working GPKG."""
        # Store all cadastres for lookup
        self._all_cadastres = []

        # Get cadastres from working GPKG
        gpkg_path = self.get_append_file_path()
        if gpkg_path:
            self._all_cadastres = get_gpkg_cadastre_list(gpkg_path)

        QgsMessageLog.logMessage(f"Filter: loaded {len(self._all_cadastres)} cadastres from working GPKG", "Kataster", Qgis.Info)

    def _extract_cadastre_code(self, text):
        """Extract cadastre code from filter text (handles plain code or name)."""
        if not text:
            return None

        text = text.strip()

        # Check if it's a plain 6-digit code
        if text.isdigit() and len(text) == 6:
            return text

        # Try to find by name in cached cadastres from working GPKG
        if hasattr(self, '_all_cadastres') and self._all_cadastres:
            text_lower = text.lower()
            for code, name in self._all_cadastres:
                if text_lower == name.lower() or text_lower in name.lower():
                    return code

        # Last resort: use the full cadastre lookup (searches all cadastres, not just downloaded)
        code, name = find_cadastre_code(text)
        if code:
            return code

        return None

    def apply_filter(self):
        """Apply filter to KN layers based on selected cadastre."""
        filter_text = self.filter_combo.currentText().strip()

        if not filter_text:
            self.clear_filter()
            return

        cadastre_code = self._extract_cadastre_code(filter_text)

        if not cadastre_code:
            self.iface.messageBar().pushMessage("Filter", f"Could not find cadastre: {filter_text}", Qgis.Warning, 3)
            return

        # Build filter expressions
        # ParcelC/ParcelE: nationalCadastralReference LIKE 'code_%'
        # CadastralUnit: nationalCadastalZoningReference = 'code'
        parcel_filter = f"\"nationalCadastralReference\" LIKE '{cadastre_code}_%'"
        zoning_filter = f"\"nationalCadastalZoningReference\" = '{cadastre_code}'"

        filtered_count = 0

        # Apply filters to matching layers
        for layer in QgsProject.instance().mapLayers().values():
            layer_name = layer.name()
            if layer_name in ['ParcelC', 'ParcelE']:
                layer.setSubsetString(parcel_filter)
                filtered_count += 1
                QgsMessageLog.logMessage(f"Filter applied to {layer_name}: {parcel_filter}", "Kataster", Qgis.Info)
            elif layer_name == 'CadastralUnit':
                layer.setSubsetString(zoning_filter)
                filtered_count += 1
                QgsMessageLog.logMessage(f"Filter applied to {layer_name}: {zoning_filter}", "Kataster", Qgis.Info)

        if filtered_count > 0:
            self.iface.messageBar().pushMessage("Filter", f"Filtered {filtered_count} layer(s) to cadastre {cadastre_code}", Qgis.Info, 3)
            self.iface.mapCanvas().refresh()
        else:
            self.iface.messageBar().pushMessage("Filter", "No KN layers found in project", Qgis.Warning, 3)

    def clear_filter(self):
        """Clear filter from all KN layers."""
        cleared_count = 0

        for layer in QgsProject.instance().mapLayers().values():
            layer_name = layer.name()
            if layer_name in ['ParcelC', 'ParcelE', 'CadastralUnit']:
                if layer.subsetString():  # Only clear if there's a filter
                    layer.setSubsetString("")
                    cleared_count += 1
                    QgsMessageLog.logMessage(f"Filter cleared from {layer_name}", "Kataster", Qgis.Info)

        # Clear the filter input
        if self.filter_combo:
            self.filter_combo.clearEditText()

        if cleared_count > 0:
            self.iface.messageBar().pushMessage("Filter", f"Cleared filter from {cleared_count} layer(s)", Qgis.Info, 3)
            self.iface.mapCanvas().refresh()
