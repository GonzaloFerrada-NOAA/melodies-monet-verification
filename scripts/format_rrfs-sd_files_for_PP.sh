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
    frame_test=${FRAME}
    if [[ ${frame_test} -lt 10 ]]; then
       frame=00${frame_test}
    else
       frame=0${frame_test}
    fi
    # First check to see if the file is already created
    if [[ -r ${monetdir}/phyf_${START_TIME}_${frame}.nc ]]; then
       continue
    else
       # Grab what is needed out of the phy files (these should all be there)
       ncks -O -v hgtsfc,ugrd,vgrd,delz,lon,lat,time,pfull,phalf,grid_xt,grid_yt,dpres,pressfc,tmp dynf${frame}.nc  temp_phyf${frame}.nc 
       ncks -A -v ebu_smoke,min_fplume,max_fplume,hwp,hwp_ave,frp_output,hpbl phyf${frame}.nc temp_phyf${frame}.nc
       # Do these seperately since they may not be in the output (extended diags)
       ncks -A -v fire_type phyf${frame}.nc temp_phyf${frame}.nc
       ncks -A -v fire_end_hr phyf${frame}.nc temp_phyf${frame}.nc
       ncks -A -v fhist phyf${frame}.nc temp_phyf${frame}.nc
       ncks -A -v lu_qfire phyf${frame}.nc temp_phyf${frame}.nc
       ncks -A -v lu_nofire phyf${frame}.nc temp_phyf${frame}.nc
       ncks -A -v peak_hr phyf${frame}.nc temp_phyf${frame}.nc
       ncks -A -v coef_bb_dc phyf${frame}.nc temp_phyf${frame}.nc
       ncks -O -d pfull,63,63 -d phalf,64,64 temp_phyf${frame}.nc temp_phyf${frame}.nc
       mv temp_phyf${frame}.nc ${monetdir}/phyf_${START_TIME}${cycleHH}_${frame}.nc
    fi
  #done # nfiles loop
done < ${STMP_LIST} # nexp loop

exit 0

