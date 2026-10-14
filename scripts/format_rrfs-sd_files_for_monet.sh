#!/bin/bash -l

module load ncl
module load nco

set -x

# Loop over the lines in the STMP_LIST
while IFS= read -r exp;
do
echo "Working on experiment in directory ${exp}"
  # Get the current date
  cycledir=${exp}/${START_TIME}${cycleHH}/fcst_fv3lam
  monetdir=${exp}/monet
  mkdir -p ${monetdir}
  cd ${cycledir}
  # Get the number of files (minus 1 for the loop)
#  nfiles=$((`ls dynf* | wc -l` - 1))
#  for f in $(seq 0 ${nfiles}); do
#    if [[ $f -lt 10 ]]; then
#      frame=00$f
#    else
    frame_test=${FRAME}
    if [[ ${frame_test} -lt 10 ]]; then
       frame=00${frame_test}
    else
       frame=0${frame_test}
    fi
    
#    fi
    # First check to see if the file is already created
    if [[ -r ${monetdir}/dynf_${START_TIME}_${frame}.nc ]]; then
       continue
    else
       # Grab what is needed out of the dyn files
       ncks -O -v coarsepm,hgtsfc,smoke,dust,delz,lon,lat,time,pfull,phalf,grid_xt,grid_yt,dpres,pressfc,tmp dynf${frame}.nc  temp_dynf${frame}.nc
       # Calculate air density
       export ncl_file=temp_dynf${frame}.nc
       ncl /home/Jordan.Schnell/scripts/rrfs-sd_verification/calculate_rrfs_airdens.ncl
       # Append AOD
       ncks -A -v tprcp,ext550,tmp2m,vgrd10m,ugrd10m phyf${frame}.nc temp_dynf${frame}.nc
       ncap2 -O -s 'AOD550=ext550.total($pfull)' temp_dynf${frame}.nc temp_dynf${frame}.nc
       ncks -O -d pfull,63,63 -d phalf,64,64 temp_dynf${frame}.nc temp_dynf${frame}.nc
       ncap2 -O -s 'wind10m=(vgrd10m^2.0 + ugrd10m^2.0)^0.5' temp_dynf${frame}.nc temp_dynf${frame}.nc
       ncap2 -O -s 'WDIR_10maboveground=180.+(180./3.14159)*atan2(ugrd10m,vgrd10m)' temp_dynf${frame}.nc temp_dynf${frame}.nc
       # Change to mixing ratio
       ncap2 -O -s 'smoke=smoke*dens' -s 'dust=dust*dens' -s 'coarsepm=coarsepm*dens' temp_dynf${frame}.nc temp_dynf${frame}.nc
       ncap2 -O -s 'pm25=smoke+dust' temp_dynf${frame}.nc temp_dynf${frame}.nc
       ncap2 -O -s 'pm10=pm25+coarsepm' temp_dynf${frame}.nc temp_dynf${frame}.nc
       ncap2 -O -s 'tprcp=1.e5*tprcp' temp_dynf${frame}.nc temp_dynf${frame}.nc
       ncap2 -O -s 'tmp2m=tmp2m-273.15' temp_dynf${frame}.nc temp_dynf${frame}.nc
       ncrename -v tprcp,precip_1hr temp_dynf${frame}.nc
       ncks -O -3 temp_dynf${frame}.nc temp_dynf${frame}.nc
       # Move the file to the monet directory
       mv temp_dynf${frame}.nc ${monetdir}/dynf_${START_TIME}${cycleHH}_${frame}.nc
    fi
#  done # nfiles loop
done < ${STMP_LIST} # nexp loop

exit 0

