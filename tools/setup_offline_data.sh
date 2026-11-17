#!/bin/bash
#==============================================================================
# setup_offline_data.sh -- ONE-TIME setup, run on a LOGIN node (needs internet).
# Pre-downloads the Natural Earth shapefiles that cartopy (used by
# MELODIES-MONET spatial plots) would otherwise try to fetch at plot time,
# which fails on compute nodes without internet access.
#
# Usage:  ./setup_offline_data.sh [DEST_DIR]
#   DEST_DIR default: <repo>/aux/cartopy   (must match CARTOPY_DATA_DIR in mm.settings)
#   Python  : ${PYTHON} if set, else ${CONDA_ENV}/bin/python, else python on PATH
#
# Safe to re-run: files already present are skipped by cartopy.
#==============================================================================
set -euo pipefail

CDATE=2026010100
source ../mm.settings

# repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)    # script lives in <repo>/tools
# dest=${1:-${repo_dir}/aux/cartopy}
repo_dir=${MELODIES_MONET_DIR}
dest=${CARTOPY_DATA_DIR}
py=${PYTHON:-${CONDA_ENV:+${CONDA_ENV}/bin/python}}
py=${py:-python}

mkdir -p "${dest}"
echo "Python  : ${py}"
echo "Dest dir: ${dest}"

# Python part: point cartopy's download dir at DEST and request each feature.
"${py}" - "${dest}" <<'EOF'
import sys
import cartopy
from cartopy.io import shapereader

dest = sys.argv[1]
cartopy.config["data_dir"] = dest          # downloads go here (same layout cartopy reads)
print(f"cartopy {cartopy.__version__}")

# (category, name) of features used by map plots (coastlines, borders, states, lakes, ...)
features = [
    ("physical", "coastline"),
    ("physical", "land"),
    ("physical", "ocean"),
    ("physical", "lakes"),
    ("physical", "rivers_lake_centerlines"),
    ("cultural", "admin_0_boundary_lines_land"),
    ("cultural", "admin_0_countries"),
    ("cultural", "admin_1_states_provinces_lakes"),
    ("cultural", "admin_1_states_provinces_lines"),
]
resolutions = ["10m", "50m", "110m"]

n_ok, n_skip = 0, 0
for res in resolutions:
    for cat, name in features:
        try:
            path = shapereader.natural_earth(resolution=res, category=cat, name=name)
            print(f"  ok   {res:>4} {cat}/{name}")
            n_ok += 1
        except Exception as e:             # not every feature exists at every resolution
            print(f"  skip {res:>4} {cat}/{name}  ({type(e).__name__})")
            n_skip += 1
print(f"Done: {n_ok} available, {n_skip} skipped")
EOF

echo ""
echo "Make sure mm.settings exports:  CARTOPY_DATA_DIR=${dest}"
