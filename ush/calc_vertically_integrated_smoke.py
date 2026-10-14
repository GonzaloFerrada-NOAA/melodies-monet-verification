import numpy as np
import xarray as xr
import netCDF4
import sys

# Grab the args
datadir = sys.argv[1]
frame   = sys.argv[2]
# Load in the file
filename = datadir + '/dynf0' + frame + '.nc'
fid      = netCDF4.Dataset(filename,'r+')
# Dimeinsions
grid_xt = fid.dimensions['grid_xt']
grid_yt = fid.dimensions['grid_yt']
grid_tt = fid.dimensions['time']
grid_pt = fid.dimensions['pfull']
# Variables
delz    = -1.0 * np.asarray(fid.variables['delz']) / 1.e4
smoke   = np.asarray(fid.variables['smoke'])
tmp     = np.asarray(fid.variables['tmp'])
dpres   = np.asarray(fid.variables['dpres'])
sz           = delz.shape
nlevs        = int(sz[1])
nlats        = int(sz[2])
nlons        = int(sz[3])
# Calculate pressure
pres = np.cumsum(dpres,axis=1)

# Calculate density
dens         = (1./287.)*(pres/tmp)
PM           = smoke*dens
# Create array to hold vertically integrated smoke
smoke_int    = np.zeros((1,nlats,nlons))
for ilat in range(nlats):
  for ilon in range(nlons):
    x = np.dot(PM[0,:,ilat,ilon].ravel(),delz[0,:,ilat,ilon].ravel()) #sum(smoke[0,:,ilat,ilon]*dens[0,:,ilat,ilon]*delz[0,:,ilat,ilon])/1.e4
    if np.isfinite(x):
       smoke_int[0,ilat,ilon] = x

# Write out the file
smoke_int_var = fid.createVariable('smoke_int','float32',(grid_tt,grid_yt,grid_xt))
smoke_int_var[:,:,:] = smoke_int





