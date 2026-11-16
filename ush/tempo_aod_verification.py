import sys, os
import csv
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
# Model-comparison step of the TEMPO verification pipeline.
#
# No cropping or regridding happens here: the calling bash script already used CDO
# (remapbil) to put the model's AOD on the TEMPO aggregated grid, so model and TEMPO
# come in co-located (checked by check_grids_match).
#
# Verification period: --date-start .. --date-end (BOTH inclusive), not necessarily
# every time in the TEMPO file. The output time axis is the regular sequence
# date_start, date_start + dt, ..., <= date_end (dt = TEMPO file's time step, 1 h by
# default), so the output is always the same shape: TEMPO/model hours that aren't
# available are written as NaN (with a warning) instead of being dropped.
# Each model value is the mean of the model steps within [t - dt/2, t + dt/2) of the
# TEMPO time t.
#
# Usage (see --help):
#   # 1) save one model's hourly paired NetCDF
#   tempo_aod_verification.py --save-netcdf --date-start 2026-10-28_12:00:00 --date-end 2026-10-29_12:00:00 \
#       --model-name RRFS-SD --file-model aod_RRFS-SD_2026102700.nc --model-var AOD550 \
#       --tempo-file tempo_aggregated_hourly_0p1deg_2026102812.nc --file-out tempo_RRFS-SD_2026102700.nc
#   # 2) plot several models' saved NetCDFs together
#   tempo_aod_verification.py --make-figure --date-start 2026-10-28_12:00:00 --date-end 2026-10-29_12:00:00 \
#       --model-names RRFS-SD HRRR-Smoke --plot-files tempo_RRFS-SD_2026102700.nc tempo_HRRR-Smoke_2026102700.nc \
#       --path-fig ./figs --regions-csv lambert_map_settings.csv
# =============================================================================

NO_DATA_MSG = "No data available for current times"
METRICS_TO_SHOW = ["R", "MB", "cRMSE"]

font = fm.FontProperties(fname="/scratch3/BMC/acomp/Gonzalo.Ferrada/verif/realtime/sandbox/OpenSans-Regular.ttf")
for f in fm.findSystemFonts(fontpaths=["/scratch3/BMC/acomp/Gonzalo.Ferrada/verif/realtime/sandbox/Google_Sans_Code/static"]):
    fm.fontManager.addfont(f)
fmono = fm.FontProperties(family="Google Sans Code", size=5, weight="light")


# Levels:
lev_aod         = np.concatenate([[0, 0.05, 0.1, 0.15], np.arange(2, 11) / 10.0, [1.2, 1.5, 2.0]])
lev_bias_pos    = np.concatenate([[0.05], np.arange(1, 7) / 10.0])
lev_bias        = np.sort(np.concatenate([-lev_bias_pos, lev_bias_pos]))

# Colormaps
cm1 = es.hue([227,227,227], [136,183,211], 5)
cm2 = es.hue([85,180,94],[251,232,135],[252,170,95],[242,114,69],[194,27,38],[135,27,87],[255,192,203], len(lev_aod) - 1 - 5)
cmap_aod = ListedColormap(np.vstack((cm1.colors, cm2.colors)))
cmap_bias = es.hue('pan', len(lev_bias) - 1)

