#!/usr/bin/env python
# coding: utf-8
import pandas as pd
import xarray as xr
import monetio as mio
from melodies_monet.util import write_util
import kml2geojson
import sys

start_time_reformat=sys.argv[1]
end_time_reformat=sys.argv[2] 
print(sys.argv[1])
print(sys.argv[2])
dates = pd.date_range(start=start_time_reformat,end=end_time_reformat,freq='H')

def get_local_time(ds):
    from numpy import zeros
    if 'utcoffset' in ds.data_vars:
        tim = t.time.copy()
        o = tim.expand_dims({'x':t.x.values}).transpose('time','x')
        on = xr.Dataset({'time_local':o,'utcoffset':t.utcoffset})
        y = on.to_dataframe()
        y['time_local'] = y.time_local + pd.to_timedelta(y.utcoffset, unit='H')
        time_local = y[['time_local']].to_xarray()
        ds = xr.merge([ds,time_local])
    return ds

dk = mio.airnow_kml.add_data(dates,wide_fmt=False,n_procs=12,download=True,daily=True)

