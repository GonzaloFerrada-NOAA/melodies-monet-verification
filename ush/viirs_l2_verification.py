import sys, os
import re
import csv
import glob
import argparse
import warnings
from types import SimpleNamespace
import numpy as np
import netCDF4 as nc
from datetime import datetime, timedelta
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
import matplotlib.font_manager as fm

sys.path.insert(0, "/scratch3/BMC/gsd-fv3-dev/Gonzalo.Ferrada/.HOME/PYTHON/shared-packages/pyearthsciences")
import src as es
from src.ll2lamb import ll2lamb

# =============================================================================
# Model vs. VIIRS (L2 aggregated, time-resolved) AOD verification.
# Same workflow/arguments as tempo_aod_verification.py.
#
# Verification period: --date-start .. --date-end (BOTH inclusive). The output time axis
# is the regular sequence date_start, date_start + dt, ..., <= date_end (dt = VIIRS file
# time step), so the output always has the same shape: VIIRS/model times that aren't
# available are written as NaN (with a warning) instead of being dropped. If the period
# goes past --viirs-file, sibling files of the same product/grid in the same directory
# are used for the missing times. Each model value is the mean of the model steps within
# [t - dt/2, t + dt/2) of the VIIRS time t.
#
# Usage (see --help):
#   # 1) save one model's paired NetCDF
#   viirs_verification.py --save-netcdf --date-start 2026-10-28_12:00:00 --date-end 2026-10-29_12:00:00 \
#       --model-name MPAS-Aerosols --file-model aod_MPAS-Aerosols_2026102700.nc --model-var AOD550,AOD550_SIMPLE \
#       --viirs-file viirs_all_aggregated_1h_0p1deg_2026102812.nc --viirs-var aod550 \
#       --file-out viirs_MPAS-Aerosols_2026102700.nc
#   # 2) plot several models' saved NetCDFs together
#   viirs_verification.py --make-figure --date-start 2026-10-28_12:00:00 --date-end 2026-10-29_12:00:00 \
#       --model-names RRFS-SD MPAS-Aerosols --plot-files viirs_RRFS-SD_2026102700.nc viirs_MPAS-Aerosols_2026102700.nc \
#       --path-fig ./figs --regions-csv lambert_map_settings_viirs.csv
#
# Saved variables: viirs_aod(time,lat,lon) and mean_viirs_aod(lat,lon) with ALL available
# VIIRS data; model_aod / mean_model_aod for the first --model-var, model_aod_<suffix> /
# mean_model_aod_<suffix> for each extra (AOD550_SIMPLE -> _simple), kept only where VIIRS
# is valid. Plots: one column per mean_model_aod* field (aod_model -> "NAME",
# _simple -> "NAME (SIMPLE)"); the CONUS figure has two rows, North America over CONUS.
# =============================================================================

