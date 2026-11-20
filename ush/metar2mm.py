#!/usr/bin/env python
"""
metar2mm.py -- convert hourly MADIS METAR netCDF files into a MELODIES-MONET
point-surface (pt_sfc) obs file, same layout as get-airnow / get-aeronet output.

Usage:
  metar_to_mm.py INDIR START END OUTFILE
    INDIR   : directory with MADIS files named YYYYMMDD_HH00.nc
    START   : first valid hour (UTC), YYYYMMDDHH
    END     : last valid hour (UTC),  YYYYMMDDHH
    OUTFILE : e.g. metar.20260714.nc

Output dims: time, y=1, x=nsites
  siteid(x), latitude(x), longitude(x), x(x)
  temperature, dewpoint, windSpeed, windDir, precip1Hour, visibility  (time, y, x)
  (MADIS names kept; units converted to C, mm to match ish-lite)
Missing values are NaN (not -1, since -1 C is a valid temperature).
"""
import os
import sys

import numpy as np
import pandas as pd
import xarray as xr

indir, start, end, fout = sys.argv[1:5]
times = pd.date_range(pd.to_datetime(start, format="%Y%m%d%H"),
                      pd.to_datetime(end, format="%Y%m%d%H"), freq="h")

# MADIS names are kept; only units are converted to match the ish-lite/model units
# MADIS name -> (unit conversion, output units)
VARS = {
    "temperature": (lambda v: v - 273.15, "C"),        # K -> C
    "dewpoint":    (lambda v: v - 273.15, "C"),        # K -> C
    "windSpeed":   (lambda v: v,          "M/S"),
    "windDir":     (lambda v: v,          "DEGREES"),
    "precip1Hour": (lambda v: v * 1000.0, "MM"),       # m -> mm
    "visibility":  (lambda v: v,          "M"),        # meters, as in ISH
}
BAD_QC = ["X", "Q", "B", "k"]   # MADIS DD flags rejected (only if <var>DD was extracted)
BOX = (0.0, -179.0, 90.0, 0.0)  # lat0, lon0, lat1, lon1 -- same box used for ish-lite


def read_hour(t):
    """One MADIS file -> DataFrame with one row per station, valid at hour t."""
    f = os.path.join(indir, t.strftime("%Y%m%d_%H00"))
    if not os.path.exists(f):
        print(f"WARNING: missing {f}")
        return None
    with xr.open_dataset(f) as ds:     # _FillValue (3.4e38) is decoded to NaN here
        df = pd.DataFrame({
            "siteid":    np.char.strip(ds["stationName"].values.astype(str)),  # char(recNum,5) -> 'KDEN'
            "latitude":  ds["latitude"].values.astype("f8"),
            "longitude": ds["longitude"].values.astype("f8"),
        })
        for mv, (conv, _) in VARS.items():
            if mv not in ds:
                continue
            v = conv(ds[mv].values.astype("f8"))
            if mv + "DD" in ds:        # optional QC screening
                v[np.isin(ds[mv + "DD"].values.astype(str), BAD_QC)] = np.nan
            df[mv] = v
        # Several reports per station per file (routine + SPECI): keep the one closest
        # to the top of the hour if timeObs is available, otherwise the first one.
        if "timeObs" in ds:
            df["dt"] = np.abs(ds["timeObs"].values - np.datetime64(t))
            df = df.sort_values("dt").drop(columns="dt")
    df = df[df["siteid"] != ""].drop_duplicates("siteid", keep="first")
    df["time"] = t
    return df


obs = pd.concat([d for d in (read_hour(t) for t in times) if d is not None], ignore_index=True)

# Wind direction is undefined for calm winds
if {"windSpeed", "windDir"} <= set(obs.columns):
    obs.loc[obs["windSpeed"] == 0, "windDir"] = np.nan

# Station table: first position seen for each station, inside BOX
sites = obs.drop_duplicates("siteid").set_index("siteid")[["latitude", "longitude"]].sort_index()
lat0, lon0, lat1, lon1 = BOX
sites = sites[sites.latitude.between(lat0, lat1) & sites.longitude.between(lon0, lon1)]
obs = obs[obs["siteid"].isin(sites.index)]
nx = len(sites)

# Build the (time, y, x) dataset
out = xr.Dataset(coords={"time": times, "x": np.arange(nx, dtype="int64")})
coord_attr = "latitude longitude siteid"
for name, (_, units) in VARS.items():
    if name not in obs:
        continue
    arr = (obs.pivot(index="time", columns="siteid", values=name)    # time x station
              .reindex(index=times, columns=sites.index).values)       # fill gaps with NaN
    out[name] = (("time", "y", "x"), arr[:, None, :])
    out[name].attrs = {"units": units, "coordinates": coord_attr}

out["siteid"] = ("x", sites.index.values.astype(object))   # written as netCDF 'string'
out["latitude"] = ("x", sites.latitude.values)
out["longitude"] = ("x", sites.longitude.values)
out.attrs["title"] = "MADIS METAR converted for MELODIES-MONET (pt_sfc)"

# Encoding to match the CLI-generated obs files
enc = {v: {"_FillValue": np.nan} for v in out.data_vars if out[v].dtype.kind == "f"}
enc["time"] = {"units": f"hours since {times[0]:%Y-%m-%d}",
               "calendar": "proleptic_gregorian", "dtype": "int64"}
out.to_netcdf(fout, encoding=enc)
print(f"Wrote {fout}: {len(times)} times x {nx} sites")
