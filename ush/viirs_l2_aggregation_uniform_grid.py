import sys, os
import re
import glob
import argparse
import numpy as np
import netCDF4 as nc
from datetime import datetime, timedelta

# =============================================================================
# VIIRS (Enterprise JRR-AOD) L2 granules -> uniform lon/lat grid, time-binned.
#
# Processes ONE satellite per call (npp = Suomi-NPP, j01 = NOAA-20, n21 = NOAA-21), or
# --sat all to pool every pixel from all available satellites in each window (each
# pixel weighs equally, so the cell stats are pixel-weighted across satellites).
# For each output time t, every granule whose mid-time falls in the centered window
# [t - dt/2, t + dt/2) is pooled; QC-passed pixels are binned into the grid
# (nearest-box membership) and per-cell mean/std/max/count of AOD550 are written.
#   dt = 1 h -> 14z = [13:30, 14:30)      dt = 3 h -> 15z = [13:30, 16:30)
# Granules are matched by the time in their filename, so files from the previous/next
# day sitting in the same directory are picked up automatically for windows at 00z.
#
# Usage (see --help for all options and defaults):
#   viirs_l2_aggregation_uniform_grid.py --sat all \
#       --start 2026-10-27_00:00:00 --end 2026-10-28_00:00:00 --dt 1 --res 0.1 \
#       --viirs-dir /path/to/l2 --file-out /path/viirs_all_aggregated_1h_0p1deg_2026102700.nc \
#       --qcall-threshold 0 --min-pixel-fraction 0.25
# =============================================================================

# North-America bounding box
LON_MIN, LON_MAX = -180.0, -30.0
LAT_MIN, LAT_MAX = 0.0, 80.0

SATELLITES = {"npp": "Suomi-NPP", "j01": "NOAA-20", "n21": "NOAA-21"}

EARTH_RADIUS_KM = 6371.0
VIIRS_PIXEL_KM  = 0.75     # nominal M-band pixel at nadir (grows to ~1.6 km at swath edge)

# e.g. JRR-AOD_v3r2_j01_s202610271559328_e202610271600573_c202610271620253.nc
#      s/e = YYYYMMDDHHMMSS + tenths of second (ignored)
VIIRS_FILENAME = re.compile(r"JRR-AOD_v\w+?_(npp|j01|n21)_s(\d{14})\d_e(\d{14})\d_c\d+\.nc$")


# -----------------------------------------------------------------------------
# Arguments / time axis
# -----------------------------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser(description="Aggregate VIIRS L2 AOD granules onto a uniform grid.",
                                formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--sat", required=True, choices=list(SATELLITES) + ["all"],
                   help="satellite: npp (Suomi-NPP), j01 (NOAA-20), n21 (NOAA-21) or all")
    p.add_argument("--start", required=True, help="first output time, YYYY-MM-DD_HH:MM:SS")
    p.add_argument("--end", required=True, help="last output time bound, YYYY-MM-DD_HH:MM:SS")
    p.add_argument("--dt", type=float, default=1.0, help="output time step in hours")
    p.add_argument("--res", type=float, default=0.1, help="grid resolution in degrees")
    p.add_argument("--viirs-dir", default=".",
                   help="directory with JRR-AOD_*.nc granules (all days/satellites can share it)")
    p.add_argument("--file-out", default=None,
                   help="output file (default: viirs_<sat>_aggregated_<dt>_<res>deg_<start>.nc)")
    p.add_argument("--qcall-threshold", type=int, default=0,
                   help="keep pixels with QCAll <= this (0=high, 1=medium, 2=low)")
    p.add_argument("--min-pixel-fraction", type=float, default=0.25,
                   help="keep a cell only if its QC-passed pixel count is >= this fraction of the "
                        "theoretical nadir-pixel count for the cell (0 disables)")
    p.add_argument("--include-end", action=argparse.BooleanOptionalAction, default=True,
                   help="include END as the last output time (--no-include-end to exclude it)")
    return p.parse_args()


