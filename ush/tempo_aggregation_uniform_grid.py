import os
import re
import glob
import argparse
import numpy as np
import netCDF4 as nc
from datetime import datetime, timedelta

# =============================================================================
# TEMPO L2 -> uniform lon/lat grid, time-binned aggregation.
#
# Reads raw TEMPO_AODALH_L2_V*.nc granules, keeps only pixels that pass the per-pixel
# QC (aod550 > 0, dqf <= --dqf-max AND none of the rejected qctest bits set, see QCTEST_REJECT_BITS),
# bins them into a uniform lon/lat grid (nearest-box membership, not xESMF), and writes
# mean/std/max for aod550 and alh at each output time. For each output time t, every
# granule whose start time falls in the centered window [t - dt/2, t + dt/2) is pooled:
#   dt = 1 h -> 14z = [13:30, 14:30)      dt = 3 h -> 15z = [13:30, 16:30)
# All granules in --tempo-dir are scanned, so periods spanning several days (and the
# windows around 00z) pick up files from the adjacent days automatically.
#
# Run before the model comparison script -- the comparison script then regrids the
# model onto this single fixed TEMPO grid instead of TEMPO being regridded separately
# for every model.
#
# Usage (see --help for all options and defaults):
#   tempo_aggregation_uniform_grid.py --start 2026-10-27_00:00:00 --end 2026-10-28_00:00:00 \
#       --dt 1 --res 0.1 --tempo-dir /path/to/tempo/l2 \
#       --file-out /path/tempo_aggregated_hourly_0p1deg_2026102700.nc --dqf-max 0
# =============================================================================

# TEMPO's field of regard is North America only:
LON_MIN, LON_MAX = -150.0, -40.0
LAT_MIN, LAT_MAX = 15.0, 65.0

# qctest is bit-encoded (several bits can be on at once), so each bit is tested with a
# bitwise AND rather than by comparing the qctest value. A pixel is rejected if ANY of:
#   bit 0 (1)  ABI cloudy           bit 1 (2)  ABI probably cloudy
#   bit 2 (4)  internal test cloudy bit 3 (8)  cloud adjacent
#   bit 4 (16) high SZA or VZA
# Kept (not used for rejection): bit 5 (32) sunglint over water, bit 6 (64) snow/ice.
QCTEST_REJECT_BITS = 0b0001_1111   # = 31

TEMPO_FILENAME_TIME = re.compile(r"_(\d{8}T\d{6})Z")  # e.g. ..._20260908T163559Z_...


# -----------------------------------------------------------------------------
# Arguments / time axis
# -----------------------------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser(description="Aggregate TEMPO L2 AOD/ALH granules onto a uniform grid.",
                                formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    # No default dates on purpose -- a plausible-looking fallback date is what produced
    # two straight all-NaN "successful" runs. argparse fails loudly if they're missing.
    p.add_argument("--start", required=True, help="first output time, YYYY-MM-DD_HH:MM:SS")
    p.add_argument("--end", required=True, help="last output time bound, YYYY-MM-DD_HH:MM:SS")
    p.add_argument("--dt", type=float, default=1.0, help="output time step in hours")
    p.add_argument("--res", type=float, default=0.1, help="grid resolution in degrees")
    p.add_argument("--tempo-dir", required=True,
                   help="directory with TEMPO_AODALH_L2_V*.nc granules (several days can share it)")
    p.add_argument("--file-out", default=None,
                   help="output file (default: tempo_aggregated_<dt>_<res>deg_<start YYYYMMDDHH>.nc)")
    p.add_argument("--dqf-max", type=int, default=0,
                   help="keep pixels with dqf <= this (0=high, 1=medium, 2=low)")
    p.add_argument("--include-end", action=argparse.BooleanOptionalAction, default=True,
                   help="include END as the last output time (--no-include-end to exclude it)")
    return p.parse_args()


