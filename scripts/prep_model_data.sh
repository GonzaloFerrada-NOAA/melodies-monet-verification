#!/bin/bash -l
module load nco
module load wgrib2

export PS4='+ [$(date "+%Y-%m-%d %H:%M:%S")] ${LINENO}: '
set -x

# Test:
# MODEL_NAME="RRFS-SD"
# PATH_IN="/scratch3/BMC/acomp/Gonzalo.Ferrada/tmp/rrfs_a"
# START_TIME="20260525"
# cycleHH="00"
# FCST_LENGTH="84"
# FCST_DT="3600"
# SCRIPTS_DIR="/scratch3/BMC/acomp/Gonzalo.Ferrada/verif/melodies-monet-verification"
# PATH_OUT="/scratch3/BMC/acomp/Gonzalo.Ferrada/verif/melodies-monet-verification/model_output"

# Prepare directories:
path_in="${PATH_IN}"
path_out="${PATH_OUT}/${MODEL_NAME}/${START_TIME}${cycleHH}"
mkdir -p ${path_out}
rm -rf ${path_out}/*

# Get model-dependent specs:
shopt -s nocasematch # case insensitive
case "${MODEL_NAME}" in
    rrfs-sd|rrfs-smoke)
        script="${SCRIPTS_DIR}/prep_rrfs-sd.sh"
        ;;
    mpas-aerosols|chempas-fire|cmf|fire-weather-nest|fwx-nest|fwxnest)
        script="${SCRIPTS_DIR}/prep_mpas-aerosols.sh"
        ;;
    hrrr-smoke)
        script="${SCRIPTS_DIR}/prep_hrrr-smoke.sh"
        ;;
    gefs-aerosols|gcafs|ufs-aerosols)
        script="${SCRIPTS_DIR}/prep_gefs-aerosols.sh"
        ;;
    rap-smoke|rap-chem)
        script="${SCRIPTS_DIR}/prep_rap-chem.sh"
        ;;
    *)
        echo "ERROR: MODEL_NAME='${MODEL_NAME}' not supported. Exiting."
        exit 1
        ;;
esac
shopt -u nocasematch # back to be case sensitive

exit 

# Construct list of frames:
dt_hr=$(( FCST_DT / 3600 ))

for i in $(seq 0 "${dt_hr}" "${FCST_LENGTH}"); do
    
    HHH=$(printf "%03d" "$i")
    # FILEOUT=${path_out}/aqm_${MODEL_NAME}_${START_TIME}${cycleHH}_f${HHH}.nc
    FILEOUT=${path_out}/frame_${HHH}_aqm_${MODEL_NAME}_${START_TIME}${cycleHH}.nc
    rm -f ${FILEOUT}
    
    # Skip header, then read comma-separated columns
    tail -n +2 "${csv_file}" | while IFS=',' read -r \
        VAROUT VAR_MATCH VAR_INTERMEDIATE MULTIPLY_BY_DENSITY SFC_ONLY EXISTS_AT_INIT FILE_PREFIX FILE_SUFFIX
    do
    
        if [[ "${EXISTS_AT_INIT}" == "false" ]] && [[ ${i} -eq 0 ]]; then
            echo "Skipping ${VAROUT} for frame ${HHH} since it does not exist at initialization time."
            continue
        fi
        
        echo "VAROUT=${VAROUT}"

        FILEIN=${path_in}/${FILE_PREFIX}.f${HHH}.${FILE_SUFFIX}
        FILETMP=${path_out}/tmp_${VAROUT}_f${HHH}.nc
        
        # Check if input file exists:
        if [[ ! -f "${FILEIN}" ]]; then
            echo "Input file ${FILEIN} does not exist or is empty."
            exit 1
        fi
        
        if [[ "${SFC_ONLY}" == "true" ]]; then
            OPTS="-nc_nlev 1"
        else
            OPTS=""
        fi
        
        wgrib2 ${OPTS} -set_ext_name 1 -match "${VAR_MATCH}" -set center 7 ${FILEIN} -netcdf ${FILETMP}
        
        # Check if wgrib2 succeeded and created a non-empty file:
        if [[ ! -s "${FILETMP}" ]]; then
            echo "ERROR: wgrib2 failed or created empty file for ${VAROUT}"
            exit 1
        else
            # Rename variable to VAROUT:
            if [[ "${VAROUT}" != "${VAR_INTERMEDIATE}" ]]; then
                ncrename -h -O -v "${VAR_INTERMEDIATE}","${VAROUT}" ${FILETMP}
            fi
            # Append to output:
            ncks -h -A -v "${VAROUT}" "${FILETMP}" "${FILEOUT}"
            rm -f "${FILETMP}"
        fi
        
    done
    
    # Calculate wind speed and direction:    
    # ncap2 -h -O -s '
    # WIND_10maboveground=float((UGRD_10maboveground^2.0 + VGRD_10maboveground^2.0)^(0.5));
    # WDIR_10maboveground=float(180.+(180./3.14159)*atan2(UGRD_10maboveground,VGRD_10maboveground));

    # pm25=float(dust_fine+smoke);
    # pm10=float(pm25+dust_coarse);

    # VIS_surface=float(0.000621371*VIS_surface);
    # where(VIS_surface>10.0f) VIS_surface=10.0f;

    # TMP_2maboveground=float(TMP_2maboveground-273.15);
    # DPT_2maboveground=float(DPT_2maboveground-273.15);

    # lat=latitude;
    # lon=longitude;
    # ' "${FILEOUT}" "${FILEOUT}"
    
    
    ncap2 -h -O -s '
    lat=latitude;
    lon=longitude;
    ' "${FILEOUT}" "${FILEOUT}"
    
    # Remove underisable variables:
    # ncks -h -O -x -v hlevel "${FILEOUT}" "${FILEOUT}"
    # ncks -h -O -x -v UGRD_10maboveground,VGRD_10maboveground "${FILEOUT}" "${FILEOUT}"
    
    # compress:
    ncks -O -4 -L 1 "${FILEOUT}" "${FILEOUT}"
    
done

echo "prep_model_data.sh completed successfully for cycle ${START_TIME}${cycleHH}"
exit 0
