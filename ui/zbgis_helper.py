# -*- coding: utf-8 -*-
"""
ZBGIS integration helper for the Kataster plugin.
"""

import math
from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.core import (
    QgsProject,
    QgsMessageLog,
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
)


def open_zbgis(iface):
    """
    Open zbgis.skgeodesy.sk with current map extent coordinates.

    Args:
        iface: QGIS interface
    """
    try:
        canvas = iface.mapCanvas()
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
        iface.messageBar().pushMessage("ZBGIS", f"Opening at zoom {zoom}", Qgis.Info, 3)

    except Exception as e:
        QgsMessageLog.logMessage(f"Error opening ZBGIS: {e}", "Kataster", Qgis.Critical)
        iface.messageBar().pushMessage("Error", f"Failed to open ZBGIS: {e}", Qgis.Critical, 5)
