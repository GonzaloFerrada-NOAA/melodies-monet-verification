#!/usr/bin/env python
"""
extract_frame_netcdf.py -- build ONE verification frame from raw netCDF model
output, using a per-model "recipe" (YAML) that says how each species is obtained.
Also provides the recipe engine shared with extract_frame_grib2.py.

Usage:
  extract_frame_netcdf.py --recipe RECIPE.yaml --species aod550,pm25,t2m \
                          --out frame.nc [--lead H] IN1 [IN2 ...]

  * Several input files (e.g. RRFS dynf + phyf) are merged; they must share the grid.
    Inputs may also be given comma-separated in one argument (handy for GNU parallel).
  * Only the requested species are computed, and only the raw variables they
    need are read from disk (xarray opens files lazily).
  * Output keeps 'time' as the record (unlimited) dimension so frames can be
    joined with ncrcat.
  * --lead is accepted for a common interface with the GRIB2 extractor (unused here).

Recipe format (see aux/recipes/*.yaml):
  lat: lat                         # raw latitude / longitude names (default lat, lon)
  lon: lon
  output_format: NETCDF4           # NETCDF4, NETCDF4_CLASSIC, NETCDF3_64BIT, NETCDF3_CLASSIC
  chunks: {pfull: 1}               # optional: stream 3-D fields level by level (dask)
  variables:
    name: "python/xarray expression"
    name: {expr: "...", units: "...", long_name: "..."}
  Expressions can use raw variable names, other recipe entries, and np.
"""
import argparse
import ast
import os
import sys

import numpy as np
import xarray as xr
import yaml

# Names available inside recipe expressions (besides variables / recipe entries)
EVAL_GLOBALS = {"np": np, "xr": xr,
                "__builtins__": {"abs": abs, "min": min, "max": max}}


# ---------------------------------------------------------------------------
# Shared recipe engine
# ---------------------------------------------------------------------------
class RecipeNamespace(dict):
    """Lazy name lookup for eval().

    'source' is anything with a `.variables` container of names and
    `source[name]` returning a DataArray (an xarray Dataset, or the GRIB2 reader).

    When an expression uses a name:
      1) a recipe entry with that name is computed (once, then cached);
      2) otherwise the raw variable from the source is used.
    While an entry is being computed, its own name refers to the RAW variable,
    so 'smoke: smoke * dens' is valid.
    """

    def __init__(self, source, recipe_vars):
        super().__init__()
        self.source = source
        self.recipe = recipe_vars
        self.stack = []                      # entries currently being evaluated

    def __missing__(self, name):             # called only when name not cached
        if name in self.recipe and name not in self.stack:
            self.stack.append(name)
            try:
                val = eval(self.recipe[name]["expr"], EVAL_GLOBALS, self)
            except NameError as err:
                raise NameError(f"in recipe entry '{name}': {err}") from None
            finally:
                self.stack.pop()
            self[name] = val                 # cache computed entry
            return val
        if name in self.source.variables:
            val = self.source[name]
            if name not in self.recipe:      # don't let raw shadow a recipe entry
                self[name] = val
            return val
        raise KeyError(name)                 # eval then tries np/builtins -> NameError


def load_recipe(path):
    """Read recipe YAML; normalize every variable entry to a dict with 'expr'."""
    with open(path) as f:
        recipe = yaml.safe_load(f)
    norm = {}
    for name, spec in (recipe.get("variables") or {}).items():
        norm[name] = {"expr": str(spec)} if not isinstance(spec, dict) else dict(spec)
        norm[name]["expr"] = " ".join(norm[name]["expr"].split())  # allow multi-line
    recipe["variables"] = norm
    return recipe


