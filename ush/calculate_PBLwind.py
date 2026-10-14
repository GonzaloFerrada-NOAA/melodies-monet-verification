#!/usr/bin/env python
# coding: utf-8

import numpy as np
import pandas as pd
import xarray as xr
import sys
import glob
import os.path
import subprocess
import netCDF4
import math
from scipy import stats

# Get the filename and read it in as writable
filename = sys.argv[1]
#filename = 'test_rrfs-sd_file.nc'
fid = netCDF4.Dataset(filename,'r+')

variables = list(dset.variables)

# Grab the necessary variables
grid_xt = fid.dimensions['grid_xt']
grid_yt = fid.dimensions['grid_yt']
grid_tt = fid.dimensions['time']
grid_pt = fid.dimensions['pfull']
hsfc = np.asarray(fid.variables['hgtsfc'])
if 'hpbl_thetav' in variables:
  hpbl = np.asarray(fid.variables['hpbl_thetav'])
else:
  hpbl = np.asarray(fid.variables['hpbl'])   
ugrd = np.asarray(fid.variables['ugrd'])
vgrd = np.asarray(fid.variables['vgrd'])
temp = np.asarray(fid.variables['tmp'])
delz = np.asarray(fid.variables['delz'])
frp  = np.asarray(fid.variables['frp_output'])
sz   = delz.shape              # Time x Level x lat x lon
# Flip the 3D variables such that pfull[0] = surface
ugrd = ugrd[:,::-1,:,:]
vgrd = vgrd[:,::-1,:,:]
temp = temp[:,::-1,:,:]
delz = delz[:,::-1,:,:]

# Calculate wind speed (direction calculated element-wise below)
#windspd = ( ugrd*ugrd + vgrd*vgrd )**(0.5)
# Calculate successive temperature differences
delt = [temp[:,i+1,:,:] - temp[:,i,:,:] for i in range(sz[1]-1)]
delt = np.array(delt)
delt = np.transpose(delt,(1,0,2,3))

# Calculate total height [AGL, m]
Z    = -1. * np.cumsum(delz,axis=1) # + hsfc # don't add surface height, pbl is relative to ground

# Allocate final arrays
wspd_pbl = np.empty((1,sz[2],sz[3]))
wdir_pbl = np.empty((1,sz[2],sz[3]))
dtdz_pbl = np.empty((1,sz[2],sz[3]))
plume_windeff_on = np.empty((1,sz[2],sz[3])).astype(int)
wspd_pbl[:] = np.nan
wdir_pbl[:] = np.nan
dtdz_pbl[:] = np.nan
plume_windeff_on[:] = np.nan
for ilat in range(sz[2]):
   for ilon in range(sz[3]):
      # Determine the first index where the height (Z) is greather than PBLH
      ix_all = np.argwhere(Z[0,:,ilat,ilon]>hpbl[0,ilat,ilon]).astype(int)
      # Check to make sure we have a value (fill values outside projection)
      if ix_all.size != 0:
         # Make sure it is an integer, calculate the average
         ix = int(ix_all[0])
         u = ugrd[:,0:ix,ilat,ilon]
         v = vgrd[:,0:ix,ilat,ilon]
         windspd = ( u*u + v*v ) ** (0.5)
         wspd_pbl[0,ilat,ilon]  = np.average(windspd[:,0:ix])
         # Create a temporary array to hold the values for wdir
         wdir = np.zeros((ix))
         # perform atan2 calculation one value at a time
         for iix in range(ix):
            wdir[iix] = 180.+(180./3.14159)*math.atan2(ugrd[0,iix,ilat,ilon],vgrd[0,iix,ilat,ilon])
         # Calculate and store the angular mean
         wdir_pbl[0,ilat,ilon]  = stats.circmean(wdir,high=360)
         del wdir
         dtdz_pbl[:,ilat,ilon]  = delt[:,ix,ilat,ilon] / delz[:,ix,ilat,ilon]
         if frp[0,ilat,ilon] > 10.:
            plume_windeff_on[0,ilat,ilon] = 0
         if hpbl[0,ilat,ilon]) > 2000. and wspd_pbl[0,ilat,ilon] > 5.0 and frp[0,ilat,ilon] > 10. and frp[0,ilat,ilon] < 1000.:
            plume_windeff_on[0,ilat,ilon] = 1

# Create the variables in the file
wspd_pbl_var = fid.createVariable('wspd_pbl','float32',(grid_tt, grid_yt, grid_xt))
wdir_pbl_var = fid.createVariable('wdir_pbl','float32',(grid_tt, grid_yt, grid_xt))
dtdz_pbl_var = fid.createVariable('dtdz_pbl','float32',(grid_tt, grid_yt, grid_xt))
plume_windeff_on_var = fid.createVariable('plume_windeff_on','i4',(grid_tt, grid_yt, grid_xt))
# Write the variables
wspd_pbl_var[:,:,:] = wspd_pbl
wdir_pbl_var[:,:,:] = wdir_pbl
dtdz_pbl_var[:,:,:] = dtdz_pbl
plume_windeff_on_var[:,:,:] = plume_windeff_on
 




