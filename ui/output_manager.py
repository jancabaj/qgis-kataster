# -*- coding: utf-8 -*-
"""
Output path and file management for the Kataster plugin.
"""

import os
from qgis.PyQt.QtCore import QSettings, qVersion
_QT6 = int(qVersion().split('.')[0]) >= 6
from qgis.PyQt.QtWidgets import QFileDialog
from qgis.core import QgsMessageLog, Qgis


class OutputManager:
    """Manages output paths and file selection for the plugin."""

    def __init__(self, plugin_dir, dlg=None):
        """
        Initialize the output manager.

        Args:
            plugin_dir: Plugin directory path
            dlg: Optional dialog reference for file browsers
        """
        self.plugin_dir = plugin_dir
        self.dlg = dlg
        self.settings = QSettings()
        self.settings_key = 'kataster/output_path'
        self.settings_key_append = 'kataster/append_file_path'

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

    def get_append_file_path(self):
        """Get saved append file path from settings."""
        return self.settings.value(self.settings_key_append, '')

    def set_append_file_path(self, path):
        """Save append file path to settings."""
        if path:
            self.settings.setValue(self.settings_key_append, path)
            QgsMessageLog.logMessage(f"Append file path set to: {path}", "Kataster", Qgis.Info)

    def browse_output_path(self):
        """Open directory browser dialog."""
        if not self.dlg:
            return

        current_path = self.dlg.output_path_input.text()
        if not current_path or not os.path.exists(current_path):
            current_path = self.get_output_path()

        folder = QFileDialog.getExistingDirectory(self.dlg, "Select Output Folder", current_path)
        if folder:
            self.dlg.output_path_input.setText(folder)
            self.set_output_path(folder)

    def browse_append_file(self):
        """Open file browser dialog for selecting GPKG to append to."""
        if not self.dlg:
            return

        current_path = self.dlg.get_append_file_path()
        if not current_path:
            # Try saved path, then fall back to output folder
            current_path = self.get_append_file_path() or self.get_output_path()

        file_path, _ = QFileDialog.getSaveFileName(
            self.dlg,
            "Select or Create GeoPackage",
            current_path,
            "GeoPackage (*.gpkg)",
            options=QFileDialog.Option.DontConfirmOverwrite if _QT6 else QFileDialog.DontConfirmOverwrite  # Allow selecting existing files
        )
        if file_path:
            # Ensure .gpkg extension
            if not file_path.lower().endswith('.gpkg'):
                file_path += '.gpkg'
            self.dlg.set_append_file_path(file_path)
            self.set_append_file_path(file_path)  # Save to settings