def parse_args(doc):
    """Command line shared by all extractors."""
    p = argparse.ArgumentParser(description=doc,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--recipe", required=True)
    p.add_argument("--species", required=True, help="comma-separated species names")
    p.add_argument("--out", required=True)
    p.add_argument("--lead", type=int, default=None, help="forecast lead hour")
    p.add_argument("--check", action="store_true",
                   help="only validate recipe + inputs (fast), write nothing")
    p.add_argument("inputs", nargs="+")
    a = p.parse_args()
    a.inputs = [f for arg in a.inputs for f in arg.split(",") if f]
    a.species = [s for s in a.species.split(",") if s]
    return a


def check_species(recipe, species):
    """Exit before reading any data if a requested species has no recipe entry."""
    rvars = recipe["variables"]
    missing = [s for s in species if s not in rvars]
    if missing:
        sys.exit(f"ERROR: species {missing} not defined in recipe\n"
                 f"       available: {sorted(rvars)}\n"
                 f"       -> remove them from 'species' in experiments.yaml "
                 f"or add them to the recipe")


def raw_names_needed(recipe, species):
    """Raw variable names the requested species depend on (static parse of the
    recipe expressions; follows recipe entries recursively). Same resolution rule
    as RecipeNamespace: inside entry X, the name X means the RAW variable X."""
    rvars = recipe["variables"]
    raw, done = set(), set()

    def names_in(expr):
        return {n.id for n in ast.walk(ast.parse(expr, mode="eval"))
                if isinstance(n, ast.Name)}

    def walk(entry, stack):
        for n in names_in(rvars[entry]["expr"]):
            if n in rvars and n not in stack:
                if n not in done:
                    done.add(n)
                    walk(n, stack | {n})
            else:
                raw.add(n)

    for s in species:
        done.add(s)
        walk(s, {s})
    raw |= {recipe.get("lat", "lat"), recipe.get("lon", "lon")}
    builtins = set(EVAL_GLOBALS) | set(EVAL_GLOBALS["__builtins__"])
    return raw - builtins


def evaluate(source, recipe, species, time=None):
    """Compute the requested species (+ recipe coords) into a new Dataset."""
    rvars = recipe["variables"]
    check_species(recipe, species)
    ns = RecipeNamespace(source, rvars)
    out = xr.Dataset()
    for name in species:
        try:
            da = ns[name]
        except (NameError, KeyError) as err:
            sys.exit(f"ERROR computing '{name}': {err}")
        if not isinstance(da, xr.DataArray):
            sys.exit(f"ERROR: recipe entry '{name}' did not return an array")

        da = da.reset_coords(drop=True)      # drop leftovers such as scalar pfull
        if time is not None and "time" not in da.dims:
            da = da.expand_dims(time=time.values)          # e.g. after a time-mean
        da = da.astype("float32")
        da.encoding = {}                     # don't inherit source packing/chunks
        da.attrs = {k: v for k, v in rvars[name].items() if k != "expr"}
        out[name] = da

    if time is not None:                     # time with its source units/attrs
        out = out.assign_coords(time=time.variable)

    lat = source[recipe.get("lat", "lat")].reset_coords(drop=True)
    lon = source[recipe.get("lon", "lon")].reset_coords(drop=True)
    return standardize_grid(out, lat, lon)


LAT_ATTRS = {"units": "degrees_north", "standard_name": "latitude", "long_name": "latitude"}
LON_ATTRS = {"units": "degrees_east", "standard_name": "longitude", "long_name": "longitude"}


def standardize_grid(out, lat, lon):
    """Uniform, CF-style horizontal coordinates (what cdo and MONET expect):
      2-D lat/lon (curvilinear: FV3, Lambert) -> dims (y, x); lat(y,x), lon(y,x)
      1-D lat/lon on two dims (regular)       -> dims (lat, lon) coordinate variables
      1-D lat/lon on one dim (unstructured)   -> dim (ncells); lat(ncells), lon(ncells)
    For curvilinear/unstructured grids lat/lon become auxiliary coordinates, so
    xarray writes  coordinates = "lon lat"  on every field. Native 1-D grid axes
    (e.g. rotated grid_xt/grid_yt, or projection x/y in m) are dropped so cdo
    uses the 2-D lat/lon."""
    if lat.ndim == 2:
        ren = {lat.dims[0]: "y", lat.dims[1]: "x"}
    elif lat.dims != lon.dims:
        ren = {lat.dims[0]: "lat", lon.dims[0]: "lon"}
    else:
        ren = {lat.dims[0]: "ncells"}

    out = out.drop_vars([d for d in ren if d in out.coords])
    to_rename = {k: v for k, v in ren.items() if k != v}   # e.g. GRIB2 y->y: nothing
    if to_rename:
        out = out.rename_dims(to_rename)
    lat_dims = tuple(ren[d] for d in lat.dims)
    lon_dims = tuple(ren[d] for d in lon.dims)
    out = out.assign_coords(lat=(lat_dims, lat.values, LAT_ATTRS),
                            lon=(lon_dims, lon.values, LON_ATTRS))
    for c in ("lat", "lon"):
        out[c].encoding = {"_FillValue": None}       # no fill value on coordinates
    return out


def write_frame(out, path, recipe):
    """Write a frame with 'time' unlimited; temp name + rename = no half files."""
    if "time" in out.coords:
        # Times are kept as raw numbers (files opened with decode_times=False);
        # keep the source units, force a calendar cdo understands (FV3 writes
        # 'JULIAN', which cdo rejects), drop model-specific attributes.
        t = out["time"]
        out["time"].attrs = {"units": t.attrs.get("units", "hours since 1970-01-01"),
                             "calendar": "standard", "standard_name": "time",
                             "long_name": "valid time", "axis": "T"}
        out["time"].encoding = {"dtype": "float64", "_FillValue": None}
    tmp = path + ".tmp"
    out.load().to_netcdf(tmp, format=recipe.get("output_format", "NETCDF4"),
                         unlimited_dims=["time"] if "time" in out.dims else None)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# netCDF driver
# ---------------------------------------------------------------------------
def main():
    a = parse_args(__doc__)
    recipe = load_recipe(a.recipe)
    check_species(recipe, a.species)                 # fail before opening files

    # Optional 'chunks' (e.g. {pfull: 1}) streams 3-D fields level by level with
    # dask: peak memory ~ a few 2-D fields. Synchronous scheduler = one thread per
    # job (GNU parallel provides the parallelism).
    chunks = recipe.get("chunks")
    if chunks:
        import dask
        dask.config.set(scheduler="synchronous")

    # Open lazily and merge; duplicates (lat, lon, time, ...) taken from first file
    # decode_times=False: keep time as numbers + units (avoids JULIAN cftime objects)
    dsets = [xr.open_dataset(f, chunks=chunks, decode_times=False) for f in a.inputs]
    ds = xr.merge(dsets, compat="override", join="override", combine_attrs="override")

    # All raw variables needed must exist in the inputs (cheap: metadata only)
    absent = sorted(n for n in raw_names_needed(recipe, a.species) if n not in ds.variables)
    if absent:
        sys.exit(f"ERROR: variables {absent} needed by the recipe are not in {a.inputs}")
    if a.check:
        print(f"Recipe check OK: {a.inputs}")
        return

    time = ds["time"] if "time" in ds.dims else None
    out = evaluate(ds, recipe, a.species, time)
    write_frame(out, a.out, recipe)
    for d in dsets:
        d.close()


if __name__ == "__main__":
    main()