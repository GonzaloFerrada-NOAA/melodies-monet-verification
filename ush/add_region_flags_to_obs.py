#!/usr/bin/env python
"""
add_region_flags.py -- add site region flags to a MELODIES-MONET obs file
(replaces Jordan's make_westoreastof97_<dataset>.ncl scripts).

Usage:
  add_region_flags.py FILE DATASET        # DATASET: aeronet, airnow, aqs, ish-lite, ...

Adds (same dims as 'siteid'):
  westoreastof97 : "west" if longitude <= -97, else "east"     (all datasets)
  isinconus      : "CONUS" inside 25-49N, 125-67W, else "NOCON" (ish-lite only)
The file is rewritten in place.
"""
import os
import sys

import numpy as np
import xarray as xr

fin, dataset = sys.argv[1], sys.argv[2].lower()

with xr.open_dataset(fin) as ds:
    ds = ds.load()                                   # read all, then close the file

dims = ds["siteid"].dims                             # flags follow siteid's shape
lon = ds["longitude"].values
lat = ds["latitude"].values

ds["westoreastof97"] = (dims, np.where(lon <= -97.0, "west", "east"))

if dataset == "ish-lite":
    conus = (lon >= -125.0) & (lon <= -67.0) & (lat >= 25.0) & (lat <= 49.0)
    ds["isinconus"] = (dims, np.where(conus, "CONUS", "NOCON"))

# Write to a temp file, then replace the original
tmp = fin + ".tmp"
ds.to_netcdf(tmp)
os.replace(tmp, fin)
print(f"Added region flags to {fin}")
