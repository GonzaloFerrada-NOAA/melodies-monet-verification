begin

filename=getenv("ncl_file")

f       = addfile(filename,"w")
dpres   = f->dpres
dpres  := dpres(:,::-1,:,:)
tmp     = f->tmp(:,::-1,:,:)
slp     = f->pressfc(:,:,:)

dpres_cumsum    = dim_cumsum_n(dpres,1,0)
pres            = conform(dpres_cumsum,slp,(/0,2,3/)) - dpres_cumsum
dens            = (1/287.) * (pres / tmp)
dens           := dens(:,::-1,:,:)
copy_VarCoords(tmp,dens)

f->dens = dens

end
