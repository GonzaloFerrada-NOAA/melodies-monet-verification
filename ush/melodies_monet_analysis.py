#!/usr/bin/env python
# coding: utf-8

# # MELODIES-MONET-analysis dev
# First lets just import the driver to see how it happens and so we can play around with it a little

#This is needed to tell matplotlib to use a non-interactive backend and avoid display errors.
import matplotlib
matplotlib.use('Agg')
from melodies_monet import driver
from melodies_monet.util.region_select import select_region
import pandas as pd
import os
import dask
import sys
import glob
import re

platform    = sys.argv[1] # 'airnow' 'aeronet' 'openaq'
plot_type   = sys.argv[2] if len(sys.argv) > 2 else ''   # plot type from mm_run.sh (e.g. 'ts_site')

print("Hello from mm_analysis")
print(f"    platform    = {platform}")
print(f"    plot_type   = {plot_type}")


def rename_outputs(an, platform):
    """Rename MM outputs to <dataset>_<var>_<start>_<end>_<region>_<what>[_<extra>].<ext>
    (no spaces). MM names are rebuilt from control.yaml, so nothing is parsed by guessing."""
    out = an.output_dir
    s_mm, e_mm = (t.strftime('%Y-%m-%d_%H') for t in (an.start_time, an.end_time))  # MM's date stamps
    s, e = (t.strftime('%Y%m%d%H') for t in (an.start_time, an.end_time))           # our date stamps
    obs_vars = list(dict.fromkeys(v for p in an.paired.values() for v in p.obs_vars))
    slug = lambda x: re.sub(r'[^A-Za-z0-9.\-]+', '_', x.strip()).strip('_').lower()   # 'Hit Rate' -> 'hit_rate'

    def move(src, var, dname, what, extras, ext):
        parts = [platform, var, s, e, dname, what] + [x for x in extras if x]
        dst = os.path.join(out, '_'.join(parts) + ext)
        os.replace(src, dst)                                   # overwrites on reruns
        print('renamed:', os.path.basename(src), '->', os.path.basename(dst))

    # Plot groups: <grp>.<type>.<var>.<start>.<end>.<dtype>.<dname>[.<extra>...].<ext>
    for grp, gd in an.control_dict.get('plots', {}).items():
        for var in obs_vars:
            for dtype, dname in zip(gd['domain_type'], gd['domain_name']):
                prefix = os.path.join(out, f"{grp}.{gd['type']}.{var}.{s_mm}.{e_mm}.{dtype}.{dname}")
                for src in glob.glob(glob.escape(prefix) + '*'):
                    root, ext = os.path.splitext(src)                        # ext from full path: '.png'
                    rest = root[len(prefix):]                                # '', '.Hit Rate', '.aeronet_rrfs-netcdf'
                    extras = [x.removeprefix(f"{platform}_") for x in rest.strip('.').split('.')]  # pair label -> model label
                    what = gd['type']
                    if what == 'csi':                                         # score name replaces 'csi'
                        what, extras = slug(gd.get('score_name', 'csi')), []
                    move(src, var, dname, what, [slug(x) for x in extras], ext)

    # Stats: stats.<var>.<dtype>.<dname>.<start>.<end>.<ext>  (.png/.csv/.txt)
    st = an.control_dict.get('stats', {})
    for var in obs_vars:
        for dtype, dname in zip(st.get('domain_type', []), st.get('domain_name', [])):
            prefix = os.path.join(out, f"stats.{var}.{dtype}.{dname}.{s_mm}.{e_mm}")
            for src in glob.glob(glob.escape(prefix) + '.*'):
                move(src, var, dname, 'stats', [], os.path.splitext(src)[1])


# ### Driver class
# Now lets create an instance of the python driver analysis class. It consists of 4 main parts; model instances, observation instances, a paired instance of both.  This will allow us to move things around the plotting function for spatial and overlays and more complex plots.

an = driver.analysis()

# ### Control File
#
# set the yaml control file and begin by reading the file

an.control = 'control.yaml'        # 'control.yaml.rapchemtest'
an.read_control() # control='control.yaml')

dask.config.set(**{'array.slicing.split_large_chunks': True})

# ### Loading the model data
#
# driver will automatically loop through the "models" found in the model section of the yaml file and create an instance of the driver.model class for each that includes the label, mapping information, and xarray object as well as the filenames.  Note it can open multiple files easily by including hot keys

an.open_models()

# --- Make generic (non-native mod_type) model datasets MONET-ready -------------
# Our converter writes surface-only (time, y, x) files with lat/lon coords.
# MONET pairing expects latitude/longitude coords and a 'z' dimension.
for label, m in an.models.items():
    ds = m.obj
    ren = {k: v for k, v in {"lat": "latitude", "lon": "longitude"}.items() if k in ds.variables}
    if ren:
        ds = ds.rename(ren)                     # lat/lon -> latitude/longitude
    if "z" not in ds.dims:
        ds = ds.expand_dims("z", axis=1)        # (time,y,x) -> (time,z,y,x), size-1 z (lazy, no copy)
    m.obj = ds
    print(f"MONET-ready: {label} dims={dict(ds.sizes)}")

#All the info in the analysis class can also be called.
print(an.start_time)
print(an.end_time)

an.open_obs()

# All the info in the observation class can also be called.
an.obs[platform].obj

#This just pairs the data
print("Pairing...")
an.pair_data()

# Save paired data to netCDF (analysis.save in control.yaml -> output_dir_save).
# Every plot type re-pairs the same data, so save it only once per species: in the stats run.
if plot_type == 'stats':
    print('Saving paired data...')
    an.save_analysis()

# And the stats
if (plot_type == 'stats'):
    print('Doing stats...')
    an.stats()
else:
    # And this generates all the plots.
    print('Plotting...')
    an.plotting()
    
rename_outputs(an, platform)
