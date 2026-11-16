#!/usr/bin/env python
"""
extract_frame_grib2.py -- build ONE verification frame from GRIB2 model output.
Same interface and recipe engine as extract_frame_netcdf.py; raw fields are
pulled from GRIB2 with wgrib2 (must be in PATH).

Usage:
  extract_frame_grib2.py --recipe RECIPE.yaml --species aod550,pm25,t2m \
                         --out frame.nc --lead H [--check] IN1.grib2 [IN2.grib2 ...]

GRIB2 recipes add a 'fields' section that defines the raw 2-D fields:
  fields:
    t2:     ':TMP:2 m above ground:'               # regex on 'wgrib2 -s' inventory
    apcp1h: {match: ':APCP:surface:{lead_m1}-{lead} hour acc', optional: true}
  * Each pattern must match exactly ONE record.
    Test a pattern with:   wgrib2 file.grib2 -s | grep -E 'pattern'
  * {lead} / {lead_m1} are replaced by the forecast hour and hour-1 (--lead).
  * optional: true -> all-NaN field when nothing matches (e.g. APCP at f000).
  * 'lat' and 'lon' are always available (from the GRIB2 grid).
Before extracting anything, every field needed by the requested species is
matched against the inventory; all problems are reported at once.
--check stops after that validation (used once by mm_prep_model_data.sh).
"""
import os
import re
import subprocess
import sys
import tempfile

import numpy as np
import xarray as xr

# Shared recipe engine (same directory)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract_frame_netcdf import (check_species, evaluate, load_recipe,
                                  parse_args, raw_names_needed, write_frame)


def run(cmd, stdin=None):
    """Run a command; on failure print its stderr and exit."""
    res = subprocess.run(cmd, input=stdin, text=True, capture_output=True)
    if res.returncode != 0:
        sys.exit(f"ERROR running {' '.join(cmd)}:\n{res.stderr}")
    return res.stdout


class GribSource:
    """Lazy GRIB2 reader: looks like a Dataset to the recipe engine.

    `name in src.variables` and `src[name]` -> DataArray (time, y, x).
    resolve() maps field names to inventory records (no data read);
    a field is extracted with wgrib2 only the first time it is requested.
    """

    def __init__(self, files, fields, workdir, lead):
        self.fields = fields
        self.workdir = workdir
        self.lead = lead
        self.records = {}                    # field -> (file, inv line) or None
        self.cache = {}
        self._grid = None
        self.variables = set(fields) | {"lat", "lon"}

        # Inventory of all records: list of (file, 'wgrib2 -s' line)
        self.inv = []
        for f in files:
            self.inv += [(f, ln) for ln in run(["wgrib2", f, "-s"]).splitlines() if ln]
        if not self.inv:
            sys.exit(f"ERROR: no GRIB2 records in {files}")

    def pattern(self, name):
        pat = self.fields[name]["match"]
        if self.lead is not None:
            pat = pat.replace("{lead_m1}", str(self.lead - 1)).replace("{lead}", str(self.lead))
        return pat

    def resolve(self, names):
        """Match every needed field to exactly one record; report ALL problems."""
        errors = []
        for name in sorted(names):
            if name in ("lat", "lon"):
                continue
            if name not in self.fields:
                errors.append(f"'{name}' is used in the recipe but not defined in 'fields'")
                continue
            pat = self.pattern(name)
            hits = [(f, ln) for f, ln in self.inv if re.search(pat, ln)]
            if len(hits) == 1:
                self.records[name] = hits[0]
            elif not hits and self.fields[name].get("optional"):
                self.records[name] = None                      # -> NaN field
            elif not hits:
                errors.append(f"field '{name}': no record matches '{pat}'")
            else:
                lines = "\n      ".join(ln for _, ln in hits)
                errors.append(f"field '{name}': '{pat}' matches {len(hits)} records:\n      {lines}")
        if errors:
            sys.exit(f"ERROR in recipe fields for {sorted({f for f, _ in self.inv})}:\n  - "
                     + "\n  - ".join(errors))

    def _read(self, grib, inv_line, tag):
        """Convert ONE record (given by its inventory line) to netCDF and load it."""
        nc = os.path.join(self.workdir, f"{tag}.nc")
        # -i: record list from stdin -> wgrib2 seeks straight to it (fast)
        run(["wgrib2", grib, "-i", "-nc_nlev", "1", "-set", "center", "7",
             "-netcdf", nc], stdin=inv_line + "\n")
        with xr.open_dataset(nc, decode_times=False) as ds:   # time as numbers + units
            return ds.load()

    def grid(self):
        """lat, lon and valid time, taken from the first record (read once)."""
        if self._grid is None:
            self._grid = self._read(*self.inv[0], tag="_grid")
        return self._grid

    @property
    def time(self):
        return self.grid()["time"]

    def __getitem__(self, name):
        if name == "lat":
            return self.grid()["latitude"]
        if name == "lon":
            return self.grid()["longitude"]
        if name not in self.cache:
            self.cache[name] = self._field(name)
        return self.cache[name]

    def _field(self, name):
        if name not in self.records:          # not pre-resolved (shouldn't happen)
            self.resolve({name})
        rec = self.records[name]
        if rec is None:                        # optional field, no record -> NaN
            nan = xr.full_like(self.grid()["latitude"], np.nan, dtype="float32")
            return nan.reset_coords(drop=True).expand_dims("time").assign_coords(
                time=self.time.variable)

        ds = self._read(*rec, tag=name)
        # The data variable: has a time dim and is not lat/lon
        var = [v for v in ds.data_vars
               if v not in ("latitude", "longitude") and "time" in ds[v].dims][0]
        da = ds[var]
        da = da.squeeze([d for d in da.dims if d != "time" and da.sizes[d] == 1])
        # Same time stamp for every field (accumulations can carry a different one)
        return da.assign_coords(time=self.time.variable)


def main():
    a = parse_args(__doc__)
    recipe = load_recipe(a.recipe)
    check_species(recipe, a.species)                 # fail before scanning GRIB2
    fields = {k: (v if isinstance(v, dict) else {"match": str(v)})
              for k, v in (recipe.get("fields") or {}).items()}

    # Scratch dir for the per-field netCDFs, next to the output; auto-deleted
    outdir = os.path.dirname(os.path.abspath(a.out))
    with tempfile.TemporaryDirectory(dir=outdir, prefix=".grib2_") as wd:
        src = GribSource(a.inputs, fields, wd, a.lead)
        src.resolve(raw_names_needed(recipe, a.species))   # validate before any extraction
        if a.check:
            print(f"Recipe check OK: {a.inputs}")
            return
        out = evaluate(src, recipe, a.species, src.time)
        write_frame(out, a.out, recipe)


if __name__ == "__main__":
    main()