def parse_time(value):
    """Accepts YYYY-MM-DD_HH:MM:SS (also 'YYYY-MM-DD HH:MM:SS' or YYYYMMDDHH)."""
    for fmt in ("%Y-%m-%d_%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y%m%d%H"):
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
def list_viirs_files(viirs_dir, sats):
    """All granules for the requested satellite(s), tagged with their mid-time (from the
    s/e filename stamps). Returns [(filepath, mid_time, sat), ...] sorted by time."""
    paths = []
    for sat in sats:
        paths += glob.glob(os.path.join(viirs_dir, f"JRR-AOD_*_{sat}_s*.nc"))
    tagged = []
    for fp in paths:
        m = VIIRS_FILENAME.search(os.path.basename(fp))
        if not m:
            print(f"  [SKIP] {os.path.basename(fp)}: unrecognized filename")
            continue
        ts = datetime.strptime(m.group(2), "%Y%m%d%H%M%S")
        te = datetime.strptime(m.group(3), "%Y%m%d%H%M%S")
        tagged.append((fp, ts + (te - ts) / 2, m.group(1)))   # ~85 s granules; mid-time is unambiguous
    return sorted(tagged, key=lambda x: x[1])


def files_in_window(tagged_files, t_center, dt_hours):
    """Granules (any satellite in the list) whose mid-time falls in [t - dt/2, t + dt/2).
    Returns [(filepath, sat), ...]."""
    half = timedelta(hours=dt_hours / 2)
    return [(f, s) for f, t, s in tagged_files if t_center - half <= t < t_center + half]


def satellite_source_label(sats):
    """{'npp','j01','n21'} -> 'Suomi-NPP/NOAA-20/21'; {'npp','n21'} -> 'Suomi-NPP/NOAA-21';
    {'j01'} -> 'NOAA-20'; empty -> 'none'. Fixed order: Suomi-NPP, NOAA-20, NOAA-21."""
    names = [SATELLITES[s] for s in SATELLITES if s in sats]
    noaa  = [n.split("-")[1] for n in names if n.startswith("NOAA-")]   # e.g. ['20', '21']
    parts = [n for n in names if not n.startswith("NOAA-")]            # e.g. ['Suomi-NPP']
    if noaa:
        parts.append("NOAA-" + "/".join(noaa))
    return "/".join(parts) if parts else "none"


# -----------------------------------------------------------------------------
# Grid
# -----------------------------------------------------------------------------
def build_uniform_grid(res):
    """Cell-center lon/lat. n_lon/n_lat from the bounds so centers/edges align exactly."""
    n_lon = round((LON_MAX - LON_MIN) / res)
    n_lat = round((LAT_MAX - LAT_MIN) / res)
    lon = LON_MIN + res * (np.arange(n_lon) + 0.5)
    lat = LAT_MIN + res * (np.arange(n_lat) + 0.5)
    return lon, lat


def min_pixels_per_lat(lat, res, min_fraction):
    """Minimum QC-passed pixel count for a cell to be valid, one value per grid row.
    Theoretical max = exact spherical cell area / nadir pixel area:
        A = R^2 * dlon * (sin(lat_top) - sin(lat_bottom))     [km^2]
    e.g. 0.1 deg: ~220 pixels at the equator, ~155 at 45N, ~38 at 80N.
    Computed once per run (n_lat values), so it costs nothing per time step."""
    dlon = np.deg2rad(res)
    lat_top = np.deg2rad(lat + res / 2)
    lat_bot = np.deg2rad(lat - res / 2)
    area = EARTH_RADIUS_KM ** 2 * dlon * (np.sin(lat_top) - np.sin(lat_bot))
    n_theo = area / VIIRS_PIXEL_KM ** 2
    return min_fraction * n_theo


# -----------------------------------------------------------------------------
# QC + granule reading
# -----------------------------------------------------------------------------
def viirs_qc_mask(aod, qcall, qcret, qcextn, qcall_max):
    """All pixel-level filters at once. Flags are raw signed bytes (int8), so bit tests
    are done on their uint8 view (bit 7 set == negative int8 == -128 fill as well).
      - AOD550 finite and in [0, 5]   (negative retrievals, down to -0.05, are dropped)
      - QCAll in [0, qcall_max]        (fill -128 and 3=no retrieval are rejected)
      - QCRet bit 7 == 0               (not adjacent to cloud or snow)
      - QCExtn bits 0-1 < 2            (external cloud mask: conf./prob. clear only)"""
    qcret_u  = qcret.view(np.uint8)
    qcextn_u = qcextn.view(np.uint8)
    return (np.isfinite(aod) & (aod >= 0.0) & (aod <= 5.0)
            & (qcall >= 0) & (qcall <= qcall_max)
            & ((qcret_u & 0x80) == 0)
            & ((qcextn_u & 0x03) < 2))