REGIONS = ["CONUS", "R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "R10"]


# =============================================================================
# Arguments
# =============================================================================
def parse_args():
    p = argparse.ArgumentParser(description="Model vs. TEMPO AOD verification (save paired NetCDF and/or plot).",
                                formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--date-start", required=True, help="first verification time (inclusive), YYYY-MM-DD_hh:mm:ss")
    p.add_argument("--date-end", required=True, help="last verification time (inclusive), YYYY-MM-DD_hh:mm:ss")
    p.add_argument("--save-netcdf", action="store_true", help="pair one model with TEMPO and save the hourly NetCDF")
    p.add_argument("--make-figure", action="store_true", help="plot the saved NetCDF(s) of one or more models")

    s = p.add_argument_group("--save-netcdf options")
    s.add_argument("--model-name", help="model name (log/attributes only)")
    s.add_argument("--file-model", help="model AOD file, already CDO-regridded onto the TEMPO grid")
    s.add_argument("--model-var", default="AOD550",
                   help="AOD variable(s) in --file-model, comma-separated, e.g. 'AOD550,AOD550_SIMPLE'. "
                        "The first is saved as aod_model, each extra as aod_model_<suffix> (see model_out_name)")
    s.add_argument("--tempo-file", help="TEMPO aggregated file (tempo_aggregated_*.nc)")
    s.add_argument("--file-out", help="output NetCDF (paired hourly model/TEMPO AOD)")

    g = p.add_argument_group("--make-figure options")
    g.add_argument("--model-names", nargs="+", help="model names, one per --plot-files entry (column titles)")
    g.add_argument("--plot-files", nargs="+", help="NetCDFs written by --save-netcdf, one per model")
    g.add_argument("--path-fig", default="./", help="output directory for the figures")
    g.add_argument("--regions-csv", help="lambert_map_settings CSV with the plotting regions")

    args = p.parse_args()
    if not (args.save_netcdf or args.make_figure):
        p.error("nothing to do: pass --save-netcdf and/or --make-figure")
    if args.save_netcdf:
        missing = [o for o, v in (("--file-model", args.file_model), ("--tempo-file", args.tempo_file),
                                  ("--file-out", args.file_out)) if not v]
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
# Functions
# =============================================================================
def is_valid_file(filepath):
    """Returns True if filepath points to a real, usable file (i.e. not "None"/empty/missing)."""
    return bool(filepath) and filepath.lower() != "none" and os.path.isfile(filepath)

def load_map_regions(csv_path):
    """Reads a Lambert-region-specs CSV into
    {region: {'ps': [cen_lat, cen_lon], 'axl': [x1, x2, y1, y2], 'mapres': str}}."""
    regions = {}
    with open(csv_path, newline='') as f:
        for row in csv.DictReader(f):
            regions[row['region']] = {
                'ps': [float(row['cen_lat']), float(row['cen_lon'])],
                'axl': [float(row['x1']), float(row['x2']), float(row['y1']), float(row['y2'])],
                'mapres': row['MapRes'],
            }
    return regions

def region_rows(region, map_regions):
    """Builds the (label, ps, axl, mapres) row list for a map figure: a single row per region."""
    r = map_regions[region]
    return [(region, r['ps'], r['axl'], r['mapres'])]

def _read_times(time_var):
    """Converts a NetCDF time variable to an array of python datetimes using its own units/calendar."""
    if not hasattr(time_var, 'units'):
        raise ValueError(f"Time variable '{time_var.name}' has no 'units' attribute")
    return np.array(
        nc.num2date(
            time_var[:],
            units=time_var.units,
            calendar=getattr(time_var, 'calendar', 'standard'),
            only_use_cftime_datetimes=False,
            only_use_python_datetimes=True
        )
    )

def _to_nan(arr):
    """Masked array -> float32 ndarray with NaN where masked/fill."""
    return np.ma.filled(np.ma.asarray(arr, dtype=np.float32), np.nan)

def model_out_name(var, primary):
    """Output NetCDF name for a model AOD variable: the primary (first) one is 'aod_model';
    each extra is 'aod_model_<suffix>', where suffix is the variable name with the primary's
    name + '_' stripped, lower case:
        AOD550_SIMPLE (primary AOD550) -> aod_model_simple
        MIE_AOD                        -> aod_model_mie_aod"""
    if var == primary:
        return "aod_model"
    suffix = var[len(primary) + 1:] if var.startswith(primary + "_") else var
    return f"aod_model_{suffix.lower()}"

def column_label(model_name, out_name):
    """Column title from an aod_model* variable name:
        aod_model                   -> MPAS-Aerosols
        aod_model_simple            -> MPAS-Aerosols (SIMPLE)
        aod_model_mie_calculation   -> MPAS-Aerosols (MIE CALCULATION)"""
    if out_name == "aod_model":
        return model_name
    suffix = out_name[len("aod_model_"):].replace("_", " ").upper()
    return f"{model_name} ({suffix})"

def read_model_data(filepath, model_vars):
    """Reads the model AOD variable(s) `model_vars` (list, first = primary) from a NetCDF file
    already bilinearly regridded (CDO remapbil) onto the TEMPO grid. The model's native time
    axis is kept as-is. Returns {'lon','lat','time','aod': {var: array}}; extra variables
    missing from the file are skipped with a warning (a missing primary is an error)."""
    with nc.Dataset(filepath, 'r') as ds:
        for name in ('time', 'XTIME', 'Time'):
            if name in ds.variables:
                time_var = ds.variables[name]
                break
        else:
            raise KeyError(f"Could not find either 'time' or 'XTIME' or 'Time' in {filepath}")

        times = _read_times(time_var)
        lon = ds.variables['lon'][:]
        lat = ds.variables['lat'][:]

        aods = {}
        for i, var in enumerate(model_vars):
            if var not in ds.variables:
                if i == 0:
                    raise KeyError(f"Primary model AOD variable '{var}' not found in {filepath}")
                es.msg(f"  [WARN] '{var}' not found in {filepath}, skipping it")
                continue
            aod = _to_nan(ds.variables[var][:])
            # Some models report AOD as exactly zero everywhere at the first time step
            # (spin-up artifact); left as-is this biases the matched hourly mean low.
            if aod.shape[0] > 0 and np.nansum(aod[0]) == 0:
                es.msg(f"  First model time step has zero {var} everywhere, setting to NaN")
                aod[0] = np.nan
            aods[var] = aod

        return {'lon': lon, 'lat': lat, 'time': times, 'aod': aods}

def read_tempo_netcdf(filepath):
    """Reads the pre-aggregated TEMPO file (time, lat, lon) and its time axis."""
    with nc.Dataset(filepath, 'r') as ds:
        times = _read_times(ds.variables['time'])
        return {
            'lon': ds.variables['lon'][:],
            'lat': ds.variables['lat'][:],
            'time': times,
            'aod': _to_nan(ds.variables['aod550'][:]),
        }

def build_period_axis(date_start, date_end, tempo_time):
    """Regular output times date_start, date_start + dt, ..., <= date_end (both ends
    inclusive). dt is the TEMPO file's time step (1 h if it can't be determined)."""
    dt = (tempo_time[1] - tempo_time[0]) if len(tempo_time) > 1 else timedelta(hours=1)
    times, t = [], date_start
    while t <= date_end:
        times.append(t)
        t += dt
    if not times:
        raise ValueError("Empty verification period: --date-end must be >= --date-start.")
    return np.array(times), dt

def select_tempo_period(tempo, period_time):
    """TEMPO AOD on the period's time axis: the file's slice where its time matches a
    period time exactly, NaN where the file has no such time."""
    out = np.full((len(period_time),) + tempo['aod'].shape[1:], np.nan, dtype=np.float32)
    index = {t: i for i, t in enumerate(tempo['time'])}
    n_found = 0
    for i, t in enumerate(period_time):
        j = index.get(t)
        if j is not None:
            out[i] = tempo['aod'][j]
            n_found += 1
    if n_found < len(period_time):
        es.msg(f"  [WARN] {len(period_time) - n_found} of {len(period_time)} period time(s) are not in the TEMPO file "
               f"({tempo['time'][0]} .. {tempo['time'][-1]}); filled with NaN")
    return out

def check_grids_match(model, tempo):
    """Verifies the model (post-CDO-regrid) and TEMPO grids actually line up -- if CDO's
    target grid wasn't really TEMPO's grid, every pixel-wise comparison below would be
    silently wrong, so this fails loudly instead."""
    model_shape = next(iter(model['aod'].values())).shape[1:]
    if model_shape != tempo['aod'].shape[1:]:
        raise ValueError(f"Model grid {model_shape} != TEMPO grid {tempo['aod'].shape[1:]} -- CDO regrid target mismatch?")
    if not (np.allclose(model['lon'], tempo['lon'], atol=1e-3) and np.allclose(model['lat'], tempo['lat'], atol=1e-3)):
        raise ValueError("Model and TEMPO lon/lat coordinates don't match after CDO regridding.")

def match_model_to_obs_times(model_time, model_aod, obs_time, dt):
    """For each time t in obs_time, averages model_aod over the model steps in
    [t - dt/2, t + dt/2) (dt = 1 h -> 14z <- model steps in [13:30, 14:30)).
    NaN where no model step falls in the window."""
    matched = np.full((len(obs_time),) + model_aod.shape[1:], np.nan, dtype=np.float32)
    half = dt / 2
    n_missing = 0
    for i, t in enumerate(obs_time):
        idx = (model_time >= t - half) & (model_time < t + half)
        if not np.any(idx):
            n_missing += 1
            continue
        with np.errstate(invalid='ignore'), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)          # all-NaN cells
            matched[i] = np.nanmean(model_aod[idx], axis=0)
    if n_missing:
        es.msg(f"  [WARN] {n_missing} of {len(obs_time)} period time(s) have no model step within "
               f"+/-{half} (model covers {model_time[0]} .. {model_time[-1]}); filled with NaN")
    return matched

def write_output_netcdf(filepath, lon, lat, time_axis, model_aods, tempo_aod, attrs=None):
    """Writes the paired, still-hourly model/TEMPO AOD to a NetCDF4 file (NaN where
    unavailable). model_aods: {out_name: (source_var, array)}, out_name = aod_model or
    aod_model_<suffix>. The period mean is NOT computed here -- the plotting step does it."""
    t0 = time_axis[0]

    with nc.Dataset(filepath, "w", format="NETCDF4") as ds:
        ds.createDimension("lon", len(lon))
        ds.createDimension("lat", len(lat))
        ds.createDimension("time", len(time_axis))

        var_lon = ds.createVariable("lon", "f4", ("lon",))
        var_lon.long_name = "longitude"
        var_lon.units = "degrees_east"
        var_lon[:] = lon

        var_lat = ds.createVariable("lat", "f4", ("lat",))
        var_lat.long_name = "latitude"
        var_lat.units = "degrees_north"
        var_lat[:] = lat

        var_time = ds.createVariable("time", "f8", ("time",))
        var_time.long_name = "time"
        var_time.units = f"hours since {t0.strftime('%Y-%m-%d %H:%M:%S')}"
        var_time.calendar = "standard"
        var_time[:] = [(t - t0).total_seconds() / 3600.0 for t in time_axis]

        def add_var(name, data, long_name):
            v = ds.createVariable(name, "f4", ("time", "lat", "lon"), zlib=True, complevel=3, fill_value=np.nan)
            v.long_name = long_name
            v[:, :, :] = np.asarray(data, dtype=np.float32)

        add_var("aod_tempo", tempo_aod, "hourly TEMPO aerosol optical depth")
        for out_name, (src_var, data) in model_aods.items():
            add_var(out_name, data, f"hourly model {src_var}, matched to TEMPO's time steps")
            ds[out_name].source_variable = src_var

        for k, val in (attrs or {}).items():
            setattr(ds, k, val)

def read_output_netcdf(filepath):
    """Reads back a per-model hourly NetCDF file written by write_output_netcdf.
    'models' holds every aod_model* variable in the file, aod_model first, then the
    extras in file order: {out_name: array}."""
    with nc.Dataset(filepath, 'r') as ds:
        names = [v for v in ds.variables if v == 'aod_model' or v.startswith('aod_model_')]
        names.sort(key=lambda v: v != 'aod_model')              # aod_model first, rest keep order
        return {
            'lon': ds.variables['lon'][:],
            'lat': ds.variables['lat'][:],
            'aod_tempo': _to_nan(ds.variables['aod_tempo'][:]),
            'models': {v: _to_nan(ds.variables[v][:]) for v in names},
        }

def nanmean_time(arr):
    """NaN-ignoring mean over time (axis 0), silent for all-NaN cells."""
    with np.errstate(invalid='ignore'), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmean(arr, axis=0)


# =============================================================================
# Plotting
# =============================================================================
def no_data_metrics():
    """Stand-in for es.metrics when there are no valid pairs: same .Text interface, values N/A."""
    return SimpleNamespace(Text=[f"{m}: N/A" for m in METRICS_TO_SHOW])

def get_metrics(lon, lat, obs, model, origin, axlims):
    """Computes metrics over the pixels actually visible in a plot panel (N/A if none valid).
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
    """Spatial AOD comparison. columns: list of (label, lon, lat, field) tuples, column 0 is TEMPO.
    rows: list of (label, ps, axl, mapres) tuples, one per map row. `field` is the period
    (time-nanmean'd) 2D array."""
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
    """Bias (model - TEMPO) comparison. columns: list of (label, lon, lat, field) tuples, one per model.
    rows: list of (label, ps, axl, mapres) tuples, one per map row.
    M[i][row] holds the metrics for model i over the pixels shown in that row's panel (see get_metrics)."""
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
        es.msg(f"TEMPO_FILE={args.tempo_file}")

        # 1) Model -- already regridded onto TEMPO's grid by CDO upstream
        es.msg("Reading model data...")
        model = read_model_data(args.file_model, model_vars)

        # 2) TEMPO pre-aggregated data
        es.msg("Reading TEMPO data...")
        tempo = read_tempo_netcdf(args.tempo_file)
        check_grids_match(model, tempo)

        # 3) Period time axis (inclusive) and TEMPO/model on it
        period_time, dt = build_period_axis(date_start, date_end, tempo['time'])
        es.msg(f"  {len(period_time)} time step(s) of {dt} in the period")
        tempo_aod = select_tempo_period(tempo, period_time)

        # 4) Each model variable time-matched -> {out_name: (source_var, array)}
        if not np.any(np.isfinite(tempo_aod)):
            es.msg(f"  [WARN] No TEMPO data available between {date_start} and {date_end}")
        model_matched = {}
        for var, aod in model['aod'].items():
            out_name = model_out_name(var, model_vars[0])
            es.msg(f"Matching model {var} to TEMPO's times -> {out_name}")
            matched = match_model_to_obs_times(model['time'], aod, period_time, dt)
            # No-data warning (the file is still written, NaN-filled)
            if not np.any(np.isfinite(matched)):
                es.msg(f"  [WARN] No {args.model_name or 'model'} {var} data available between {date_start} and {date_end}")
            model_matched[out_name] = (var, matched)

        es.msg(f"Saving NetCDF output ({len(period_time)} time steps)...")
        write_output_netcdf(args.file_out, tempo['lon'], tempo['lat'], period_time,
                            model_matched, tempo_aod,
                            attrs={"model": args.model_name or "",
                                   "model_file": args.file_model,
                                   "model_variables": ",".join(model['aod']),
                                   "tempo_file": args.tempo_file,
                                   "period": f"[{date_start:%Y-%m-%d %H:%M}, {date_end:%Y-%m-%d %H:%M}] UTC (inclusive)"})
        es.msg(f"Saved NetCDF output to {args.file_out}")


    if args.make_figure:
        es.msg("Plotting...")

        # labels for output figure name
        stime1 = date_start.strftime("%Y-%m-%d_%H")
        stime2 = date_end.strftime("%Y-%m-%d_%H")
        ymd = date_start.strftime('%Y-%m-%d')

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

        obs_label = "TEMPO"

        es.msg("Reading per-model NetCDF output...")
        outputs = [read_output_netcdf(f) for f in model_files]

        # Period mean (nanmean across time). The model is masked to TEMPO's own per-hour
        # validity first, so hours/pixels TEMPO never observed don't enter the model mean.
        # One column per aod_model* variable in each file: aod_model -> "NAME",
        # aod_model_<suffix> -> "NAME (SUFFIX)" (see column_label).
        model_specs = []                     # (column label, output dict, key of its period mean)
        for name, o in zip(model_names, outputs):
            tempo_valid = np.isfinite(o['aod_tempo'])
            o['aod_tempo_daily'] = nanmean_time(o['aod_tempo'])
            for out_name, data in o['models'].items():
                key = f"{out_name}_daily"
                o[key] = nanmean_time(np.where(tempo_valid, data, np.nan))
                label = column_label(name, out_name)
                if not np.any(np.isfinite(o[key])):
                    es.msg(f"  [WARN] {label}: no paired model/TEMPO data in this period -- plotting NaNs")
                model_specs.append((label, o, key))

        spatial_columns = [(obs_label, outputs[0]['lon'], outputs[0]['lat'], outputs[0]['aod_tempo_daily'])]
        spatial_columns += [(name, o['lon'], o['lat'], o[field]) for name, o, field in model_specs]

        # Plot spatial_overlay
        for region in REGIONS:
            if region not in map_regions:
                es.msg(f"  {region}: not in {os.path.basename(args.regions_csv)}, skipping")
                continue
            rows = region_rows(region, map_regions)

            make_spatial_figure(spatial_columns, rows, lev_aod, cmap_aod, date_start, date_end, obs_label)

            fig_out = f"{args.path_fig}/plot_grp2.spatial_overlay.aod_550nm.{stime1}.{stime2}.{region}.TEMPO_{ymd}.png"
            plt.gcf().savefig(fig_out, bbox_inches='tight', dpi=200)
            plt.close('all')
            es.msg(f"{fig_out} saved!")

        # Plot spatial_bias
        bias_columns = [(name, o['lon'], o['lat'], o[field] - o['aod_tempo_daily'])
                         for name, o, field in model_specs]

        for region in REGIONS:
            if region not in map_regions:
                continue
            rows = region_rows(region, map_regions)

            M = [[get_metrics(o['lon'], o['lat'], o['aod_tempo_daily'], o[field], row_ps, row_axl)
                  for (_, row_ps, row_axl, _) in rows]
                 for _, o, field in model_specs]

            make_bias_figure(bias_columns, M, rows, lev_bias, cmap_bias, date_start, date_end, obs_label)

            fig_out = f"{args.path_fig}/plot_grp3.spatial_bias.aod_550nm.{stime1}.{stime2}.{region}.TEMPO_{ymd}.png"
            plt.gcf().savefig(fig_out, bbox_inches='tight', dpi=200)
            plt.close('all')
            es.msg(f"{fig_out} saved!")