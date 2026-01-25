# -*- coding: utf-8 -*-
"""
Kataster - A QGIS plugin for Slovak cadastre data.

Copyright (C) 2025 by jancabaj
License: GNU General Public License v2+
"""
import os.path

from qgis.PyQt.QtCore import QSettings, QTranslator, QCoreApplication
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction
from qgis.core import QgsMessageLog, Qgis

from .resources import *
from .kataster_dialog import katasterDialog
from .ui import LayerFilter, OutputManager, open_zbgis
from .core import DownloadManager


class kataster:
    """QGIS Plugin Implementation."""

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.actions = []
        self.menu = self.tr(u'&kataster')
        self.toolbar = None
        self.first_start = None

        # Managers
        self.output_manager = None
        self.layer_filter = None
        self.download_manager = None

        self.settings = QSettings()

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
            callback=lambda: open_zbgis(self.iface),
            parent=self.iface.mainWindow(),
            status_tip=self.tr(u'Open zbgis.skgeodesy.sk at current map extent'),
            add_to_menu=False,
            use_custom_toolbar=True
        )

        # Initialize output manager (needed for filter)
        if self.output_manager is None:
            self.output_manager = OutputManager(self.plugin_dir)

        # Initialize filter toolbar only if not already initialized
        if self.layer_filter is None:
            self.layer_filter = LayerFilter(
                self.iface,
                self.toolbar,
                self.output_manager.get_append_file_path
            )
            self.layer_filter.init_toolbar()

        self.first_start = True

    def unload(self):
        """Removes the plugin menu item and icon from QGIS GUI."""
        for action in self.actions:
            self.iface.removePluginMenu(self.tr(u'&kataster'), action)
            self.iface.removeToolBarIcon(action)

        # Clear actions list
        self.actions.clear()

        # Remove toolbar from QGIS interface
        if self.toolbar is not None:
            self.iface.mainWindow().removeToolBar(self.toolbar)
            self.toolbar.deleteLater()
            self.toolbar = None

        # Clean up managers
        self.layer_filter = None
        self.output_manager = None
        self.download_manager = None

    # -------------------------------------------------------------------------
    # Main actions
    # -------------------------------------------------------------------------

    def load_parcels(self):
        """Load selected layers by calling download manager."""
        validated = self.download_manager.validate_inputs()
        if not validated:
            return

        cadastre_codes, output_info, layers, selection_info = validated

        # Start download with callback to refresh filter
        self.download_manager.start_download(
            cadastre_codes,
            output_info,
            layers,
            selection_info,
            on_refresh_filter=self.layer_filter.refresh_combo
        )

    def run(self):
        """Run method that performs all the real work."""
        if self.first_start:
            self.first_start = False
            self.dlg = katasterDialog()

            # Initialize managers with dialog reference
            self.output_manager = OutputManager(self.plugin_dir, self.dlg)
            self.download_manager = DownloadManager(self.plugin_dir, self.iface, self.dlg)

            # Connect dialog buttons
            self.dlg.load_button.clicked.connect(self.load_parcels)
            self.dlg.browse_button.clicked.connect(self.output_manager.browse_output_path)
            self.dlg.browse_append_button.clicked.connect(self.output_manager.browse_append_file)

        # Reset selection mode to cadastre unit
        self.dlg.radio_cadastre.setChecked(True)
        self.dlg.cadastre_input.clear()
        self.dlg.filename_input.clear()
        # Reset output mode to new file
        self.dlg.radio_new_file.setChecked(True)
        self.dlg.progress_bar.setValue(0)
        self.dlg.status_label.setText("Ready")
        self.dlg.output_path_input.setText(self.output_manager.get_output_path())
        # Load saved append file path
        saved_append_path = self.output_manager.get_append_file_path()
        if saved_append_path:
            self.dlg.set_append_file_path(saved_append_path)
        self.dlg.show()