def in_bbox(lon, lat):
    return (lon >= LON_MIN) & (lon < LON_MAX) & (lat >= LAT_MIN) & (lat < LAT_MAX)


def read_viirs_granule(filepath, qcall_max):
    """Returns QC-passed lon/lat/aod inside the bounding box, or None if the granule
    doesn't touch the box, has no valid pixels, or can't be read."""
    try:
        with nc.Dataset(filepath, "r") as ds:
            ds.set_auto_maskandscale(False)            # raw values; fills handled explicitly
            lat = ds["Latitude"][:]
            lon = ds["Longitude"][:]
            geo_ok = (lat >= -90) & (lat <= 90) & (lon >= -180) & (lon <= 180)   # drops -999 fill
            box = geo_ok & in_bbox(lon, lat)
            if not box.any():
                return None                            # cheap early exit: only lat/lon were read

            aod    = ds["AOD550"][:]
            qcall  = ds["QCAll"][:].astype(np.int8)
            qcret  = ds["QCRet"][:].astype(np.int8)
            qcextn = ds["QCExtn"][:].astype(np.int8)
    except (OSError, RuntimeError, KeyError, IndexError) as exc:
        print(f"  [SKIP] {os.path.basename(filepath)}: unreadable ({exc})")
        return None

    valid = box & viirs_qc_mask(aod, qcall, qcret, qcextn, qcall_max)
    if not valid.any():
        return None
    return {"lon": lon[valid].astype(np.float64),
            "lat": lat[valid].astype(np.float64),
            "aod550": aod[valid].astype(np.float64)}


