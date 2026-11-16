import os
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

import pyearthsciences as es                        # pip install -e <path to pyearthsciences repo>
from pyearthsciences.ll2lamb import ll2lamb

# =============================================================================
# Model vs. satellite (TEMPO or VIIRS, aggregated L2) AOD verification.
#
# The model AOD must already be regridded (CDO) onto the observation grid, or a
# sub-box of it. One script for both observing systems: --obs-name only sets labels.
#
# Verification period: --date-start .. --date-end (BOTH inclusive). The output time axis
# is the regular sequence date_start, date_start + dt, ..., <= date_end (dt = obs file
# time step), so the output always has the same shape: obs/model times that aren't
# available are written as NaN (with a warning). If the period goes past --obs-file,
# sibling files of the same product/grid in the same directory fill the missing times.
# Each model value is the mean of the model steps within [t - dt/2, t + dt/2).
#
# Usage (see --help):
#   # 1) pair one model with the observations and save the NetCDF
#   aod_verification.py --save-netcdf --obs-name VIIRS \
#       --date-start 2026-10-28_12:00:00 --date-end 2026-10-29_12:00:00 \
#       --model-name MPAS-Aerosols --file-model aod_MPAS-Aerosols_2026102700.nc --model-var AOD550 \
#       --obs-file viirs_all_aggregated_1h_0p1deg_2026102812.nc --obs-var aod550 \
#       --file-out viirs_MPAS-Aerosols_2026102700.nc
#   # 2) plot several models' saved NetCDFs together (one PNG per CSV region)
#   aod_verification.py --make-figure --obs-name VIIRS \
#       --date-start 2026-10-28_12:00:00 --date-end 2026-10-29_12:00:00 \
#       --model-names RRFS-SD MPAS-Aerosols \
#       --plot-files viirs_RRFS-SD_2026102700.nc viirs_MPAS-Aerosols_2026102700.nc \
#       --path-fig ./figs --regions-csv lambert_map_settings_viirs.csv
#
# Saved variables:
#   obs_aod(time,lat,lon), obs_count(time,lat,lon)     all available observations
#   model_aod(time,lat,lon)                            only where obs_aod is valid
#   mean_obs_aod(lat,lon)                              period mean, all obs (top-left panel)
#   mean_obs_aod_paired(lat,lon)                       period mean, only where model_aod is valid (bias)
#   mean_model_aod(lat,lon)                            period mean of model_aod
#   sum_obs_count(lat,lon)                             total retrievals in the period
#
# Figure (one per region), 2 rows x (N models + 1) columns:
#   row 0: obs mean AOD | model mean AOD ...            -> AOD colorbar (right)
#   row 1: obs retrievals count | model - obs bias ...  -> count colorbar (below col 0),
#                                                          bias colorbar (right)
# =============================================================================

NO_DATA_MSG = "No data available for current times"
METRICS_TO_SHOW = ["R", "MB", "cRMSE"]

# Levels
lev_aod      = np.concatenate([[0, 0.05, 0.1, 0.15], np.arange(2, 11) / 10.0, [1.2, 1.5, 2.0]])
lev_bias_pos = np.concatenate([[0.05], np.arange(1, 7) / 10.0])
lev_bias     = np.sort(np.concatenate([-lev_bias_pos, lev_bias_pos]))

# Colormaps
cm1 = es.hue([227,227,227], [136,183,211], 5)
cm2 = es.hue([85,180,94],[251,232,135],[252,170,95],[242,114,69],[194,27,38],[135,27,87],[255,192,203], len(lev_aod) - 1 - 5)
cmap_aod   = ListedColormap(np.vstack((cm1.colors, cm2.colors)))
cmap_bias  = es.hue('pan', len(lev_bias) - 1)
cmap_count = es.hue('finn', 64)                     # continuous on log10(count)


