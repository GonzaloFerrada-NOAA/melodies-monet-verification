#!/usr/bin/env python
"""
Build the verification website for one cycle.

    <web_dir>/index.html                 ALL settings (map, colours, labels, scales, experiments,
                                         ...), the left panel and the map viewer; rebuilt on every
                                         run, so every cycle (old and new) uses the latest settings
    <web_dir>/<YYYYMMDDHH>/<page>.html   DATA ONLY, one file per dataset (airnow, aeronet, hms,
                                         viirs, tempo, viirs-models-bias, ...): index.html loads it
                                         in a hidden frame and starts the viewer with it
    <web_dir>/<YYYYMMDDHH>/cycle.json    window of the cycle (read when index.html is built)

The data files are not meant to be opened on their own; always go through index.html.

Inputs (paths templates in the yaml 'paths' section):
    paired      MELODIES-MONET paired netCDF, <species>_<CDATE>_<platform>_<exp>.nc4 (time, x)
    hms_fires   HMS fire text files, one per day of the window
    viirs/tempo hourly aggregated AOD (lon, lat, time, aod550, count)
    sat_bias    satellite_aod_verification.py output (obs_aod, model_aod)

Usage:
    python generate_website.py website.yaml --cycle YYYYMMDDHH \
           --experiments EXP1 [EXP2 ...] [--experiments-yaml experiments.yaml] [--pages airnow hms ...]
"""
import argparse
import base64
import glob
import json
import math
import os
import re
import struct
import sys
import zlib
from collections import Counter
from datetime import datetime

import numpy as np
import pandas as pd
import yaml

# Experiment colours, given in the order of --experiments (then experiments.fallback_colors)
EXP_COLORS = ["#4269D0", "#FF725C", "#EFB118", "#6CC5B0", "#9c6b4e",
              "#a463f2", "#ff89b7", "#3ca951", "#97bbf5"]
PLOTLY_CDN = "https://cdn.plot.ly/plotly-2.30.0.min.js"

# =========================================================================
# 1. SETTINGS
# =========================================================================
ap = argparse.ArgumentParser(description="Build the verification website (index + pages of one cycle).")
ap.add_argument("config", help="yaml settings (filled html_dashboard.yaml)")
ap.add_argument("--cycle", required=True, help="cycle, YYYYMMDDHH (= CDATE)")
ap.add_argument("--experiments", nargs="+", required=True,
                help="experiment names (experiments.yaml keys); colours are given in this order")
ap.add_argument("--experiments-yaml", default=None,
                help="experiments.yaml: species mapping and output_interval_hr of each experiment")
ap.add_argument("--pages", nargs="+", default=None, help="build only these pages (default: all in 'pages')")
ARGS = ap.parse_args()
with open(ARGS.config) as f:
    cfg = yaml.safe_load(f)

CDATE = ARGS.cycle
if not re.fullmatch(r"\d{10}", CDATE):
    sys.exit(f"--cycle must be YYYYMMDDHH, got {CDATE!r}")
CYCLE = pd.Timestamp(datetime.strptime(CDATE, "%Y%m%d%H"))
WIN_START = CYCLE - pd.Timedelta(hours=float(cfg.get("window_hours_before", 0)))
WIN_END = CYCLE + pd.Timedelta(hours=float(cfg["window_hours_after"]))
WIN_HOURS = int(round((WIN_END - WIN_START) / pd.Timedelta(hours=1)))
WEB_DIR = os.path.abspath(cfg["web_dir"])
CYCLE_DIR = os.path.join(WEB_DIR, CDATE)
PAGES = cfg["pages"]
DECIMALS = cfg.get("decimals") or {}
SPEC_ALIAS = cfg.get("species_aliases") or {}       # workflow species name -> name shown


def path_of(key, **kw):
    """Fill a 'paths' template: {cdate}, {yyyymmdd}, {date:%...} + the keywords given."""
    fields = {"cdate": CDATE, "yyyymmdd": CDATE[:8], "date": CYCLE}
    fields.update(kw)
    return cfg["paths"][key].format(**fields)


def decimals_for(var):
    """Decimal places for a variable (config 'decimals', else its 'default')."""
    return int(DECIMALS.get(var, DECIMALS.get("default", 2)))


def rnd(v, nd):
    """Round to nd decimals; NaN/inf/None -> None (JSON null = gap in the plot)."""
    if v is None:
        return None
    v = float(v)
    if math.isnan(v) or math.isinf(v):
        return None
    r = round(v, nd)
    return int(r) if r.is_integer() else r


def js_literal(obj):
    """Compact JSON that is also safe inside <script> (no '</script>' breakouts)."""
    return json.dumps(obj, separators=(",", ":"), allow_nan=False).replace("</", "<\\/")


def write_atomic(path, text):
    """Write via a temporary file + rename: a viewer never sees a half-written page."""
    tmp = f"{path}.tmp{os.getpid()}"
    with open(tmp, "w") as f:
        f.write(text)
    os.chmod(tmp, 0o644)                                  # readable by the web server
    os.replace(tmp, path)


# ---- Experiments: names from the command line, details from experiments.yaml ----
xyaml = {}
if ARGS.experiments_yaml:
    with open(ARGS.experiments_yaml) as f:
        xyaml = yaml.safe_load(f) or {}
ecfg = cfg.get("experiments") or {}
palette = EXP_COLORS + list(ecfg.get("fallback_colors") or [])
labels = ecfg.get("labels") or {}
EXPS = []
for k, name in enumerate(ARGS.experiments):
    x = xyaml.get(name) or {}
    EXPS.append({
        "id": name,
        "label": str(labels.get(name, name)),
        "color": palette[k % len(palette)],
        "style": ecfg.get("default_style", "solid"),
        # hours between model outputs: where its lines break
        "every": int(x.get("output_interval_hr") or ecfg.get("default_output_hours", 1)),
        # obs species (lower case) -> model variable, to find the model column in the paired files
        "species": {str(obs).lower(): str(mod) for mod, obs in (x.get("species") or {}).items()},
    })
OBS_STYLE = ecfg.get("observation") or {}
OBSERVATION = {"label": OBS_STYLE.get("label", "Observation"), "color": OBS_STYLE.get("color", "#000000")}
print(f"Cycle {CDATE}, window {WIN_START} .. {WIN_END} ({WIN_HOURS} h)")
print("Experiments: " + ", ".join(f"{e['id']} ({e['color']})" for e in EXPS))

# ---- Discrete colour scales. Bound i = lower edge of bin i. A variable uses its own
# 'colors' if given, otherwise the first len(bounds) default colours.
color_scales = {}
cs = cfg.get("color_scales") or {}
default_colors = cs.get("default_colors", [])
for var, spec in (cs.get("variables") or {}).items():
    bounds = [float(b) for b in spec["bounds"]]
    if bounds != sorted(bounds):
        sys.exit(f"color_scales: bounds for '{var}' must be increasing")
    if "colors" in spec:
        colors = spec["colors"]
        if len(colors) != len(bounds):
            sys.exit(f"color_scales: '{var}' has {len(bounds)} bounds but {len(colors)} colors (need one per bound)")
    else:
        if len(bounds) > len(default_colors):
            sys.exit(f"color_scales: '{var}' has {len(bounds)} bounds but only {len(default_colors)} "
                     f"default colors; give it its own 'colors' list")
        colors = default_colors[:len(bounds)]
    color_scales[str(var)] = {"bounds": bounds, "colors": list(colors)}


def scale_of(spec, where):
    """Colour scale of a gridded layer: its own bounds/colors, else a color_scales entry."""
    if "bounds" in spec:
        b = [float(x) for x in spec["bounds"]]
        c = spec.get("colors") or default_colors[:len(b)]
        if len(c) != len(b):
            sys.exit(f"{where}: need one colour per bound")
        return {"bounds": b, "colors": list(c)}
    s = color_scales.get(spec.get("color_scale", "AOD 550 nm"))
    if not s:
        sys.exit(f"{where}: no colour scale (give bounds/colors or a color_scales entry)")
    return s


# =========================================================================
# 2. STATION PAGES: MELODIES-MONET paired netCDF
#    <OUTPUT_DIR>/<platform>/<species>_<CDATE>_<platform>_<exp>.nc4, one per species and
#    experiment, dims (time, x): x = siteid; latitude/longitude(/site)(x); the observation
#    variable (= species name) and the model variable (experiments.yaml species mapping;
#    MELODIES-MONET adds '_new' when both have the same name).
# =========================================================================
def read_pair(path, wf_species, exp):
    """One paired file -> dict with times, sites and obs/model arrays (time, site), or (None, why)."""
    import xarray as xr
    with xr.open_dataset(path) as ds:
        # netCDF names may have lost illegal characters; the original name is in long_name
        names = {str(ds[v].attrs.get("long_name", v)): v for v in ds.variables}
        names.update({v: v for v in ds.variables})
        ovar = names.get(wf_species)
        mvar = None
        m = exp["species"].get(wf_species.lower())
        for c in ([m + "_new", m] if m else []):
            if c in names and names[c] != ovar:
                mvar = names[c]
                break
        if mvar is None:                         # no mapping: the one other (time, x) float variable
            skip = {ovar, "time", "time_local", "x", "siteid", "site", "latitude", "longitude"}
            cand = [v for v in ds.data_vars if v not in skip and set(ds[v].dims) == {"time", "x"}
                    and np.issubdtype(ds[v].dtype, np.floating)]
            mvar = cand[0] if len(cand) == 1 else None
        if ovar is None or mvar is None:
            return None, f"observation '{wf_species}' or model variable not found"
        times = pd.DatetimeIndex(pd.to_datetime(ds["time"].values))
        keep = np.asarray((times >= WIN_START) & (times <= WIN_END))
        if not keep.any():
            return None, "no times inside the window"

        def per_site(name):                      # (x) values; first time for (time, x) variables
            a = ds[name].transpose(..., "x").values
            return a if a.ndim == 1 else a.reshape(-1, a.shape[-1])[0]
        xs = per_site("siteid") if "siteid" in ds.variables else ds["x"].values   # x is only an index
        sids = [s.decode() if isinstance(s, bytes) else str(s) for s in xs]
        site = per_site("site") if "site" in ds.variables else sids
        return {"exp": exp["id"], "times": list(times[keep]), "sids": sids,
                "names": [s.decode() if isinstance(s, bytes) else str(s) for s in site],
                "lat": per_site("latitude").astype(float), "lon": per_site("longitude").astype(float),
                "obs": ds[ovar].transpose("time", "x").values[keep].astype(float),
                "mod": ds[mvar].transpose("time", "x").values[keep].astype(float)}, ""


def platform_payload(stations):
    """Compact data for one platform.

    species:  {sp: {columns: [...], time: [...], avail: [...]}} -> column order + shared time axis
    stations: {id: {name, lat, lon, obs_max: {sp: max}, d: {sp: {v: [[col0], [col1], ...], t?: [...]}}}}
    A station only carries its own 't' when its time axis differs from the shared one.
    """
    species_meta = {}
    for st in stations.values():
        for sp, d in st["data"].items():
            m = species_meta.setdefault(sp, {"columns": [], "times": Counter()})
            m["columns"] += [c for c in d["series"] if c not in m["columns"]]
            m["times"][tuple(d["time"])] += 1
    for m in species_meta.values():
        if "Observation" in m["columns"]:           # Observation always first
            m["columns"].remove("Observation")
            m["columns"].insert(0, "Observation")
        m["time"] = list(m.pop("times").most_common(1)[0][0])

    out = {}
    for sid, st in stations.items():
        e = {"name": st["name"], "lat": st["lat"], "lon": st["lon"], "obs_max": {}, "d": {}}
        for sp, d in st["data"].items():
            entry = {"v": [d["series"].get(c) for c in species_meta[sp]["columns"]]}
            if d["time"] != species_meta[sp]["time"]:
                entry["t"] = d["time"]
            e["d"][sp] = entry
            obs = [x for x in d["series"].get("Observation", []) if x is not None]
            e["obs_max"][sp] = max(obs) if obs else None
        out[sid] = e
    # Columns with at least one value for each variable (experiments panel greys out the rest)
    for sp, m in species_meta.items():
        m["avail"] = [c for k, c in enumerate(m["columns"])
                      if any(st["d"].get(sp) and st["d"][sp]["v"][k] and
                             any(x is not None for x in st["d"][sp]["v"][k]) for st in out.values())]
    return {"species": species_meta, "stations": out}


def build_stations(page, pc):
    """Station page: payload + page settings, or None when there is no paired file."""
    folder = pc.get("folder", page)                 # workflow platform name (output folder)
    frames, skipped = {}, Counter()                 # shown species -> [read_pair dicts]
    for exp in EXPS:
        suffix = f"_{CDATE}_{folder}_{exp['id']}.nc4"
        for fpath in sorted(glob.glob(path_of("paired", platform=folder, exp=exp["id"]))):
            base = os.path.basename(fpath)
            if not base.endswith(suffix):
                continue
            wf_sp = base[:-len(suffix)]
            pr, why = read_pair(fpath, wf_sp, exp)
            if pr is None:
                skipped[why] += 1
                print(f"  skipped {base}: {why}")
                continue
            frames.setdefault(wf_sp, []).append(pr)          # workflow name; index.html shows the alias
    if not frames:
        print(f"{page}: no paired files ({path_of('paired', platform=folder, exp='<exp>')}) -> page skipped")
        return None

    db = {}                                         # sid -> {name, lat, lon, data: {species: ...}}
    for sp, frs in frames.items():
        T = sorted(set().union(*[fr["times"] for fr in frs]))     # union of the time axes
        tpos = {t: i for i, t in enumerate(T)}
        spos, meta = {}, []
        for fr in frs:
            for s, nm, la, lo in zip(fr["sids"], fr["names"], fr["lat"], fr["lon"]):
                if s not in spos:
                    spos[s] = len(spos)
                    meta.append((s, nm, la, lo))
        obs = np.full((len(T), len(spos)), np.nan)
        mods = {}
        for fr in frs:
            ix = np.ix_([tpos[t] for t in fr["times"]], [spos[s] for s in fr["sids"]])
            obs[ix] = np.where(np.isnan(obs[ix]), fr["obs"], obs[ix])   # obs is the same in every file
            mods.setdefault(fr["exp"], np.full(obs.shape, np.nan))[ix] = fr["mod"]
        tstr = [t.strftime("%Y-%m-%d %H:%M") for t in T]
        nd = decimals_for(SPEC_ALIAS.get(sp, sp))         # rounding of the stored values
        order = [e["id"] for e in EXPS if e["id"] in mods]
        for j, (s, nm, la, lo) in enumerate(meta):
            if not (np.isfinite(la) and np.isfinite(lo)):
                continue
            cols = {"Observation": obs[:, j], **{e: mods[e][:, j] for e in order}}
            if not any(np.isfinite(c).any() for c in cols.values()):
                continue                            # nothing at all for this site
            st = db.setdefault(s, {"name": nm, "lat": round(float(la), 4), "lon": round(float(lo), 4), "data": {}})
            st["data"][sp] = {"time": tstr, "series": {k: [rnd(x, nd) for x in v] for k, v in cols.items()}}

    payload = platform_payload(db)
    species = list(payload["species"])
    available = [v for v in species if any(st["obs_max"].get(v) is not None for st in payload["stations"].values())]
    exp_avail = {v: [c for c in payload["species"][v]["avail"] if c != "Observation"] for v in species}
    print(f"{page}: {len(payload['stations'])} stations; species with observations: {', '.join(available) or 'none'}")
    return payload, {"species": species, "available": available, "expAvail": exp_avail}


# =========================================================================
# 3. HMS FIRE DETECTIONS (fires page)
#    One entry per unique (Lon, Lat) inside the window. FRP time series only
#    for GOES fires seen more than once (geostationary = fixed grid).
# =========================================================================
HMS_COLS = ["Lon", "Lat", "YearDay", "Time", "Satellite", "Method", "Ecosystem", "FRP"]


