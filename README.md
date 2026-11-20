# melodies-monet-verification (offline / retrospective)

Rocoto workflow that verifies **retrospective** air-quality and meteorological model runs (RRFS-SD, cheMPAS-Fire/CMF) against surface and satellite observations using [MELODIES-MONET](https://melodies-monet.readthedocs.io), and builds an interactive verification website for each cycle.

> **Scope**
> - **Retrospective runs only.** There is no real-time / near-real-time support in this branch.
> - **Tested on Ursa only.** It should also work on Hera and Gaea after adapting `melodies-monet-verification.xml` (account, partitions, queue) and the module section of `mm.settings` (see [Porting to Hera/Gaea](#porting-to-heragaea)).

---

## 1. Python environment

```bash
conda create --name melodies-monet -y -c conda-forge python=3.11 \
    melodies-monet pandas pyyaml monet monetio netcdf4 wrf-python typer pooch regionmask pip parallel
conda activate melodies-monet

# pytspack (needed to regrid AERONET data)
pip install git+https://github.com/noaa-oar-arl/pytspack.git@v0.1.3

# pyearthsciences (plotting); --no-deps so it does not touch the conda-installed packages
python -m pip install --no-deps "git+https://github.com/GonzaloFerrada-NOAA/pyearthsciences.git"
```

`parallel` (GNU parallel) is used to extract model frames and download VIIRS files concurrently. CDO, NCO, NCL, wgrib2 and ImageMagick come from the HPC modules loaded in `mm.settings`, not from conda.

## 2. Quick start

```bash
git clone https://github.com/GonzaloFerrada-NOAA/melodies-monet-verification.git
cd melodies-monet-verification
```

1. **Edit `mm.settings`**: set `CONDA_ENV` to your environment and `MELODIES_MONET_DIR` to your clone.
2. **Edit `melodies-monet-verification.xml`**: `ACCOUNT`, partitions, `MELODIES_MONET_DIR`, the `<cycledef>` dates, and the experiment list in the `prep_model` metatask (see below).
3. **Edit `experiments.yaml`**: describe your model run(s).
4. **One-time Cartopy setup** (login node, needs internet; compute nodes have none):
   ```bash
   cd tools && ./setup_offline_data.sh && cd ..
   ```
5. **Run the workflow from the repository top directory** (the task scripts `source mm.settings` by relative path):
   ```bash
   module load rocoto
   rocotorun  -w melodies-monet-verification.xml -d melodies-monet-verification.db
   rocotostat -w melodies-monet-verification.xml -d melodies-monet-verification.db
   ```
   Re-run `rocotorun` (or put it in a cron job) until all tasks are `SUCCEEDED`.

### ⚠️ Keep the XML experiment list in sync with `experiments.yaml`

The `prep_model` metatask must list **every** experiment name (top-level key) defined in `experiments.yaml`, space-separated:

```xml
<metatask name="prep_model">
    <var name="experiment">CMF-netcdf</var>          <!-- e.g. "CMF-netcdf RRFSv1-RT" -->
```

If an experiment is in `experiments.yaml` but not here, its model data is never prepared and it will be skipped in the verification.

## 3. Workflow tasks

| Task (metatask) | Script | What it does |
|---|---|---|
| `prep_model_<exp>` | `scripts/prep_model_data.sh` | Reads raw model output (GRIB2 or netCDF), extracts the requested species frame by frame with a *recipe* (`aux/recipes/`), concatenates to `output/model_data/aqm_<exp>_<CDATE>.nc`, and regrids AOD to the VIIRS/TEMPO grids. |
| `get_obs_<dataset>` | `scripts/get_obs.sh` | Downloads/aggregates observations into `obs/`: AERONET, AirNow, METAR (MADIS), VIIRS, TEMPO, HMS fire points. Runs on the service partition (internet/HPSS access). |
| `satellite_aod_<viirs\|tempo>` | `scripts/satellite_aod.sh` | Gridded model-vs-satellite AOD verification. |
| `melodies-monet_<dataset>` | `scripts/melodies-monet.sh` | Builds a MELODIES-MONET `control.yaml` per species from `aux/yaml_templates/` and runs the pairing + statistics (and plots, if enabled) for AirNow, AERONET and METAR. |
| `website` | `scripts/website.sh` | Builds the interactive dashboard in `output/website_<CDATE>/`. |

Output layout (all under `MELODIES_MONET_DIR`): `obs/`, `output/` (model data, paired files, website), `figures/`, `logs/`, `run/` (scratch, kept only when something fails).

## 4. Configuration files

### `experiments.yaml`

One top-level block per model experiment. The block name is the experiment label used everywhere (XML, file names, plot legends, website). Experiments are drawn in the order they appear.

```yaml
CMF-netcdf:
  raw_output_directory: '/path/to/run/{init?fmt=%Y%m%d}/rrfs_mpassit_{init?fmt=%H}_v2.1.4/det'
  raw_output_filenames: 'mpassit.{valid?fmt=%Y-%m-%d_%H}.00.00.nc'
  raw_output_format: 'netcdf'     # 'grib2' or 'netcdf'
  model_name: 'CMF'               # 'RRFS-SD' or 'CMF' -> selects the recipe
  model_type: 'standard'          # keep 'standard' (data is pre-converted to generic netCDF)
  initialization_hr: 0            # cycle hour of the run
  simulation_length_hr: 24        # forecast length to verify
  output_interval_hr: 1
  relative_day_to_obs: 0          # 0 for retrospective runs
  # recipe: /path/to/custom_recipe.yaml   # optional: overrides the default recipe
  # plot style
  color: 'red'
  marker: '.'
  linestyle: '-'
  markersize: 8
  linewidth: 1.5
  # species mapping   <model variable (from recipe)> : <MELODIES-MONET obs name>
  species:
    aod550: "aod_550nm"
    pm25: "PM2.5"
    t2m: "TEMP"           # AirNow
    t2m: "temperature"    # METAR (same model var can map to several obs names)
    td2m: "dewpoint"
    ws: "windSpeed"
    wd: "windDir"
    pp1h: "precip1Hour"
    vis: "visibility"
```

Notes:
- **File-name templates** use METplus-style tokens: `{init?fmt=%Y%m%d%H}` (cycle time), `{valid?fmt=...}` (init + lead), `{lead?fmt=%3H}` (zero-padded lead hours). Several space-separated templates are allowed when one lead time is split across files (e.g. `'dynf{lead?fmt=%3H}.nc phyf{lead?fmt=%3H}.nc'`).
- **Recipe** = `aux/recipes/<rrfs-sd|cmf>_<raw_output_format>.yaml`. It defines how each model variable on the left side of `species` is computed from the raw output; every species key must exist in the recipe.
- Remove a species from the mapping if your run does not produce it (e.g. `pm10` for smoke-only runs); it is then skipped for that experiment.
- The file is read by a small built-in parser (`tools/yaml_helpers.sh`), not a full YAML library: keep the two-level layout shown above (repeated keys under `species` are intentional and supported).

### `mm.settings`

Shared environment sourced by every task script (do not execute it). It:
- defines the Python environment (`CONDA_ENV`, `PYTHON`, `MM_BIN`, `PARALLEL_BIN`, `SRUN_PYTHON`),
- defines all workflow directories derived from `MELODIES_MONET_DIR` (`obs/`, `output/`, `figures/`, `run/`, `aux/`, ...) and the path to `experiments.yaml`,
- sets the cycle date variables from `CDATE` (passed by Rocoto),
- holds workflow options, e.g. `MELODIES_MONET_MAKE_PLOTS` (`true` also produces the MELODIES-MONET PNG plots — time series, spatial bias/overlay, box, Taylor, scorecard, CSI — which is much slower; `false` only computes the statistics needed by the website),
- loads the HPC modules (CDO, NCO, NCL, wgrib2, ImageMagick) and activates the conda env.

Every variable is set with a default **only if it is not already defined**, so you can override any of them from the environment (e.g. an `<envar>` in the XML) without editing the file.

### `melodies-monet-verification.xml`

Standard Rocoto workflow (`realtime="F"`). Edit the entities at the top (`ACCOUNT`, `QUEUE`, `PARTCOMPUTE`, `PARTSERVICE`, `MELODIES_MONET_DIR`), the `<cycledef>` (one cycle per day: `YYYYMMDDHHMM YYYYMMDDHHMM 24:00:00`), the `prep_model` experiment list, and, if needed, the dataset lists of the `get_obs`, `satellite_aod` and `melodies-monet` metatasks.

## 5. Observation availability

`get_obs.sh` aborts for dates outside these ranges:

| Dataset | Source | Available from |
|---|---|---|
| AERONET (L1.5) | monetio | — |
| AirNow | monetio | — |
| METAR | MADIS archive | 2001-07-01 |
| HMS fire points | NESDIS | 2003-04-01 |
| VIIRS AOD (JRR) | NOAA NODD (Google Cloud) | 2012-01-19 (S-NPP). NOAA-20 from 2018-01-01, NOAA-21 from 2023-09-06; S-NPP ends 2026-11-02. Reprocessed archive through 2022-12-31, operational after. |
| TEMPO AOD | NOAA HPSS (`/BMC/fdr/Permanent`) | **2025-06-01** — no TEMPO verification before this date. Requires `hsi` access. |

If a dataset is not available for your period, remove it from the corresponding `<var name="dataset">` lists in the XML.

## 6. Porting to Hera/Gaea

Only tested on Ursa. To run elsewhere:
1. **XML**: `ACCOUNT`, `QUEUE`, `PARTCOMPUTE`, `PARTSERVICE` (the service partition must have internet; TEMPO also needs HPSS), and `MELODIES_MONET_DIR`.
2. **`mm.settings`**: `CONDA_ENV`, `MELODIES_MONET_DIR`, and the `module load` block (`rdhpcs-conda`, `cdo`, `eccodes/2.34.0`, `nco`, `ncl`, `imagemagick`, `wgrib2` are Ursa module names; adjust to what the machine provides).
3. Re-run `tools/setup_offline_data.sh` for the new clone.

## 7. Website

The dashboard is a static HTML site in `output/website_<CDATE>/`. Its map and charts use JavaScript libraries loaded from public CDNs when the page is opened (Leaflet 1.9.4 and Leaflet.markercluster 1.5.3 from unpkg.com, Plotly.js 2.30.0 from cdn.plot.ly), so viewers need internet access to those CDNs and to the map tile servers (Esri, plus OSM if used). All data is embedded in the generated files. Layout options are in `aux/yaml_templates/html_dashboard.yaml`.