# =============================================================================
# Arguments
# =============================================================================
def parse_args():
    p = argparse.ArgumentParser(description="Model vs. satellite (TEMPO/VIIRS) AOD verification "
                                            "(save paired NetCDF and/or plot).",
                                formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--obs-name", required=True, help="observing system, e.g. TEMPO or VIIRS (labels, file names)")
    p.add_argument("--date-start", required=True, help="first verification time (inclusive), YYYY-MM-DD_hh:mm:ss")
    p.add_argument("--date-end", required=True, help="last verification time (inclusive), YYYY-MM-DD_hh:mm:ss")
    p.add_argument("--save-netcdf", action="store_true", help="pair one model with the obs and save the NetCDF")
    p.add_argument("--make-figure", action="store_true", help="plot the saved NetCDF(s) of one or more models")

    s = p.add_argument_group("--save-netcdf options")
    s.add_argument("--model-name", help="model name (crop box, log and attributes)")
    s.add_argument("--file-model", help="model AOD file, already CDO-regridded onto the obs grid")
    s.add_argument("--model-var", default="aod550", help="AOD variable in --file-model")
    s.add_argument("--obs-file", help="aggregated obs file (e.g. tempo_aggregated_*.nc, viirs_*_aggregated_*.nc)")
    s.add_argument("--obs-var", default="aod550", help="obs AOD variable, e.g. aod550, aod550_median")
    s.add_argument("--obs-count-var", default="count", help="obs number-of-retrievals variable")
    s.add_argument("--file-out", help="output NetCDF (paired model/obs AOD)")

    g = p.add_argument_group("--make-figure options")
    g.add_argument("--model-names", nargs="+", help="model names, one per --plot-files entry (column titles)")
    g.add_argument("--plot-files", nargs="+", help="NetCDFs written by --save-netcdf, one per model")
    g.add_argument("--path-fig", default="./", help="output directory for the figures")
    g.add_argument("--regions-csv", help="Lambert map settings CSV; one figure per region in it")

    args = p.parse_args()
    if not (args.save_netcdf or args.make_figure):
        p.error("nothing to do: pass --save-netcdf and/or --make-figure")
    if args.save_netcdf:
        missing = [o for o, v in (("--model-name", args.model_name), ("--file-model", args.file_model),
                                  ("--obs-file", args.obs_file), ("--file-out", args.file_out)) if not v]
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


# =============================================================================
# Grid / time helpers
# =============================================================================
def is_valid_file(filepath):
    """True if filepath points to a real file (not "None"/empty/missing)."""
    return bool(filepath) and filepath.lower() != "none" and os.path.isfile(filepath)


def crop_model_boundaries(model):
    """(west, east, south, north) box in degrees to crop a model/obs pair to."""
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
    """NaN-ignoring mean over time (axis 0); NaN where no valid value."""
    cnt = np.isfinite(arr).sum(axis=0)
    tot = np.nansum(arr, axis=0)
    return np.where(cnt > 0, tot / np.maximum(cnt, 1), np.nan).astype(np.float32)


def time_sum(arr):
    """NaN-ignoring sum over time (axis 0); NaN where never valid."""
    cnt = np.isfinite(arr).sum(axis=0)
    return np.where(cnt > 0, np.nansum(arr, axis=0), np.nan).astype(np.float32)


# =============================================================================
# Observations
# =============================================================================
def obs_sibling_files(obs_file, date_start, date_end):
    """Other files in obs_file's directory with a date stamp from date_start-1 to date_end,
    restricted to the same product (same name once the date stamp is removed), so e.g. a
    0.25-deg/3-h file is never mixed with a 0.1-deg/1-h one."""
    folder = os.path.dirname(os.path.abspath(obs_file))
    stem = lambda f: re.sub(r"\d{8,10}(?=\.nc$)", "", os.path.basename(f))   # drop date stamp
    target = stem(obs_file)
    found, day = [], (date_start - timedelta(days=1)).replace(hour=0, minute=0, second=0)
    while day <= date_end:
        for f in glob.glob(os.path.join(folder, f"*{day:%Y%m%d}*.nc")):
            if stem(f) == target and os.path.abspath(f) != os.path.abspath(obs_file) and f not in found:
                found.append(f)
        day += timedelta(days=1)
    return sorted(found)


def read_obs(obs_file, var, count_var, date_start, date_end):
    """Obs AOD and retrieval count on the inclusive period axis (NaN where no file holds
    that time). Uses obs_file first, then sibling files for times it doesn't cover.
    Returns lon, lat (S -> N), period times, aod, count (time, lat, lon), files used,
    satellites, dt."""
    with nc.Dataset(obs_file) as ds:
        t_main = _read_times(ds["time"])
        lon, lat = ds["lon"][:].astype(np.float64), ds["lat"][:].astype(np.float64)
    flip_lat = lat[0] > lat[-1]                              # keep everything S -> N
    lat_out = lat[::-1] if flip_lat else lat

    dt = (t_main[1] - t_main[0]) if len(t_main) > 1 else timedelta(hours=1)
    period = build_period_axis(date_start, date_end, dt)
    if len(period) == 0:
        raise ValueError("Empty verification period: --date-end must be >= --date-start.")

    files = [obs_file]
    if not set(period) <= set(t_main):                     # main file doesn't span the period
        extra = obs_sibling_files(obs_file, date_start, date_end)
        if extra:
            es.msg(f"  Period extends past {os.path.basename(obs_file)}; also using: "
                   + ", ".join(os.path.basename(f) for f in extra))
            files += extra
        else:
            es.msg(f"  [WARN] no other obs files found in {os.path.dirname(obs_file)} for "
                   f"{date_start} - {date_end}; continuing with {os.path.basename(obs_file)} only")

    shape = (len(period), lat.size, lon.size)
    aod = np.full(shape, np.nan, dtype=np.float32)
    count = np.full(shape, np.nan, dtype=np.float32)
    filled = np.zeros(len(period), dtype=bool)
    pos = {t: i for i, t in enumerate(period)}
    sources = []                                           # satellite_source of the files used
    for path in files:
        with nc.Dataset(path) as ds:
            if not (np.allclose(ds["lon"][:], lon) and np.allclose(ds["lat"][:], lat)):
                es.msg(f"  [WARN] {os.path.basename(path)} has a different grid, skipped")
                continue
            for name in (var, count_var):
                if name not in ds.variables:
                    raise KeyError(f"'{name}' not found in {path}")
            used = False
            for it, ti in enumerate(_read_times(ds["time"])):
                i = pos.get(ti)
                if i is not None and not filled[i]:        # first file wins on overlap
                    aod[i] = _to_nan(ds[var][it])
                    count[i] = _to_nan(ds[count_var][it])
                    filled[i] = used = True
            src = getattr(ds, "satellite_source", "")
            if used and src and src != "none" and src not in sources:
                sources.append(src)

    if not filled.all():
        es.msg(f"  [WARN] {int((~filled).sum())} of {len(period)} obs time(s) in the period are not "
               "available in any file; filled with NaN")
    if flip_lat:
        aod, count = aod[:, ::-1, :], count[:, ::-1, :]
    return lon, lat_out, period, aod, count, files, ", ".join(sources), dt


# =============================================================================
# Model
# =============================================================================
def read_model(file_model, var, obs_lon, obs_lat, obs_times, dt):
    """Reads the model AOD on the obs grid and averages its steps inside each obs window
    [t - dt/2, t + dt/2). Returns aod(time, lat, lon) aligned to obs_times (NaN where the
    model has no step in the window) and the (row, col) slices of the obs grid the model covers."""
    with nc.Dataset(file_model) as ds:
        for name in ("time", "XTIME", "Time"):
            if name in ds.variables:
                time_var = ds[name]
                break
        else:
            raise KeyError(f"Could not find 'time', 'XTIME', or 'Time' in {file_model}")
        mtimes = _read_times(time_var)
        if var not in ds.variables:
            raise KeyError(f"Model AOD variable '{var}' not found in {file_model}")

        lon = ds["lon"][:].astype(np.float64)
        lat = ds["lat"][:].astype(np.float64)
        lon = np.where(lon >= 180.0, lon - 360.0, lon)           # 0..360 -> -180..180
        flip_lat = lat[0] > lat[-1]                              # cdo may write N -> S
        if flip_lat:
            lat = lat[::-1]

        # Model grid must be the obs grid (or a sub-box of it)
        res = abs(obs_lon[1] - obs_lon[0])
        xs = crop_slice(obs_lon, lon.min() - res / 4, lon.max() + res / 4)
        ys = crop_slice(obs_lat, lat.min() - res / 4, lat.max() + res / 4)
        if (obs_lon[xs].size != lon.size or obs_lat[ys].size != lat.size
                or not np.allclose(obs_lon[xs], lon, atol=res / 100)
                or not np.allclose(obs_lat[ys], lat, atol=res / 100)):
            raise ValueError(f"{file_model}: lon/lat don't match the obs grid -- regrid it first")

        half = dt / 2
        windows = [np.flatnonzero((mtimes >= t - half) & (mtimes < t + half)) for t in obs_times]
        n_missing = sum(idx.size == 0 for idx in windows)
        if n_missing:
            es.msg(f"  [WARN] {n_missing} of {len(obs_times)} period time(s) have no model step "
                   f"(model steps {mtimes[0]} - {mtimes[-1]}); filled with NaN")

        v = ds[var]
        if v.shape[0] != len(mtimes):
            raise ValueError(f"{file_model}: '{time_var.name}' has {len(mtimes)} steps but {var} has {v.shape[0]}")

        aod = np.full((len(obs_times), lat.size, lon.size), np.nan, dtype=np.float32)
        for it, idx in enumerate(windows):
            if idx.size == 0:
                continue                                         # model doesn't reach this time
            steps = _to_nan(v[idx])
            steps = steps.reshape(idx.size, *steps.shape[-2:])   # drop singleton level dims
            if idx[0] == 0 and np.nansum(steps[0]) == 0:
                # Spin-up artifact: some models write all-zero AOD at the first step
                es.msg(f"  First model time step has zero {var} everywhere, setting to NaN")
                steps[0] = np.nan
            with np.errstate(invalid="ignore"), warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN cells
                aod[it] = np.nanmean(steps, axis=0)

    return (aod[:, ::-1, :] if flip_lat else aod), ys, xs


# =============================================================================
# Output NetCDF
# =============================================================================
def write_output_netcdf(filepath, lon, lat, times, fields3d, fields2d, attrs):
    """fields3d / fields2d: {name: (data, long_name, units)} on (time,lat,lon) / (lat,lon)."""
    with nc.Dataset(filepath, "w", format="NETCDF4") as ds:
        ds.createDimension("time", len(times))
        ds.createDimension("lat", len(lat))
        ds.createDimension("lon", len(lon))

        v = ds.createVariable("lon", "f4", ("lon",)); v.long_name = "longitude"; v.units = "degrees_east"; v[:] = lon
        v = ds.createVariable("lat", "f4", ("lat",)); v.long_name = "latitude";  v.units = "degrees_north"; v[:] = lat
        v = ds.createVariable("time", "f8", ("time",))
        v.long_name = "time"; v.units = f"hours since {times[0]:%Y-%m-%d %H:%M:%S}"; v.calendar = "standard"
        v[:] = [(t - times[0]).total_seconds() / 3600.0 for t in times]

        for dims, fields in ((("time", "lat", "lon"), fields3d), (("lat", "lon"), fields2d)):
            for name, (data, long_name, units) in fields.items():
                x = ds.createVariable(name, "f4", dims, zlib=True, complevel=3, fill_value=np.nan)
                x.long_name = long_name; x.units = units
                x[:] = np.asarray(data, dtype=np.float32)
        for k, val in attrs.items():
            setattr(ds, k, val)


def read_output_netcdf(filepath):
    """Reads back the 2D period fields the plots need."""
    with nc.Dataset(filepath) as ds:
        return {
            "lon": ds["lon"][:].astype(np.float64),
            "lat": ds["lat"][:].astype(np.float64),
            "mean_obs": _to_nan(ds["mean_obs_aod"][:]),
            "mean_obs_paired": _to_nan(ds["mean_obs_aod_paired"][:]),
            "mean_model": _to_nan(ds["mean_model_aod"][:]),
            "count": _to_nan(ds["sum_obs_count"][:]),
            "satellites": getattr(ds, "obs_satellites", ""),
        }


# =============================================================================
# Plotting: 2 rows x (N models + 1) columns
# =============================================================================
def load_map_regions(csv_path):
    """CSV -> {region: {'ps': [cen_lat, cen_lon], 'axl': [x1, x2, y1, y2], 'mapres': str}}, CSV order."""
    regions = {}
    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            regions[row["region"]] = {
                "ps": [float(row["cen_lat"]), float(row["cen_lon"])],
                "axl": [float(row["x1"]), float(row["x2"]), float(row["y1"]), float(row["y2"])],
                "mapres": row["MapRes"],
            }
    return regions


def no_data_metrics():
    """Stand-in for es.metrics when there are no valid pairs: same .Text interface, values N/A."""
    return SimpleNamespace(Text=[f"{m}: N/A" for m in METRICS_TO_SHOW])


def get_metrics(lon, lat, obs, model, origin, axlims):
    """Metrics over the pixels visible in a panel (cos-lat weighted); N/A if none valid.
    origin: Lambert specs (same Origin given to es.spatial); axlims: panel [x1, x2, y1, y2] in km."""
    lon2d, lat2d = np.meshgrid(lon, lat)
    x, y = ll2lamb(lat2d, lon2d, origin)
    x_f, y_f, lat_f = x.ravel(), y.ravel(), lat2d.ravel()
    obs_f, model_f = np.asarray(obs).ravel(), np.asarray(model).ravel()

    mask = (x_f >= axlims[0]) & (x_f <= axlims[1]) & (y_f >= axlims[2]) & (y_f <= axlims[3])
    if not np.any(mask & np.isfinite(obs_f) & np.isfinite(model_f)):
        return no_data_metrics()
    return es.metrics(obs_f[mask], model_f[mask], np.cos(np.radians(lat_f[mask])),
                      metrics_to_show=METRICS_TO_SHOW, num_format='.3f')


def count_ticks(cmax):
    """1-2-5 tick values from 1 up to cmax (the colorbar runs from 1 to the data maximum)."""
    ticks, dec = [], 1
    while dec <= cmax:
        ticks += [m * dec for m in (1, 2, 5) if m * dec <= cmax]
        dec *= 10
    return np.array(ticks, dtype=float)


def _spatial(lon, lat, field, **kw):
    """es.spatial that never aborts the figure; returns (cb, mappable) or (None, None)."""
    try:
        return es.spatial(lon, lat, field, **kw)
    except Exception as exc:
        es.msg(f"  [WARN] es.spatial failed ({exc}); leaving panel blank")
        return None, None


def _no_data_label(ax, field):
    """Writes NO_DATA_MSG in the middle of the axes if `field` has no finite values."""
    if not np.any(np.isfinite(field)):
        ax.text(0.5, 0.5, NO_DATA_MSG, transform=ax.transAxes, ha='center', va='center',
                fontsize=9, color=(0.35, 0.35, 0.35), zorder=100)


def _trim_level_labels(cb):
    """Drops the end labels of a Levels (data2levels) colorbar, as before."""
    axis = cb.ax.yaxis if cb.orientation == "vertical" else cb.ax.xaxis
    ticks = cb.get_ticks()[1:-1]
    labels = [t.get_text() for t in axis.get_ticklabels()][1:-1]
    cb.set_ticks(ticks)
    cb.set_ticklabels(labels)


def make_figure(obs_col, model_cols, region, obs_label, initial_time, end_time):
    """obs_col: (lon, lat, mean_aod, count); model_cols: [(name, lon, lat, mean_aod, bias, metrics)].
    region: {'ps', 'axl', 'mapres'} from the CSV."""
    ncols, nrows = len(model_cols) + 1, 2
    ps, axl, mapres = region["ps"], region["axl"], region["mapres"]
    base = dict(Projection='lambert', Origin=ps, MapRes=mapres, GeoTicks='off', MapWidth=0.3)
    kw_aod  = dict(base, Levels=lev_aod,  Colormap=cmap_aod)
    kw_bias = dict(base, Levels=lev_bias, Colormap=cmap_bias)

    # Axes created in row-major order first: es.reorganizeaxes lays them out in creation order
    fig = plt.figure()
    axes = [[fig.add_subplot(nrows, ncols, r * ncols + c + 1) for c in range(ncols)] for r in range(nrows)]
    ids, mms = [], []
    cb_aod = cb_bias = cb_count = h_count = None

    # Count on log10 scale: 0 retrievals -> NaN (blank); scale runs from 1 to the data maximum
    o_lon, o_lat, o_aod, o_count = obs_col
    count = np.where(o_count >= 1, o_count, np.nan)
    cmax = max(float(np.nanmax(count)), 2.0) if np.any(np.isfinite(count)) else 10.0   # avoid a 0-width range
    cticks = count_ticks(cmax)

    # ---- column 0: observations ------------------------------------------------------
    ax = axes[0][0]; plt.sca(ax)
    _spatial(o_lon, o_lat, o_aod, Colorbar='off', **kw_aod)
    _no_data_label(ax, o_aod)
    ids.append(es.figid(obs_label, ax=ax, FontName="sans-serif"))

    ax = axes[1][0]; plt.sca(ax)
    cb_count, h_count = _spatial(o_lon, o_lat, np.log10(count), Colormap=cmap_count, **base)
    if h_count is not None:
        h_count.set_clim(0, np.log10(cmax))
    if cb_count is not None:
        # Set the final ticks now: es.reorganizecolorbar copies the current ticks onto the
        # horizontal bar it recreates, and any tick past log10(cmax) would stretch its axis
        cb_count.set_ticks(np.log10(cticks))
    _no_data_label(ax, count)
    ids.append(es.figid("# observations", ax=ax, FontName="sans-serif"))

    # ---- columns 1..N: models (colorbars attached to the last column) ----------------
    for col, (name, lon, lat, aod, bias, M) in enumerate(model_cols, start=1):
        last = col == ncols - 1
        es.msg(f"{name}: AOD and bias")

        ax = axes[0][col]; plt.sca(ax)
        cb, _ = _spatial(lon, lat, aod, Colorbar='on' if last else 'off', **kw_aod)
        cb_aod = cb if last else cb_aod
        _no_data_label(ax, aod)
        ids.append(es.figid(name, ax=ax, FontName="sans-serif"))

        ax = axes[1][col]; plt.sca(ax)
        cb, _ = _spatial(lon, lat, bias, Colorbar='on' if last else 'off', **kw_bias)
        cb_bias = cb if last else cb_bias
        _no_data_label(ax, bias)
        ax.set_facecolor((0.8, 0.8, 0.8))
        ids.append(es.figid(f"{name} bias", ax=ax, FontName="sans-serif"))
        mms.append(ax.text(0.015, 0.015, "\n".join(M.Text), transform=ax.transAxes, ha="left", va="bottom",
                           family="monospace", fontsize=5, zorder=101,
                           bbox=dict(facecolor="white", alpha=0.75, edgecolor="none", pad=1.5)))

    # ---- layout ------------------------------------------------------------------------
    for row in axes:
        for ax in row:
            ax.set_xlim(axl[0], axl[1]); ax.set_ylim(axl[2], axl[3])
            ax.set_xticks([]); ax.set_yticks([])
    es.reorganizeaxes(nrows, ncols, width=300, height=None, spacing_horiz=10, spacing_vert=35,
                      remove_tick_labels=True, margin=120)

    # ---- colorbars ---------------------------------------------------------------------
    if cb_aod is not None:                               # right of row 0
        cb_aod = es.reorganizecolorbar(cb_aod, nrows, ncols, "right", proportion=0.96, extent_axes=[1, 1])
        _trim_level_labels(cb_aod)
        cb_aod.ax.tick_params(labelsize=6)
    if cb_bias is not None:                              # right of row 1
        cb_bias = es.reorganizecolorbar(cb_bias, nrows, ncols, "right", proportion=0.96, extent_axes=[2, 2])
        _trim_level_labels(cb_bias)
        cb_bias.ax.tick_params(labelsize=6)
    if cb_count is not None:                             # horizontal, under the count panel
        cb_count = es.reorganizecolorbar(cb_count, nrows, ncols, "bottom", proportion=0.9, extent_axes=[1, 1])
        x0, y0, w, _ = cb_count.ax.get_position().bounds
        cb_count.ax.set_position([x0, y0 + 0.02, w, 0.015])
        cb_count.set_ticks(np.log10(cticks))
        cb_count.set_ticklabels([f"{t:g}" for t in cticks])
        cb_count.ax.set_xlim(0, np.log10(cmax))          # bar spans exactly 1 .. data maximum
        cb_count.ax.minorticks_off()
        cb_count.ax.tick_params(labelsize=7)
        if len(cticks) > 7:
            plt.setp(cb_count.ax.get_xticklabels(), rotation=90)

    plt.setp(ids, fontsize=11)

    stime1 = initial_time.strftime("%Y-%m-%d %H")
    stime2 = end_time.strftime("%Y-%m-%d %H")
    axes[0][0].text(0, 1.2, f"AOD 550 nm vs. {obs_label} (period mean)\n{stime1}Z to {stime2}Z",
                    fontsize=9, transform=axes[0][0].transAxes, color=(0.5, 0.5, 0.5))
    return fig


# =============================================================================
# Main Execution
# =============================================================================
if __name__ == "__main__":

    args = parse_args()
    date_start = parse_time(args.date_start)
    date_end = parse_time(args.date_end)
    if date_end < date_start:
        raise ValueError("--date-end must be >= --date-start")
    obs_name = args.obs_name.upper()                  # always TEMPO / VIIRS in labels, attrs and file names
    es.msg(f"{obs_name} period: {date_start} to {date_end} (both inclusive)")


    if args.save_netcdf:
        es.msg(f"MODEL_NAME={args.model_name}  FILE_MODEL={args.file_model} [{args.model_var}]")
        es.msg(f"OBS_FILE={args.obs_file} [{args.obs_var}, {args.obs_count_var}]")

        # 1) Observations (already QC'd/aggregated -> read as is), on the inclusive period axis
        es.msg(f"Reading {obs_name} data...")
        lon, lat, times, obs, cnt, obs_files, obs_sats, dt = read_obs(
            args.obs_file, args.obs_var, args.obs_count_var, date_start, date_end)
        es.msg(f"  {len(times)} time step(s) of {dt}, {times[0]} - {times[-1]}")

        # 2) Model, time-matched to the obs windows
        es.msg("Reading model data...")
        model, ys, xs = read_model(args.file_model, args.model_var, lon, lat, times, dt)

        # 3) Crop obs to the model's grid, then everything to the model's domain box
        lon, lat, obs, cnt = lon[xs], lat[ys], obs[:, ys, xs], cnt[:, ys, xs]
        west, east, south, north = crop_model_boundaries(args.model_name)
        cx, cy = crop_slice(lon, west, east), crop_slice(lat, south, north)
        lon, lat = lon[cx], lat[cy]
        obs, cnt, model = obs[:, cy, cx], cnt[:, cy, cx], model[:, cy, cx]

        # 4) Obs keeps all its data; model only where obs is valid; paired obs only where model is
        obs_valid = np.isfinite(obs)
        if not obs_valid.any():
            es.msg(f"  [WARN] No {obs_name} data available between {date_start} and {date_end}")
        model[~obs_valid] = np.nan
        if not np.isfinite(model).any():
            es.msg(f"  [WARN] No {args.model_name} data paired with {obs_name} between {date_start} and {date_end}")
        obs_paired = np.where(np.isfinite(model), obs, np.nan)

        es.msg(f"Saving NetCDF output ({len(times)} time steps)...")
        write_output_netcdf(args.file_out, lon, lat, times,
            fields3d={
                "obs_aod":   (obs,   f"{obs_name} AOD at 550 nm (all available data)", "1"),
                "obs_count": (cnt,   f"{obs_name} number of retrievals", "1"),
                "model_aod": (model, f"model {args.model_var} at 550 nm (only where obs_aod is valid)", "1"),
            },
            fields2d={
                "mean_obs_aod":        (time_mean(obs),        "time-mean of obs_aod (NaNs ignored)", "1"),
                "mean_obs_aod_paired": (time_mean(obs_paired), "time-mean of obs_aod where model_aod is valid", "1"),
                "mean_model_aod":      (time_mean(model),      "time-mean of model_aod (NaNs ignored)", "1"),
                "sum_obs_count":       (time_sum(cnt),         f"total {obs_name} retrievals in the period", "1"),
            },
            attrs={
                "obs_name": obs_name,
                "obs_files": ", ".join(obs_files),
                "obs_variable": args.obs_var,
                "obs_count_variable": args.obs_count_var,
                "obs_satellites": obs_sats,
                "model": args.model_name,
                "model_file": args.file_model,
                "model_variable": args.model_var,
                "period": f"[{date_start:%Y-%m-%d %H:%M}, {date_end:%Y-%m-%d %H:%M}] UTC (inclusive)",
                "masking": "obs_aod: all available data; model_aod: only where obs_aod is valid",
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
        for name, o in zip(model_names, outputs):
            if not np.any(np.isfinite(o["mean_model"])):
                es.msg(f"  [WARN] {name}: no paired model/{obs_name} data in this period -- plotting NaNs")

        # Obs column from the file with the largest domain (each file is cropped to its model's box)
        o_obs = max(outputs, key=lambda o: o["lon"].size * o["lat"].size)
        sats = o_obs["satellites"]
        obs_label = f"{obs_name} ({sats})" if sats else obs_name
        obs_col = (o_obs["lon"], o_obs["lat"], o_obs["mean_obs"], o_obs["count"])

        # One figure per region in the CSV
        for region, spec in map_regions.items():
            es.msg(f"Region {region}")
            model_cols = []
            for name, o in zip(model_names, outputs):
                bias = o["mean_model"] - o["mean_obs_paired"]
                M = get_metrics(o["lon"], o["lat"], o["mean_obs_paired"], o["mean_model"], spec["ps"], spec["axl"])
                model_cols.append((name, o["lon"], o["lat"], o["mean_model"], bias, M))

            fig = make_figure(obs_col, model_cols, spec, obs_label, date_start, date_end)
            fig_out = (f"{args.path_fig}/{obs_name}_aod550_model_verification_{stime1}Z_{stime2}Z_{region}.png")
            fig.savefig(fig_out, bbox_inches='tight', dpi=300)
            plt.close('all')
            es.msg(f"{fig_out} saved!")