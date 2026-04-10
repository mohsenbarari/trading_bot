#!/usr/bin/env bash
# Download Iran OSM data for offline tileserver-gl if not already present.
set -euo pipefail

MAP_DIR="$(dirname "$0")/../map_data"
PBF_FILE="$MAP_DIR/iran-latest.osm.pbf"
MBTILES_FILE="$MAP_DIR/iran.mbtiles"

mkdir -p "$MAP_DIR"

if [ -f "$MBTILES_FILE" ]; then
    echo "Map data already exists at $MBTILES_FILE — skipping download."
    exit 0
fi

echo "Downloading Iran OSM data..."
wget -O "$PBF_FILE" "https://download.geofabrik.de/asia/iran-latest.osm.pbf"

echo "Download complete: $PBF_FILE"
echo ""
echo "NOTE: To convert PBF to MBTiles for tileserver-gl, run:"
echo "  docker run -v \$(pwd)/map_data:/data openmaptiles/openmaptiles-tools generate-vectortiles /data/iran-latest.osm.pbf /data/iran.mbtiles"
echo ""
echo "Then create map_data/config.json with your tileserver-gl configuration."
