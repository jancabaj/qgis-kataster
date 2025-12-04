# Kataster SR - QGIS Plugin

A QGIS plugin for loading cadastral parcel data from the official Slovak Cadastre OGC Features API.

## Features

- Load cadastral parcels by cadastre name or 6-digit code
- Support for multiple layer types:
  - Cadastral Parcels - C Register
  - Cadastral Parcels - E Register
  - Cadastral Zoning (boundaries)
- Configurable output folder with persistent settings
- Optional coordinate transformation from EPSG:4258 to EPSG:5514 (S-JTSK Krovak)
- Automatic merging of layers into single GeoPackage
- Pre-configured styling for all layers
- Automatic handling of corrupt coordinate data

## Installation

### Plugin Installation

1. Download the plugin zip file
2. In QGIS, go to Plugins → Manage and Install Plugins
3. Select "Install from ZIP" and choose the downloaded file
4. Enable the plugin

### Python Dependencies

This plugin requires the `requests` library, which is typically included with QGIS by default. QGIS comes with Python built-in, so you don't need to install Python separately.

**Only if you get an error about missing `requests` module (rare):**

**Windows:**
1. Open OSGeo4W Shell (Start Menu → QGIS → OSGeo4W Shell)
2. Run: `py3_env`
3. Run: `python -m pip install requests`

**Linux/Mac:**
1. Open terminal
2. Run: `python3 -m pip install --user requests`

Or install via QGIS Python console:
1. In QGIS, go to Plugins → Python Console
2. Run: `import subprocess; subprocess.check_call(['python', '-m', 'pip', 'install', 'requests'])`

## Usage

1. Click the Kataster SR icon in the toolbar
2. Enter a cadastre name (e.g., "Nitra") or 6-digit code (e.g., "815713")
3. Choose output folder (defaults to plugin's KN directory)
4. Select which layers to fetch
5. Choose whether to transform coordinates to EPSG:5514
6. Click "Run"

The plugin will:
- Download data from the Slovak Cadastre API
- Fix any coordinate issues automatically
- Merge layers into a single GeoPackage file
- Apply pre-configured styles
- Load layers into your QGIS project

## Data Source

Data is loaded from the official Slovak Cadastre INSPIRE WFS service:
- https://inspirews.skgeodesy.sk/geoserver/cp/ogc/features/v1 (C Register)
- https://inspirews.skgeodesy.sk/geoserver/cp_uo/ogc/features/v1 (E Register)

## Requirements

- QGIS 3.0 or higher (includes Python 3.6+)
- Python `requests` library
- Internet connection

## License

GPL v2 or later

## Author

Jan Cabaj (jan807931@gmail.com)

## Issues

Report issues at: https://github.com/jancabaj/qgis-kataster/issues
