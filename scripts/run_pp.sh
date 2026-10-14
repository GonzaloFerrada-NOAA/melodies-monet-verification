#!/bin/bash --login

# Load the python environment
source /scratch1/BMC/wrf-chem/Jordan/miniconda3/bin/activate melodies-monet
# Determine which package to run
var_pkg=${VAR_PKG_2PLOT}

# Loop over the lines in the STMP_LIST
while IFS= read -r exp;
do
echo "Working on experiment in directory ${exp}"
  # Get the current date
  monetdir=${exp}/monet
  mkdir -p ${monetdir}
  cd ${monetdir}
    frame_test=${FRAME}
    if [[ ${frame_test} -lt 10 ]]; then
       frame=00${frame_test}
    else
       frame=0${frame_test}
    fi
    # First check to see if the file is already created
    if [[ ! -r ${monetdir}/phyf_${START_TIME}_${frame}.nc ]]; then
       continue
    else

    if [[ ${var_pkg} == "PBL_DIAG" ]]; then
      python ${SCRIPTS_DIR}/calculate_PBLwind.py ${monetdir}/phyf_${START_TIME}_${frame}.nc
    fi
    fi # file exists check
done  < ${STMP_LIST} # nexp loop

exit 0