def build_fires(page, pc):
    gap_min = cfg["hms"]["gap_minutes"]
    frp_dec = decimals_for("FRP")
    days = pd.date_range(WIN_START.normalize(), (WIN_END - pd.Timedelta(seconds=1)).normalize(), freq="D")
    files = [path_of("hms_fires", date=d) for d in days]
    for f in files:
        print(f"  HMS file {'found  ' if os.path.exists(f) else 'MISSING'}: {f}")
    tables = [pd.read_csv(f, names=HMS_COLS, skiprows=1, header=None, skipinitialspace=True)
              for f in files if os.path.exists(f)]
    if not tables:
        print(f"{page}: no HMS files -> page skipped")
        return None

    # Column arrays (index i = one fire location, sorted small -> large FRP)
    fires = {"lon": [], "lat": [], "sat": [], "frp": [], "n": [], "m": [], "eco": [],
             "t": [],              # minutes after t0 of the (first) detection; null = has a series
             "methods": [],
             "t0": WIN_START.strftime("%Y-%m-%dT%H:%M:00Z"),
             "minutes": int((WIN_END - WIN_START) / pd.Timedelta(minutes=1))}
    series = {}                    # i -> {"t": [minutes after t0 (null point = gap)], "frp": [...]}
    hms = pd.concat(tables, ignore_index=True).drop_duplicates()
    hms["Satellite"] = hms["Satellite"].astype(str).str.strip()
    hms["Method"] = hms["Method"].astype(str).str.strip()
    hms["FRP"] = hms["FRP"].where(hms["FRP"] >= 0)                   # -999 (e.g. ANALYSIS) -> NaN
    # YearDay (YYYYDDD) + Time (HHMM) -> UTC; a daily file holds a few next-day detections
    hms["t"] = (pd.to_datetime(hms["YearDay"].astype(str), format="%Y%j")
                + pd.to_timedelta(hms["Time"] // 100, unit="h")
                + pd.to_timedelta(hms["Time"] % 100, unit="m"))
    hms = hms[(hms["t"] >= WIN_START) & (hms["t"] < WIN_END)].sort_values("t")
    hms["tmin"] = ((hms["t"] - WIN_START) / pd.Timedelta(minutes=1)).astype(int)

    groups = [(g["FRP"].max(), lon, lat, g) for (lon, lat), g in hms.groupby(["Lon", "Lat"])]
    groups.sort(key=lambda x: (-1 if pd.isna(x[0]) else x[0]))       # biggest drawn last (on top)
    for i, (fmax, lon, lat, g) in enumerate(groups):
        sat, method, n = g["Satellite"].iloc[0], g["Method"].iloc[0], len(g)
        has_ts = sat.startswith("GOES") and n > 1
        if has_ts:
            tt, ff, prev = [], [], None
            for tm, v in zip(g["tmin"], g["FRP"]):
                if prev is not None and tm - prev > gap_min:         # null point = line break
                    tt.append(int(prev) + 1)
                    ff.append(None)
                tt.append(int(tm))
                ff.append(rnd(v, frp_dec))
                prev = tm
            series[i] = {"t": tt, "frp": ff}
        if method not in fires["methods"]:
            fires["methods"].append(method)
        fires["lon"].append(round(float(lon), 4))
        fires["lat"].append(round(float(lat), 4))
        fires["sat"].append(sat)
        fires["frp"].append(rnd(fmax, frp_dec))
        fires["n"].append(int(n))
        fires["m"].append(fires["methods"].index(method))
        fires["eco"].append(int(g["Ecosystem"].iloc[0]))
        fires["t"].append(None if has_ts else int(g["tmin"].iloc[0]))
    print(f"{page}: {len(fires['lon'])} fire locations ({len(series)} with time series)")
    return {"fires": fires, "series": series}, {"count": len(fires["lon"])}


# =========================================================================
# 4. GRIDDED PAGES: satellite AOD (viirs, tempo) and model - satellite AOD (*-models-bias)
#    Every hour of the window with data is quantized to one byte per cell
#    and stored as lossless 8-bit grayscale PNGs on the NATIVE lat/lon grid (row 0 = north),
#    cropped to aod.crop and optionally coarsened f x f (weighted mean):
#       code 0      -> missing (drawn fully transparent)
#       code 1..255 -> value: sqrt encoding   v = max * ((code - 1) / 254)^2
#                             linear encoding v = min + (max - min) * (code - 1) / 254
#    sqrt (AOD) gives ~0.005 steps near 0.1 and ~0.04 near 5; linear (bias) ~0.008 for +-1.
#    The page stretches the rows onto Web Mercator itself and reads hovered values from
#    the native grid. Means are computed in the browser from the hourly frames.
# =========================================================================
def png_gray8(a):
    """Lossless 8-bit grayscale PNG of a 2-D uint8 array, with numpy + zlib only."""
    h, w = a.shape
    a16, prev, rows = a.astype(np.int16), np.zeros(w, np.int16), []
    for r in range(h):
        cur = a16[r]
        # PNG row filters 0 = None, 1 = Sub (minus left), 2 = Up (minus row above)
        cand = (cur, np.concatenate(([cur[0]], cur[1:] - cur[:-1])), cur - prev)
        # libpng's heuristic: smallest sum of |residual| (as signed bytes)
        k = min(range(3), key=lambda f: int(np.abs((cand[f] + 128) % 256 - 128).sum()))
        rows.append(bytes([k]) + (cand[k] % 256).astype(np.uint8).tobytes())
        prev = cur

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


def png_b64(val, enc):
    """float field (NaN = missing) -> base64 PNG of uint8 codes (0 = missing)."""
    q = np.zeros(val.shape, np.uint8)
    ok = np.isfinite(val)
    if enc["kind"] == "linear":
        x = (np.clip(val[ok], enc["min"], enc["max"]) - enc["min"]) / (enc["max"] - enc["min"])
    else:
        x = np.sqrt(np.clip(val[ok], 0, enc["max"]) / enc["max"])
    q[ok] = 1 + np.rint(254 * x).astype(np.uint8)
    return base64.b64encode(png_gray8(q)).decode()


def nc_times(var):
    """CF time variable ('<unit> since <date>') -> list of pandas Timestamps."""
    unit, ref = var.units.split(" since ")
    step = {"days": "D", "day": "D", "hours": "h", "hour": "h", "minutes": "min",
            "minute": "min", "seconds": "s", "second": "s"}[unit.strip().lower()]
    t0 = pd.Timestamp(ref.strip()).tz_localize(None)
    return [(t0 + pd.to_timedelta(float(v), unit=step)).round("min") for v in np.ma.filled(var[:], np.nan)]


def block_sum(x, f):
    """Sum over f x f blocks (array already trimmed to a multiple of f)."""
    ny, nx = x.shape
    return x.reshape(ny // f, f, nx // f, f).sum(axis=(1, 3))


class Grid:
    """Regular lat/lon grid of one file: cells ordered north->south, west->east, cropped to
    crop = [west, east, south, north] and trimmed to whole f x f blocks."""

    def __init__(self, lon, lat, crop, f, name):
        lon = np.where(lon > 180, lon - 360, lon)                    # 0..360 -> -180..180
        xo, yo = np.argsort(lon), np.argsort(lat)[::-1]              # columns west->east, rows north->south
        if crop:
            w, e, s, n = crop
            xo, yo = xo[(lon[xo] >= w) & (lon[xo] <= e)], yo[(lat[yo] >= s) & (lat[yo] <= n)]
        self.f = f
        self.xo, self.yo = xo[:xo.size // f * f], yo[:yo.size // f * f]
        if not (self.xo.size and self.yo.size):
            raise ValueError(f"{name}: no grid cells inside the crop box {crop}")
        lonc = lon[self.xo].reshape(-1, f).mean(1)
        latc = lat[self.yo].reshape(-1, f).mean(1)
        dlon = float(np.median(np.diff(lonc))) if lonc.size > 1 else 1.0
        dlat = float(-np.median(np.diff(latc))) if latc.size > 1 else 1.0
        if not (np.allclose(np.diff(lonc), dlon, atol=1e-3 * dlon) and np.allclose(-np.diff(latc), dlat, atol=1e-3 * dlat)):
            print(f"  WARNING: {name} grid is not regular; it will be drawn as if it were")
        self.meta = {"west": float(lonc[0] - dlon / 2), "east": float(lonc[-1] + dlon / 2),
                     "north": float(latc[0] + dlat / 2), "south": float(latc[-1] - dlat / 2),
                     "nx": int(lonc.size), "ny": int(latc.size)}

    def cells(self, a):
        """(lat, lon) array in file order -> ordered + cropped (float64)."""
        return np.asarray(a, np.float64)[np.ix_(self.yo, self.xo)]

    def reduce(self, a, w):
        """Ordered field + weights -> (sum w, sum w*a) on the output grid (missing: w = 0)."""
        ok = np.isfinite(a) & np.isfinite(w) & (w > 0)
        w = np.where(ok, w, 0.0)
        wa = w * np.where(ok, a, 0.0)
        if self.f > 1:
            w, wa = block_sum(w, self.f), block_sum(wa, self.f)
        return w, wa


def ratio(sw, swa):
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(sw > 0, swa / sw, np.nan)


def slot_of(t):
    """Hourly slot of a time inside the window (None outside)."""
    if not (WIN_START <= t <= WIN_END):                             # both ends: same span as the charts
        return None
    return int(round((t - WIN_START) / pd.Timedelta(hours=1)))


def finish_layer(grid, frames, enc, name):
    """Hourly frames only: the page computes the mean of any sub-period itself."""
    frames.sort(key=lambda fr: fr["h"])
    kb = sum(len(fr["png"]) for fr in frames) / 1024
    print(f"  {name}: {len(frames)} hours with data, grid {grid.meta['nx']} x {grid.meta['ny']}, {kb:.0f} KB")
    return {"grid": grid.meta, "enc": enc, "frames": frames}


AOD_CFG = cfg.get("aod") or {}
CROP = AOD_CFG.get("crop") or (None if not cfg["map"].get("bounds") else
                               [cfg["map"]["bounds"][0][1], cfg["map"]["bounds"][1][1],
                                cfg["map"]["bounds"][0][0], cfg["map"]["bounds"][1][0]])


def load_aod(path, acfg, name):
    """Hourly frames of one AOD product (count-weighted when coarsened) -> layer, or None."""
    if not os.path.exists(path):
        print(f"{name}: file not found: {path}")
        return None
    from netCDF4 import Dataset
    var, cnt = acfg.get("variable", "aod550"), acfg.get("count_variable", "count")
    enc = {"kind": "sqrt", "max": float(acfg.get("max_value", 5.0))}
    miss = acfg.get("missing_at_or_below", 0.0)          # <= this = missing (exact zeros); null = keep all
    frames = []
    with Dataset(path) as nc:
        grid = Grid(np.asarray(nc["lon"][:], float), np.asarray(nc["lat"][:], float),
                    CROP, max(1, int(acfg.get("coarsen", 1))), name)
        flip = nc[var].dimensions[-2:] == ("lon", "lat")            # (time, lon, lat) files
        for k, t in enumerate(nc_times(nc["time"])):
            slot = slot_of(t)
            if slot is None or any(fr["h"] == slot for fr in frames):
                continue
            a = np.ma.filled(nc[var][k], np.nan)
            w = np.ma.filled(nc[cnt][k], 0) if cnt in nc.variables else np.ones(a.shape)
            if flip:
                a, w = a.T, w.T
            a, w = grid.cells(a), grid.cells(w)
            if miss is not None:
                w = np.where(a > miss, w, 0.0)                      # e.g. exact zeros -> missing
            sw, swa = grid.reduce(a, w)
            if not (sw > 0).any():
                continue                                            # hour without data: not selectable
            frames.append({"h": slot, "n": int((sw > 0).sum()), "png": png_b64(ratio(sw, swa), enc)})
    if not frames:
        print(f"{name}: no data inside the window")
        return None
    return finish_layer(grid, frames, enc, name)


def build_aod(page, pc):
    ds = pc.get("dataset", page)
    acfg = AOD_CFG.get(ds) or {}
    label = acfg.get("label", f"{ds.upper()} AOD 550 nm")
    layer = load_aod(path_of(ds), acfg, label)
    if layer is None:
        print(f"{page}: no {ds} AOD -> page skipped")
        return None
    return {"layers": {ds: layer}}, {"dataset": ds, "layers": {ds: {"field": "aod", "dataset": ds}}}


# Model-vs-satellite pages (viirs-models-bias, tempo-models-bias): one variable
# (aod_bias.variable); the Experiments panel picks ONE field at a time:
#   "Observation"  satellite AOD (from the experiment file with the largest domain)
#   "<exp>"        model AOD (only where the satellite has data)
#   "<exp>|bias"   model - satellite
# Layer keys: "obs", "<exp>|model", "<exp>|bias". Hourly frames from satellite_aod output.
def build_bias(page, pc):
    ds = pc.get("dataset", "viirs")
    bcfg = cfg.get("aod_bias") or {}
    f = max(1, int(bcfg.get("coarsen", 1)))
    spec = {k: bcfg.get(k) or {} for k in ("observation", "model", "bias")}
    enc = {"observation": {"kind": "sqrt", "max": float(spec["observation"].get("max", 5))},
           "model": {"kind": "sqrt", "max": float(spec["model"].get("max", 5))},
           "bias": {"kind": "linear", "min": float(spec["bias"].get("min", -1)), "max": float(spec["bias"].get("max", 1))}}
    layers, meta, choices = {}, {}, []
    obs_best = None                                 # (cells, grid, frames) of the largest domain
    from netCDF4 import Dataset
    for exp in EXPS:
        path = path_of("sat_bias", dataset=ds, exp=exp["id"])
        if not os.path.exists(path):
            print(f"  {exp['id']}: file not found: {path}")
            continue
        frames = {"observation": [], "model": [], "bias": []}
        with Dataset(path) as nc:
            grid = Grid(np.asarray(nc["lon"][:], float), np.asarray(nc["lat"][:], float), CROP, f, exp["id"])
            for k, t in enumerate(nc_times(nc["time"])):
                slot = slot_of(t)
                if slot is None or any(fr["h"] == slot for fr in frames["observation"]):
                    continue
                o = grid.cells(np.ma.filled(nc["obs_aod"][k], np.nan))
                m = grid.cells(np.ma.filled(nc["model_aod"][k], np.nan))
                for fk, a in (("observation", o), ("model", m), ("bias", m - o)):
                    sw, swa = grid.reduce(a, np.ones(a.shape))
                    if (sw > 0).any():
                        frames[fk].append({"h": slot, "n": int((sw > 0).sum()), "png": png_b64(ratio(sw, swa), enc[fk])})
        ncell = grid.meta["nx"] * grid.meta["ny"]
        if frames["observation"] and (obs_best is None or ncell > obs_best[0]):
            obs_best = (ncell, grid, frames["observation"])
        for fk, choice in (("model", exp["id"]), ("bias", f"{exp['id']}|bias")):
            if not frames[fk]:
                continue
            lid = f"{exp['id']}|{fk}"
            layers[lid] = finish_layer(grid, frames[fk], enc[fk], lid)
            meta[lid] = {"field": fk, "exp": exp["id"]}
            choices.append(choice)
    if obs_best is not None:
        layers["obs"] = finish_layer(obs_best[1], obs_best[2], enc["observation"], "observation")
        meta["obs"] = {"field": "observation", "exp": None}
        choices.insert(0, "Observation")
    if not layers:
        print(f"{page}: no model-{ds} files -> page skipped")
        return None
    return {"layers": layers}, {"dataset": ds, "layers": meta, "choices": choices}


BUILDERS = {"stations": build_stations, "fires": build_fires, "aod": build_aod, "aod_bias": build_bias}

# =========================================================================
# 5. AOD figure settings (PNG button) shared by the gridded layers
# =========================================================================
NE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/"
sn = AOD_CFG.get("snapshot") or {}
DEFAULT_REGIONS = {"CONUS": {"bounds": [-130, -55, 23, 51], "lambert_bounds": [-126, -69, 23, 50],
                             "lambert_origin": [38.5, -97.5]},
                   "North America": {"bounds": [-180, -40, 12, 73], "lambert_bounds": [-160, -55, 15, 68],
                                     "lambert_origin": [41.75, -105]}}
regions = []
for n, r in (sn.get("regions") or DEFAULT_REGIONS).items():
    r = r if isinstance(r, dict) else {"bounds": r}          # "name: [w, e, s, n]" or "name: {bounds, ...}"
    b = [float(x) for x in r["bounds"]]
    lb = [float(x) for x in (r.get("lambert_bounds") or b)]
    if len(b) != 4 or len(lb) != 4:
        sys.exit(f"aod.snapshot.regions: '{n}' needs bounds/lambert_bounds [west, east, south, north]")
    o = [float(x) for x in (r.get("lambert_origin") or [(lb[2] + lb[3]) / 2, (lb[0] + lb[1]) / 2])]
    regions.append({"name": str(n), "bounds": b, "lambertBounds": lb, "origin": o})
proj = str(sn.get("projection", "mercator")).lower()
if proj not in ("mercator", "latlon", "lambert"):
    sys.exit("aod.snapshot.projection: mercator | latlon | lambert")
AOD_SNAPSHOT = {
    "width": int(sn.get("width", 1200)), "projection": proj, "regions": regions,
    "boundaries": sn.get("boundaries") or [
        {"url": NE + "ne_50m_admin_1_states_provinces_lakes.geojson", "color": "#707070", "width": 0.5},
        {"url": NE + "ne_50m_admin_0_countries_lakes.geojson", "color": "#3a3a3a", "width": 0.8}],
}
ad = AOD_CFG.get("display") or {}
# VIIRS visible imagery (NASA GIBS true colour, daily): tiles are fetched by the viewer's
# browser from NASA (no account, no key); nothing is stored in the html. One at a time.
_vc = cfg.get("visible_imagery") or {}
VISIBLE = {
    "url": _vc.get("url", "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/{layer}/default/{time}/"
                          "GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg"),
    "maxNativeZoom": int(_vc.get("max_native_zoom", 9)),
    "background": _vc.get("background", "#3a3a3a"),          # map colour behind the imagery (no base map)
    "attribution": _vc.get("attribution", "Imagery &copy; NASA GIBS / Worldview"),
    "start": _vc.get("start", "noaa20"),                     # shown when the website opens; null = none
    "layers": [{"id": str(k), "label": str(v.get("label", k)), "layer": str(v["layer"])}
               for k, v in (_vc.get("layers") or {
                   "snpp":   {"label": "Suomi-NPP", "layer": "VIIRS_SNPP_CorrectedReflectance_TrueColor"},
                   "noaa20": {"label": "NOAA-20",   "layer": "VIIRS_NOAA20_CorrectedReflectance_TrueColor"},
                   "noaa21": {"label": "NOAA-21",   "layer": "VIIRS_NOAA21_CorrectedReflectance_TrueColor"},
               }).items()],
}
AOD_DISPLAY = {"start": str(ad.get("start", "mean")), "opacity": float(ad.get("opacity", 0.75)),
               "frame_ms": int(ad.get("frame_ms", 700))}


# =========================================================================
# 6. WRITE THE DATA OF THIS CYCLE AND index.html
#    <web_dir>/<CDATE>/<page>.html   DATA ONLY (no settings, no viewer code): a script that
#                                    hands its data + facts about the run to index.html
#    <web_dir>/<CDATE>/cycle.json    window of the cycle (read back when index.html is built)
#    <web_dir>/index.html            ALL settings + the left panel + the map viewer code; every
#                                    cycle (old and new) is drawn with the settings of the last run
#    pages:     observation platforms (buttons); reference: layers over any platform
# =========================================================================
REFS = cfg.get("reference") or {}
for _k in set(PAGES) & set(REFS):
    sys.exit(f"'{_k}' is both in 'pages' and in 'reference'; file names must differ")
DATA_FORMAT = 2                                     # bump when the data files change shape


def html_subst(template, subs):
    for k, v in subs.items():
        template = template.replace(k, v)
    return template


THEME_BG = {t: cfg["map"]["themes"][t].get("panel_bg", d) for t, d in (("dark", "#171717"), ("light", "#ebebeb"))}
TITLE = str(cfg.get("title", "Model Verification"))

DATA_TEMPLATE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>__TITLE__</title></head>
<body><p style="font-family:sans-serif">Data file of the verification website: open it through index.html.</p>
<script>
// Data only (no settings, no viewer): index.html loads this file in a hidden frame
parent.postMessage({ src: "mmv-data", page: __PAGE__, meta: __META__, data: __DATA__ }, "*");
</script></body></html>
"""


def write_pages():
    """Data of every page of this cycle -> <web_dir>/<CDATE>/<page>.html (+ cycle.json)."""
    os.makedirs(CYCLE_DIR, exist_ok=True)
    run = {"format": DATA_FORMAT, "cycle": CYCLE.strftime("%Y-%m-%d %H:%M UTC"), "cycleTag": CDATE,
           "window": [WIN_START.strftime("%Y-%m-%d %H:%M"), WIN_END.strftime("%Y-%m-%d %H:%M")],   # x-axis range
           "windowHours": WIN_HOURS}
    write_atomic(os.path.join(CYCLE_DIR, "cycle.json"), json.dumps(run))
    counts = []
    for page, pc in [*PAGES.items(), *REFS.items()]:
        if ARGS.pages and page not in ARGS.pages:
            continue
        kind = pc.get("type")
        if kind not in BUILDERS:
            print(f"--- {page}: type '{kind}' has no builder yet -> skipped")
            continue
        print(f"--- {page} ({kind})")
        res = BUILDERS[kind](page, pc)
        if res is None:
            continue
        data, meta = res
        meta = {"kind": kind, **run, **meta}
        out = os.path.join(CYCLE_DIR, f"{page}.html")
        write_atomic(out, html_subst(DATA_TEMPLATE, {
            "__TITLE__": f"{page} {CDATE} (data)", "__PAGE__": js_literal(page),
            "__META__": js_literal(meta), "__DATA__": js_literal(data)}))
        counts.append(f"{page}: {os.path.getsize(out) / 1024**2:.2f} MB")
    print(f"Data written to {CYCLE_DIR}: " + ("; ".join(counts) or "none"))
    return counts


def settings():
    """Everything the viewer needs that is not data (index.html sends it with each page)."""
    aod = {}
    for ds, acfg in AOD_CFG.items():
        if ds in ("crop", "display", "snapshot") or not isinstance(acfg, dict):
            continue
        aod[ds] = {"label": acfg.get("label", f"{ds.upper()} AOD 550 nm"), "decimals": int(acfg.get("decimals", 2)),
                   "scale": scale_of(acfg, f"aod.{ds}")}
    bcfg = cfg.get("aod_bias") or {}
    bias = {"variable": str(bcfg.get("variable", "AOD 550 nm"))}
    for fk, lbl in (("observation", "{obs} AOD 550 nm"), ("model", "{exp} AOD 550 nm"), ("bias", "{exp} − {obs} AOD")):
        fc = bcfg.get(fk) or {}
        bias[fk] = {"label": fc.get("label", lbl), "decimals": int(fc.get("decimals", bcfg.get("decimals", 2))),
                    "scale": scale_of(fc, f"aod_bias.{fk}")}
    units = cfg.get("variables") or {}
    names = {*units, *SPEC_ALIAS.values(), bias["variable"], "FRP"}
    keep = ("label", "type", "variables", "default", "dataset", "obs_label")
    return {
        "map": cfg["map"], "stations": cfg["stations"], "hms": cfg["hms"],
        "colorScales": color_scales, "units": units, "aliases": SPEC_ALIAS,
        "decimals": {"default": decimals_for("default"), **{v: decimals_for(v) for v in names}},
        "experiments": [{k: e[k] for k in ("id", "label", "color", "style", "every")} for e in EXPS],
        "observation": OBSERVATION,
        "aodDisplay": AOD_DISPLAY, "aodSnapshot": AOD_SNAPSHOT, "visible": VISIBLE,
        "aod": aod, "bias": bias,
        "pages": {p: {k: pc[k] for k in keep if k in pc} for p, pc in PAGES.items()},
        "refs": {r: {k: rc[k] for k in keep if k in rc} for r, rc in REFS.items()},
    }


def write_index():
    """index.html: settings + viewer, and every <YYYYMMDDHH>/ found in web_dir."""
    cycles = {}
    for d in sorted(os.listdir(WEB_DIR)):
        p = os.path.join(WEB_DIR, d)
        if not (re.fullmatch(r"\d{10}", d) and os.path.isdir(p)):
            continue
        try:
            with open(os.path.join(p, "cycle.json")) as f:
                run = json.load(f)
        except (OSError, ValueError):
            print(f"  {d}: no cycle.json (built by an older version) -> rerun this cycle; not listed")
            continue
        if run.get("format") != DATA_FORMAT:
            print(f"  {d}: data format {run.get('format')} != {DATA_FORMAT} -> rerun this cycle; not listed")
            continue
        found = sorted(f[:-5] for f in os.listdir(p) if f.endswith(".html"))
        cycles[d] = {"pages": found, **{k: run[k] for k in ("cycle", "window", "windowHours")}}
    pages = [{"id": p, "label": str(pc.get("label", p)), "kind": pc.get("type")} for p, pc in PAGES.items()]
    refs = [{"id": r, "label": str(rc.get("label", r)), "kind": rc.get("type"), "show": bool(rc.get("show", False))}
            for r, rc in REFS.items()]
    idx = {
        "title": TITLE, "pages": pages, "refs": refs, "cycles": cycles,
        "startPage": cfg.get("start_page") or (pages[0]["id"] if pages else None),
        "experiments": [{k: e[k] for k in ("id", "label", "color", "style")} for e in EXPS],
        "observation": OBSERVATION, "opacity": AOD_DISPLAY["opacity"],
        "visible": [{"id": v["id"], "label": v["label"]} for v in VISIBLE["layers"]], "visibleStart": VISIBLE["start"],
        "settings": settings(),
    }
    pop = cfg["popup"]
    viewer = html_subst(PAGE_TEMPLATE, {
        "__TITLE__": TITLE, "__PLOTLY__": PLOTLY_CDN,
        "__DARK_BG__": THEME_BG["dark"], "__LIGHT_BG__": THEME_BG["light"],
        "__POP_W__": str(pop["width"]), "__POP_H__": str(pop["height"]),
        "__POP_MINW__": str(pop["min_width"]), "__POP_MINH__": str(pop["min_height"])})
    out = os.path.join(WEB_DIR, "index.html")
    write_atomic(out, html_subst(INDEX_TEMPLATE, {
        "__TITLE__": TITLE, "__DARK_BG__": THEME_BG["dark"], "__LIGHT_BG__": THEME_BG["light"],
        "__INDEX__": js_literal(idx), "__VIEWER__": js_literal(viewer)}))
    print(f"{out}: {len(cycles)} cycle(s)")


# =========================================================================
# 7. PAGE TEMPLATE: one map page (stations | fires | aod | bias)
#    Talks to index.html with postMessage:
#      page -> index  {src: "mmv-page", type: "ready", variables, available, defaultVariable,
#                      experiments: "multi" | "single" | "none", avail: {variable: [experiment ids]}}
#                     {src: "mmv-page", type: "view", center, zoom}      (map moved)
#      index -> page  {src: "mmv-index", state: {theme, variable, traces: {id: {visible, color,
#                      style}}, single}}
#    URL hash: #v=lat,lon,zoom&t=dark|light (start view and theme, set by index.html)
# =========================================================================
PAGE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>__TITLE__</title>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css" />
    <link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css" />
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <script src="https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js"></script>
    <script src="__PLOTLY__"></script>
    <style>
        /* Colours for the two themes (dark = default; body.light switches) */
        :root { --bg: __DARK_BG__; --fg: #ededed; --map-bg: #111111; --btn-bg: #262626; --btn-fg: #e5e5e5;
                --btn-border: #404040; --btn-hover: #333333;
                --legend-bg: rgba(23,23,23,0.88); --legend-fg: #ededed; --legend-hover: #333333; --muted: #9a9a9a; }
        body.light { --bg: __LIGHT_BG__; --fg: #1f2937; --map-bg: #dddddd; --btn-bg: #ffffff; --btn-fg: #1f2937;
                --btn-border: #c4c4c4; --btn-hover: #f3f4f6;
                --legend-bg: rgba(255,255,255,0.93); --legend-fg: #1f2937; --legend-hover: #e5e7eb; --muted: #6b7280; }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", sans-serif; }
        html, body { height: 100%; width: 100%; overflow: hidden; background: var(--map-bg); }
        #map { position: absolute; inset: 0; background: var(--map-bg); }
        #status { position: absolute; top: 12px; left: 50%; transform: translateX(-50%); z-index: 1000;
                  background: var(--legend-bg); color: var(--legend-fg); padding: 8px 14px; border-radius: 6px;
                  font-size: 13px; box-shadow: 0 2px 8px rgba(0,0,0,0.4); display: none; }
        /* Selectors shown only when the page is opened on its own (not inside index.html) */
        .solo { background: var(--legend-bg); color: var(--legend-fg); padding: 8px 10px; border-radius: 6px;
                font-size: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.4); display: flex; flex-direction: column; gap: 5px; }
        .solo b { font-size: 13px; }
        .solo label { display: flex; align-items: center; gap: 6px; }
        .solo select { flex: 1; font-size: 12px; padding: 2px 4px; background: var(--btn-bg); color: var(--btn-fg);
                       border: 1px solid var(--btn-border); border-radius: 4px; }
        .solo a { color: #38bdf8; }
        /* Popups: chart box of fixed size so Leaflet can place the popup before Plotly draws */
        .leaflet-popup-content-wrapper { padding: 0; border-radius: 8px; overflow: hidden; box-shadow: 0 6px 24px rgba(0,0,0,0.45); }
        .leaflet-popup-content { margin: 0; line-height: normal; }
        .popup-below .leaflet-popup-tip-container { top: -20px; transform: rotate(180deg); }   /* tip points up */
        .plot-box { width: __POP_W__; height: __POP_H__; min-width: __POP_MINW__; min-height: __POP_MINH__; background: #fff; }
        .plot-box .modebar { right: 34px !important; }                     /* clear the close (x) button */
        /* Station charts: legend in its own panel, right of the chart (below it on narrow windows) */
        .plot-wrap { display: flex; background: #fff; }
        .plot-wrap .plot-box .modebar { right: 6px !important; }           /* the (x) is over the legend */
        .plot-legend { flex: none; width: 200px; height: __POP_H__; min-height: __POP_MINH__; overflow-y: auto;
                       padding: 38px 8px 8px 10px; border-left: 1px solid #e2e2e2; background: #fafafa;
                       font-size: 11.5px; color: #1f2937; user-select: none; }
        .plot-legend .lg-row { display: flex; align-items: center; gap: 6px; cursor: pointer; border-radius: 3px;
                               padding: 1px 3px; white-space: nowrap; }
        .plot-legend .lg-row svg { flex: none; }
        .plot-legend .lg-row:hover { background: #e5e7eb; }
        .plot-legend .off { opacity: 0.35; text-decoration: line-through; }
        .plot-legend .hint { color: #6b7280; margin-top: 6px; white-space: normal; }
        @media (max-width: 1100px) {
            .plot-wrap { flex-direction: column; }
            .plot-wrap .plot-box .modebar { right: 34px !important; }
            .plot-legend { width: auto; height: 90px; min-height: 0; padding: 6px 8px; border-left: none;
                           border-top: 1px solid #e2e2e2; display: flex; flex-wrap: wrap; align-content: flex-start;
                           column-gap: 12px; }
            .plot-legend .hint { width: 100%; }
        }
        .plot-box .modebar-group { background: transparent !important; }
        .leaflet-container a.leaflet-popup-close-button {                   /* always visible, above the chart */
            z-index: 1100; width: 26px; height: 26px; top: 6px; right: 6px; font: 22px/24px Tahoma, Verdana, sans-serif;
            color: #444; background: rgba(255,255,255,0.9); border-radius: 50%; text-align: center; }
        .leaflet-container a.leaflet-popup-close-button:hover { color: #000; background: #e5e5e5; }
        .plot-msg { display: flex; flex-direction: column; align-items: center; justify-content: center;
                    height: 100%; color: #555; text-align: center; padding: 24px; }
        .plot-msg h3 { margin-bottom: 8px; color: #111; font-size: 16px; }
        /* Legends */
        .fire-legend, .color-legend { background: var(--legend-bg); color: var(--legend-fg); padding: 8px 10px; border-radius: 6px;
                       font-size: 12px; line-height: 1.5; box-shadow: 0 2px 8px rgba(0,0,0,0.4); }
        .fire-legend b, .color-legend b { display: block; margin-bottom: 3px; }
        .fire-legend .row, .color-legend .row { display: flex; align-items: center; gap: 7px; }
        .fire-legend svg { flex: none; }
        .color-legend .sw { width: 12px; height: 12px; border-radius: 50%; border: 1px solid #111; flex: none; }
        .sat-row, .bin-row { cursor: pointer; border-radius: 3px; padding: 0 4px; margin: 0 -4px; user-select: none; }
        .sat-row:hover, .bin-row:hover { background: var(--legend-hover); }
        .sat-row.off, .bin-row.off { opacity: 0.35; text-decoration: line-through; }
        .cnt { color: var(--muted); margin-left: auto; padding-left: 10px; }
        .hint { color: var(--muted); font-size: 10.5px; margin-top: 2px; }
        .show-all { color: #0284c7; cursor: pointer; font-size: 11px; }
        .fire-card { padding: 12px 40px 12px 14px; font-size: 13px; color: #0f172a; min-width: 230px; }
        .fire-card h4 { font-size: 14px; margin-bottom: 8px; }
        .fire-card table { border-collapse: collapse; }
        .fire-card td { padding: 2px 10px 2px 0; }
        .fire-card td:first-child { color: #555; }
        /* Gridded fields: hard cell edges instead of blurred upscaling */
        .aod-img { image-rendering: pixelated; image-rendering: crisp-edges; }
        #aod-hover { position: absolute; z-index: 1001; pointer-events: none; display: none; white-space: nowrap;
                     background: var(--legend-bg); color: var(--legend-fg); font-size: 12px; padding: 3px 7px;
                     border-radius: 4px; box-shadow: 0 1px 5px rgba(0,0,0,0.4); }
        /* Colour bars: vertical (title rotated 90 deg), one per colour scale shown, side by side */
        .aod-legend { background: var(--legend-bg); color: var(--legend-fg); padding: 10px 8px 10px 6px; border-radius: 6px;
                      font-size: 12px; line-height: 1.2; box-shadow: 0 2px 8px rgba(0,0,0,0.4); display: flex; gap: 6px; }
        .vgrp { display: flex; gap: 4px; height: min(240px, 38vh); }
        .vtitle { writing-mode: vertical-rl; transform: rotate(180deg); font-weight: 700; white-space: nowrap; text-align: center; }
        .vbar { display: flex; flex-direction: column-reverse; width: 12px; border: 1px solid #111; }
        .vbar span { flex: 1; }
        .vticks { position: relative; width: 30px; font-size: 10.5px; }
        .vticks span { position: absolute; left: 2px; transform: translateY(50%); white-space: nowrap; }
        /* Time slider + opacity + PNG: centre bottom of the map */
        #aod-slider { position: absolute; left: 50%; bottom: 22px; transform: translateX(-50%); z-index: 1000;
                      width: min(1200px, calc(100% - 32px)); display: flex; align-items: center; gap: 10px;
                      background: var(--legend-bg); color: var(--legend-fg); padding: 8px 14px; border-radius: 10px;
                      font-size: 14px; box-shadow: 0 2px 8px rgba(0,0,0,0.4); }
        #aod-slider[hidden] { display: none; }
        #vis-bar { position: absolute; top: 12px; left: 50%; transform: translateX(-50%); z-index: 1000;
                   display: flex; align-items: center; gap: 8px; background: var(--legend-bg); color: var(--legend-fg);
                   padding: 6px 12px; border-radius: 10px; font-size: 13px; box-shadow: 0 2px 8px rgba(0,0,0,0.4); }
        #vis-bar[hidden] { display: none; }
        body.vis-on #status { top: 56px; }                 /* below the imagery day bar */
        #vis-bar .ts-btn { padding: 2px 9px; min-width: 0; font-size: 13px; }
        #vis-name { font-weight: 700; white-space: nowrap; }
        #vis-range { width: 160px; accent-color: #9ca3af; }
        #vis-day { font-weight: 600; font-variant-numeric: tabular-nums; white-space: nowrap; }
        .vis-pane img.leaflet-tile { filter: none; }
        .ts-btn { font-size: 14px; font-weight: 600; padding: 4px 11px; min-width: 38px; border-radius: 6px; cursor: pointer;
                  border: 1px solid var(--btn-border); background: var(--btn-bg); color: var(--btn-fg); white-space: nowrap; }
        .ts-btn:hover { background: var(--btn-hover); }
        .ts-btn.active { background: #0284c7; color: #fff; border-color: #38bdf8; }
        .ts-btn:disabled { opacity: 0.4; cursor: default; }
        .ts-track { position: relative; flex: 1; min-width: 80px; height: 30px; }
        .ts-ticks { position: absolute; left: 8.5px; right: 8.5px; top: 21px; height: 7px; pointer-events: none; }
        .ts-ticks span { position: absolute; width: 2px; height: 7px; margin-left: -1px; background: #38bdf8; border-radius: 1px; }
        #aod-range { -webkit-appearance: none; appearance: none; position: relative; width: 100%; height: 22px;
                     margin: 0; background: transparent; cursor: pointer; }
        #aod-range::-webkit-slider-runnable-track { height: 5px; border-radius: 3px; background: var(--btn-border); }
        #aod-range::-moz-range-track { height: 5px; border-radius: 3px; background: var(--btn-border); }
        #aod-range::-webkit-slider-thumb { -webkit-appearance: none; width: 17px; height: 17px; margin-top: -6px;
                     border-radius: 50%; background: #0284c7; border: 2px solid #fff; }
        #aod-range::-moz-range-thumb { width: 17px; height: 17px; box-sizing: border-box; border-radius: 50%;
                     background: #0284c7; border: 2px solid #fff; }
        #aod-slider.mean #aod-range::-webkit-slider-thumb { background: #888; }
        #aod-slider.mean #aod-range::-moz-range-thumb { background: #888; }
        /* Mean period: gold band between two gold handles (drag them to choose the hours averaged) */
        .ts-band { position: absolute; top: 9px; height: 5px; background: rgba(234, 179, 8, 0.55); border-radius: 3px; pointer-events: none; }
        #aod-slider.mean .ts-band { background: rgba(234, 179, 8, 0.9); }
        #aod-slider:not(.mean) .ts-band, #aod-slider:not(.mean) .ts-h { display: none; }   /* only while Mean is on */
        .ts-h { position: absolute; top: 4px; width: 15px; height: 15px; margin-left: -7.5px; border-radius: 50%; z-index: 2;
                background: #eab308; border: 2px solid #fff; box-shadow: 0 0 3px rgba(0,0,0,0.6); cursor: ew-resize; touch-action: none; }
        .ts-h:hover, .ts-h.drag { background: #facc15; }
        body.embedded .ts-opac, body.embedded #aod-save { display: none; }     /* in the left panel of index.html */
        #aod-time-lbl { flex: none; min-width: 168px; text-align: right; font-weight: 600; font-variant-numeric: tabular-nums; white-space: nowrap; }
        .ts-opac { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--muted);
                   border-left: 1px solid var(--btn-border); padding-left: 10px; }
        .ts-opac input { width: 80px; accent-color: #0284c7; }
        @media (max-width: 700px) {
            #aod-slider { gap: 6px; padding: 6px 8px; font-size: 12px; flex-wrap: wrap; }
            #aod-time-lbl { min-width: 128px; }
            .ts-btn { padding: 3px 7px; min-width: 30px; font-size: 13px; }
        }
        /* AOD figure options (centre of the window) */
        #snap-modal { position: fixed; inset: 0; z-index: 3000; background: rgba(0,0,0,0.45);
                      display: flex; align-items: center; justify-content: center; }
        #snap-modal[hidden] { display: none; }
        .snap-box { width: min(380px, 92vw); background: var(--btn-bg); color: var(--btn-fg); border: 1px solid var(--btn-border);
                    border-radius: 10px; padding: 18px 20px; box-shadow: 0 10px 40px rgba(0,0,0,0.5); font-size: 13px; }
        .snap-box h3 { font-size: 15px; margin-bottom: 4px; }
        .snap-box .sub { color: var(--muted); font-size: 12px; margin-bottom: 12px; }
        .snap-box label { display: flex; align-items: center; gap: 8px; padding: 5px 0; cursor: pointer; }
        .snap-box label .cnt { font-size: 11px; }
        .snap-box .grp { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px;
                         color: var(--muted); margin: 8px 0 2px; }
        .snap-btns { display: flex; justify-content: flex-end; gap: 8px; margin-top: 14px; }
        .snap-btns button { font-size: 13px; padding: 6px 14px; border-radius: 6px; cursor: pointer;
                            border: 1px solid var(--btn-border); background: var(--btn-bg); color: var(--btn-fg); }
        .snap-btns button.primary { background: #0284c7; color: #fff; border-color: #38bdf8; }
        .snap-btns button:disabled { opacity: 0.5; cursor: default; }
    </style>
</head>
<body>
    <div id="map"></div><div id="status"></div><div id="aod-hover"></div>
    <!-- Day of the VIIRS visible imagery (daily composites), top centre -->
    <div id="vis-bar" hidden>
        <span id="vis-name"></span>
        <button class="ts-btn" type="button" id="vis-prev" title="Previous day">‹</button>
        <input type="range" id="vis-range" min="0" step="1" value="0">
        <button class="ts-btn" type="button" id="vis-next" title="Next day">›</button>
        <span id="vis-day"></span>
    </div>
    <!-- Time slider (gridded pages) -->
    <div id="aod-slider" hidden>
        <button class="ts-btn" type="button" id="aod-mean" title="Mean of the hours between the gold handles">Mean</button>
        <button class="ts-btn" type="button" id="aod-play">▶</button>
        <div class="ts-track" id="aod-track"><div class="ts-ticks" id="aod-ticks"></div><div class="ts-band" id="aod-band"></div>
             <input type="range" id="aod-range" min="0" step="1" value="0" title="Hours with data are marked; others are skipped">
             <div class="ts-h" id="aod-h0" title="Start of the mean period"></div><div class="ts-h" id="aod-h1" title="End of the mean period"></div></div>
        <span id="aod-time-lbl"></span>
        <span class="ts-opac">Opacity<input type="range" id="aod-opacity" min="0.1" max="1" step="0.05"></span>
        <button class="ts-btn" type="button" id="aod-save" title="Save as a PNG figure (white background, boundaries only)">⤓ PNG</button>
    </div>
    <!-- Figure options -->
    <div id="snap-modal" hidden><div class="snap-box">
        <h3>Save figure (PNG)</h3>
        <div class="sub" id="snap-sub"></div>
        <div id="snap-opts"></div>
        <div class="snap-btns"><button type="button" id="snap-cancel">Cancel</button>
             <button type="button" class="primary" id="snap-go">Save PNG</button></div>
    </div></div>

    <script>
        // ---------------- written by generate_website.py ----------------
        // Viewer code only. index.html (which holds all the settings) sends {init: {cfg, data,
        // v: "lat,lon,zoom", t: theme, vis: imagery id}} once this frame has started.
        window.addEventListener("message", function boot(e) {
            if (e.source !== window.parent || !e.data || e.data.src !== "mmv-index" || !e.data.init) return;
            window.removeEventListener("message", boot);
            start(e.data.init);
        });
        window.parent.postMessage({ src: "mmv-page", type: "booted" }, "*");

        function start(INIT) {
        const CFG = INIT.cfg;             // settings from index.html + facts about the run (window, ...)
        const DATA = INIT.data || {};     // stations: {species, stations} | fires: {fires, series} | aod/bias: {layers}
        const KIND = CFG.kind;            // stations | fires | aod | bias | base
        const EMBEDDED = window.parent !== window;     // inside index.html

        const UNITS = CFG.units, COLOR_SCALES = CFG.colorScales;
        const decOf = v => CFG.decimals[v] ?? CFG.decimals.default ?? 2;                  // decimal places per variable
        const fmtVal = (x, v) => x.toFixed(decOf(v));
        const unitOf = v => (UNITS[v] && UNITS[v] !== "unitless" ? " " + UNITS[v] : "");
        const deg = (v, pos, neg, nd = 4) => `${Math.abs(v).toFixed(nd)}°${v >= 0 ? pos : neg}`;
        const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
        const tms = t => Date.parse(t.replace(" ", "T") + "Z");                  // "YYYY-MM-DD HH:MM" -> ms
        const tPlus1 = t => new Date(tms(t) + 60000).toISOString().slice(0, 16).replace("T", " ");
        const statusBox = document.getElementById("status");
        const showStatus = txt => { statusBox.textContent = txt; statusBox.style.display = txt ? "block" : "none"; };
        const post = msg => { if (EMBEDDED) try { window.parent.postMessage(Object.assign({ src: "mmv-page" }, msg), "*"); } catch (e) {} };

        function main() {
        const REF_ON = {};                // reference layers on: {hms, viirs, tempo}
        const GRID = {};                  // gridded-layer module (filled by initGrids)
        let gridOpacity = CFG.aodDisplay.opacity;
        // Curves: Observation + experiments. index.html sets on/off, colour and line style.
        const TRACE = { Observation: { visible: true, color: CFG.observation.color, style: "solid" } };
        CFG.experiments.forEach(x => { TRACE[x.id] = { visible: true, color: x.color, style: x.style }; });
        function traceSettings() {
            return [{ id: "Observation", name: CFG.observation.label, isObs: true, every: 1 },
                    ...CFG.experiments.map(x => ({ id: x.id, name: x.label, isObs: false, every: x.every || 1 }))]
                   .map(it => Object.assign(it, TRACE[it.id]));
        }
        let activeVariable = CFG.defaultVariable;                  // null = none (stations hidden)
        const expsWith = v => (CFG.expAvail && CFG.expAvail[v]) || [];
        let singleExp = expsWith(activeVariable)[0] || (CFG.experiments[0] && CFG.experiments[0].id) || null;   // bias pages
        const HOOK = { variable() {}, traces() {}, single() {} };  // set by the module of this page kind

        // ======================= MAP =======================
        const hashGet = k => INIT[k];                       // start view, theme, imagery
        const hv = (hashGet("v") || "").split(",").map(Number);
        const baseLayers = {};
        CFG.map.base_layers.forEach(b => {
            baseLayers[b.name] = L.tileLayer(b.url, { attribution: b.attribution, maxZoom: b.max_zoom || 19 });
        });
        const bnd = CFG.map.boundaries;
        const boundaries = L.tileLayer(bnd.url, { attribution: bnd.attribution, pane: "overlayPane" });
        // Panning limited to config map.bounds; the minimum zoom keeps the view inside them.
        const mapBounds = CFG.map.bounds ? L.latLngBounds(CFG.map.bounds) : null;
        const ZC = CFG.map.zoom_behavior || {};
        const map = L.map("map", { center: hv.length === 3 && hv.every(isFinite) ? [hv[0], hv[1]] : CFG.map.center,
                                   zoom: hv.length === 3 && hv.every(isFinite) ? hv[2] : CFG.map.zoom,
                                   maxBounds: mapBounds, maxBoundsViscosity: 1.0,
                                   zoomSnap: ZC.snap ?? 0.25, zoomDelta: ZC.delta ?? 0.5,
                                   wheelPxPerZoomLevel: ZC.wheel_px_per_level ?? 120, wheelDebounceTime: 20 });
        function fitMinZoom() {
            if (!mapBounds) return;
            map.setMinZoom(map.getBoundsZoom(mapBounds, true));
            if (map.getZoom() < map.getMinZoom()) map.setZoom(map.getMinZoom());
            map.panInsideBounds(mapBounds, { animate: false });
        }
        fitMinZoom();
        map.on("resize", fitMinZoom);
        map.on("moveend", () => {                  // index.html keeps the view when switching pages
            const c = map.getCenter();
            post({ type: "view", center: [+c.lat.toFixed(4), +c.lng.toFixed(4)], zoom: +map.getZoom().toFixed(2) });
        });
        const layerControl = L.control.layers(baseLayers, { [bnd.name]: boundaries }, { position: "topright" }).addTo(map);

        // ---- dark / light theme: legend colours + base map (config map.themes) ----
        const THEMES = CFG.map.themes;
        let theme = hashGet("t");
        if (!EMBEDDED && !theme) try { theme = localStorage.getItem("verif-theme"); } catch (e) {}
        // ---- VIIRS visible imagery (NASA GIBS, daily true colour), one at a time ----
        // In its own pane at the very back (below the AOD fields, boundaries, stations and fires).
        // While one is on, the base map is off and the map background is dark gray.
        const VIS = CFG.visible;
        const visPane = map.createPane("visPane");
        visPane.style.zIndex = 150;                          // base map tiles: 200, AOD: 250, markers: 400+
        visPane.classList.add("vis-pane");
        const TRANSPARENT = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7";
        const isoDay = ms => new Date(ms).toISOString().slice(0, 10);
        const VIS_DAYS = [];                                 // days of the window (one composite per day)
        for (let t = Date.parse(CFG.window[0].slice(0, 10) + "T00:00:00Z"); t <= tms(CFG.window[1]); t += 86400000) VIS_DAYS.push(isoDay(t));
        let visDay = VIS_DAYS.includes(CFG.cycle.slice(0, 10)) ? CFG.cycle.slice(0, 10) : VIS_DAYS[0];
        const visLayers = {};                                // id -> tile layer
        let visKey = null, visSyncing = false;
        VIS.layers.forEach(v => {
            visLayers[v.id] = L.tileLayer(VIS.url, { layer: v.layer, time: visDay, pane: "visPane", tileSize: 256,
                maxNativeZoom: VIS.maxNativeZoom, maxZoom: 19, attribution: VIS.attribution, errorTileUrl: TRANSPARENT });
        });
        function setVisible(key) {                          // key = id or null
            visKey = visLayers[key] ? key : null;
            visSyncing = true;
            Object.entries(visLayers).forEach(([id, l]) => {
                if (id === visKey) { l.options.time = visDay; if (!map.hasLayer(l)) map.addLayer(l); else l.redraw(); }
                else if (map.hasLayer(l)) map.removeLayer(l);
            });
            visSyncing = false;
            applyTheme(theme);
            const bar = document.getElementById("vis-bar");
            bar.hidden = !visKey;
            document.body.classList.toggle("vis-on", !!visKey);
            if (visKey) {
                document.getElementById("vis-name").textContent = `${VIS.layers.find(v => v.id === visKey).label} VIIRS visible`;
                syncVisBar();
            }
        }
        function syncVisBar() {
            const i = VIS_DAYS.indexOf(visDay), rng = document.getElementById("vis-range");
            rng.max = VIS_DAYS.length - 1; rng.value = i;
            document.getElementById("vis-day").textContent = visDay;
            document.getElementById("vis-prev").disabled = i <= 0;
            document.getElementById("vis-next").disabled = i >= VIS_DAYS.length - 1;
        }
        function setVisDay(d) {
            if (!VIS_DAYS.includes(d) || d === visDay) return;
            visDay = d;
            const l = visKey && visLayers[visKey];
            if (l) { l.options.time = visDay; l.redraw(); }
            syncVisBar();
        }
        (function visBar() {
            const bar = document.getElementById("vis-bar");
            L.DomEvent.disableClickPropagation(bar);
            L.DomEvent.disableScrollPropagation(bar);
            bar.title = "Daily composite of the daytime passes (NASA GIBS)";
            document.getElementById("vis-prev").onclick = () => setVisDay(VIS_DAYS[VIS_DAYS.indexOf(visDay) - 1]);
            document.getElementById("vis-next").onclick = () => setVisDay(VIS_DAYS[VIS_DAYS.indexOf(visDay) + 1]);
            document.getElementById("vis-range").addEventListener("input", e => setVisDay(VIS_DAYS[+e.target.value]));
        })();
        // Also in the layer control (top right); picking one there switches off the others
        VIS.layers.forEach(v => {
            const l = visLayers[v.id];
            layerControl.addOverlay(l, `${v.label} VIIRS visible`);
            l.on("add remove", e => {
                if (visSyncing) return;
                const key = e.type === "add" ? v.id : (visKey === v.id ? null : visKey);
                setVisible(key);
                post({ type: "visible", key: visKey });
                visChanged();
            });
        });

        function applyTheme(t) {
            theme = THEMES[t] ? t : "dark";
            const th = THEMES[theme];
            document.body.classList.toggle("light", theme === "light");
            Object.values(baseLayers).forEach(l => map.removeLayer(l));
            if (visKey) {                                    // imagery on: no base map, gray background, boundaries on
                map.getContainer().style.background = VIS.background;
                map.addLayer(boundaries);
                return;
            }
            map.getContainer().style.background = "";
            map.addLayer(baseLayers[th.base] || Object.values(baseLayers)[0]);
            if (th.boundaries) map.addLayer(boundaries); else map.removeLayer(boundaries);
        }
        // Imagery at start: #vis=<id> from index.html ("" = none); on its own: config visible_imagery.start
        const visHash = hashGet("vis");
        visKey = visLayers[visHash !== undefined ? visHash : VIS.start] ? (visHash !== undefined ? visHash : VIS.start) : null;
        setVisible(visKey);
        // index.html shows this page once its map has painted (or after 2.5 s at most)
        let painted = false;
        const sendPainted = () => { if (!painted) { painted = true; post({ type: "painted" }); } };
        [...Object.values(baseLayers), ...Object.values(visLayers)].forEach(l => l.once("load", sendPainted));
        setTimeout(sendPainted, 2500);

        // ---- popups that stay inside the map ----
        // Open above the marker; if that would be cut off at the top, open below it instead,
        // and slide sideways near the edges. The tip keeps pointing at the marker.
        const SmartPopup = L.Popup.extend({
            options: { maxWidth: 2000, autoPan: false },
            _updatePosition() {
                L.Popup.prototype._updatePosition.call(this);
                if (!this._map || !this._container) return;
                const c = this._container, tip = this._tipContainer, pad = 8;
                c.classList.remove("popup-below");
                tip.style.marginLeft = "";
                const mapR = this._map.getContainer().getBoundingClientRect();
                const p = this._map.latLngToContainerPoint(this._latlng);
                let r = c.getBoundingClientRect();
                const top = r.top - mapR.top;
                if (top < pad && mapR.height - p.y > p.y) {          // no room above -> below
                    c.classList.add("popup-below");
                    const newTop = p.y + 22;
                    c.style.bottom = (parseFloat(c.style.bottom) - (newTop - top)) + "px";
                }
                r = c.getBoundingClientRect();
                const left = r.left - mapR.left;
                let dx = 0;
                if (left < pad) dx = pad - left;
                else if (left + r.width > mapR.width - pad) dx = (mapR.width - pad) - (left + r.width);
                if (dx) {
                    c.style.left = (parseFloat(c.style.left) + dx) + "px";
                    tip.style.marginLeft = (-20 - dx) + "px";
                }
            }
        });
        const smartPopup = content => new SmartPopup().setContent(content);
        // ONE canvas for station circles AND fire symbols (two canvases would swallow each other's clicks)
        const canvasRenderer = L.canvas({ padding: 0.5, tolerance: 3 });

        // ======================= CHART HELPERS =======================
        function downloadCSV(gd) {         // toolbar button: plotted values as CSV
            const meta = gd._csv || {};
            const traces = gd.data.filter(t => t.visible !== "legendonly");
            const times = [...new Set(traces.flatMap(t => t.x))].sort();
            const lookup = traces.map(t => new Map(t.x.map((x, i) => [x, meta.blankY ? null : t.y[i]])));
            const q = s => (/[",]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s);
            const lines = (meta.header || []).map(h => "# " + h);
            lines.push(["time_utc", ...traces.map(t => q(t.name))].join(","));
            times.forEach(x => {
                const vals = lookup.map(m => { const v = m.get(x); return v === null || v === undefined ? "" : v; });
                if (meta.blankY || vals.some(v => v !== "")) lines.push([x, ...vals].join(","));
            });
            const a = document.createElement("a");
            a.href = URL.createObjectURL(new Blob([lines.join("\n") + "\n"], { type: "text/csv" }));
            a.download = (meta.filename || "data") + ".csv";
            document.body.appendChild(a); a.click(); a.remove();
            setTimeout(() => URL.revokeObjectURL(a.href), 2000);
        }
        // PNG of 765 x 495 css px at scale 2. Station charts get their legend at the bottom.
        const PNG_W = 765, PNG_H = 495;
        function savePlotPNG(gd) {
            const data = JSON.parse(JSON.stringify(gd.data.filter(t => t.visible !== "legendonly")));
            const layout = JSON.parse(JSON.stringify(gd.layout));
            const P = gd._png || {};
            Object.assign(layout, P.layout || {}, { width: PNG_W, height: PNG_H, autosize: false });
            if (P.legend && data.length) {
                const longest = Math.max(...data.map(t => (t.name || "").length));
                const entry = Math.min(PNG_W - 40, Math.round(longest * 6 + 48));
                const cols = Math.max(1, Math.floor((PNG_W - 30) / entry));
                const rows = Math.ceil(data.length / cols), lh = rows * 19 + 10;
                layout.showlegend = true;
                layout.legend = { orientation: "h", xref: "container", yref: "container", x: 0.5, xanchor: "center",
                                  y: 6 / PNG_H, yanchor: "bottom", entrywidth: entry, entrywidthmode: "pixels",
                                  traceorder: "normal", font: { size: 10 } };
                layout.margin = Object.assign({}, layout.margin, { b: 48 + lh });
            }
            Plotly.toImage({ data, layout }, { format: "png", width: PNG_W, height: PNG_H, scale: 2 }).then(url => {
                const a = document.createElement("a");
                a.href = url;
                a.download = ((gd._csv && gd._csv.filename) || "chart") + ".png";
                document.body.appendChild(a); a.click(); a.remove();
            });
        }
        const plotConfig = () => ({
            responsive: true, displaylogo: false, displayModeBar: true,
            modeBarButtonsToRemove: ["lasso2d", "select2d", "toImage"],
            modeBarButtonsToAdd: [{ name: "Download plot as PNG", icon: Plotly.Icons.camera, click: savePlotPNG },
                                  { name: "Download data as CSV", icon: Plotly.Icons.disk, click: downloadCSV }]
        });
        // Line breaks: a null point wherever two consecutive values are further apart than
        // stations.gap_minutes (hourly data); an experiment with output every N h gets
        // (N - 1) * 60 + gap_minutes.
        const GAP_MIN = CFG.stations.gap_minutes ?? 65;
        function curve(x0, y0, everyH) {
            const maxMs = ((everyH - 1) * 60 + GAP_MIN) * 60000, x = [], y = [];
            let prev = null;
            for (let i = 0; i < x0.length; i++) {
                if (y0[i] === null || y0[i] === undefined) continue;
                if (prev !== null && tms(x0[i]) - tms(prev) > maxMs) { x.push(tPlus1(prev)); y.push(null); }
                x.push(x0[i]); y.push(y0[i]);
                prev = x0[i];
            }
            return { x, y };
        }
        const spikeAxis = { showspikes: true, spikemode: "across", spikesnap: "cursor",
                            spikethickness: 1, spikedash: "dash", spikecolor: "#555555" };

        // =====================================================================
        // STATIONS PAGE: circles coloured by the max observation (variables with a colour
        // scale) or clustered drop markers; click = time series of obs + experiments
        // =====================================================================
        function initStations() {
            const S = DATA.stations;
            const NO_DATA_COLOR = CFG.stations.no_data_color;
            const markerCluster = L.markerClusterGroup().addTo(map);
            const stationLayer = L.layerGroup().addTo(map);
            let stationMarkers = {}, colorMode = false, openStationKey = null;
            let hiddenTraces = new Set();          // curves hidden in the chart legend panel

            function renderStationPlot(div, sid) {
                const st = S[sid];
                if (!st) return;
                const v = activeVariable, e = st.d[v];
                const legendBox = div.parentElement.querySelector(".plot-legend");
                if (!e) {
                    Plotly.purge(div);
                    if (legendBox) legendBox.innerHTML = "";
                    div.innerHTML = `<div class="plot-msg"><h3>${esc(st.name)}</h3>
                        <p>No <b>${esc(v)}</b> data for this ${esc(CFG.label)} station.</p></div>`;
                    return;
                }
                if (div.querySelector(".plot-msg")) div.innerHTML = "";
                const cols = DATA.species[v].columns, x0 = e.t || DATA.species[v].time;
                const traces = [], items = [];
                traceSettings().forEach(it => {
                    const k = cols.indexOf(it.id), y0 = k >= 0 ? e.v[k] : null;
                    if (!it.visible || !y0 || !y0.some(y => y !== null)) return;      // off, or no data
                    const { x, y } = curve(x0, y0, it.every);
                    const tr = { x, y, name: it.name, type: "scatter", mode: it.isObs ? "lines+markers" : "lines",
                                 visible: hiddenTraces.has(it.id) ? "legendonly" : true,
                                 line: { color: it.color, dash: it.style, width: it.isObs ? 2.2 : 1.7 } };
                    if (it.isObs) tr.marker = { size: 5, color: it.color };
                    items.push(it);
                    traces.push(tr);
                });
                const fname = `${CFG.page}_${sid}_${v.replace(/\s+/g, "_")}_${CFG.cycleTag}`;
                const title = coords => ({ text: `<b>${esc(st.name)}</b>${coords} (${esc(CFG.label)}) — ${esc(v)}`,
                                           font: { size: 14, color: "#0f172a" }, x: 0.02, xanchor: "left", y: 0.97 });
                const layout = {
                    title: title(""),
                    xaxis: { title: { text: "UTC Time", standoff: 6 }, automargin: true, range: CFG.window, ...spikeAxis },
                    yaxis: { title: v + (UNITS[v] ? ` (${UNITS[v]})` : ""), hoverformat: `.${decOf(v)}f` },
                    hovermode: "x unified", showlegend: false,
                    margin: { l: 55, r: 25, t: 45, b: 45 }, autosize: true
                };
                Plotly.react(div, traces, layout, plotConfig());
                div._items = items;
                div._png = { legend: true, layout: { title: title(`  ${deg(st.lat, "N", "S", 2)} ${deg(st.lon, "E", "W", 2)}`),
                                                     margin: { l: 60, r: 25, t: 45, b: 45 } } };
                if (legendBox) buildChartLegend(div, legendBox);
                div._csv = { filename: fname, header: [
                    `Station: ${st.name} (id ${sid}), platform ${CFG.label}`,
                    `Lat ${st.lat}, Lon ${st.lon}`, `Variable: ${v} [${UNITS[v] || ""}]`, `Cycle: ${CFG.cycle}`] };
            }
            // ---- legend panel of the chart (click: show/hide, double-click: only this one) ----
            const DASH_SVG = { solid: "", dash: "6,3", dot: "1.5,2.5", dashdot: "6,2.5,1.5,2.5" };
            const lineSym = it => `<svg width="28" height="10"><line x1="1" y1="5" x2="27" y2="5" stroke="${it.color}" ` +
                `stroke-width="${it.isObs ? 2.2 : 1.8}" stroke-dasharray="${DASH_SVG[it.style] || ""}"/>` +
                (it.isObs ? `<circle cx="14" cy="5" r="3" fill="${it.color}"/>` : "") + `</svg>`;
            function buildChartLegend(div, box) {
                const items = div._items || [];
                if (!items.length) { box.innerHTML = `<div class="hint">No curves selected</div>`; return; }
                box.innerHTML = items.map((it, i) =>
                    `<div class="lg-row" data-i="${i}" title="${esc(it.name)}\nClick: show/hide · Double-click: only this one">` +
                    `${lineSym(it)}<span>${esc(it.name)}</span></div>`).join("") +
                    `<div class="hint">click: show/hide · double-click: only · <span class="show-all">show all</span></div>`;
                syncChartLegend(div, box);
                if (box._wired) return;                 // listeners once per popup
                box._wired = true;
                const chart = () => box.parentElement.querySelector(".plot-box");
                const apply = () => {
                    const d = chart(), its = d._items || [];
                    Plotly.restyle(d, { visible: its.map(it => (hiddenTraces.has(it.id) ? "legendonly" : true)) });
                    syncChartLegend(d, box);
                };
                box.addEventListener("click", e => {
                    const its = chart()._items || [], row = e.target.closest(".lg-row");
                    if (e.target.closest(".show-all")) hiddenTraces.clear();
                    else if (row) {
                        const id = its[+row.dataset.i].id;
                        hiddenTraces.has(id) ? hiddenTraces.delete(id) : hiddenTraces.add(id);
                    } else return;
                    apply();
                });
                box.addEventListener("dblclick", e => {    // both clicks already toggled it back
                    const row = e.target.closest(".lg-row");
                    if (!row) return;
                    const its = chart()._items || [], id = its[+row.dataset.i].id;
                    const alone = its.every(it => (it.id === id) !== hiddenTraces.has(it.id));
                    hiddenTraces = alone ? new Set() : new Set(its.filter(it => it.id !== id).map(it => it.id));
                    apply();
                });
            }
            function syncChartLegend(div, box) {
                const its = div._items || [];
                box.querySelectorAll(".lg-row").forEach(r => r.classList.toggle("off", hiddenTraces.has(its[+r.dataset.i].id)));
            }
            function refreshCurrentPlot() {
                if (!openStationKey) return;
                const div = document.querySelector(".leaflet-popup .plot-box[data-sid]");
                if (div) renderStationPlot(div, openStationKey);
            }

            // ---- markers ----
            function binIndex(v, scale) {     // last lower edge <= value; "no data" = colors.length
                if (v === null || v === undefined) return scale.colors.length;
                let i = 0;
                while (i + 1 < scale.bounds.length && v >= scale.bounds[i + 1]) i++;
                return i;
            }
            const scaleColor = (v, scale) => { const i = binIndex(v, scale); return i === scale.colors.length ? NO_DATA_COLOR : scale.colors[i]; };
            const binVisible = {};            // per variable; last entry = "no data", hidden by default
            const binVis = v => (binVisible[v] ||= [...Array(COLOR_SCALES[v].colors.length).fill(true), false]);
            const hasObs = st => st.obs_max[activeVariable] !== null && st.obs_max[activeVariable] !== undefined;
            const stationShown = st => binVis(activeVariable)[binIndex(st.obs_max[activeVariable], COLOR_SCALES[activeVariable])];
            function applyStationFilter() {
                if (!colorMode) return;
                Object.entries(stationMarkers).forEach(([k, m]) => {
                    const show = stationShown(S[k]), on = stationLayer.hasLayer(m);
                    if (show && !on) stationLayer.addLayer(m);
                    else if (!show && on) stationLayer.removeLayer(m);
                });
            }
            function stationTooltip(st) {
                const mx = st.obs_max[activeVariable];
                const val = mx === null || mx === undefined ? "no data" : `max obs. ${fmtVal(mx, activeVariable)}${unitOf(activeVariable)}`;
                return `<b>${esc(st.name)}</b><br>${esc(activeVariable)}: ${val}`;
            }
            function clearStations() {
                map.closePopup();
                markerCluster.clearLayers();
                stationLayer.clearLayers();
                stationMarkers = {};
                colorLegend.remove();
                showStatus("");
            }
            function updateMarkers() {
                const reopen = openStationKey;
                clearStations();
                if (!activeVariable) return;
                if (!Object.keys(S).length) showStatus(`No ${CFG.label} stations for this cycle`);
                const scale = COLOR_SCALES[activeVariable];
                colorMode = !!scale;
                Object.entries(S).forEach(([sid, st]) => {
                    if (!st.d[activeVariable]) return;          // station without this variable
                    const marker = scale
                        ? L.circleMarker([st.lat, st.lon], {
                              renderer: canvasRenderer, radius: CFG.stations.marker_radius, color: "#111111", weight: 1,
                              fillColor: scaleColor(st.obs_max[activeVariable], scale), fillOpacity: 0.95 })
                        : L.marker([st.lat, st.lon], { title: st.name });
                    marker.bindTooltip(() => stationTooltip(st));
                    marker.bindPopup(smartPopup(`<div class="plot-wrap"><div class="plot-box" data-sid="${esc(sid)}"></div>` +
                                                `<div class="plot-legend"></div></div>`));
                    marker.on("popupopen", e => {
                        if (openStationKey !== sid) hiddenTraces = new Set();
                        openStationKey = sid;
                        renderStationPlot(e.popup.getElement().querySelector(".plot-box"), sid);
                    });
                    marker.on("popupclose", e => {
                        if (openStationKey === sid) openStationKey = null;
                        const el = e.popup.getElement();
                        if (el) Plotly.purge(el.querySelector(".plot-box"));
                    });
                    if (!scale) { if (hasObs(st)) markerCluster.addLayer(marker); }
                    else if (stationShown(st)) stationLayer.addLayer(marker);
                    stationMarkers[sid] = marker;
                });
                updateColorLegend();
                if (reopen && stationMarkers[reopen]) stationMarkers[reopen].openPopup();
            }
            function recolorStations() {
                const scale = COLOR_SCALES[activeVariable];
                Object.entries(stationMarkers).forEach(([k, m]) => {
                    if (m.setStyle) m.setStyle({ fillColor: scaleColor(S[k].obs_max[activeVariable], scale) });
                });
                applyStationFilter();
                updateColorLegend();
            }

            // ---- colour legend (click a bin: show/hide; double-click: only that bin) ----
            const colorLegend = L.control({ position: "bottomleft" });
            colorLegend.onAdd = () => {
                const d = L.DomUtil.create("div", "color-legend");
                L.DomEvent.disableClickPropagation(d);
                L.DomEvent.disableScrollPropagation(d);
                const vis = () => binVis(activeVariable);
                d.addEventListener("click", e => {
                    if (e.target.closest(".show-all")) vis().fill(true);
                    else {
                        const row = e.target.closest(".bin-row");
                        if (!row) return;
                        vis()[+row.dataset.i] = !vis()[+row.dataset.i];
                    }
                    applyStationFilter(); syncLegendRows();
                });
                d.addEventListener("dblclick", e => {
                    const row = e.target.closest(".bin-row");
                    if (!row) return;
                    const v = vis();
                    v.forEach((_, i) => v[i] = (i === +row.dataset.i));
                    applyStationFilter(); syncLegendRows();
                });
                return d;
            };
            function syncLegendRows() {
                const vis = binVis(activeVariable);
                document.querySelectorAll(".color-legend .bin-row").forEach(r => r.classList.toggle("off", !vis[+r.dataset.i]));
            }
            function updateColorLegend() {
                const scale = COLOR_SCALES[activeVariable];
                if (!(colorMode && scale && Object.keys(stationMarkers).length)) { colorLegend.remove(); return; }
                if (!colorLegend._map) colorLegend.addTo(map);
                const b = scale.bounds, u = unitOf(activeVariable), vis = binVis(activeVariable);
                const cnt = Array(scale.colors.length + 1).fill(0);
                Object.keys(stationMarkers).forEach(k => cnt[binIndex(S[k].obs_max[activeVariable], scale)]++);
                const row = (i, color, lbl) =>
                    `<div class="row bin-row${vis[i] ? "" : " off"}" data-i="${i}" title="Click: show/hide · Double-click: only this one">` +
                    `<span class="sw" style="background:${color}"></span>${lbl}<span class="cnt">${cnt[i]}</span></div>`;
                let h = `<b>${esc(activeVariable)} — max obs. (${CFG.windowHours} h)</b>`;
                scale.colors.forEach((c, i) => {
                    const lbl = i === b.length - 1 ? `≥ ${b[i]}` : i === 0 ? `< ${b[1]}` : `${b[i]} – ${b[i + 1]}`;
                    h += row(i, c, lbl + u);
                });
                h += row(scale.colors.length, NO_DATA_COLOR, "no data");
                h += `<div class="hint">click: show/hide · double-click: only · <span class="show-all">show all</span></div>`;
                colorLegend.getContainer().innerHTML = h;
            }

            // v = null -> no variable: stations hidden
            HOOK.variable = v => {
                const wasNone = activeVariable === null;
                activeVariable = v;
                if (!v) return clearStations();
                // circles <-> circles: recolour (open popup stays); otherwise rebuild
                if (!wasNone && !!COLOR_SCALES[v] === colorMode && Object.keys(stationMarkers).length) recolorStations();
                else updateMarkers();
                refreshCurrentPlot();
            };
            HOOK.traces = refreshCurrentPlot;
            if (activeVariable) updateMarkers();
            else showStatus(`No ${CFG.label} observations for this cycle`);
        }

        // =====================================================================
        // FIRES PAGE: HMS detections (symbol = satellite, size = max FRP), FRP time series
        // =====================================================================
        // Leaflet's canvas only knows circles, so add squares and triangles.
        L.Canvas.include({
            _updateShape(layer) {
                if (!this._drawing || layer._empty()) return;
                const p = layer._point, ctx = this._ctx, r = Math.max(Math.round(layer._radius), 1);
                ctx.beginPath();
                if (layer.options.shape === "square") ctx.rect(p.x - r, p.y - r, 2 * r, 2 * r);
                else if (layer.options.shape === "triangle") {
                    const h = r * 1.25;
                    ctx.moveTo(p.x, p.y - h); ctx.lineTo(p.x + h, p.y + 0.8 * h); ctx.lineTo(p.x - h, p.y + 0.8 * h);
                    ctx.closePath();
                } else ctx.arc(p.x, p.y, r, 0, 2 * Math.PI);
                this._fillStroke(ctx, layer);
            }
        });
        const ShapeMarker = L.CircleMarker.extend({
            options: { shape: "circle" },
            _updatePath() { this._renderer._updateShape(this); }
        });
        // Returns the fire layer (not on the map yet); its legend follows the layer.
        function initFires(F, SERIES) {
            const SAT_STYLE = CFG.hms.satellites, SAT_OTHER = CFG.hms.other_satellite;
            const FRP_EDGES = CFG.hms.frp_classes, RADII = CFG.hms.radii;
            const fireRadius = frp => {
                if (frp === null) return RADII[0];
                let i = 0;
                while (i < FRP_EDGES.length && frp >= FRP_EDGES[i]) i++;
                return RADII[i];
            };
            const fireLayer = L.layerGroup();
            const SAT_KEYS = [...Object.keys(SAT_STYLE), "Other"];
            const satCat = sat => (sat in SAT_STYLE ? sat : "Other");
            const satVisible = Object.fromEntries(SAT_KEYS.map(k => [k, true]));
            const satCount = Object.fromEntries(SAT_KEYS.map(k => [k, 0]));
            const fireTimeStr = mins => new Date(Date.parse(F.t0) + mins * 60000).toISOString().slice(0, 16).replace("T", " ");
            function fireCard(i, frpTxt, tStr) {
                const n = F.n[i];
                const rows = [
                    ["Lat", deg(F.lat[i], "N", "S")], ["Lon", deg(F.lon[i], "E", "W")],
                    ["Satellite", F.sat[i]], ["Method", F.methods[F.m[i]]], ["Ecosystem", F.eco[i]],
                    [n > 1 ? "Max FRP" : "FRP", frpTxt], [n > 1 ? "First obs." : "Time", `${tStr} UTC`]
                ];
                return `<div class="fire-card"><h4>${n > 1 ? `HMS fire — ${n} detections` : "HMS fire — single detection"}</h4><table>` +
                       rows.map(([k, v]) => `<tr><td>${k}</td><td><b>${esc(v)}</b></td></tr>`).join("") + `</table></div>`;
            }
            function renderFirePlot(div, i) {
                const s = SERIES[i], sty = SAT_STYLE[F.sat[i]] || SAT_OTHER;
                const x = s.t.map(fireTimeStr), valid = s.frp.some(v => v !== null);
                const trace = {
                    x, y: valid ? s.frp : x.map(() => 0), type: "scatter", mode: "lines+markers", name: "FRP (MW)",
                    connectgaps: false, line: { color: "#444444", width: 1 },
                    marker: { size: 8, color: sty.color, line: { color: "#222222", width: 1 },
                              symbol: { square: "square", circle: "circle", triangle: "triangle-up" }[sty.shape] },
                    hovertemplate: valid ? `%{y:.${decOf("FRP")}f} MW<extra></extra>` : "detected (FRP n/a)<extra></extra>"
                };
                const info = [`Lat: ${deg(F.lat[i], "N", "S")}`, `Lon: ${deg(F.lon[i], "E", "W")}`, `Satellite: ${F.sat[i]}`,
                              `Method: ${F.methods[F.m[i]]}`, `Ecosystem: ${F.eco[i]}`].join("  ·  ");
                const sub = `${F.n[i]} detections · max FRP ` + (F.frp[i] === null ? "n/a" : `${F.frp[i]} MW`);
                const fname = `hms_fire_${F.lat[i].toFixed(3)}_${F.lon[i].toFixed(3)}_${CFG.cycleTag}`;
                const layout = {
                    title: { text: `<b>HMS fire — Fire Radiative Power</b><br><span style="font-size:12px">${info}</span>` +
                                   `<br><span style="font-size:11px;color:#555">${sub}</span>`,
                             font: { size: 14, color: "#0f172a" }, x: 0.02, xanchor: "left", y: 0.97 },
                    xaxis: { title: "UTC Time", range: [fireTimeStr(0), fireTimeStr(F.minutes)], ...spikeAxis },
                    yaxis: { title: "FRP (MW)", rangemode: "tozero", showticklabels: valid },
                    hovermode: "x unified", showlegend: false, margin: { l: 60, r: 25, t: 95, b: 45 }, autosize: true,
                    annotations: valid ? [] : [{ text: "FRP not available for this detection method", xref: "paper", yref: "paper",
                                                 x: 0.5, y: 0.5, showarrow: false, font: { size: 13, color: "#777" } }]
                };
                Plotly.newPlot(div, [trace], layout, plotConfig());
                div._csv = { filename: fname, blankY: !valid, header: [
                    `HMS fire: Lat ${F.lat[i]}, Lon ${F.lon[i]}`, `Satellite: ${F.sat[i]}, Method: ${F.methods[F.m[i]]}, Ecosystem: ${F.eco[i]}`,
                    `FRP in MW${valid ? "" : " (not available for this detection method)"}`, `Cycle: ${CFG.cycle}`] };
            }
            const fireMarkers = [];
            for (let i = 0; i < F.lon.length; i++) {        // sorted small -> large FRP
                const sat = F.sat[i], frp = F.frp[i], sty = SAT_STYLE[sat] || SAT_OTHER;
                const m = new ShapeMarker([F.lat[i], F.lon[i]], {
                    renderer: canvasRenderer, shape: sty.shape, radius: fireRadius(frp),
                    color: "#1a1a1a", weight: 0.7, fillColor: sty.color, fillOpacity: 0.9 });
                const frpTxt = frp === null ? "n/a" : `${frp} MW`;
                if (F.t[i] === null) {                      // GOES fire with a time series
                    m.bindTooltip(`Max FRP = ${frpTxt}, detected by ${esc(sat)} (${F.n[i]} obs.)`);
                    m.bindPopup(smartPopup(`<div class="plot-box"></div>`));
                    m.on("popupopen", e => renderFirePlot(e.popup.getElement().querySelector(".plot-box"), i));
                    m.on("popupclose", e => { const el = e.popup.getElement(); if (el) Plotly.purge(el.querySelector(".plot-box")); });
                } else {                                    // info card only
                    const tStr = fireTimeStr(F.t[i]);
                    m.bindTooltip(F.n[i] > 1
                        ? `Max FRP = ${frpTxt}, detected by ${esc(sat)} (${F.n[i]} obs., first ${tStr} UTC)`
                        : `FRP = ${frpTxt}, detected by ${esc(sat)} at ${tStr} UTC`);
                    m.bindPopup(smartPopup(() => fireCard(i, frpTxt, tStr)));
                }
                const cat = satCat(sat);
                fireMarkers.push({ m, cat });
                satCount[cat]++;
            }
            function refreshFires() {         // re-add visible satellites, keeping the FRP draw order
                fireLayer.clearLayers();
                fireMarkers.forEach(f => { if (satVisible[f.cat]) fireLayer.addLayer(f.m); });
                document.querySelectorAll(".fire-legend .sat-row").forEach(r => r.classList.toggle("off", !satVisible[r.dataset.sat]));
            }
            const sym = (shape, color, r) => {
                const s = 2 * r + 4, c = s / 2;
                const g = shape === "square" ? `<rect x="${c - r}" y="${c - r}" width="${2 * r}" height="${2 * r}"/>`
                        : shape === "triangle" ? `<polygon points="${c},${c - 1.25 * r} ${c + 1.25 * r},${c + r} ${c - 1.25 * r},${c + r}"/>`
                        : `<circle cx="${c}" cy="${c}" r="${r}"/>`;
                return `<svg width="${s}" height="${s}"><g fill="${color}" stroke="#1a1a1a" stroke-width="0.7">${g}</g></svg>`;
            };
            const fireLegend = L.control({ position: "bottomleft" });
            fireLegend.onAdd = () => {
                const d = L.DomUtil.create("div", "fire-legend");
                let h = `<b>HMS fires (${F.lon.length} locations)</b>`;
                SAT_KEYS.forEach(k => {
                    const v = SAT_STYLE[k] || SAT_OTHER;
                    h += `<div class="row sat-row${satVisible[k] ? "" : " off"}" data-sat="${esc(k)}" title="Click: show/hide · Double-click: only this one">` +
                         `${sym(v.shape, v.color, 5)}${esc(k)}<span class="cnt">${satCount[k]}</span></div>`;
                });
                h += `<div class="hint">click: show/hide · double-click: only · <span class="show-all">show all</span></div>`;
                h += `<b style='margin-top:6px'>Max FRP (${CFG.windowHours} h)</b>`;
                const E = FRP_EDGES;
                [`< ${E[0]} MW`, ...E.slice(0, -1).map((e, k) => `${e}–${E[k + 1]} MW`), `> ${E[E.length - 1]} MW`]
                    .forEach((t, k) => h += `<div class="row">${sym("circle", "#bbbbbb", RADII[k])}${t}</div>`);
                d.innerHTML = h;
                L.DomEvent.disableClickPropagation(d);
                L.DomEvent.disableScrollPropagation(d);
                d.addEventListener("click", e => {
                    if (e.target.closest(".show-all")) { SAT_KEYS.forEach(k => satVisible[k] = true); return refreshFires(); }
                    const row = e.target.closest(".sat-row");
                    if (!row) return;
                    satVisible[row.dataset.sat] = !satVisible[row.dataset.sat];
                    refreshFires();
                });
                d.addEventListener("dblclick", e => {
                    const row = e.target.closest(".sat-row");
                    if (!row) return;
                    SAT_KEYS.forEach(k => satVisible[k] = (k === row.dataset.sat));
                    refreshFires();
                });
                return d;
            };
            refreshFires();
            fireLayer.on("add", () => fireLegend.addTo(map));
            fireLayer.on("remove", () => fireLegend.remove());
            return fireLayer;
        }

        // =====================================================================
        // GRIDDED PAGES (aod, bias): image overlays with a time slider
        // Each field arrives as an 8-bit PNG on its native lat/lon grid (row 0 = north;
        // code 0 = missing). Leaflet stretches an image overlay linearly in Web Mercator, so
        // the rows are re-spaced here: every output row is a slice of equal Mercator height,
        // filled with the grid row that contains its latitude.
        // aod page: its one layer; bias page: the layer of (selected experiment, variable).
        // =====================================================================
        function initGrids() {
            const aodPane = map.createPane("aodPane");
            aodPane.style.zIndex = 250;                 // above the base map, below boundaries
            aodPane.style.pointerEvents = "none";
            const BLANK = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7";
            const hoverBox = document.getElementById("aod-hover");
            const hideHover = () => { hoverBox.style.display = "none"; };
            const AD = CFG.aodDisplay;
            const T0 = tms(CFG.window[0]);
            const NSLOT = Math.round((tms(CFG.window[1]) - T0) / 3600000) + 1;      // hourly slots, both ends included
            const slotLabel = s => new Date(T0 + s * 3600000).toISOString().slice(0, 16).replace("T", " ") + " UTC";
            const shortLabel = s => new Date(T0 + s * 3600000).toISOString().slice(5, 13).replace("T", " ") + "Z";
            let aodSel = "mean";                        // "mean" or slot = hours after the window start
            let meanA = 0, meanB = NSLOT - 1;           // mean period (slots, both included): the gold handles
            const meanTxt = () => `Mean ${shortLabel(meanA)} – ${shortLabel(meanB)}`;

            // PNG decoded here, byte for byte (the browser decoder may colour-convert the codes)
            async function decodePNG(bytes) {
                const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength), idat = [];
                let p = 8, w = 0, h = 0;
                while (p + 8 <= bytes.length) {
                    const len = dv.getUint32(p), type = String.fromCharCode(...bytes.subarray(p + 4, p + 8));
                    if (type === "IHDR") { w = dv.getUint32(p + 8); h = dv.getUint32(p + 12); }
                    else if (type === "IDAT") idat.push(bytes.subarray(p + 8, p + 8 + len));
                    else if (type === "IEND") break;
                    p += 12 + len;
                }
                const stream = new Blob(idat).stream().pipeThrough(new DecompressionStream("deflate"));
                const raw = new Uint8Array(await new Response(stream).arrayBuffer());
                const out = new Uint8Array(w * h);
                for (let r = 0; r < h; r++) {
                    const f = raw[r * (w + 1)], src = r * (w + 1) + 1, dst = r * w;
                    for (let j = 0; j < w; j++) {
                        const x = raw[src + j], a = j ? out[dst + j - 1] : 0, b = r ? out[dst - w + j] : 0,
                              c = j && r ? out[dst - w + j - 1] : 0;
                        let v = x;
                        if (f === 1) v = x + a;
                        else if (f === 2) v = x + b;
                        else if (f === 3) v = x + ((a + b) >> 1);
                        else if (f === 4) {
                            const q = a + b - c, pa = Math.abs(q - a), pb = Math.abs(q - b), pc = Math.abs(q - c);
                            v = x + (pa <= pb && pa <= pc ? a : pb <= pc ? b : c);
                        }
                        out[dst + j] = v & 255;
                    }
                }
                return out;
            }
            function binIndex(v, scale) {
                let i = 0;
                while (i + 1 < scale.bounds.length && v >= scale.bounds[i + 1]) i++;
                return i;
            }

            // One gridded layer. Frames are hourly only; the mean of [meanA, meanB] is computed
            // here (each hour with data counts once per cell) and re-coded with the same encoding.
            function makeLayer(key, VI, VC, zIndex, ref) {
                const G = VI.grid, E = VI.enc, D2R = Math.PI / 180, N = G.nx * G.ny;
                const dLon = (G.east - G.west) / G.nx, dLat = (G.north - G.south) / G.ny;
                const mercY = lat => Math.log(Math.tan(Math.PI / 4 + lat * D2R / 2));
                const yN = mercY(G.north), yS = mercY(G.south);
                const latMin = G.south <= 0 && G.north >= 0 ? 0 : Math.min(Math.abs(G.south), Math.abs(G.north));
                const NYM = Math.min(Math.ceil((yN - yS) / (dLat * D2R / Math.cos(latMin * D2R))), 8 * G.ny);
                const rowMap = new Int32Array(NYM);     // output (Mercator) row -> grid row
                for (let r = 0; r < NYM; r++) {
                    const lat = Math.atan(Math.sinh(yN - (r + 0.5) * (yN - yS) / NYM)) / D2R;
                    rowMap[r] = Math.min(G.ny - 1, Math.max(0, Math.floor((G.north - lat) / dLat)));
                }
                const lin = E.kind === "linear";
                const codeVal = lin ? c => E.min + (E.max - E.min) * (c - 1) / 254 : c => E.max * ((c - 1) / 254) ** 2;
                const valCode = lin ? v => 1 + Math.round(254 * (Math.min(E.max, Math.max(E.min, v)) - E.min) / (E.max - E.min))
                                    : v => 1 + Math.round(254 * Math.sqrt(Math.min(E.max, Math.max(0, v)) / E.max));
                const VAL = new Float32Array(256);
                for (let c = 1; c < 256; c++) VAL[c] = codeVal(c);
                const LUT = new Uint32Array(256);       // code -> RGBA (little-endian ABGR); 0 = transparent
                for (let c = 1; c < 256; c++) {
                    const hx = VC.scale.colors[binIndex(VAL[c], VC.scale)];
                    const [r, g, b] = [1, 3, 5].map(k => parseInt(hx.slice(k, k + 2), 16));
                    LUT[c] = ((255 << 24) | (b << 16) | (g << 8) | r) >>> 0;
                }
                const codeCache = {}, urlCache = {};    // frame index (or "a-b" for a mean) -> codes / image URL
                async function frameCodes(k) {
                    if (codeCache[k]) return codeCache[k];
                    const bytes = Uint8Array.from(atob(VI.frames[k].png), ch => ch.charCodeAt(0));
                    if (typeof DecompressionStream !== "undefined") return (codeCache[k] = await decodePNG(bytes));
                    const blob = new Blob([bytes], { type: "image/png" });     // very old browsers: may be inexact
                    let bmp;
                    try { bmp = await createImageBitmap(blob, { colorSpaceConversion: "none", premultiplyAlpha: "none" }); }
                    catch (e) { bmp = await createImageBitmap(blob); }
                    const cv = document.createElement("canvas");
                    cv.width = G.nx; cv.height = G.ny;
                    const ctx = cv.getContext("2d", { willReadFrequently: true });
                    ctx.drawImage(bmp, 0, 0);
                    const px = ctx.getImageData(0, 0, G.nx, G.ny).data, codes = new Uint8Array(N);
                    for (let i = 0; i < N; i++) codes[i] = px[4 * i];
                    return (codeCache[k] = codes);
                }
                let meanKey = null;                     // only the last mean is kept
                async function meanCodes(a, b) {
                    const mk = `${a}-${b}`;
                    if (codeCache[mk]) return codeCache[mk];
                    const sum = new Float32Array(N), cnt = new Uint16Array(N);
                    for (let k = 0; k < VI.frames.length; k++) {
                        const h = VI.frames[k].h;
                        if (h < a || h > b) continue;
                        const c = await frameCodes(k);
                        for (let i = 0; i < N; i++) if (c[i]) { sum[i] += VAL[c[i]]; cnt[i]++; }
                    }
                    const out = new Uint8Array(N);
                    for (let i = 0; i < N; i++) if (cnt[i]) out[i] = valCode(sum[i] / cnt[i]);
                    if (meanKey) { delete codeCache[meanKey]; if (urlCache[meanKey]) URL.revokeObjectURL(urlCache[meanKey]); delete urlCache[meanKey]; }
                    meanKey = mk;
                    return (codeCache[mk] = out);
                }
                async function codesOf(k) { return typeof k === "string" ? meanCodes(...k.split("-").map(Number)) : frameCodes(k); }
                async function frameURL(k) {
                    if (urlCache[k]) return urlCache[k];
                    const codes = await codesOf(k);
                    const cv = document.createElement("canvas");
                    cv.width = G.nx; cv.height = NYM;
                    const ctx = cv.getContext("2d"), img = ctx.createImageData(G.nx, NYM);
                    const out = new Uint32Array(img.data.buffer);
                    for (let r = 0; r < NYM; r++) {
                        const src = rowMap[r] * G.nx, dst = r * G.nx;
                        for (let j = 0; j < G.nx; j++) out[dst + j] = LUT[codes[src + j]];
                    }
                    ctx.putImageData(img, 0, 0);
                    const blob = await new Promise(res => cv.toBlob(res, "image/png"));
                    return (urlCache[k] = URL.createObjectURL(blob));
                }
                const layer = L.imageOverlay(BLANK, [[G.south, G.west], [G.north, G.east]],
                                             { pane: "aodPane", opacity: gridOpacity, interactive: false, className: "aod-img", zIndex });
                const bySlot = {};
                VI.frames.forEach((f, k) => { bySlot[f.h] = k; });
                const A = { key, VI, VC, layer, ref, codes: null, tok: 0,
                            slots: Object.keys(bySlot).map(Number).sort((a, b) => a - b) };
                // frame of a selection: "a-b" key for the mean (undefined = no hour with data in it)
                A.frameAt = sel => sel === "mean" ? (A.slots.some(s => s >= meanA && s <= meanB) ? `${meanA}-${meanB}` : undefined)
                                                  : bySlot[sel];
                A.render = async sel => {
                    const tok = ++A.tok, k = A.frameAt(sel);
                    if (!map.hasLayer(layer)) return;
                    if (k === undefined) { A.codes = null; layer.setUrl(BLANK); return; }
                    const url = await frameURL(k), codes = await codesOf(k);
                    if (tok !== A.tok || !map.hasLayer(layer)) return;
                    A.codes = codes;
                    layer.setUrl(url);
                };
                A.prefetch = sel => { const k = A.frameAt(sel); if (k !== undefined) frameURL(k); };
                A.paint = P => {                        // current frame -> canvas in projection P
                    const w = P.pw, h = P.ph, cv = document.createElement("canvas");
                    cv.width = w; cv.height = h;
                    if (!A.codes) return cv;
                    const ctx = cv.getContext("2d"), img = ctx.createImageData(w, h), out = new Uint32Array(img.data.buffer);
                    for (let y = 0; y < h; y++) {
                        for (let x = 0; x < w; x++) {
                            const ll = P.inv(x, y);
                            if (!ll) continue;
                            const lon = ((ll[0] + 180) % 360 + 360) % 360 - 180;
                            const i = Math.floor((G.north - ll[1]) / dLat), j = Math.floor((lon - G.west) / dLon);
                            if (i >= 0 && i < G.ny && j >= 0 && j < G.nx) out[y * w + x] = LUT[A.codes[i * G.nx + j]];
                        }
                    }
                    ctx.putImageData(img, 0, 0);
                    return cv;
                };
                A.valueAt = ll => {
                    if (!A.codes) return null;
                    const i = Math.floor((G.north - ll.lat) / dLat), j = Math.floor((ll.lng - G.west) / dLon);
                    const c = i >= 0 && i < G.ny && j >= 0 && j < G.nx ? A.codes[i * G.nx + j] : 0;
                    return c ? VAL[c] : null;
                };
                layer.on("add", () => { syncControls(); A.render(aodSel); });
                layer.on("remove", () => { A.codes = null; hideHover(); syncControls(); });
                return A;
            }
            const AODS = [];                            // every gridded layer known to this page
            const aodOn = () => AODS.filter(A => map.hasLayer(A.layer));
            // Draw order: VIIRS < TEMPO < this page's own field
            const Z = { viirs: 1, tempo: 2 };
            function wantedKeys() {
                const keys = [];
                if (KIND === "aod") keys.push(...Object.keys(DATA.layers));
                if (KIND === "bias" && activeVariable) {         // Observation | <exp> | <exp>|bias
                    const k = singleExp === "Observation" ? "obs" : /\|bias$/.test(singleExp || "") ? singleExp : `${singleExp}|model`;
                    if (DATA.layers[k]) keys.push(k);
                }
                AODS.forEach(A => { if (A.ref && REF_ON[A.ref]) keys.push(A.key); });
                return keys;
            }
            let syncing = false;
            function syncLayers() {               // show exactly the wanted layers
                const want = new Set(wantedKeys());
                syncing = true;
                AODS.forEach(A => {
                    const on = map.hasLayer(A.layer);
                    if (want.has(A.key) && !on) map.addLayer(A.layer);
                    else if (!want.has(A.key) && on) map.removeLayer(A.layer);
                });
                syncing = false;
                if (KIND === "bias") showStatus(activeVariable && !want.size ? "No data for this selection" : "");
            }
            GRID.add = (layers, metas, ref) => {   // own layers, or a reference product (viirs, tempo)
                Object.keys(layers).forEach(k => {
                    if (AODS.some(A => A.key === k)) return;
                    const A = makeLayer(k, layers[k], metas[k], ref ? (Z[ref] || 3) : 5, ref);
                    AODS.push(A);
                    if (ref) {                      // also in the layer control (top right)
                        layerControl.addOverlay(A.layer, A.VC.label);
                        A.layer.on("add remove", e => {
                            if (syncing) return;    // user toggled it in the layer control
                            REF_ON[ref] = e.type === "add";
                            post({ type: "ref", key: ref, on: REF_ON[ref] });
                        });
                    }
                });
                syncLayers();
            };
            GRID.sync = syncLayers;
            GRID.refresh = () => syncControls();

            // ---- time slider (only hours with data in a shown layer can be selected) ----
            const tsBox = document.getElementById("aod-slider"), tsRange = document.getElementById("aod-range");
            const tsTicks = document.getElementById("aod-ticks"), tsLbl = document.getElementById("aod-time-lbl");
            const tsMean = document.getElementById("aod-mean"), tsPlay = document.getElementById("aod-play");
            const tsTrack = document.getElementById("aod-track"), tsBand = document.getElementById("aod-band");
            const tsH = [document.getElementById("aod-h0"), document.getElementById("aod-h1")];
            L.DomEvent.disableClickPropagation(tsBox);
            L.DomEvent.disableScrollPropagation(tsBox);
            tsRange.max = NSLOT - 1;
            let slots = [], playing = false, playTok = 0, dragging = false, lastV = 0;
            const sleep = ms => new Promise(r => setTimeout(r, ms));
            const pos = s => `calc(8.5px + ${s / Math.max(1, NSLOT - 1)} * (100% - 17px))`;   // slot -> left (thumb centre)
            function nearestSlot(v, dir) {      // dir > 0: next at/after v; dir < 0: last at/before v; 0: closest
                if (!slots.length) return null;
                if (dir > 0) return slots.find(s => s >= v) ?? slots[slots.length - 1];
                if (dir < 0) return [...slots].reverse().find(s => s <= v) ?? slots[0];
                return slots.reduce((b, s) => (Math.abs(s - v) < Math.abs(b - v) ? s : b));
            }
            function syncSliderUI() {
                const mean = aodSel === "mean";
                tsMean.classList.toggle("active", mean);
                tsBox.classList.toggle("mean", mean);
                if (!mean && !dragging) tsRange.value = aodSel;
                const nh = slots.filter(s => s >= meanA && s <= meanB).length;
                tsLbl.textContent = mean ? meanTxt() : slotLabel(aodSel);
                tsLbl.title = mean ? `${nh} hour(s) with data in the period` : "";
                tsH[0].style.left = pos(meanA); tsH[1].style.left = pos(meanB);
                tsBand.style.left = pos(meanA);
                tsBand.style.width = `calc(${(meanB - meanA) / Math.max(1, NSLOT - 1)} * (100% - 17px))`;
                tsPlay.textContent = playing ? "❚❚" : "▶";
                tsPlay.title = playing ? "Pause" : "Play the hours with data";
            }
            // Gold handles: drag to set the mean period (snaps to whole hours); on release the
            // map shows the mean of that period.
            tsH.forEach((h, i) => {
                h.addEventListener("pointerdown", e => {
                    e.preventDefault(); e.stopPropagation();
                    h.setPointerCapture(e.pointerId);
                    h.classList.add("drag");
                    stopPlay();
                    const move = ev => {
                        const r = tsTrack.getBoundingClientRect();
                        let s = Math.round((ev.clientX - r.left - 8.5) / Math.max(1, r.width - 17) * (NSLOT - 1));
                        s = Math.max(0, Math.min(NSLOT - 1, s));
                        if (i === 0) meanA = Math.min(s, meanB); else meanB = Math.max(s, meanA);
                        syncSliderUI();
                        if (aodSel === "mean") updateAodLegend();
                    };
                    const up = () => {
                        h.classList.remove("drag");
                        h.removeEventListener("pointermove", move);
                        h.removeEventListener("pointerup", up);
                        h.removeEventListener("pointercancel", up);
                        setAodSel("mean");
                    };
                    h.addEventListener("pointermove", move);
                    h.addEventListener("pointerup", up);
                    h.addEventListener("pointercancel", up);
                });
                h.addEventListener("dblclick", () => { meanA = 0; meanB = NSLOT - 1; setAodSel("mean"); });   // reset
            });
            // Slider between the bottom-left and bottom-right controls when there is room;
            // otherwise it spans the bottom and the controls move up above it.
            const ctlL = map.getContainer().querySelector(".leaflet-bottom.leaflet-left");
            const ctlR = map.getContainer().querySelector(".leaflet-bottom.leaflet-right");
            function layoutSlider() {
                ctlL.style.bottom = ctlR.style.bottom = "";
                tsBox.style.left = tsBox.style.width = "";
                if (tsBox.hidden) return;
                const W = map.getSize().x, lw = ctlL.offsetWidth;
                const rw = Math.max(0, ...[...ctlR.children].filter(c => !c.classList.contains("leaflet-control-attribution"))
                                                           .map(c => c.offsetWidth + 10));
                const gap = W - lw - rw - 30;
                if (gap >= 560) {
                    tsBox.style.width = Math.min(1200, gap) + "px";
                    tsBox.style.left = (lw + 15 + gap / 2) + "px";
                } else ctlL.style.bottom = ctlR.style.bottom = (tsBox.offsetHeight + 28) + "px";
            }
            if (window.ResizeObserver) {
                const ro = new ResizeObserver(() => layoutSlider());
                [ctlL, ctlR, map.getContainer()].forEach(el => ro.observe(el));
            }
            function updateSlider() {
                const on = aodOn();
                tsBox.hidden = !on.length;
                layoutSlider();
                if (!on.length) { stopPlay(); syncSliderUI(); return; }
                slots = [...new Set(on.flatMap(A => A.slots))].sort((a, b) => a - b);
                tsTicks.innerHTML = slots.map(s => `<span style="left:${s / (NSLOT - 1) * 100}%"></span>`).join("");
                tsPlay.disabled = !slots.length;
                if (aodSel !== "mean" && !slots.includes(aodSel)) aodSel = slots.length ? nearestSlot(aodSel, 0) : "mean";
                syncSliderUI();
            }
            async function setAodSel(sel) {
                aodSel = sel;
                hideHover();
                syncSliderUI();
                updateAodLegend();
                const slow = sel === "mean" && aodOn().length;
                if (slow) showStatus("Computing the mean…");
                await Promise.all(aodOn().map(A => A.render(sel)));
                if (slow) showStatus("");
            }
            function stopPlay() {
                if (!playing) return;
                playing = false; playTok++;
                tsPlay.textContent = "▶"; tsPlay.title = "Play the hours with data";
            }
            async function play() {
                if (!slots.length) return;
                playing = true;
                const tok = ++playTok;
                syncSliderUI();
                if (aodSel === "mean") await setAodSel(slots[0]);
                while (playing && tok === playTok) {
                    const i = slots.indexOf(aodSel), next = slots[(i + 1) % slots.length];
                    aodOn().forEach(A => A.prefetch(next));
                    await sleep(AD.frame_ms);
                    if (!playing || tok !== playTok) break;
                    await setAodSel(next);
                }
            }
            tsPlay.onclick = () => (playing ? stopPlay() : play());
            tsMean.onclick = () => {
                stopPlay();
                setAodSel(aodSel === "mean" ? nearestSlot(+tsRange.value, 0) ?? "mean" : "mean");
            };
            tsRange.addEventListener("pointerdown", () => { dragging = true; lastV = aodSel === "mean" ? null : aodSel; });
            const endDrag = () => { if (dragging) { dragging = false; syncSliderUI(); } };
            window.addEventListener("pointerup", endDrag);
            window.addEventListener("pointercancel", endDrag);
            tsRange.addEventListener("change", endDrag);
            tsRange.addEventListener("input", () => {
                const v = +tsRange.value;
                stopPlay();
                const prev = dragging ? lastV : (aodSel === "mean" ? null : aodSel);
                const s = nearestSlot(v, prev === null ? 0 : Math.sign(v - prev));
                lastV = v;
                if (s === null) return;
                if (!dragging) tsRange.value = s;
                if (s !== aodSel) setAodSel(s); else syncSliderUI();
            });

            // ---- colour bar (bottom right): one bar per colour scale shown ----
            const aodLegend = L.control({ position: "bottomright" });
            aodLegend.onAdd = () => L.DomUtil.create("div", "aod-legend");
            function updateAodLegend() {
                const on = aodOn();
                if (!on.length) { aodLegend.remove(); return; }
                if (!aodLegend._map) aodLegend.addTo(map);
                const groups = [];
                on.forEach(A => {
                    const id = JSON.stringify(A.VC.scale), g = groups.find(x => x.id === id);
                    if (g) g.labels.push(A.VC.label); else groups.push({ id, scale: A.VC.scale, labels: [A.VC.label] });
                });
                const sub = aodSel === "mean" ? meanTxt() : `${slotLabel(aodSel)} (±30 min)`;   // time: in the slider
                aodLegend.getContainer().title = sub;
                aodLegend.getContainer().innerHTML = groups.map(g => {
                    const S = g.scale, m = S.colors.length;     // colours bottom -> top, ticks at the bin edges
                    return `<div class="vgrp"><div class="vtitle">${esc(g.labels.join(" · "))}</div>` +
                           `<div class="vbar">${S.colors.map(c => `<span style="background:${c}"></span>`).join("")}</div>` +
                           `<div class="vticks">${S.bounds.slice(1).map((b, k) =>
                               `<span style="bottom:${(k + 1) / m * 100}%">${b}</span>`).join("")}</div></div>`;
                }).join("");
            }
            function syncControls() {
                updateSlider(); updateAodLegend();
                post({ type: "grid", on: aodOn().length > 0 || !!visKey });   // index.html: opacity / PNG row
            }

            // ---- value under the cursor (native grid) ----
            let tipOpen = false;
            map.on("tooltipopen", () => { tipOpen = true; hideHover(); });
            map.on("tooltipclose", () => { tipOpen = false; });
            map.on("mousemove", e => {
                const tgt = e.originalEvent && e.originalEvent.target, on = aodOn();
                if (!on.length || tipOpen || (tgt && tgt.closest && tgt.closest(".leaflet-popup, .leaflet-control"))) return hideHover();
                const parts = on.map(A => [A, A.valueAt(e.latlng)]).filter(([, v]) => v !== null);
                if (!parts.length) return hideHover();
                hoverBox.innerHTML = parts.map(([A, v]) => `${esc(A.VC.label)}: <b>${v.toFixed(A.VC.decimals)}</b>`).join("<br>");
                hoverBox.style.left = (e.containerPoint.x + 14) + "px";
                hoverBox.style.top = (e.containerPoint.y + 14) + "px";
                hoverBox.style.display = "block";
            });
            map.on("mouseout", hideHover);

            // ---- opacity (slider box when the page is on its own; left panel in index.html) ----
            const opac = document.getElementById("aod-opacity");
            opac.value = gridOpacity;
            GRID.opacity = v => { gridOpacity = v; opac.value = v; AODS.forEach(A => A.layer.setOpacity(v)); };
            opac.addEventListener("input", () => GRID.opacity(+opac.value));
            // ---- Save the field as a PNG figure ----
            // Drawn from the data itself: white background, the layer(s) shown and boundary lines
            // from Natural Earth. Each output pixel looks up its grid cell through the inverse projection.
            const SNAP = CFG.aodSnapshot;
            let boundaryGeo = null;
            const loadBoundaries = () => (boundaryGeo ||= Promise.all(SNAP.boundaries.map(bd =>
                fetch(bd.url).then(r => (r.ok ? r.json() : null)).catch(() => null))));
            const D2R = Math.PI / 180;
            const wrap180 = d => ((d + 180) % 360 + 360) % 360 - 180;
            function makeProj(kind, bb, W, SC, origin) {
                const [w, e, s, n] = bb;
                let fx, fy, ix, H, X0, X1, Y0, Y1;
                if (kind === "latlon") {
                    fx = (lon, lat) => lon; fy = (lon, lat) => lat;
                    ix = (x, y) => [x, y];
                    [X0, X1, Y0, Y1] = [w, e, s, n];
                } else if (kind === "mercator") {
                    const my = lat => Math.log(Math.tan(Math.PI / 4 + Math.max(-85, Math.min(85, lat)) * D2R / 2));
                    fx = (lon, lat) => lon * D2R; fy = (lon, lat) => my(lat);
                    ix = (x, y) => [x / D2R, Math.atan(Math.sinh(y)) / D2R];
                    [X0, X1, Y0, Y1] = [w * D2R, e * D2R, my(s), my(n)];
                } else {                                // Lambert conformal conic, tangent at the origin latitude
                    const [la0, lo0] = origin, p0 = la0 * D2R;
                    const nn = Math.sin(p0), t = lat => Math.tan(Math.PI / 4 + Math.max(-89.9, Math.min(89.9, lat)) * D2R / 2);
                    const F = Math.cos(p0) * t(la0) ** nn / nn, rho = lat => F / t(lat) ** nn, r0 = rho(la0);
                    fx = (lon, lat) => rho(lat) * Math.sin(nn * wrap180(lon - lo0) * D2R);
                    fy = (lon, lat) => r0 - rho(lat) * Math.cos(nn * wrap180(lon - lo0) * D2R);
                    ix = (x, y) => {
                        const r = Math.sign(nn) * Math.hypot(x, r0 - y), th = Math.atan2(x, r0 - y);
                        if (Math.abs(th) > Math.PI * Math.abs(nn)) return null;
                        return [lo0 + th / nn / D2R, (2 * Math.atan((F / r) ** (1 / nn)) - Math.PI / 2) / D2R];
                    };
                    [X0, X1, Y0, Y1] = [Infinity, -Infinity, Infinity, -Infinity];
                    for (let k = 0; k <= 200; k++) {
                        const lon = w + (e - w) * k / 200, lat = s + (n - s) * k / 200;
                        [[lon, s], [lon, n], [w, lat], [e, lat]].forEach(([a, b]) => {
                            const x = fx(a, b), y = fy(a, b);
                            X0 = Math.min(X0, x); X1 = Math.max(X1, x); Y0 = Math.min(Y0, y); Y1 = Math.max(Y1, y);
                        });
                    }
                }
                const k = W / (X1 - X0);
                H = Math.round((Y1 - Y0) * k);
                return { W, H, pw: Math.round(W * SC), ph: Math.round(H * SC),
                         fwd: (lon, lat) => [(fx(lon, lat) - X0) * k, (Y1 - fy(lon, lat)) * k],
                         inv: (px, py) => ix(X0 + (px + 0.5) / SC / k, Y1 - (py + 0.5) / SC / k) };
            }
            function drawLines(ctx, geo, P, sty) {
                ctx.strokeStyle = sty.color || "#3a3a3a";
                ctx.lineWidth = sty.width || 0.8;
                ctx.lineJoin = "round";
                ctx.beginPath();
                const line = cs => {
                    let prev = null;
                    cs.forEach(([lon, lat]) => {
                        const [x, y] = P.fwd(lon, lat);
                        if (prev === null || Math.abs(lon - prev) > 180) ctx.moveTo(x, y); else ctx.lineTo(x, y);
                        prev = lon;
                    });
                };
                geo.features.forEach(f => {
                    const g = f.geometry;
                    if (!g) return;
                    if (g.type === "LineString") line(g.coordinates);
                    else if (g.type === "MultiLineString" || g.type === "Polygon") g.coordinates.forEach(line);
                    else if (g.type === "MultiPolygon") g.coordinates.forEach(p => p.forEach(line));
                });
                ctx.stroke();
            }
            const PROJ_NAME = { mercator: "Mercator", latlon: "Lat/Lon", lambert: "Lambert conformal" };
            // VIIRS visible imagery for the figure: the NASA GIBS tiles (Web Mercator) covering the
            // region are stitched, then each output pixel is looked up through the inverse projection
            // (works for Mercator, Lat/Lon and Lambert). Zoom: about one tile pixel per output pixel,
            // at most the native zoom and 256 tiles. The tiles must allow cross-origin reading (CORS);
            // otherwise the figure is saved without the imagery.
            async function paintImagery(P) {
                const V = VIS.layers.find(v => v.id === visKey);
                if (!V) return null;
                const MAXLAT = 85.0511, mercY = lat => Math.log(Math.tan(Math.PI / 4 + Math.max(-MAXLAT, Math.min(MAXLAT, lat)) * D2R / 2));
                let lo0 = Infinity, lo1 = -Infinity, la0 = Infinity, la1 = -Infinity;
                const NS = 60;                                  // lon/lat extent of the frame, sampled
                for (let i = 0; i <= NS; i++) for (let j = 0; j <= NS; j++) {
                    const ll = P.inv(i / NS * (P.pw - 1), j / NS * (P.ph - 1));
                    if (!ll) continue;
                    const lon = Math.max(-180, Math.min(180, ll[0]));
                    lo0 = Math.min(lo0, lon); lo1 = Math.max(lo1, lon); la0 = Math.min(la0, ll[1]); la1 = Math.max(la1, ll[1]);
                }
                if (!(lo1 > lo0 && la1 > la0)) return null;
                const tiles = z => {
                    const n = 2 ** z, ty = lat => Math.floor((1 - mercY(lat) / Math.PI) / 2 * n);
                    const x0 = Math.max(0, Math.floor((lo0 + 180) / 360 * n)), x1 = Math.min(n - 1, Math.floor((lo1 + 180) / 360 * n));
                    const y0 = Math.max(0, ty(la1)), y1 = Math.min(n - 1, ty(la0));
                    return { n, x0, x1, y0, y1, count: (x1 - x0 + 1) * (y1 - y0 + 1) };
                };
                let z = Math.max(0, Math.min(VIS.maxNativeZoom, Math.ceil(Math.log2(360 * P.pw / (256 * (lo1 - lo0))))));
                while (z > 0 && tiles(z).count > 256) z--;
                const T = tiles(z), MW = (T.x1 - T.x0 + 1) * 256, MH = (T.y1 - T.y0 + 1) * 256;
                const load = src => new Promise(res => {
                    const im = new Image();
                    im.crossOrigin = "anonymous";                // needed to read the pixels back
                    im.onload = () => res(im); im.onerror = () => res(null);
                    im.src = src;
                });
                const mos = document.createElement("canvas");
                mos.width = MW; mos.height = MH;
                const mctx = mos.getContext("2d", { willReadFrequently: true });
                const jobs = [];
                for (let x = T.x0; x <= T.x1; x++) for (let y = T.y0; y <= T.y1; y++) {
                    const url = L.Util.template(VIS.url, { layer: V.layer, time: visDay, z, x, y, s: "" });
                    jobs.push(load(url).then(im => { if (im) mctx.drawImage(im, (x - T.x0) * 256, (y - T.y0) * 256); }));
                }
                await Promise.all(jobs);
                let src;
                try { src = mctx.getImageData(0, 0, MW, MH).data; }
                catch (e) { return { error: "imagery not included (NASA tiles could not be read back: CORS)" }; }
                const cv = document.createElement("canvas");
                cv.width = P.pw; cv.height = P.ph;
                const ctx = cv.getContext("2d"), img = ctx.createImageData(P.pw, P.ph), out = img.data;
                const sc = 256 * T.n;
                for (let y = 0; y < P.ph; y++) {
                    for (let x = 0; x < P.pw; x++) {
                        const ll = P.inv(x, y);
                        if (!ll || ll[1] > MAXLAT || ll[1] < -MAXLAT) continue;
                        const X = Math.floor((wrap180(ll[0]) + 180) / 360 * sc) - T.x0 * 256;
                        const Y = Math.floor((1 - mercY(ll[1]) / Math.PI) / 2 * sc) - T.y0 * 256;
                        if (X < 0 || X >= MW || Y < 0 || Y >= MH) continue;
                        const i = (Y * MW + X) * 4, o = (y * P.pw + x) * 4;
                        if (!src[i + 3]) continue;                  // tile missing
                        out[o] = src[i]; out[o + 1] = src[i + 1]; out[o + 2] = src[i + 2]; out[o + 3] = 255;
                    }
                }
                ctx.putImageData(img, 0, 0);
                return { canvas: cv };
            }
            const visLabel = () => `${(VIS.layers.find(v => v.id === visKey) || { label: "" }).label} VIIRS true color`;

            async function saveAodPNG(region, kind) {
                const on = aodOn().filter(A => A.codes);
                if (!on.length && !visKey) return;
                const SC = 2, TOP = 50, BOT = 62;
                let bb = region && (kind === "lambert" ? region.lambertBounds : region.bounds);
                const W = SNAP.width;                   // same figure width for every region
                let origin = region && region.origin;
                if (!bb) {                              // current view: its bounds, drawn at the configured size
                    const mb = map.getBounds();
                    bb = [mb.getWest(), mb.getEast(), mb.getSouth(), mb.getNorth()];
                    origin = [(bb[2] + bb[3]) / 2, (bb[0] + bb[1]) / 2];
                }
                const P = makeProj(kind, bb, W, SC, origin), H = P.H;
                const geos = await loadBoundaries();
                const cv = document.createElement("canvas");
                cv.width = W * SC; cv.height = (H + TOP + BOT) * SC;
                const ctx = cv.getContext("2d");
                ctx.scale(SC, SC);
                const imagery = visKey ? await paintImagery(P) : null;   // {canvas} | {error} | null
                ctx.fillStyle = "#ffffff"; ctx.fillRect(0, 0, W, H + TOP + BOT);
                ctx.save();
                ctx.beginPath(); ctx.rect(0, TOP, W, H); ctx.clip();
                ctx.translate(0, TOP);
                if (visKey) { ctx.fillStyle = VIS.background; ctx.fillRect(0, 0, W, H); }   // as on the map
                if (imagery && imagery.canvas) {
                    ctx.imageSmoothingEnabled = true;           // imagery: smooth; AOD cells: hard edges
                    ctx.drawImage(imagery.canvas, 0, 0, W, H);
                }
                ctx.imageSmoothingEnabled = false;
                on.forEach(A => ctx.drawImage(A.paint(P), 0, 0, W, H));
                let nB = 0;
                geos.forEach((g, k) => { if (g && g.features) { drawLines(ctx, g, P, SNAP.boundaries[k]); nB++; } });
                ctx.restore();
                ctx.strokeStyle = "#333"; ctx.lineWidth = 1; ctx.strokeRect(0.5, TOP + 0.5, W - 1, H - 1);
                const when = aodSel === "mean" ? `mean, ${slotLabel(meanA)} – ${slotLabel(meanB)}` : `${slotLabel(aodSel)} (±30 min)`;
                ctx.fillStyle = "#111"; ctx.textBaseline = "alphabetic"; ctx.textAlign = "left";
                ctx.font = "bold 16px Helvetica, Arial, sans-serif";
                const visTxt = visKey ? `${visLabel()} ${visDay}` : "";
                ctx.fillText(on.length ? `${on.map(A => A.VC.label).join(" + ")} — ${when}${visTxt ? " · " + visTxt : ""}` : visTxt, 10, 22);
                ctx.font = "12px Helvetica, Arial, sans-serif"; ctx.fillStyle = "#555";
                const projTxt = kind === "lambert" ? `Lambert conformal, origin ${deg(origin[0], "N", "S", 2)} ${deg(origin[1], "E", "W", 2)}`
                                                   : PROJ_NAME[kind];
                ctx.fillText(`Cycle ${CFG.cycle}${region ? " · " + region.name : ""} · ${projTxt}`, 10, 40);
                ctx.textAlign = "right";
                ctx.fillText(`${deg(bb[2], "N", "S", 2)} – ${deg(bb[3], "N", "S", 2)}, ` +
                             `${deg(bb[0], "E", "W", 2)} – ${deg(bb[1], "E", "W", 2)}`, W - 10, 40);
                if (on.length) {                                // colour bar of the first AOD field shown
                    const S = on[0].VC.scale, nb = S.colors.length, bw = Math.min(W - 120, 44 * nb);
                    const x0 = (W - bw) / 2, y0 = TOP + H + 12, sw = bw / nb;
                    S.colors.forEach((c, k) => { ctx.fillStyle = c; ctx.fillRect(x0 + k * sw, y0, sw + 0.5, 12); });
                    ctx.strokeStyle = "#111"; ctx.strokeRect(x0 + 0.5, y0 + 0.5, bw, 12);
                    ctx.fillStyle = "#111"; ctx.font = "11px Helvetica, Arial, sans-serif"; ctx.textAlign = "center";
                    S.bounds.slice(1).forEach((v, k) => ctx.fillText(String(v), x0 + (k + 1) * sw, y0 + 26));
                }
                ctx.textAlign = "right"; ctx.fillStyle = "#777"; ctx.font = "10px Helvetica, Arial, sans-serif";
                const credits = [nB ? "Boundaries: Natural Earth" : "(boundaries not available)"];
                if (visKey) credits.push(imagery && imagery.canvas ? "Imagery: NASA GIBS / Worldview" : `(${(imagery && imagery.error) || "imagery not available"})`);
                ctx.fillText(credits.join(" · "), W - 8, TOP + H + BOT - 6);
                if (imagery && imagery.error) {
                    showStatus("Figure saved without the imagery: NASA tiles could not be read back");
                    setTimeout(() => showStatus(""), 6000);
                }
                const stamp8 = s => slotLabel(s).slice(0, 13).replace(/[-:]/g, "").replace(" ", "");      // YYYYMMDDHH
                const stamp = aodSel === "mean" ? `mean_${stamp8(meanA)}-${stamp8(meanB)}` : `${stamp8(aodSel)}`;
                const reg = region ? "_" + region.name.replace(/[^A-Za-z0-9]+/g, "") : "";
                const name = [...on.map(A => A.key.replace(/[^A-Za-z0-9.-]+/g, "_")),
                              ...(visKey ? [`${visKey}_truecolor_${visDay.replace(/-/g, "")}`] : [])].join("_");
                const blob = await new Promise(res => cv.toBlob(res, "image/png"));
                const a = document.createElement("a");
                a.href = URL.createObjectURL(blob);
                a.download = `${CFG.page}_${name}${reg}_${kind}_${stamp}.png`;
                document.body.appendChild(a); a.click(); a.remove();
                setTimeout(() => URL.revokeObjectURL(a.href), 2000);
            }
            (function snapDialog() {          // options window: region + projection
                const modal = document.getElementById("snap-modal"), opts = document.getElementById("snap-opts");
                const go = document.getElementById("snap-go");
                const regions = [{ name: "Current map view", bounds: null }, ...SNAP.regions];
                const bstr = b => `${deg(b[0], "E", "W", 0)} – ${deg(b[1], "E", "W", 0)}, ${deg(b[2], "N", "S", 0)} – ${deg(b[3], "N", "S", 0)}`;
                opts.innerHTML = `<div class="grp">Region</div>` + regions.map((r, k) =>
                    `<label><input type="radio" name="snap-reg" value="${k}" ${k ? "" : "checked"}>${esc(r.name)}` +
                    `<span class="cnt" data-k="${k}">${r.bounds ? bstr(r.bounds) : "as on screen"}</span></label>`).join("") +
                    `<div class="grp">Projection</div>` + Object.entries(PROJ_NAME).map(([k, t]) =>
                    `<label><input type="radio" name="snap-proj" value="${k}" ${k === SNAP.projection ? "checked" : ""}>${t}</label>`).join("");
                const close = () => { modal.hidden = true; };
                opts.addEventListener("change", () => {
                    const lam = modal.querySelector("input[name=snap-proj]:checked").value === "lambert";
                    regions.forEach((r, k) => { if (r.bounds) opts.querySelector(`.cnt[data-k="${k}"]`).textContent =
                        bstr(lam ? r.lambertBounds : r.bounds); });
                });
                document.getElementById("aod-save").onclick = () => {
                    const on = aodOn().filter(A => A.codes);
                    if (!on.length && !visKey) return;
                    document.getElementById("snap-sub").textContent = [
                        on.length ? `${on.map(A => A.VC.label).join(" + ")} — ${aodSel === "mean" ? meanTxt() : slotLabel(aodSel)}` : "",
                        visKey ? `${visLabel()} ${visDay}` : ""].filter(Boolean).join(" · ");
                    modal.hidden = false;
                };
                document.getElementById("snap-cancel").onclick = close;
                modal.addEventListener("click", e => { if (e.target === modal) close(); });
                document.addEventListener("keydown", e => { if (e.key === "Escape") close(); });
                go.onclick = async () => {
                    const r = regions[+modal.querySelector("input[name=snap-reg]:checked").value];
                    const kind = modal.querySelector("input[name=snap-proj]:checked").value;
                    go.disabled = true; go.textContent = "Saving…";
                    try { await saveAodPNG(r.bounds ? r : null, kind); close(); }
                    finally { go.disabled = false; go.textContent = "Save PNG"; }
                };
            })();

            GRID.png = () => document.getElementById("aod-save").click();
            if (KIND === "bias") {
                HOOK.variable = v => { activeVariable = v; syncLayers(); };
                HOOK.single = syncLayers;
            }
            if (KIND === "aod" || KIND === "bias") {
                GRID.add(DATA.layers, CFG.layers, null);
                // Start frame (config aod.display.start): mean of the window, or the first/latest hour with data
                const s0 = AODS.flatMap(A => A.slots).sort((a, b) => a - b);
                if (s0.length && AD.start !== "mean") setAodSel(AD.start === "first" ? s0[0] : s0[s0.length - 1]);
            }
            syncControls();
        }
        const ensureGrids = () => { if (!GRID.add) initGrids(); };
        // The PNG figure lives in the gridded-layer module: start it when the imagery is on
        function visChanged() { if (visKey) ensureGrids(); if (GRID.refresh) GRID.refresh(); }

        // ======================= REFERENCE OBSERVATIONS =======================
        // HMS fires, VIIRS and TEMPO AOD over any page. Their data come from their own pages
        // (hms.html, viirs.html, tempo.html), loaded by index.html and handed over here. The
        // left panel of index.html and the layer control (top right) switch them on and off.
        const REF_LOADED = {}, FIRE_REFS = {};
        let refSyncing = false;
        function addRef(key, rcfg, rdata) {
            if (REF_LOADED[key]) return;
            REF_LOADED[key] = true;
            if (rcfg.kind === "fires") {
                const fl = initFires(rdata.fires, rdata.series);
                FIRE_REFS[key] = fl;
                layerControl.addOverlay(fl, rcfg.label);
                fl.on("add remove", e => {
                    if (refSyncing) return;             // user toggled it in the layer control
                    REF_ON[key] = e.type === "add";
                    post({ type: "ref", key, on: REF_ON[key] });
                });
            } else {
                ensureGrids();
                GRID.add(rdata.layers, rcfg.layers, key);
            }
            syncRefs();
        }
        function syncRefs() {
            refSyncing = true;
            Object.entries(FIRE_REFS).forEach(([k, fl]) => {
                if (REF_ON[k] && !map.hasLayer(fl)) map.addLayer(fl);
                else if (!REF_ON[k] && map.hasLayer(fl)) map.removeLayer(fl);
            });
            refSyncing = false;
            if (GRID.sync) GRID.sync();
        }

        // ======================= STATE FROM index.html =======================
        function applyState(s) {
            if (s.theme && s.theme !== theme) applyTheme(s.theme);
            if ("visible" in s && (s.visible || null) !== visKey) { setVisible(s.visible); visChanged(); }
            if (typeof s.opacity === "number" && s.opacity !== gridOpacity) {
                gridOpacity = s.opacity;
                if (GRID.opacity) GRID.opacity(s.opacity);
            }
            if (s.ref) { Object.assign(REF_ON, s.ref); syncRefs(); }
            let changed = false;
            Object.entries(s.traces || {}).forEach(([id, t]) => {
                const cur = TRACE[id];
                if (!cur) return;
                ["visible", "color", "style"].forEach(k => { if (k in t && t[k] !== cur[k]) { cur[k] = t[k]; changed = true; } });
            });
            if ("single" in s && s.single !== singleExp) { singleExp = s.single; HOOK.single(); }
            if ("variable" in s && s.variable !== activeVariable) HOOK.variable(s.variable);
            else if (changed) HOOK.traces();
        }
        window.addEventListener("message", e => {
            if (e.source !== window.parent || !e.data || e.data.src !== "mmv-index") return;
            const d = e.data;
            if (d.ref) addRef(d.ref.key, d.ref.cfg, d.ref.data);
            if (d.state) applyState(d.state);
            if (d.cmd === "png" && GRID.png) GRID.png();
        });

        // ======================= START =======================
        if (EMBEDDED) document.body.classList.add("embedded");
        if (KIND === "stations") initStations();
        else if (KIND === "fires") {
            initFires(DATA.fires, DATA.series).addTo(map);
            if (!DATA.fires.lon.length) showStatus("No HMS fire detections in this window");
        }
        else if (KIND !== "base") initGrids();   // base: empty map, reference layers only
        if (visKey) ensureGrids();               // PNG figure with the imagery
        post({ type: "ready", page: CFG.page, kind: CFG.kind, variables: CFG.variables, available: CFG.available,
               defaultVariable: CFG.defaultVariable, experiments: CFG.expMode, avail: CFG.expAvail || {} });

        }   // main()

        main();
        }   // start()
    </script>
</body>
</html>
"""


# =========================================================================
# 8. INDEX TEMPLATE: left panel + the selected page in an <iframe>
#    URL hash: #<YYYYMMDDHH>/<page> (bookmarkable)
# =========================================================================
INDEX_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>__TITLE__</title>
    <style>
        :root { --bg: __DARK_BG__; --fg: #ededed; --title: #f5f5f5; --rule: #2e2e2e; --border: #2a2a2a;
                --btn-bg: #262626; --btn-fg: #e5e5e5; --btn-border: #404040; --btn-hover: #333333;
                --hdr-fg: #f1f5f9; --row-fg: #d4d4d4; --muted: #9a9a9a; --view-bg: #111111; }
        body.light { --bg: __LIGHT_BG__; --fg: #1f2937; --title: #111827; --rule: #cfcfcf; --border: #d0d0d0;
                --btn-bg: #ffffff; --btn-fg: #1f2937; --btn-border: #c4c4c4; --btn-hover: #f3f4f6;
                --hdr-fg: #111827; --row-fg: #374151; --muted: #6b7280; --view-bg: #dddddd; }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", sans-serif; }
        html, body { height: 100%; width: 100%; overflow: hidden; background: var(--bg); }
        #app-container { display: flex; height: 100vh; height: 100dvh; width: 100vw; position: relative; --panel-w: 360px; }
        /* Left panel: everything but the experiments list stays put; that list scrolls on its own */
        #left-panel { width: var(--panel-w); flex: none; height: 100%; background: var(--bg); color: var(--fg); z-index: 10;
                      border-right: 1px solid var(--border); padding: 12px 16px 0; display: flex; flex-direction: column; gap: 18px;
                      overflow: hidden; transition: width 0.25s ease, padding 0.25s ease; }
        #left-panel > * { min-width: calc(var(--panel-w) - 32px); }
        #left-panel.collapsed { width: 0; padding-left: 0; padding-right: 0; border-right: none; }
        #panel-toggle { position: absolute; top: 12px; left: var(--panel-w); z-index: 11; width: 24px; height: 46px;
                        border: 1px solid var(--border); border-left: none; border-radius: 0 7px 7px 0; background: var(--bg);
                        color: var(--fg); font-size: 15px; font-weight: 700; cursor: pointer; transition: left 0.25s ease;
                        box-shadow: 2px 0 6px rgba(0,0,0,0.3); }
        #panel-toggle:hover { background: var(--btn-hover); }
        #app-container.panel-hidden #panel-toggle { left: 0; }
        #panel-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: -8px; }
        #site-title { font-size: 13px; font-weight: 700; color: var(--title); }
        @media (max-width: 700px) {                      /* phones: the panel slides over the map */
            #app-container { --panel-w: 300px; }
            #left-panel { position: absolute; left: 0; top: 0; box-shadow: 4px 0 16px rgba(0,0,0,0.5); }
        }
        @media (max-height: 760px) {                     /* short screens: the whole panel scrolls */
            #left-panel { overflow-y: auto; }
            #models-section { flex: none; }
            #models-list { overflow: visible; }
            #left-panel.collapsed { overflow: hidden; }
        }
        #models-section { flex: 1; min-height: 0; display: flex; flex-direction: column; }
        #models-section.off #models-list { opacity: 0.4; pointer-events: none; }
        #models-list { flex: 1; min-height: 0; overflow-y: auto; padding: 2px 6px 16px 0; }
        #view-container { flex: 1; height: 100%; position: relative; background: var(--view-bg); }
        .view-frame { position: absolute; inset: 0; width: 100%; height: 100%; border: none; }
        .view-frame.loading { visibility: hidden; }        /* next page drawn underneath, shown once its map is painted */
        #view-msg { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
                    color: var(--muted); font-size: 15px; text-align: center; padding: 24px; }
        #view-msg[hidden] { display: none; }
        .panel-title { font-size: 14px; font-weight: 700; color: var(--title); margin-bottom: 10px; display: flex; align-items: center;
                       border-bottom: 2px solid var(--rule); padding-bottom: 6px; text-transform: uppercase; letter-spacing: 0.6px; }
        .panel-title .sub { margin-left: auto; font-size: 11px; font-weight: 400; text-transform: none; letter-spacing: 0; color: var(--muted); }
        #theme-btn { font-size: 11px; font-weight: 600; padding: 3px 9px; border-radius: 12px; border: 1px solid var(--btn-border);
                     background: var(--btn-bg); color: var(--btn-fg); cursor: pointer; }
        #theme-btn:hover { background: var(--btn-hover); }
        /* Cycle picker */
        #date-section { position: relative; }
        .cyc-nav { margin-left: auto; display: flex; gap: 4px; }
        .cyc-nav button { font-size: 13px; font-weight: 600; text-transform: none; letter-spacing: 0; padding: 4px 9px; border-radius: 6px;
                          border: 1px solid var(--btn-border); background: var(--btn-bg); color: var(--btn-fg); cursor: pointer; }
        .cyc-nav button:hover { background: var(--btn-hover); }
        .cyc-nav button:disabled { opacity: 0.35; cursor: default; }
        #date-pop { position: absolute; right: 0; top: 34px; z-index: 2000; width: 252px; padding: 10px;
                    background: var(--btn-bg); color: var(--btn-fg); border: 1px solid var(--btn-border);
                    border-radius: 8px; box-shadow: 0 6px 20px rgba(0,0,0,0.45); }
        #date-pop[hidden] { display: none; }
        .cal-head { display: flex; align-items: center; justify-content: space-between; font-weight: 700; font-size: 13px; margin-bottom: 6px; }
        .cal-head button { background: none; border: none; color: var(--btn-fg); font-size: 16px; cursor: pointer; padding: 0 6px; }
        .cal-head button:disabled { opacity: 0.25; cursor: default; }
        .cal-grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 2px; text-align: center; font-size: 12px; }
        .cal-grid .dow { color: var(--muted); font-size: 10.5px; padding: 2px 0; }
        .cal-day { padding: 5px 0; border-radius: 4px; cursor: pointer; border: 1px solid transparent; }
        .cal-day:hover { background: var(--btn-hover); }
        .cal-day.off { opacity: 0.25; cursor: default; background: none; }
        .cal-day.current { background: #0284c7; color: #fff; }
        .cal-hours { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 8px; }
        .cal-hours button { font-size: 11px; padding: 2px 7px; border-radius: 10px; border: 1px solid var(--btn-border);
                            background: var(--btn-bg); color: var(--btn-fg); cursor: pointer; }
        .cal-hours button.current { background: #0284c7; color: #fff; border-color: #38bdf8; }
        .cal-note { color: var(--muted); font-size: 10.5px; margin-top: 6px; }
        /* Buttons */
        .grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-top: 8px; }
        .btn-toggle { padding: 10px 5px; font-size: 13px; font-weight: 600; text-align: center; background: var(--btn-bg); color: var(--btn-fg);
                      border: 1px solid var(--btn-border); border-radius: 6px; cursor: pointer; transition: all 0.15s ease; user-select: none;
                      display: flex; align-items: center; justify-content: center; line-height: 1.2; }
        .btn-toggle:hover { background: var(--btn-hover); }
        .btn-toggle.disabled { opacity: 0.3; cursor: not-allowed; pointer-events: none; }
        .btn-toggle.active { background: #0284c7; color: #ffffff; border-color: #38bdf8; box-shadow: 0 0 8px rgba(2, 132, 199, 0.4); }
        .hint { color: var(--muted); font-size: 11px; grid-column: 1 / -1; }
        /* Experiments */
        input[type="checkbox"], input[type="radio"] { accent-color: #0284c7; width: 15px; height: 15px; cursor: pointer; }
        .trace-row { display: flex; align-items: center; justify-content: space-between; font-size: 13px; font-weight: 600;
                     padding: 5px 0; color: var(--hdr-fg); }
        .trace-row[hidden] { display: none; }
        .trace-row.sub { padding: 2px 0 4px 26px; font-size: 12px; font-weight: 500; color: var(--row-fg); }   /* "bias" under an experiment */
        .trace-controls[hidden] { display: none; }
        .trace-row.no-data { opacity: 0.4; }
        .trace-row.no-data input { cursor: not-allowed; }
        .trace-left { display: flex; align-items: center; gap: 8px; cursor: pointer; user-select: none; min-width: 0; }
        .trace-left span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .trace-controls { display: flex; align-items: center; gap: 6px; flex: none; }
        input[type="color"] { border: 1px solid var(--btn-border); width: 24px; height: 22px; border-radius: 4px; cursor: pointer; padding: 0; background: none; }
        /* Reference observations */
        .ref-row { display: flex; align-items: center; gap: 8px; font-size: 13px; font-weight: 600; padding: 3px 0;
                   color: var(--hdr-fg); cursor: pointer; user-select: none; }
        .ref-row .cnt { color: var(--muted); margin-left: auto; padding-left: 10px; font-weight: 400; font-size: 12px; }
        .ref-row.no-data { opacity: 0.4; cursor: not-allowed; }
        .ref-head { display: flex; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px;
                    color: var(--muted); margin: 8px 0 2px; }
        .ref-head .cnt { margin-left: auto; font-weight: 400; text-transform: none; letter-spacing: 0; }
        .ref-sub { display: flex; align-items: center; gap: 6px; padding: 3px 0 2px 23px; font-size: 12px; color: var(--row-fg); }
        .ref-sub.off { opacity: 0.4; pointer-events: none; }
        .ref-sub input[type="range"] { flex: 1; accent-color: #0284c7; }
        .ref-sub .step { font-size: 11px; padding: 3px 7px; border: 1px solid var(--btn-border); border-radius: 4px;
                         background: var(--btn-bg); color: var(--btn-fg); cursor: pointer; }
        .ref-sub .step:hover { background: var(--btn-hover); }
        select.style-select { font-size: 12px; padding: 3px 6px; border: 1px solid var(--btn-border); border-radius: 4px;
                              background: var(--btn-bg); color: var(--btn-fg); cursor: pointer; }
    </style>
</head>
<body>
    <div id="app-container">
        <div id="left-panel">
            <div id="panel-top"><span id="site-title"></span><button id="theme-btn" type="button"></button></div>
            <div id="date-section"><div class="panel-title">Cycle<span class="cyc-nav">
                     <button id="cyc-prev" type="button" title="Previous cycle">‹</button>
                     <button id="date-btn" type="button"></button>
                     <button id="cyc-next" type="button" title="Next cycle">›</button></span></div>
                 <div id="date-pop" hidden></div></div>
            <div id="ref-section"><div class="panel-title">Reference observations</div><div id="ref-list"></div>
                 <div class="ref-head">VIIRS visible imagery <span class="cnt">one at a time</span></div><div id="vis-list"></div>
                 <div class="ref-sub off" id="aod-opac"><span>Opacity</span><input type="range" id="aod-opacity" min="0.1" max="1" step="0.05">
                     <button class="step" type="button" id="aod-save" title="Save the map field as a PNG figure (white background, boundaries only)">⤓ PNG</button></div></div>
            <div><div class="panel-title">Observation Platform</div>
                 <div class="grid-3" id="platform-container"></div></div>
            <div><div class="panel-title">Variable</div><div class="grid-3" id="variable-container"></div></div>
            <div id="models-section"><div class="panel-title">Experiments<span class="sub" id="exp-note"></span></div>
                 <div id="models-list"></div></div>
        </div>
        <button id="panel-toggle" type="button" title="Hide the panel">«</button>
        <div id="view-container"><div id="view-msg" hidden></div></div>
    </div>

    <script>
        // ---------------- written by generate_website.py ----------------
        const IDX = __INDEX__;    // {title, pages, refs, cycles: {YYYYMMDDHH: {pages, cycle, window, windowHours}}, settings, ...}
        const S = IDX.settings;   // ALL viewer settings (map, colours, labels, scales, experiments, ...)
        const VIEWER = __VIEWER__;    // viewer page (code only), started in an iframe for each page shown

        const CYCLES = Object.keys(IDX.cycles).sort();
        let frame = null;                                   // iframe of the page shown (messages go to it)
        const viewBox = document.getElementById("view-container"), viewMsg = document.getElementById("view-msg");
        // Switching pages: the new page loads in a hidden iframe on top of the old one, which stays
        // visible until the new map has painted its base tiles ("painted"), so there is no blank flash.
        function openViewer(cfg, data) {
            const old = [...viewBox.querySelectorAll(".view-frame")];
            const f = document.createElement("iframe");
            f.className = "view-frame" + (old.length ? " loading" : "");
            f.title = "Map";
            let shown = false;
            f._reveal = () => {
                if (shown) return;
                shown = true;
                f.classList.remove("loading");
                old.forEach(o => o.remove());
            };
            setTimeout(f._reveal, 4000);                    // never wait longer than this
            // sent when the viewer reports "booted": settings + data + start view / theme / imagery
            f._init = { cfg, data, v: view ? `${view.center[0]},${view.center[1]},${view.zoom}` : "", t: theme, vis: vis || "" };
            f.srcdoc = VIEWER;
            viewBox.insertBefore(f, viewMsg);
            frame = f;
        }
        function closeFrames() { viewBox.querySelectorAll(".view-frame").forEach(o => o.remove()); frame = null; }
        const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
        const fmtCycle = c => `${c.slice(0, 4)}-${c.slice(4, 6)}-${c.slice(6, 8)} ${c.slice(8)}Z`;
        const isoDay = c => `${c.slice(0, 4)}-${c.slice(4, 6)}-${c.slice(6, 8)}`;
        document.getElementById("site-title").textContent = IDX.title;

        // ---- state ----
        let theme = "dark";
        try { theme = localStorage.getItem("verif-theme") || "dark"; } catch (e) {}
        let cycle = CYCLES[CYCLES.length - 1] || null;     // newest cycle
        let wantPage = IDX.startPage, page = null;         // page asked for / page shown (if it exists in the cycle)
        let info = null;                                   // "ready" message of the page shown
        let variable = null, single = null, view = null;
        const varByPage = {};                              // variable chosen on each page (null = none)
        const traces = { Observation: { visible: true, color: IDX.observation.color, style: "solid" } };
        IDX.experiments.forEach(x => { traces[x.id] = { visible: true, color: x.color, style: x.style }; });
        (function fromHash() {                             // #YYYYMMDDHH/page
            const m = /^#(\d{10})?(?:\/([^&]+))?/.exec(location.hash);
            if (m && m[1] && IDX.cycles[m[1]]) cycle = m[1];
            if (m && m[2]) wantPage = m[2] === "-" ? null : decodeURIComponent(m[2]);   // "-" = no platform
        })();
        const pagesOf = c => (IDX.cycles[c] || {}).pages || [];

        // ======================= THEME + PANEL =======================
        const themeBtn = document.getElementById("theme-btn");
        function applyTheme() {
            document.body.classList.toggle("light", theme === "light");
            themeBtn.textContent = theme === "light" ? "☾ Dark mode" : "☀ Light mode";
            try { localStorage.setItem("verif-theme", theme); } catch (e) {}
        }
        themeBtn.onclick = () => { theme = theme === "light" ? "dark" : "light"; applyTheme(); sendState(); };
        applyTheme();
        (function panelToggle() {
            const app = document.getElementById("app-container"), panel = document.getElementById("left-panel");
            const tab = document.getElementById("panel-toggle");
            tab.onclick = () => {
                const hide = !panel.classList.contains("collapsed");
                panel.classList.toggle("collapsed", hide);
                app.classList.toggle("panel-hidden", hide);
                tab.textContent = hide ? "»" : "«";
                tab.title = hide ? "Show the panel" : "Hide the panel";
            };
        })();

        // ======================= PAGE IN THE IFRAME =======================
        function showMsg(txt) { viewMsg.textContent = txt; viewMsg.hidden = !txt; }
        function loadPage() {
            const av = pagesOf(cycle), ids = IDX.pages.map(p => p.id);
            // wantPage null = no platform selected: empty map, reference layers only
            page = wantPage === null ? null
                 : av.includes(wantPage) && ids.includes(wantPage) ? wantPage : (ids.find(p => av.includes(p)) || null);
            info = null; sentRefs.clear(); gridOn = false; pendingView = null;
            dropOtherCycles();
            updateRefs();
            try { history.replaceState(null, "", `#${cycle || ""}/${page ? encodeURIComponent(page) : "-"}`); } catch (e) {}
            buildPlatformButtons(); buildVariableButtons(); updateExperiments(); updateCycleNav();
            if (!cycle) { closeFrames(); showMsg("No cycles built yet"); return; }
            showMsg("");
            if (!page) return openViewer(buildCfg(null), {});
            const c = dataCache[`${cycle}/${page}`];
            if (c) return openViewer(buildCfg(page, c.meta, false), c.data);
            pendingView = `${cycle}/${page}`;              // opened by gotData() when its data arrive
            loadData(page);
        }

        // ---- settings of one page = index.html settings + facts about the run (from its data file) ----
        const expLabel = id => (S.experiments.find(x => x.id === id) || { label: id }).label;
        const alias = n => S.aliases[n] ?? n;              // workflow species name -> name shown
        function buildCfg(id, meta, isRef) {
            const P = (id && (isRef ? S.refs : S.pages)[id]) || {};
            const run = meta || IDX.cycles[cycle] || {};
            const kind = !id ? "base" : meta.kind === "aod_bias" ? "bias" : meta.kind;
            const c = { page: id || "base", kind, label: P.label || id || "", isRef: !!isRef,
                        cycle: run.cycle, cycleTag: cycle, window: run.window, windowHours: run.windowHours,
                        map: S.map, stations: S.stations, hms: S.hms, colorScales: S.colorScales, units: S.units,
                        decimals: S.decimals, experiments: S.experiments, observation: S.observation,
                        aodDisplay: S.aodDisplay, aodSnapshot: S.aodSnapshot, visible: S.visible,
                        variables: [], available: [], expAvail: {}, defaultVariable: null, expMode: "none", layers: {} };
            if (kind === "stations") {                      // variables: configured order, then any others found
                c.variables = [...(P.variables || [])];
                meta.species.map(alias).forEach(v => { if (!c.variables.includes(v)) c.variables.push(v); });
                c.available = meta.available.map(alias);
                c.expAvail = Object.fromEntries(Object.entries(meta.expAvail).map(([k, v]) => [alias(k), v]));
                c.defaultVariable = c.available.includes(P.default) ? P.default : (c.available[0] ?? null);
                c.expMode = "multi";
            } else if (kind === "aod") {
                Object.entries(meta.layers).forEach(([k, L]) => {
                    c.layers[k] = S.aod[L.dataset] || { label: k, decimals: 2, scale: S.colorScales["AOD 550 nm"] };
                });
            } else if (kind === "bias") {                   // one variable; Observation / <exp> / <exp>|bias
                const v = S.bias.variable, obs = P.obs_label || String(meta.dataset || "").toUpperCase();
                c.variables = [v]; c.available = meta.choices.length ? [v] : [];
                c.expAvail = { [v]: meta.choices }; c.defaultVariable = c.available[0] ?? null; c.expMode = "single";
                Object.entries(meta.layers).forEach(([k, L]) => {
                    const F = S.bias[L.field];
                    c.layers[k] = { label: F.label.replace("{exp}", L.exp ? expLabel(L.exp) : "").replace("{obs}", obs).trim(),
                                    decimals: F.decimals, scale: F.scale };
                });
            }
            return c;
        }
        function aliasStations(data) {                     // species keys -> names shown
            const re = o => Object.fromEntries(Object.entries(o || {}).map(([k, v]) => [alias(k), v]));
            return { species: re(data.species), stations: Object.fromEntries(Object.entries(data.stations).map(
                ([sid, st]) => [sid, Object.assign({}, st, { obs_max: re(st.obs_max), d: re(st.d) })])) };
        }
        function sendState() {
            if (!info || !frame || !frame.contentWindow) return;
            const ref = Object.fromEntries(IDX.refs.map(r => [r.id, refShown(r)]));
            frame.contentWindow.postMessage({ src: "mmv-index", state: { theme, variable, traces, single, ref, opacity, visible: vis } }, "*");
        }
        window.addEventListener("message", e => {
            const d = e.data;
            if (!d) return;
            if (d.src === "mmv-data") return gotData(e.source, d);          // a data file loaded in a hidden frame
            if (d.src !== "mmv-page" || !frame || e.source !== frame.contentWindow) return;
            if (d.type === "booted") return frame.contentWindow.postMessage({ src: "mmv-index", init: frame._init }, "*");
            if (d.type === "painted") return frame._reveal();
            if (d.type === "view") view = { center: d.center, zoom: d.zoom };
            else if (d.type === "ref") { refOn[d.key] = d.on; updateRefs(); }  // toggled in the map's layer control
            else if (d.type === "grid") { gridOn = d.on; updateRefs(); }
            else if (d.type === "visible") { vis = d.key; updateVis(); }   // picked in the map's layer control
            else if (d.type === "ready") {
                info = d;
                // variable: the one chosen before on this page, else the page default
                const av = d.available || [];
                const want = page in varByPage ? varByPage[page] : d.defaultVariable;
                variable = want === null ? null : av.includes(want) ? want : (av.includes(d.defaultVariable) ? d.defaultVariable : (av[0] ?? null));
                fixSingle();
                buildVariableButtons(); updateExperiments(); updateRefs(); deliverRefs();
            }
        });

        // ======================= REFERENCE OBSERVATIONS =======================
        // HMS fires, VIIRS and TEMPO AOD can be shown over any page. Their data are in
        // <cycle>/<ref>.html: loaded on demand in a hidden frame, which hands them over, and
        // passed on to the page shown. On the VIIRS/TEMPO model pages the AOD
        // references are switched off (that page shows the satellite field itself).
        // VIIRS visible imagery (NASA GIBS tiles, loaded by the page itself): one at a time, or none
        let vis = (IDX.visible || []).some(v => v.id === IDX.visibleStart) ? IDX.visibleStart : null;
        const visList = document.getElementById("vis-list");
        visList.innerHTML = (IDX.visible || []).map((v, i) => `<label class="ref-row"><input type="checkbox" data-v="${i}">` +
                                                              `<span>${esc(v.label)}</span></label>`).join("");
        function updateVis() { visList.querySelectorAll("input").forEach(b => { b.checked = IDX.visible[+b.dataset.v].id === vis; }); }
        visList.addEventListener("change", e => {
            const id = IDX.visible[+e.target.dataset.v].id;
            vis = e.target.checked ? id : null;          // checking one unchecks the others
            updateVis(); sendState();
        });
        updateVis();
        const refOn = Object.fromEntries(IDX.refs.map(r => [r.id, r.show]));
        const dataCache = {}, loaders = {}, sentRefs = new Set();   // data of the cycle shown: "<cycle>/<id>" -> {meta, data}
        let pendingView = null;                                      // "<cycle>/<page>" waiting for its data
        let opacity = IDX.opacity, gridOn = false;
        const refList = document.getElementById("ref-list");
        const refBlocked = r => !!info && info.kind === "bias" && r.kind === "aod";
        const refAvail = r => pagesOf(cycle).includes(r.id);
        const refShown = r => refOn[r.id] && refAvail(r) && !refBlocked(r);
        refList.innerHTML = IDX.refs.map((r, i) => `<label class="ref-row" id="ref_${i}"><input type="checkbox" data-i="${i}">` +
                                                   `<span>${esc(r.label)}</span><span class="cnt"></span></label>`).join("");
        refList.addEventListener("change", e => {
            const r = IDX.refs[+e.target.dataset.i];
            refOn[r.id] = e.target.checked;
            updateRefs(); deliverRefs();
        });
        function dropOtherCycles() {        // keep only the reference data of the cycle shown
            Object.keys(dataCache).forEach(k => { if (!k.startsWith(cycle + "/")) delete dataCache[k]; });
            Object.entries(loaders).forEach(([k, f]) => { if (!k.startsWith(cycle + "/")) { f.remove(); delete loaders[k]; } });
        }
        function loadData(id) {             // <cycle>/<id>.html in a hidden frame; it posts {meta, data}
            const ck = `${cycle}/${id}`;
            if (dataCache[ck] || loaders[ck]) return;
            const f = document.createElement("iframe");
            f.hidden = true;
            f.src = `${cycle}/${encodeURIComponent(id)}.html`;
            loaders[ck] = f;
            document.body.appendChild(f);
            setTimeout(() => {              // no answer: missing, or written by an older version
                if (loaders[ck] !== f) return;
                f.remove(); delete loaders[ck];
                if (pendingView === ck) showMsg(`Could not read ${id} for ${fmtCycle(cycle)}. ` +
                                                `If it was built by an older version of the scripts, rerun this cycle.`);
            }, 90000);
        }
        function gotData(src, d) {
            const ck = Object.keys(loaders).find(k => loaders[k].contentWindow === src);
            if (!ck) return;
            loaders[ck].remove();
            delete loaders[ck];
            const data = d.meta && d.meta.kind === "stations" ? aliasStations(d.data) : d.data;
            dataCache[ck] = { meta: d.meta, data };
            if (ck === pendingView) {       // the page asked for: start the viewer
                pendingView = null;
                openViewer(buildCfg(page, d.meta, false), data);
            }
            updateRefs(); deliverRefs();
        }
        function refCount(r) {
            const c = dataCache[`${cycle}/${r.id}`];
            if (!c) return "";
            if (c.meta.kind === "fires") return `${c.data.fires.lon.length} locations`;
            const L = Object.values(c.data.layers)[0];
            return L ? `${L.frames.length} of ${c.meta.windowHours + 1} h` : "";
        }
        function updateRefs() {
            IDX.refs.forEach((r, i) => {
                const row = document.getElementById(`ref_${i}`), box = row.querySelector("input");
                const ok = refAvail(r) && !refBlocked(r), loading = refShown(r) && !dataCache[`${cycle}/${r.id}`];
                box.disabled = !ok;
                box.checked = refShown(r);
                row.classList.toggle("no-data", !ok);
                row.title = !refAvail(r) ? "Not available for this cycle" : refBlocked(r) ? "Shown by this page itself" : "";
                row.querySelector(".cnt").textContent = loading ? "loading…" : refCount(r);
                if (refShown(r)) loadData(r.id);
            });
            document.getElementById("aod-opac").classList.toggle("off", !gridOn);
        }
        function deliverRefs() {            // data of the shown references -> page, then the state
            if (!info || !frame || !frame.contentWindow) return;
            IDX.refs.forEach(r => {
                const c = dataCache[`${cycle}/${r.id}`];
                if (refShown(r) && c && !sentRefs.has(r.id)) {
                    frame.contentWindow.postMessage({ src: "mmv-index", ref: { key: r.id, cfg: buildCfg(r.id, c.meta, true), data: c.data } }, "*");
                    sentRefs.add(r.id);
                }
            });
            sendState();
        }
        const opacInput = document.getElementById("aod-opacity");
        opacInput.value = opacity;
        opacInput.addEventListener("input", () => { opacity = +opacInput.value; sendState(); });
        document.getElementById("aod-save").onclick = () => {
            if (frame && frame.contentWindow) frame.contentWindow.postMessage({ src: "mmv-index", cmd: "png" }, "*");
        };

        // ======================= PLATFORM & VARIABLE BUTTONS =======================
        const platformContainer = document.getElementById("platform-container");
        function buildPlatformButtons() {          // only the platforms built for this cycle
            const av = pagesOf(cycle);
            platformContainer.innerHTML = av.length ? "" : `<div class="hint">No platforms for this cycle</div>`;
            IDX.pages.filter(p => av.includes(p.id)).forEach(p => {
                const btn = document.createElement("button");
                btn.className = "btn-toggle" + (p.id === page ? " active" : "");
                btn.textContent = p.label;
                btn.title = "Click again to show the reference observations only";
                btn.onclick = () => { wantPage = p.id === page ? null : p.id; loadPage(); };   // 2nd click = none
                platformContainer.appendChild(btn);
            });
        }
        const variableContainer = document.getElementById("variable-container");
        function buildVariableButtons() {
            variableContainer.innerHTML = "";
            if (!page) { variableContainer.innerHTML = `<div class="hint">No platform selected (reference observations only)</div>`; return; }
            if (!info) { variableContainer.innerHTML = `<div class="hint">Loading…</div>`; return; }
            if (!info.variables.length) { variableContainer.innerHTML = `<div class="hint">One field on this page</div>`; return; }
            info.variables.forEach(v => {
                const ok = info.available.includes(v), btn = document.createElement("button");
                btn.className = "btn-toggle" + (v === variable ? " active" : "") + (ok ? "" : " disabled");
                btn.title = ok ? "Click again to hide" : "No data for this cycle";
                btn.textContent = v;
                btn.onclick = () => {
                    variable = v === variable ? null : v;      // 2nd click = none
                    varByPage[page] = variable;
                    fixSingle();
                    buildVariableButtons(); updateExperiments(); sendState();
                };
                variableContainer.appendChild(btn);
            });
        }

        // ======================= EXPERIMENTS =======================
        // multi  (station pages): checkboxes, every experiment with data can be shown
        // single (model-bias pages): one experiment at a time (radio buttons)
        // none   (observation-only pages): greyed out
        const modelsList = document.getElementById("models-list"), modelsSection = document.getElementById("models-section");
        const styleSelect = (i, style) => `<select class="style-select" data-i="${i}">` +
            [["solid", "───"], ["dash", "—"], ["dot", "···"], ["dashdot", "-·-"]]
                .map(([v, t]) => `<option value="${v}" ${style === v ? "selected" : ""}>${t}</option>`).join("") + `</select>`;
        const ROWS = [{ id: "Observation", label: IDX.observation.label, obs: true }];
        IDX.experiments.forEach(x => ROWS.push(x, { id: `${x.id}|bias`, label: "bias", sub: true, title: `${x.label} − observation` }));
        modelsList.innerHTML = ROWS.map((r, i) => `<div class="trace-row${r.sub ? " sub" : ""}" id="row_${i}">
                <label class="trace-left"><input type="checkbox" name="exp-pick" data-i="${i}" checked><span title="${esc(r.title || r.label)}">${esc(r.label)}</span></label>
                ${r.sub ? "" : `<div class="trace-controls">${styleSelect(i, traces[r.id].style)}<input type="color" data-i="${i}" value="${traces[r.id].color}"></div>`}
            </div>`).join("");
        modelsList.addEventListener("change", e => {
            const r = ROWS[+e.target.dataset.i];
            if (!r) return;
            if (e.target.type === "radio") single = r.id;
            else if (e.target.type === "checkbox") traces[r.id].visible = e.target.checked;
            else if (e.target.tagName === "SELECT") traces[r.id].style = e.target.value;
            sendState();
        });
        modelsList.addEventListener("input", e => {
            if (e.target.type !== "color") return;
            traces[ROWS[+e.target.dataset.i].id].color = e.target.value;
            sendState();
        });
        const expAvail = () => (info && variable && info.avail && info.avail[variable]) || [];
        function fixSingle() {             // single mode: keep the chosen experiment if it has data
            if (!info || info.experiments !== "single") return;
            const av = expAvail();
            if (!av.includes(single)) single = av[0] ?? single;
        }
        function updateExperiments() {
            const mode = info ? info.experiments : "none", av = expAvail();
            modelsSection.classList.toggle("off", mode === "none");
            document.getElementById("exp-note").textContent =
                !info ? "" : mode === "none" ? "not used on this page" : mode === "single" ? "one at a time" : "";
            ROWS.forEach((r, i) => {
                const row = document.getElementById(`row_${i}`), box = row.querySelector("input[name=exp-pick]");
                const one = mode === "single";
                row.hidden = !!r.sub && !one;                                  // "bias" only on one-at-a-time pages
                box.type = one ? "radio" : "checkbox";
                const ok = mode !== "none" && !!variable && (one ? av.includes(r.id) : (r.obs || av.includes(r.id)));
                box.disabled = !ok;
                box.checked = ok && (one ? r.id === single : traces[r.id].visible);
                row.classList.toggle("no-data", !ok && mode !== "none");
                row.title = ok || mode === "none" ? "" : "No data for this platform and variable";
                const ctl = row.querySelector(".trace-controls");
                if (ctl) ctl.hidden = one;                                     // no lines on a map
            });
        }

        // ======================= CYCLE PICKER =======================
        // ‹ › step through the cycles; the button opens a calendar of the days with cycles
        // (a day with several cycles shows one button per cycle).
        const dateBtn = document.getElementById("date-btn"), pop = document.getElementById("date-pop");
        const prevBtn = document.getElementById("cyc-prev"), nextBtn = document.getElementById("cyc-next");
        function setCycle(c) { if (c && c !== cycle) { cycle = c; pop.hidden = true; loadPage(); } }
        function updateCycleNav() {
            const i = CYCLES.indexOf(cycle);
            dateBtn.textContent = cycle ? `📅 ${fmtCycle(cycle)} ▾` : "no cycles";
            dateBtn.disabled = !CYCLES.length;
            prevBtn.disabled = i <= 0;
            nextBtn.disabled = i < 0 || i >= CYCLES.length - 1;
        }
        prevBtn.onclick = () => setCycle(CYCLES[CYCLES.indexOf(cycle) - 1]);
        nextBtn.onclick = () => setCycle(CYCLES[CYCLES.indexOf(cycle) + 1]);
        const byDay = {};
        CYCLES.forEach(c => (byDay[isoDay(c)] ||= []).push(c));
        let viewMonth = null, pickDay = null;
        function renderCal() {
            const y = viewMonth.getUTCFullYear(), m = viewMonth.getUTCMonth();
            const days = new Date(Date.UTC(y, m + 1, 0)).getUTCDate(), lead = new Date(Date.UTC(y, m, 1)).getUTCDay();
            const ym = d => d.slice(0, 7), cur = `${y}-${String(m + 1).padStart(2, "0")}`;
            const first = CYCLES.length ? isoDay(CYCLES[0]) : cur, last = CYCLES.length ? isoDay(CYCLES[CYCLES.length - 1]) : cur;
            const monthName = viewMonth.toLocaleString("en-US", { month: "long", year: "numeric", timeZone: "UTC" });
            let h = `<div class="cal-head"><button type="button" data-nav="-1" ${cur <= ym(first) ? "disabled" : ""}>‹</button>` +
                    `<span>${monthName}</span><button type="button" data-nav="1" ${cur >= ym(last) ? "disabled" : ""}>›</button></div>` +
                    `<div class="cal-grid">` + ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"].map(d => `<div class="dow">${d}</div>`).join("") +
                    `<div></div>`.repeat(lead);
            for (let k = 1; k <= days; k++) {
                const d = `${cur}-${String(k).padStart(2, "0")}`;
                const cls = "cal-day" + (byDay[d] ? "" : " off") + (cycle && d === isoDay(cycle) ? " current" : "");
                h += `<div class="${cls}" data-date="${d}" title="${byDay[d] ? byDay[d].map(fmtCycle).join(", ") : "No cycle"}">${k}</div>`;
            }
            h += `</div>`;
            const hrs = byDay[pickDay] || [];
            if (hrs.length > 1) h += `<div class="cal-hours">` + hrs.map(c =>
                `<button type="button" data-cycle="${c}" class="${c === cycle ? "current" : ""}">${c.slice(8)}Z</button>`).join("") + `</div>`;
            h += `<div class="cal-note">${CYCLES.length} cycle(s), ${fmtCycle(CYCLES[0])} – ${fmtCycle(CYCLES[CYCLES.length - 1])}</div>`;
            pop.innerHTML = h;
        }
        dateBtn.addEventListener("click", e => {
            e.stopPropagation();
            if (!CYCLES.length) return;
            pop.hidden = !pop.hidden;
            if (!pop.hidden) {
                viewMonth = new Date(isoDay(cycle) + "T00:00:00Z"); viewMonth.setUTCDate(1);
                pickDay = isoDay(cycle);
                renderCal();
            }
        });
        pop.addEventListener("click", e => {
            e.stopPropagation();
            const nav = e.target.closest("[data-nav]"), hb = e.target.closest("[data-cycle]"), day = e.target.closest(".cal-day");
            if (nav && !nav.disabled) { viewMonth.setUTCMonth(viewMonth.getUTCMonth() + +nav.dataset.nav); renderCal(); }
            else if (hb) setCycle(hb.dataset.cycle);
            else if (day && !day.classList.contains("off")) {
                const cs = byDay[day.dataset.date];
                pickDay = day.dataset.date;
                if (cs.length === 1) setCycle(cs[0]);
                else renderCal();                       // several cycles that day: pick the hour
            }
        });
        document.addEventListener("click", () => { pop.hidden = true; });

        loadPage();
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    write_pages()
    write_index()