def parse_time(value):
    """Accepts YYYY-MM-DD_HH:MM:SS (also 'YYYY-MM-DD HH:MM:SS', YYYYMMDDHH or YYYYMMDD)."""
    for fmt in ("%Y-%m-%d_%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y%m%d%H", "%Y%m%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError(f"Time {value!r} must look like 2026-10-27_00:00:00")


def build_time_axis(start, end, dt_hours, include_end):
    """Output times start, start+dt, ... ; END excluded unless include_end."""
    step = timedelta(hours=dt_hours)
    times, t = [], start
    while t < end or (include_end and t == end):
        times.append(t)
        t += step
    if not times:
        raise ValueError("Empty time axis: END must be after START.")
    return times


# -----------------------------------------------------------------------------
# Files
# -----------------------------------------------------------------------------
def list_tempo_files(tempo_dir):
    """Lists every TEMPO L2 granule in tempo_dir with its granule start time, parsed from
    the filename (e.g. TEMPO_AODALH_L2_V02_20260908T163559Z_S009G01.nc -> 2026-09-08 16:35:59).
    Granules whose filename doesn't carry a parseable timestamp are skipped."""
    files = sorted(glob.glob(os.path.join(tempo_dir, "TEMPO_AODALH_L2_V*.nc")))
    tagged = []
    for filepath in files:
        match = TEMPO_FILENAME_TIME.search(os.path.basename(filepath))
        if not match:
            print(f"  [SKIP] {os.path.basename(filepath)}: no timestamp in filename")
            continue
        tagged.append((filepath, datetime.strptime(match.group(1), "%Y%m%dT%H%M%S")))
    return tagged


def files_in_window(tagged_files, t_center, dt_hours):
    """Granules whose start time falls in [t - dt/2, t + dt/2)."""
    half = timedelta(hours=dt_hours / 2)
    return [f for f, t in tagged_files if t_center - half <= t < t_center + half]


# -----------------------------------------------------------------------------
# Grid
# -----------------------------------------------------------------------------
def build_uniform_grid(res):
    """Builds cell-center lon/lat coordinates and bin edges for the output grid.
    n_lon/n_lat computed from the bounds so edges and centers always align exactly
    (avoids np.arange floating-point drift)."""
    n_lon = round((LON_MAX - LON_MIN) / res)
    n_lat = round((LAT_MAX - LAT_MIN) / res)
    lon_edges = LON_MIN + res * np.arange(n_lon + 1)
    lat_edges = LAT_MIN + res * np.arange(n_lat + 1)
    lon = 0.5 * (lon_edges[:-1] + lon_edges[1:])
    lat = 0.5 * (lat_edges[:-1] + lat_edges[1:])
    return lon, lat, lon_edges, lat_edges


# -----------------------------------------------------------------------------
# QC + granule reading
# -----------------------------------------------------------------------------
def qctest_pass(qctest):
    """True where none of the QCTEST_REJECT_BITS are set. qctest is a signed byte, so
    values are taken modulo 256 (& 0xFF) to recover the raw bit pattern; fill/masked
    pixels are rejected."""
    bits = np.ma.getdata(qctest).astype(np.int64) & 0xFF
    return ((bits & QCTEST_REJECT_BITS) == 0) & ~np.ma.getmaskarray(qctest)


def read_tempo_granule(filepath, dqf_max):
    """Reads one TEMPO L2 granule's aod550/alh + lon/lat, keeping pixels with
    aod550 > 0, dqf <= dqf_max and no rejected qctest bit set (see qctest_pass).
    Returns None (and logs a warning) for a granule that can't be read at all -- e.g. a
    truncated/corrupted download -- rather than letting the whole run crash on one bad file."""
    try:
        with nc.Dataset(filepath, 'r') as ds:
            print(filepath)
            # try:
            #     granule_flag = int(ds.variables['granule_level_quality_flag'][...])
            # except (KeyError, TypeError, ValueError):
            #     return None
            # if granule_flag != 0:
            #     return None

            geo = ds.groups['geolocation']
            product = ds.groups['product']
            qc = ds.groups['quality_diagnostic_flags']

            lon = np.ma.filled(geo['longitude'][:], np.nan).astype(np.float64)
            lat = np.ma.filled(geo['latitude'][:], np.nan).astype(np.float64)
            aod = np.ma.filled(product['aod550'][:], np.nan).astype(np.float64)
            alh = np.ma.filled(product['alh'][:], np.nan).astype(np.float64)
            dqf = np.ma.filled(qc['dqf'][:], 255).astype(np.int16)
            qct_ok = qctest_pass(qc['qctest'][:])

            valid = (
                np.isfinite(lon) & np.isfinite(lat)
                & (np.abs(lat) <= 90.0) & (np.abs(lon) <= 180.0)
                & np.isfinite(aod) & (aod > 0.0)          # drop AOD <= 0 (retrievals go down to -0.05)
                & (dqf <= dqf_max) & qct_ok
            )
            if not np.any(valid):
                return None

            return {
                'lon': lon[valid],
                'lat': lat[valid],
                'aod550': aod[valid],
                'alh': alh[valid],  # left as NaN where alh itself is missing; aod550 validity drives the pixel mask
            }

    except (OSError, RuntimeError, KeyError) as exc:
        print(f"  [SKIP] {os.path.basename(filepath)}: unreadable ({exc})")
        return None


# -----------------------------------------------------------------------------
# Binning
# -----------------------------------------------------------------------------
def _cell_stats(values, flat_idx, n_cells, grid_shape):
    """Per-cell mean/std/max/count of `values`, grouped by flat_idx (one entry per pixel).
    NaNs in `values` (e.g. missing alh at an otherwise-valid AOD pixel) are dropped
    from that cell's stats rather than propagated."""
    finite = np.isfinite(values)
    idx_f, val_f = flat_idx[finite], values[finite]

    count = np.bincount(idx_f, minlength=n_cells)
    vsum  = np.bincount(idx_f, weights=val_f, minlength=n_cells)
    vsum2 = np.bincount(idx_f, weights=val_f ** 2, minlength=n_cells)

    vmax = np.full(n_cells, -np.inf)
    np.maximum.at(vmax, idx_f, val_f)

    with np.errstate(invalid='ignore'):
        mean = np.where(count > 0, vsum / count, np.nan)
        var  = np.where(count > 0, vsum2 / count - mean ** 2, np.nan)
    std  = np.sqrt(np.clip(var, 0, None))
    maxv = np.where(count > 0, vmax, np.nan)

    return (mean.reshape(grid_shape).astype(np.float32),
            std.reshape(grid_shape).astype(np.float32),
            maxv.reshape(grid_shape).astype(np.float32),
            count.reshape(grid_shape).astype(np.int32))


def aggregate_window(files, lon_edges, lat_edges, grid_shape, dqf_max):
    """Pools every granule of one time window and bins their valid pixels into the uniform
    grid (nearest-box membership via np.digitize), returning per-cell mean/std/max for
    aod550 and alh, plus the aod550 pixel count per cell. All-NaN/zero-count fields if
    no granule/pixel survives QC for that window."""
    lon_parts, lat_parts, aod_parts, alh_parts = [], [], [], []
    for filepath in files:
        g = read_tempo_granule(filepath, dqf_max)
        if g is None:
            continue
        lon_parts.append(g['lon']); lat_parts.append(g['lat'])
        aod_parts.append(g['aod550']); alh_parts.append(g['alh'])

    n_cells = grid_shape[0] * grid_shape[1]
    empty_float = tuple(np.full(grid_shape, np.nan, dtype=np.float32) for _ in range(6))
    empty_count = np.zeros(grid_shape, dtype=np.int32)
    if not aod_parts:
        return empty_float + (empty_count,)

    lon = np.concatenate(lon_parts); lat = np.concatenate(lat_parts)
    aod = np.concatenate(aod_parts); alh = np.concatenate(alh_parts)

    ix = np.digitize(lon, lon_edges) - 1
    iy = np.digitize(lat, lat_edges) - 1
    in_grid = (ix >= 0) & (ix < grid_shape[1]) & (iy >= 0) & (iy < grid_shape[0])
    if not np.any(in_grid):
        return empty_float + (empty_count,)

    flat_idx = iy[in_grid] * grid_shape[1] + ix[in_grid]
    aod_mean, aod_std, aod_max, count = _cell_stats(aod[in_grid], flat_idx, n_cells, grid_shape)
    alh_mean, alh_std, alh_max, _     = _cell_stats(alh[in_grid], flat_idx, n_cells, grid_shape)
    return aod_mean, aod_std, aod_max, alh_mean, alh_std, alh_max, count


# -----------------------------------------------------------------------------
# Output (created up front, written one time slice at a time to keep memory low)
# -----------------------------------------------------------------------------
def create_output(filepath, lon, lat, times, dt_hours, res, dqf_max):
    ds = nc.Dataset(filepath, 'w', format='NETCDF4')
    ds.source = "TEMPO AODALH L2"
    ds.qc = (f"aod550 > 0; dqf <= {dqf_max}; qctest bits 0-4 all 0 (not ABI cloudy, ABI probably cloudy, "
             "internal test cloudy, cloud adjacent, high SZA/VZA)")
    ds.bounding_box = f"lon [{LON_MIN:g}, {LON_MAX:g}], lat [{LAT_MIN:g}, {LAT_MAX:g}]"
    ds.time_window = f"centered: [t - {dt_hours/2:g} h, t + {dt_hours/2:g} h), by granule start time"

    ds.createDimension('lon', len(lon))
    ds.createDimension('lat', len(lat))
    ds.createDimension('time', len(times))

    v = ds.createVariable('lon', 'f4', ('lon',)); v.long_name = 'longitude'; v.units = 'degrees_east';  v[:] = lon
    v = ds.createVariable('lat', 'f4', ('lat',)); v.long_name = 'latitude';  v.units = 'degrees_north'; v[:] = lat

    t0 = times[0]
    v = ds.createVariable('time', 'f8', ('time',))
    v.long_name = 'time'
    v.units = f"hours since {t0.strftime('%Y-%m-%d %H:%M:%S')}"
    v.calendar = 'standard'
    v[:] = [(t - t0).total_seconds() / 3600.0 for t in times]

    win = "hourly" if dt_hours == 1 else f"{dt_hours:g}-h"
    chunks = (1, len(lat), len(lon))
    def add_var(name, long_name, units, dtype='f4', fill=np.nan):
        x = ds.createVariable(name, dtype, ('time', 'lat', 'lon'), zlib=True, complevel=3,
                              fill_value=fill, chunksizes=chunks)
        x.long_name = long_name
        x.units = units

    add_var('aod550',     f'{win} mean TEMPO AOD at 550 nm, {res:g}-deg aggregated', '1')
    add_var('aod550_std', f'{win} standard deviation of TEMPO AOD at 550 nm', '1')
    add_var('aod550_max', f'{win} maximum TEMPO AOD at 550 nm', '1')
    add_var('alh',        f'{win} mean TEMPO aerosol layer height', 'km')
    add_var('alh_std',    f'{win} standard deviation of TEMPO aerosol layer height', 'km')
    add_var('alh_max',    f'{win} maximum TEMPO aerosol layer height', 'km')
    add_var('count', 'number of QC-passed TEMPO aod550 pixels aggregated into this grid cell', '1', 'i4', 0)
    return ds


def default_outname(dt_hours, res, start):
    step = "hourly" if dt_hours == 1 else f"{dt_hours:g}h"
    return f"tempo_aggregated_{step}_{f'{res:g}'.replace('.', 'p')}deg_{start:%Y%m%d%H}.nc"


# =============================================================================
# Main
# =============================================================================
if __name__ == "__main__":
    args = parse_args()
    start, end = parse_time(args.start), parse_time(args.end)
    times = build_time_axis(start, end, args.dt, args.include_end)
    file_out = args.file_out or default_outname(args.dt, args.res, start)

    print(f"tempo_dir={args.tempo_dir}")
    print(f"times: {times[0]} -> {times[-1]}  ({len(times)} steps of {args.dt:g} h)")
    print(f"res={args.res:g} deg  dqf<={args.dqf_max}  qctest reject mask={QCTEST_REJECT_BITS}  out={file_out}")

    tagged_files = list_tempo_files(args.tempo_dir)
    print(f"Found {len(tagged_files)} granule(s) in {args.tempo_dir}")

    lon, lat, lon_edges, lat_edges = build_uniform_grid(args.res)
    grid_shape = (len(lat), len(lon))

    ds = create_output(file_out, lon, lat, times, args.dt, args.res, args.dqf_max)
    total = 0
    try:
        for it, t in enumerate(times):
            files = files_in_window(tagged_files, t, args.dt)
            if not files:
                print(f"  {t:%Y-%m-%d %H:%M}z: no granules found")
                continue                     # slice stays NaN / count 0 (fill values)

            (aod_mean, aod_std, aod_max,
             alh_mean, alh_std, alh_max, cnt) = aggregate_window(files, lon_edges, lat_edges, grid_shape, args.dqf_max)
            ds['aod550'][it]     = aod_mean
            ds['aod550_std'][it] = aod_std
            ds['aod550_max'][it] = aod_max
            ds['alh'][it]        = alh_mean
            ds['alh_std'][it]    = alh_std
            ds['alh_max'][it]    = alh_max
            ds['count'][it]      = cnt
            total += int(cnt.sum())
            print(f"  {t:%Y-%m-%d %H:%M}z: {len(files)} granule(s) aggregated, {int(cnt.sum())} points gridded")
    finally:
        ds.close()

    if total == 0:
        os.remove(file_out)                  # don't leave an all-NaN file behind looking like a success
        raise RuntimeError(
            f"No TEMPO points were aggregated for {start} - {end} (found {len(tagged_files)} granule(s) "
            f"in {args.tempo_dir}). Check that --start/--end match --tempo-dir's dates and the QC filters "
            "aren't rejecting everything."
        )
    print(f"Saved {file_out}")