# -----------------------------------------------------------------------------
# Binning
# -----------------------------------------------------------------------------
def _cell_stats(values, flat_idx, n_cells, grid_shape):
    """Per-cell mean/std/median/max/count of `values` grouped by flat_idx (pixel -> cell)."""
    count = np.bincount(flat_idx, minlength=n_cells)
    vsum  = np.bincount(flat_idx, weights=values, minlength=n_cells)
    vsum2 = np.bincount(flat_idx, weights=values ** 2, minlength=n_cells)

    with np.errstate(invalid="ignore", divide="ignore"):
        mean = np.where(count > 0, vsum / count, np.nan)
        var  = np.where(count > 0, vsum2 / count - mean ** 2, np.nan)
    std = np.sqrt(np.clip(var, 0, None))

    # Median and max via one sort: order pixels by cell, then by value within each cell,
    # so each cell's pixels form a contiguous, sorted block v[s : s+c].
    order = np.lexsort((values, flat_idx))           # last key (flat_idx) is the primary sort
    v = values[order]
    start = np.cumsum(count) - count                 # first position of each cell's block
    cells = np.nonzero(count)[0]                     # only cells with data
    s, c = start[cells], count[cells]
    med  = np.full(n_cells, np.nan)
    maxv = np.full(n_cells, np.nan)
    med[cells]  = 0.5 * (v[s + (c - 1) // 2] + v[s + c // 2])   # odd c: same element twice
    maxv[cells] = v[s + c - 1]                                    # last element = max

    f = lambda a, dt: a.reshape(grid_shape).astype(dt)
    return (f(mean, np.float32), f(std, np.float32), f(med, np.float32),
            f(maxv, np.float32), f(count, np.int32))


def aggregate_window(files, res, grid_shape, qcall_max, min_count):
    """Pools all granules of one time window and bins their pixels into the grid.
    Cells with fewer QC-passed pixels than min_count (per-row array) are set to NaN / 0.
    files: [(filepath, sat), ...]
    Returns (mean, std, median, max, count, n_granules_used, n_cells_rejected,
             sats_used) where sats_used = satellites with QC-passed pixels in the box."""
    lon_p, lat_p, aod_p = [], [], []
    n_used, sats_used = 0, set()
    for fp, sat in files:
        g = read_viirs_granule(fp, qcall_max)
        if g is None:                            # unreadable / outside box / no valid pixels
            continue
        lon_p.append(g["lon"]); lat_p.append(g["lat"]); aod_p.append(g["aod550"])
        n_used += 1
        sats_used.add(sat)

    n_cells = grid_shape[0] * grid_shape[1]
    if not aod_p:
        nan = np.full(grid_shape, np.nan, dtype=np.float32)
        return nan, nan, nan, nan, np.zeros(grid_shape, dtype=np.int32), 0, 0, sats_used

    lon = np.concatenate(lon_p); lat = np.concatenate(lat_p); aod = np.concatenate(aod_p)

    # Box index by floor division (equivalent to digitize on uniform edges, faster)
    ix = np.floor((lon - LON_MIN) / res).astype(np.int64)
    iy = np.floor((lat - LAT_MIN) / res).astype(np.int64)
    ok = (ix >= 0) & (ix < grid_shape[1]) & (iy >= 0) & (iy < grid_shape[0])
    flat_idx = iy[ok] * grid_shape[1] + ix[ok]

    mean, std, med, vmax, cnt = _cell_stats(aod[ok], flat_idx, n_cells, grid_shape)

    # Sparse-cell filter: too few pixels (e.g. cloud edges) -> not representative of the cell
    sparse = (cnt > 0) & (cnt < min_count[:, None])   # min_count broadcast along lon
    for a in (mean, std, med, vmax):
        a[sparse] = np.nan
    cnt[sparse] = 0
    return mean, std, med, vmax, cnt, n_used, int(sparse.sum()), sats_used


# -----------------------------------------------------------------------------
# Output (created up front, written one time slice at a time to keep memory low)
# -----------------------------------------------------------------------------
def create_output(filepath, lon, lat, times, name, dt_hours, res, qcall_max, min_fraction):
    """name: satellite label, e.g. 'NOAA-20' or 'Suomi-NPP+NOAA-20+NOAA-21'."""
    ds = nc.Dataset(filepath, "w", format="NETCDF4")
    ds.createDimension("lon", len(lon))
    ds.createDimension("lat", len(lat))
    ds.createDimension("time", len(times))

    v = ds.createVariable("lon", "f4", ("lon",)); v.long_name = "longitude"; v.units = "degrees_east"; v[:] = lon
    v = ds.createVariable("lat", "f4", ("lat",)); v.long_name = "latitude";  v.units = "degrees_north"; v[:] = lat

    t0 = times[0]
    v = ds.createVariable("time", "f8", ("time",))
    v.long_name = "time"
    v.units = f"hours since {t0.strftime('%Y-%m-%d %H:%M:%S')}"
    v.calendar = "standard"
    v[:] = [(t - t0).total_seconds() / 3600.0 for t in times]

    win = f"{dt_hours:g}-h"
    chunks = (1, len(lat), len(lon))
    def add(vname, long_name, dtype="f4", fill=np.nan):
        x = ds.createVariable(vname, dtype, ("time", "lat", "lon"), zlib=True, complevel=3,
                              fill_value=fill, chunksizes=chunks)
        x.long_name = long_name; x.units = "1"
    add("aod550",     f"{win} mean VIIRS {name} AOD at 550 nm, {res:g}-deg aggregated")
    add("aod550_std", f"{win} standard deviation of VIIRS {name} AOD at 550 nm")
    add("aod550_median", f"{win} median VIIRS {name} AOD at 550 nm")
    add("aod550_max", f"{win} maximum VIIRS {name} AOD at 550 nm")
    add("count", f"number of QC-passed VIIRS {name} aod550 pixels aggregated into this grid cell", "i4", 0)

    ds.satellite = name                      # updated at the end to satellites with data
    ds.source = "NOAA Enterprise VIIRS AOD L2 (JRR-AOD)"
    ds.bounding_box = f"lon [{LON_MIN:g}, {LON_MAX:g}], lat [{LAT_MIN:g}, {LAT_MAX:g}]"
    ds.geospatial_lon_min, ds.geospatial_lon_max = LON_MIN, LON_MAX
    ds.geospatial_lat_min, ds.geospatial_lat_max = LAT_MIN, LAT_MAX
    ds.qc = (f"QCAll <= {qcall_max}; QCRet bit7 (adjacent cloud/snow) == 0; "
             "QCExtn bits0-1 (cloud mask) in {0,1}; 0 <= AOD550 <= 5")
    ds.min_pixel_fraction = (f"{min_fraction:g}: cells with fewer QC-passed pixels than this fraction of "
                             f"cell_area / ({VIIRS_PIXEL_KM} km)^2 are set to missing")
    ds.time_window =f"centered: [t - {dt_hours/2:g} h, t + {dt_hours/2:g} h), by granule mid-time"
    return ds


def default_outname(sat, dt_hours, res, start):
    step = "hourly" if dt_hours == 1 else f"{dt_hours:g}h"
    return f"viirs_{sat}_aggregated_{step}_{f'{res:g}'.replace('.', 'p')}deg_{start:%Y%m%d%H}.nc"


# =============================================================================
# Main
# =============================================================================
if __name__ == "__main__":
    args = parse_args()
    start, end = parse_time(args.start), parse_time(args.end)
    times = build_time_axis(start, end, args.dt, args.include_end)

    viirs_dir    = args.viirs_dir
    file_out     = args.file_out or default_outname(args.sat, args.dt, args.res, start)
    qcall_max    = args.qcall_threshold
    min_fraction = args.min_pixel_fraction

    sats = list(SATELLITES) if args.sat == "all" else [args.sat]
    print(f"sat={args.sat} ({', '.join(SATELLITES[s] for s in sats)})  viirs_dir={viirs_dir}")
    print(f"times: {times[0]} -> {times[-1]}  ({len(times)} steps of {args.dt:g} h)")
    print(f"res={args.res:g} deg  QCAll<={qcall_max}  min_pixel_fraction={min_fraction:g}  out={file_out}")

    tagged = list_viirs_files(viirs_dir, sats)
    found = [s for s in sats if any(x[2] == s for x in tagged)]     # satellites actually present
    for s in sats:
        print(f"  {s}: {sum(x[2] == s for x in tagged)} granule(s)")
    if not tagged:
        print(f"No {args.sat} granules in {viirs_dir}; nothing written.")
        sys.exit(0)            # missing satellite is not an error -- the bash driver just moves on
    sat_label = "+".join(SATELLITES[s] for s in found)                # e.g. Suomi-NPP+NOAA-20+NOAA-21

    lon, lat = build_uniform_grid(args.res)
    grid_shape = (len(lat), len(lon))
    min_count = min_pixels_per_lat(lat, args.res, min_fraction)
    print(f"min pixels/cell: {min_count[np.argmin(abs(lat - 0))]:.0f} (eq), "
        f"{min_count[np.argmin(abs(lat - 45))]:.0f} (45N), {min_count[np.argmin(abs(lat - 70))]:.0f} (70N)")

    ds = create_output(file_out, lon, lat, times, sat_label, args.dt, args.res, qcall_max, min_fraction)
    total = 0
    sats_with_data = set()                   # satellites with QC-passed pixels in box/period
    try:
        for it, t in enumerate(times):
            files = files_in_window(tagged, t, args.dt)
            if not files:
                print(f"  {t:%Y-%m-%d %H:%M}z: no granules")
                continue                     # slice stays NaN / count 0 (fill values)
            mean, std, med, vmax, cnt, n_used, n_rej, used = aggregate_window(
                files, args.res, grid_shape, qcall_max, min_count)
            sats_with_data |= used
            ds["aod550"][it]        = mean
            ds["aod550_std"][it]    = std
            ds["aod550_median"][it] = med
            ds["aod550_max"][it] = vmax
            ds["count"][it]      = cnt
            total += int(cnt.sum())
            print(f"  {t:%Y-%m-%d %H:%M}z: {len(files)} granule(s) in window, "
                f"{n_used} with valid pixels in box, {int(cnt.sum())} points gridded, "
                f"{n_rej} sparse cell(s) rejected")

        # Satellites that actually contributed data in this period and bounding box
        ds.satellite_source = satellite_source_label(sats_with_data)   # e.g. Suomi-NPP/NOAA-20/21
        ds.satellite = "+".join(SATELLITES[s] for s in SATELLITES if s in sats_with_data) or "none"
        print(f"satellite_source = {ds.satellite_source}")
    finally:
        ds.close()

    if total == 0:
        print("WARNING: no VIIRS points were aggregated (night-only window, or QC rejected everything).")
    print(f"Saved {file_out}")