NO_DATA_MSG = "No data available for current times"
METRICS_TO_SHOW = ["R", "MB", "cRMSE"]
REGIONS = ["CONUS", "R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "R10"]

font = fm.FontProperties(fname="/scratch3/BMC/acomp/Gonzalo.Ferrada/verif/realtime/sandbox/OpenSans-Regular.ttf")
for f in fm.findSystemFonts(fontpaths=["/scratch3/BMC/acomp/Gonzalo.Ferrada/verif/realtime/sandbox/Google_Sans_Code/static"]):
    fm.fontManager.addfont(f)
fmono = fm.FontProperties(family="Google Sans Code", size=5, weight="light")

# Levels
lev_aod      = np.concatenate([[0, 0.05, 0.1, 0.15], np.arange(2, 11) / 10.0, [1.2, 1.5, 2.0]])
lev_bias_pos = np.concatenate([[0.05], np.arange(1, 7) / 10.0])
lev_bias     = np.sort(np.concatenate([-lev_bias_pos, lev_bias_pos]))

# Colormaps
cm1 = es.hue([227,227,227], [136,183,211], 5)
cm2 = es.hue([85,180,94],[251,232,135],[252,170,95],[242,114,69],[194,27,38],[135,27,87],[255,192,203], len(lev_aod) - 1 - 5)
cmap_aod  = ListedColormap(np.vstack((cm1.colors, cm2.colors)))
cmap_bias = es.hue('pan', len(lev_bias) - 1)


# =============================================================================
# Arguments
# =============================================================================
def parse_args():
    p = argparse.ArgumentParser(description="Model vs. VIIRS aggregated AOD verification (save paired NetCDF and/or plot).",
                                formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--date-start", required=True, help="first verification time (inclusive), YYYY-MM-DD_hh:mm:ss")
    p.add_argument("--date-end", required=True, help="last verification time (inclusive), YYYY-MM-DD_hh:mm:ss")
    p.add_argument("--save-netcdf", action="store_true", help="pair one model with VIIRS and save the NetCDF")
    p.add_argument("--make-figure", action="store_true", help="plot the saved NetCDF(s) of one or more models")

    s = p.add_argument_group("--save-netcdf options")
    s.add_argument("--model-name", help="model name (crop box, log and attributes)")
    s.add_argument("--file-model", help="model AOD file, already CDO-regridded onto the VIIRS grid")
    s.add_argument("--model-var", default="aod550",
                   help="AOD variable(s) in --file-model, comma-separated, e.g. 'AOD550,AOD550_SIMPLE'. "
                        "The first is saved as model_aod, each extra as model_aod_<suffix>")
    s.add_argument("--viirs-file", help="VIIRS aggregated file (viirs_*_aggregated_*.nc)")
    s.add_argument("--viirs-var", default="aod550", help="VIIRS AOD variable, e.g. aod550, aod550_median")
    s.add_argument("--file-out", help="output NetCDF (paired model/VIIRS AOD)")

    g = p.add_argument_group("--make-figure options")
    g.add_argument("--model-names", nargs="+", help="model names, one per --plot-files entry (column titles)")
    g.add_argument("--plot-files", nargs="+", help="NetCDFs written by --save-netcdf, one per model")
    g.add_argument("--path-fig", default="./", help="output directory for the figures")
    g.add_argument("--regions-csv", help="lambert_map_settings_viirs.csv with the plotting regions (incl. North_America)")

    args = p.parse_args()
    if not (args.save_netcdf or args.make_figure):
        p.error("nothing to do: pass --save-netcdf and/or --make-figure")
    if args.save_netcdf:
        missing = [o for o, v in (("--model-name", args.model_name), ("--file-model", args.file_model),
                                  ("--viirs-file", args.viirs_file), ("--file-out", args.file_out)) if not v]
        if missing:
            p.error(f"--save-netcdf requires {', '.join(missing)}")
    if args.make_figure:
        if not (args.model_names and args.plot_files and args.regions_csv):
            p.error("--make-figure requires --model-names, --plot-files and --regions-csv")
        if len(args.model_names) != len(args.plot_files):
            p.error(f"--model-names ({len(args.model_names)}) and --plot-files ({len(args.plot_files)}) counts must match")
    return args


def parse_time(value):
    """YYYY-MM-DD_hh:mm:ss (also accepts 'YYYY-MM-DD hh:mm:ss')."""
    for fmt in ("%Y-%m-%d_%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError(f"Time {value!r} must look like 2026-10-28_12:00:00")


def var_suffix(var, primary):
    """'' for the primary variable; '_<suffix>' for extras, where suffix is the variable
    name with the primary's name + '_' stripped, lower case:
        AOD550_SIMPLE (primary AOD550) -> '_simple'      MIE_AOD -> '_mie_aod'"""
    if var == primary:
        return ""
    s = var[len(primary) + 1:] if var.startswith(primary + "_") else var
    return f"_{s.lower()}"


def column_label(model_name, suffix):
    """'' -> 'MPAS-Aerosols'; '_simple' -> 'MPAS-Aerosols (SIMPLE)';
    '_mie_calculation' -> 'MPAS-Aerosols (MIE CALCULATION)'."""
    return f"{model_name} ({suffix[1:].replace('_', ' ').upper()})" if suffix else model_name


# =============================================================================
# Grid / time helpers
# =============================================================================
def is_valid_file(filepath):
    """Returns True if filepath points to a real, usable file (i.e. not "None"/empty/missing)."""
    return bool(filepath) and filepath.lower() != "none" and os.path.isfile(filepath)


def crop_model_boundaries(model):
    """(west, east, south, north) box in degrees to crop a model/VIIRS pair to."""
    match model:
        case "HRRR-Smoke":
            return -140.0, -50.0, 15.0, 55.0
        case "MPAS-Aerosols":
            return -160.0, -40.0, 10.0, 90.0
        case "RAP-Smoke":
            return -180.0, 180.0, -20.0, 90.0
        case _:
            return -180.0, 180.0, 0.0, 90.0


def crop_slice(coord, lo, hi):
    """Contiguous slice of a monotonic increasing 1D coordinate within [lo, hi]."""
    idx = np.flatnonzero((coord >= lo) & (coord <= hi))
    if idx.size == 0:
        raise ValueError(f"No coordinates found within [{lo}, {hi}]")
    return slice(idx[0], idx[-1] + 1)


def _read_times(time_var):
    """NetCDF time variable -> array of python datetimes (uses its own units/calendar)."""
    if not hasattr(time_var, "units"):
        raise ValueError(f"Time variable '{time_var.name}' has no 'units' attribute")
    return np.array(nc.num2date(time_var[:], units=time_var.units,
                                calendar=getattr(time_var, "calendar", "standard"),
                                only_use_cftime_datetimes=False, only_use_python_datetimes=True))


def _to_nan(arr):
    """Masked array -> float32 ndarray with NaN for masked/fill values."""
    return np.ma.filled(np.ma.asarray(arr, dtype=np.float32), np.nan)


def build_period_axis(date_start, date_end, dt):
    """date_start, date_start + dt, ..., <= date_end (both ends inclusive)."""
    times, t = [], date_start
    while t <= date_end:
        times.append(t)
        t += dt
    return np.array(times)


def time_mean(arr):
    """NaN-ignoring mean over time (axis 0), silent for all-NaN cells."""
    cnt = np.isfinite(arr).sum(axis=0)
    tot = np.nansum(arr, axis=0)
    return np.where(cnt > 0, tot / np.maximum(cnt, 1), np.nan).astype(np.float32)


# =============================================================================
# VIIRS
# =============================================================================
def viirs_sibling_files(viirs_file, date_start, date_end):
    """Other aggregated files in viirs_file's directory that may hold dates in the period.
    Pattern viirs_*_aggregated_*YYYYMMDD*.nc for each day from date_start-1 to date_end,
    restricted to the same product (same name once the date stamp is removed), so a
    0.25-deg/3-h file is never mixed with a 0.1-deg/1-h one."""
    folder = os.path.dirname(os.path.abspath(viirs_file))
    stem = lambda f: re.sub(r"\d{8,10}(?=\.nc$)", "", os.path.basename(f))   # drop date stamp
    target = stem(viirs_file)
    found, day = [], (date_start - timedelta(days=1)).replace(hour=0, minute=0, second=0)
    while day <= date_end:
        for f in glob.glob(os.path.join(folder, f"viirs_*_aggregated_*{day:%Y%m%d}*.nc")):
            if stem(f) == target and os.path.abspath(f) != os.path.abspath(viirs_file) and f not in found:
                found.append(f)
        day += timedelta(days=1)
    return sorted(found)


def read_viirs(viirs_file, var, date_start, date_end):
    """VIIRS AOD on the inclusive period axis (NaN where no file holds that time).
    Uses viirs_file first, then sibling files for times it doesn't cover.
    Returns lon, lat, period times, aod(time, lat, lon), files used, satellites, dt."""
    with nc.Dataset(viirs_file) as ds:
        t_main = _read_times(ds["time"])
        lon, lat = ds["lon"][:].astype(np.float64), ds["lat"][:].astype(np.float64)
    dt = (t_main[1] - t_main[0]) if len(t_main) > 1 else timedelta(hours=1)
    period = build_period_axis(date_start, date_end, dt)
    if len(period) == 0:
        raise ValueError("Empty verification period: --date-end must be >= --date-start.")

    files = [viirs_file]
    if not set(period) <= set(t_main):                     # main file doesn't span the period
        extra = viirs_sibling_files(viirs_file, date_start, date_end)
        if extra:
            es.msg(f"  Period extends past {os.path.basename(viirs_file)}; also using: "
                   + ", ".join(os.path.basename(f) for f in extra))
            files += extra
        else:
            es.msg(f"  [WARN] no other VIIRS files found in {os.path.dirname(viirs_file)} for "
                   f"{date_start} - {date_end}; continuing with {os.path.basename(viirs_file)} only")

    aod = np.full((len(period), lat.size, lon.size), np.nan, dtype=np.float32)
    filled = np.zeros(len(period), dtype=bool)
    pos = {t: i for i, t in enumerate(period)}
    sources = []                                           # satellite_source of the files used
    for path in files:
        with nc.Dataset(path) as ds:
            if not (np.allclose(ds["lon"][:], lon) and np.allclose(ds["lat"][:], lat)):
                es.msg(f"  [WARN] {os.path.basename(path)} has a different grid, skipped")
                continue
            used = False
            for it, ti in enumerate(_read_times(ds["time"])):
                i = pos.get(ti)
                if i is not None and not filled[i]:        # first file wins on overlap
                    aod[i] = _to_nan(ds[var][it])
                    filled[i] = used = True
            src = getattr(ds, "satellite_source", "")
            if used and src and src != "none" and src not in sources:
                sources.append(src)

    if not filled.all():
        es.msg(f"  [WARN] {int((~filled).sum())} of {len(period)} VIIRS time(s) in the period are not "
               "available in any file; filled with NaN")
    return lon, lat, period, aod, files, ", ".join(sources), dt


# =============================================================================
# Model
# =============================================================================
def read_model(file_model, model_vars, viirs_lon, viirs_lat, viirs_times, dt):
    """Reads the model variable(s) on the VIIRS grid and averages their steps inside each
    VIIRS window [t - dt/2, t + dt/2). Returns {var: aod(time, lat, lon)} aligned to
    viirs_times (NaN where the model has no step in the window) and the (row, col) slices
    of the VIIRS grid the model covers. Extra variables missing from the file are skipped
    with a warning (a missing primary is an error)."""
    with nc.Dataset(file_model) as ds:
        for name in ("time", "XTIME", "Time"):
            if name in ds.variables:
                time_var = ds[name]
                break
        else:
            raise KeyError(f"Could not find 'time', 'XTIME', or 'Time' in {file_model}")
        mtimes = _read_times(time_var)

        lon = ds["lon"][:].astype(np.float64)
        lat = ds["lat"][:].astype(np.float64)
        lon = np.where(lon >= 180.0, lon - 360.0, lon)           # 0..360 -> -180..180
        flip_lat = lat[0] > lat[-1]                              # cdo may write N -> S
        if flip_lat:
            lat = lat[::-1]

        # Model grid must be the VIIRS grid (or a sub-box of it)
        res = abs(viirs_lon[1] - viirs_lon[0])
        xs = crop_slice(viirs_lon, lon.min() - res / 4, lon.max() + res / 4)
        ys = crop_slice(viirs_lat, lat.min() - res / 4, lat.max() + res / 4)
        if (viirs_lon[xs].size != lon.size or viirs_lat[ys].size != lat.size
                or not np.allclose(viirs_lon[xs], lon, atol=res / 100)
                or not np.allclose(viirs_lat[ys], lat, atol=res / 100)):
            raise ValueError(f"{file_model}: lon/lat don't match the VIIRS grid -- regrid it first")

        half = dt / 2
        windows = [np.flatnonzero((mtimes >= t - half) & (mtimes < t + half)) for t in viirs_times]
        n_missing = sum(idx.size == 0 for idx in windows)
        if n_missing:
            es.msg(f"  [WARN] {n_missing} of {len(viirs_times)} period time(s) have no model step "
                   f"(model steps {mtimes[0]} - {mtimes[-1]}); filled with NaN")

        out = {}
        for k, var in enumerate(model_vars):
            if var not in ds.variables:
                if k == 0:
                    raise KeyError(f"Primary model AOD variable '{var}' not found in {file_model}")
                es.msg(f"  [WARN] '{var}' not found in {file_model}, skipping it")
                continue
            v = ds[var]
            if v.shape[0] != len(mtimes):
                raise ValueError(f"{file_model}: '{time_var.name}' has {len(mtimes)} steps but {var} has {v.shape[0]}")

            aod = np.full((len(viirs_times), lat.size, lon.size), np.nan, dtype=np.float32)
            for it, idx in enumerate(windows):
                if idx.size == 0:
                    continue                                     # model doesn't reach this time
                steps = _to_nan(v[idx])
                steps = steps.reshape(idx.size, *steps.shape[-2:])   # drop singleton level dims
                if idx[0] == 0 and np.nansum(steps[0]) == 0:
                    # Spin-up artifact: some models write all-zero AOD at the first step
                    es.msg(f"  First model time step has zero {var} everywhere, setting to NaN")
                    steps[0] = np.nan
                with np.errstate(invalid="ignore"), warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN cells
                    aod[it] = np.nanmean(steps, axis=0)
            out[var] = aod[:, ::-1, :] if flip_lat else aod

    return out, ys, xs


# =============================================================================
# Output NetCDF
# =============================================================================
def write_output_netcdf(filepath, lon, lat, times, viirs, mean_viirs, models, attrs):
    """models: {suffix: (source_var, model_aod(time,lat,lon), mean_model_aod(lat,lon))},
    suffix '' for the primary variable -> model_aod / mean_model_aod, '_simple' ->
    model_aod_simple / mean_model_aod_simple, etc."""
    with nc.Dataset(filepath, "w", format="NETCDF4") as ds:
        ds.createDimension("time", len(times))
        ds.createDimension("lat", len(lat))
        ds.createDimension("lon", len(lon))

        v = ds.createVariable("lon", "f4", ("lon",)); v.long_name = "longitude"; v.units = "degrees_east"; v[:] = lon
        v = ds.createVariable("lat", "f4", ("lat",)); v.long_name = "latitude";  v.units = "degrees_north"; v[:] = lat
        v = ds.createVariable("time", "f8", ("time",))
        v.long_name = "time"; v.units = f"hours since {times[0]:%Y-%m-%d %H:%M:%S}"; v.calendar = "standard"
        v[:] = [(t - times[0]).total_seconds() / 3600.0 for t in times]

        def add(name, dims, data, long_name, src=None):
            x = ds.createVariable(name, "f4", dims, zlib=True, complevel=3, fill_value=np.nan)
            x.long_name = long_name; x.units = "1"
            if src:
                x.source_variable = src
            x[:] = np.asarray(data, dtype=np.float32)

        add("viirs_aod", ("time", "lat", "lon"), viirs, "VIIRS AOD at 550 nm (all available data)")
        add("mean_viirs_aod", ("lat", "lon"), mean_viirs, "time-mean of viirs_aod (NaNs ignored)")
        for suffix, (src, data, mean) in models.items():
            add(f"model_aod{suffix}", ("time", "lat", "lon"), data,
                f"model {src} at 550 nm (only where VIIRS is valid)", src)
            add(f"mean_model_aod{suffix}", ("lat", "lon"), mean,
                f"time-mean of model_aod{suffix} (NaNs ignored)", src)
        for k, val in attrs.items():
            setattr(ds, k, val)


def read_output_netcdf(filepath):
    """Reads what the plots need back from a saved NetCDF: lon, lat, mean_viirs_aod,
    every mean_model_aod* field ({suffix: field}, primary first) and the satellites."""
    with nc.Dataset(filepath) as ds:
        names = [v for v in ds.variables if v == "mean_model_aod" or v.startswith("mean_model_aod_")]
        names.sort(key=lambda v: v != "mean_model_aod")         # primary first, extras keep order
        return {
            "lon": ds["lon"][:].astype(np.float64),
            "lat": ds["lat"][:].astype(np.float64),
            "mean_viirs": _to_nan(ds["mean_viirs_aod"][:]),
            "models": {v[len("mean_model_aod"):]: _to_nan(ds[v][:]) for v in names},
            "satellites": getattr(ds, "viirs_satellites", ""),
        }


# =============================================================================
# Plotting -- same multi-column layout as the TEMPO script: column 0 = VIIRS, then one
# column per model (and per extra model AOD variable). The CONUS figure has two rows
# (North America on top, CONUS below); every other region has one row.
# =============================================================================
def load_map_regions(csv_path):
    """lambert_map_settings_viirs.csv -> {region: {'ps': [cen_lat, cen_lon], 'axl': [x1, x2, y1, y2], 'mapres': str}}."""
    regions = {}
    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            regions[row["region"]] = {
                "ps": [float(row["cen_lat"]), float(row["cen_lon"])],
                "axl": [float(row["x1"]), float(row["x2"]), float(row["y1"]), float(row["y2"])],
                "mapres": row["MapRes"],
            }
    return regions


def region_rows(region, map_regions):
    """(label, ps, axl, mapres) rows for a figure: North America + CONUS (2 rows) for
    region == "CONUS", a single row for every other region."""
    if region == "CONUS":
        na, conus = map_regions["North_America"], map_regions["CONUS"]
        return [("NA", na["ps"], na["axl"], na["mapres"]),
                ("CONUS", conus["ps"], conus["axl"], conus["mapres"])]
    r = map_regions[region]
    return [(region, r["ps"], r["axl"], r["mapres"])]


def no_data_metrics():
    """Stand-in for es.metrics when there are no valid pairs: same .Text interface, values N/A."""
    return SimpleNamespace(Text=[f"{m}: N/A" for m in METRICS_TO_SHOW])


def get_metrics(lon, lat, obs, model, origin, axlims):
    """Metrics over the pixels visible in a plot panel (cos-lat weighted); N/A if none valid.
    origin: Lambert projection specs for that row (the same Origin passed to es.spatial).
    axlims: [xmin, xmax, ymin, ymax] in projected km for that row (its axl)."""
    lon2d, lat2d = np.meshgrid(lon, lat)
    x, y = ll2lamb(lat2d, lon2d, origin)
    x_f, y_f, lat_f = x.ravel(), y.ravel(), lat2d.ravel()
    obs_f, model_f = np.asarray(obs).ravel(), np.asarray(model).ravel()

    mask = (x_f >= axlims[0]) & (x_f <= axlims[1]) & (y_f >= axlims[2]) & (y_f <= axlims[3])
    if not np.any(mask & np.isfinite(obs_f) & np.isfinite(model_f)):
        return no_data_metrics()
    return es.metrics(obs_f[mask], model_f[mask], np.cos(np.radians(lat_f[mask])),
                      metrics_to_show=METRICS_TO_SHOW, num_format='.3f')


def _spatial(lon, lat, field, **kw):
    """es.spatial that never aborts the figure: all-NaN fields are still passed through
    (plotted as NaNs); if drawing fails anyway, the panel is left blank and logged."""
    try:
        return es.spatial(lon, lat, field, **kw)
    except Exception as exc:
        es.msg(f"  [WARN] es.spatial failed ({exc}); leaving panel blank")
        return None


def _no_data_label(ax, field):
    """Writes NO_DATA_MSG in the middle of the axes if `field` has no finite values."""
    if not np.any(np.isfinite(field)):
        ax.text(0.5, 0.5, NO_DATA_MSG, transform=ax.transAxes, ha='center', va='center',
                fontsize=9, fontproperties=font, color=(0.35, 0.35, 0.35), zorder=100)


def _finalize_grid(axes, axl, ncols, hasMetrics):
    """Shared axis-limit/tick cleanup and layout pass for a multi-row multi-column figure."""
    nrows = len(axes)
    for row in range(nrows):
        for ax in axes[row]:
            ax.set_xlim(axl[row][0], axl[row][1])
            ax.set_ylim(axl[row][2], axl[row][3])
            ax.set_xticks([])
            ax.set_yticks([])

    vertical_spacement = 10
    if nrows > 1 and hasMetrics:
        vertical_spacement = 35

    es.reorganizeaxes(nrows, ncols, width=300, height=None, spacing_horiz=10, spacing_vert=vertical_spacement,
                      remove_tick_labels=True, margin=120)


def _finalize_colorbar(cb, nrows, ncols, rotate_labels=False):
    """Shared colorbar repositioning/tick cleanup, stretched across the full row width."""
    cb = es.reorganizecolorbar(cb, nrows, ncols, 'bottom', proportion=0.96, extent_axes=[1, ncols])
    cb.ax.tick_params(labelsize=9.5)

    x0, y0, w, _ = cb.ax.get_position().bounds
    cb.ax.set_position([x0, y0, w, 0.02])

    cb_ticks = cb.get_ticks()[1:-1]
    cb_labels = [t.get_text() for t in cb.ax.get_xticklabels()][1:-1]
    cb.set_ticks(cb_ticks)
    cb.set_ticklabels(cb_labels)
    if rotate_labels:
        plt.setp(cb.ax.get_xticklabels(), rotation=90)
    return cb


def make_spatial_figure(columns, rows, lev_aod, cmap_aod, initial_time, end_time, obs_label):
    """Spatial AOD comparison. columns: list of (label, lon, lat, field) tuples, column 0 is VIIRS.
    Each column may have its own lon/lat grid, since models can be cropped to different domains.
    rows: list of (label, ps, axl, mapres) tuples, one per map row."""
    ncols = len(columns)
    nrows = len(rows)

    axes = [[None] * ncols for _ in range(nrows)]
    ids = []
    cb = None

    for row, (row_label, row_ps, row_axl, row_mapres) in enumerate(rows):
        for col, (name, lon, lat, field) in enumerate(columns):
            es.msg(f"{row_label} {name} AOD")
            ax = plt.subplot(nrows, ncols, row * ncols + col + 1)
            axes[row][col] = ax
            kw = dict(Projection='lambert', Origin=row_ps, Levels=lev_aod, MapRes=row_mapres,
                      Colormap=cmap_aod, GeoTicks='off', MapWidth=0.3)

            if col == 0 or cb is not None:
                _spatial(lon, lat, field, Colorbar='off', **kw)
            else:
                out = _spatial(lon, lat, field, **kw)
                if out is not None:
                    cb = out[0]

            _no_data_label(ax, field)
            if row == 0:
                ids.append(es.figid(f'{name}', ax=ax))

    _finalize_grid(axes, [r[2] for r in rows], ncols, False)
    if cb is not None:
        _finalize_colorbar(cb, nrows, ncols)
    plt.setp(ids, fontsize=11, fontproperties=font)

    stime1 = initial_time.strftime("%Y-%m-%d %H")
    stime2 = end_time.strftime("%Y-%m-%d %H")
    label_str = f"{obs_label} AOD 550 nm (period mean)\n{stime1}Z to {stime2}Z"
    axes[0][0].text(0, 1.2, label_str, fontsize=9, transform=axes[0][0].transAxes, fontproperties=font, color=(0.5, 0.5, 0.5))


def make_bias_figure(columns, M, rows, lev_bias, cmap_bias, initial_time, end_time, obs_label):
    """Bias (model - VIIRS) comparison. columns: list of (label, lon, lat, field) tuples, one per model.
    rows: list of (label, ps, axl, mapres) tuples, one per map row.
    M[i][row] holds the metrics for column i over the pixels shown in that row's panel (see get_metrics)."""
    ncols = len(columns)
    nrows = len(rows)

    axes = [[None] * ncols for _ in range(nrows)]
    ids = []
    mms = []
    cb = None

    for row, (row_label, row_ps, row_axl, row_mapres) in enumerate(rows):
        for col, (name, lon, lat, field) in enumerate(columns):
            es.msg(f"{row_label} {name} Bias")
            ax = plt.subplot(nrows, ncols, row * ncols + col + 1)
            axes[row][col] = ax
            kw = dict(Projection='lambert', Origin=row_ps, Levels=lev_bias, MapRes=row_mapres,
                      Colormap=cmap_bias, GeoTicks='off', MapWidth=0.3)

            if cb is None:
                out = _spatial(lon, lat, field, **kw)
                if out is not None:
                    cb = out[0]
            else:
                _spatial(lon, lat, field, Colorbar='off', **kw)

            _no_data_label(ax, field)
            if row == 0:
                ids.append(es.figid(f"{name} bias", ax=ax))

            mms.append(es.figid('\n'.join(M[col][row].Text), Location='outright'))
            ax.set_facecolor((0.8, 0.8, 0.8))

    _finalize_grid(axes, [r[2] for r in rows], ncols, True)
    if cb is not None:
        _finalize_colorbar(cb, nrows, ncols, rotate_labels=False)
    plt.setp(ids, fontsize=11, fontproperties=font)
    plt.setp(mms, fontsize=4, fontproperties=fmono)

    stime1 = initial_time.strftime("%Y-%m-%d %H")
    stime2 = end_time.strftime("%Y-%m-%d %H")
    label_str = f"AOD 550 nm bias against {obs_label} (period mean)\n{stime1}Z to {stime2}Z"
    axes[0][0].text(0, 1.2, label_str, fontsize=9, transform=axes[0][0].transAxes, fontproperties=font, color=(0.5, 0.5, 0.5))


# =============================================================================
# Main Execution
# =============================================================================
if __name__ == "__main__":

    args = parse_args()
    date_start = parse_time(args.date_start)
    date_end = parse_time(args.date_end)
    if date_end < date_start:
        raise ValueError("--date-end must be >= --date-start")
    es.msg(f"period: {date_start} to {date_end} (both inclusive)")


    if args.save_netcdf:
        model_vars = [v.strip() for v in args.model_var.split(",") if v.strip()]
        es.msg(f"MODEL_NAME={args.model_name}  FILE_MODEL={args.file_model} {model_vars}")
        es.msg(f"VIIRS_FILE={args.viirs_file} [{args.viirs_var}]")

        # 1) VIIRS (already QC'd/aggregated -> read as is), on the inclusive period axis
        es.msg("Reading VIIRS data...")
        lon, lat, times, viirs, viirs_files, viirs_sats, dt = read_viirs(
            args.viirs_file, args.viirs_var, date_start, date_end)
        es.msg(f"  {len(times)} time step(s) of {dt}, {times[0]} - {times[-1]}")

        # 2) Model variable(s), time-matched to the VIIRS windows
        es.msg("Reading model data...")
        model, ys, xs = read_model(args.file_model, model_vars, lon, lat, times, dt)

        # 3) Crop VIIRS to the model's grid, then everything to the model's domain box
        lon, lat, viirs = lon[xs], lat[ys], viirs[:, ys, xs]
        west, east, south, north = crop_model_boundaries(args.model_name)
        cx, cy = crop_slice(lon, west, east), crop_slice(lat, south, north)
        lon, lat, viirs = lon[cx], lat[cy], viirs[:, cy, cx]

        # 4) VIIRS keeps all its data; each model variable only where VIIRS is valid
        viirs_valid = np.isfinite(viirs)
        if not viirs_valid.any():
            es.msg(f"  [WARN] No VIIRS data available between {date_start} and {date_end}")
        models = {}
        for var, aod in model.items():
            aod = aod[:, cy, cx]
            aod[~viirs_valid] = np.nan
            if not np.isfinite(aod).any():
                es.msg(f"  [WARN] No {args.model_name} {var} data paired with VIIRS between {date_start} and {date_end}")
            models[var_suffix(var, model_vars[0])] = (var, aod, time_mean(aod))

        es.msg(f"Saving NetCDF output ({len(times)} time steps)...")
        write_output_netcdf(args.file_out, lon, lat, times, viirs, time_mean(viirs), models, {
            "model": args.model_name,
            "model_file": args.file_model,
            "model_variables": ",".join(model),
            "viirs_files": ", ".join(viirs_files),
            "viirs_variable": args.viirs_var,
            "viirs_satellites": viirs_sats,
            "period": f"[{date_start:%Y-%m-%d %H:%M}, {date_end:%Y-%m-%d %H:%M}] UTC (inclusive)",
            "masking": "viirs_aod: all available data; model_aod*: only where viirs_aod is valid",
        })
        es.msg(f"Saved NetCDF output to {args.file_out}")


    if args.make_figure:
        es.msg("Plotting...")

        # labels for output figure name
        stime1 = date_start.strftime("%Y-%m-%d_%H")
        stime2 = date_end.strftime("%Y-%m-%d_%H")
        ymd = date_start.strftime("%Y-%m-%d")
        hh = date_start.strftime("%H")

        if not is_valid_file(args.regions_csv):
            raise RuntimeError(f"--regions-csv is not a valid file (got {args.regions_csv!r}).")
        map_regions = load_map_regions(args.regions_csv)
        if "North_America" not in map_regions:
            raise RuntimeError(f"{args.regions_csv} has no North_America region (needed for the CONUS figure).")

        # Skip models whose NetCDF output is missing (e.g. that model wasn't run for this cycle)
        model_names, model_files = [], []
        for name, path in zip(args.model_names, args.plot_files):
            if is_valid_file(path):
                model_names.append(name)
                model_files.append(path)
            else:
                es.msg(f"  {name}: no NetCDF output ({path}), skipping")

        if not model_files:
            raise RuntimeError("None of the --plot-files exist.")

        es.msg("Reading per-model NetCDF output...")
        outputs = [read_output_netcdf(f) for f in model_files]

        # VIIRS label, e.g. "VIIRS (Suomi-NPP/NOAA-20/21)" from the aggregation files
        sats = outputs[0]["satellites"]
        obs_label = f"VIIRS ({sats})" if sats else "VIIRS"

        # One column per mean_model_aod* field: '' -> "NAME", '_simple' -> "NAME (SIMPLE)"
        model_specs = []                     # (column label, output dict, suffix)
        for name, o in zip(model_names, outputs):
            for suffix, field in o["models"].items():
                label = column_label(name, suffix)
                if not np.any(np.isfinite(field)):
                    es.msg(f"  [WARN] {label}: no paired model/VIIRS data in this period -- plotting NaNs")
                model_specs.append((label, o, suffix))

        # Each model's NetCDF was cropped to its own domain, so lon/lat are kept per column.
        spatial_columns = [(obs_label, outputs[0]["lon"], outputs[0]["lat"], outputs[0]["mean_viirs"])]
        spatial_columns += [(label, o["lon"], o["lat"], o["models"][sfx]) for label, o, sfx in model_specs]

        # Plot spatial_overlay
        for region in REGIONS:
            if region not in map_regions:
                es.msg(f"  {region}: not in {os.path.basename(args.regions_csv)}, skipping")
                continue
            rows = region_rows(region, map_regions)

            make_spatial_figure(spatial_columns, rows, lev_aod, cmap_aod, date_start, date_end, obs_label)

            fig_out = f"{args.path_fig}/plot_grp2.spatial_overlay.aod_550nm.{stime1}.{stime2}.{region}.VIIRS_{ymd}-{hh}Z.png"
            plt.gcf().savefig(fig_out, bbox_inches='tight', dpi=200)
            plt.close('all')
            es.msg(f"{fig_out} saved!")

        # Plot spatial_bias
        bias_columns = [(label, o["lon"], o["lat"], o["models"][sfx] - o["mean_viirs"])
                        for label, o, sfx in model_specs]

        for region in REGIONS:
            if region not in map_regions:
                continue
            rows = region_rows(region, map_regions)

            M = [[get_metrics(o["lon"], o["lat"], o["mean_viirs"], o["models"][sfx], row_ps, row_axl)
                  for (_, row_ps, row_axl, _) in rows]
                 for _, o, sfx in model_specs]

            make_bias_figure(bias_columns, M, rows, lev_bias, cmap_bias, date_start, date_end, obs_label)

            fig_out = f"{args.path_fig}/plot_grp3.spatial_bias.aod_550nm.{stime1}.{stime2}.{region}.VIIRS_{ymd}-{hh}Z.png"
            plt.gcf().savefig(fig_out, bbox_inches='tight', dpi=200)
            plt.close('all')
            es.msg(f"{fig_out} saved!")