#!/bin/bash

source mm.settings
YAML="${MELODIES_MONET_DIR}/experiments.yaml"

# conda activate ${CONDA_ENV}

# Determine the maximum simulation_length_hr from the experiments:
max_sim_len=0
while IFS= read -r exp; do
    len=$(yaml_get "${YAML}" "${exp}" simulation_length_hr) || { echo "ERROR: simulation_length_hr missing for ${exp}" >&2; exit 1; }
    len=$((10#${len})) # force base 10 (e.g. "072")
    if (( len > max_sim_len )); then max_sim_len=${len}; fi
done < <(yaml_experiments "${YAML}")

# Simulation length in whole days, rounded up (24 h -> 1, 25-48 h -> 2, ...)
max_sim_len_days=$(( (max_sim_len + 23) / 24 ))

# Get previous 24 h:
set_prev_dates
# Maximum date
set_date_vars "$(shift_cdate "${CDATE}" +${max_sim_len_days})" _END


# WORKDIR="${OBS_DIR}/${CDATE}"
WORKDIR="${OBS_DIR}"
ismkdir "${WORKDIR}"
cd "${WORKDIR}"

#--------------------------------------------------
# SURFRAD  ----------------------------------------
#--------------------------------------------------
if [[ "${DATASET}" == "surfrad" ]]; then
    baseurl="https://gml.noaa.gov/aftp/data/radiation/surfrad/realtime"
    declare -a sites=("bon" "dra" "fpk" "gwn" "psu" "red" "rut" "slv" "sxf" "tbl" "was")
    for isite in "${sites[@]}"
    do
        filename="${isite}${YY}${JJJ}.dat"
        rm -f ${filename}*
        wget "${baseurl}/${isite}/${filename}"
    done
fi

#--------------------------------------------------
# AERONET  ----------------------------------------
#--------------------------------------------------
if [[ "${DATASET}" == "aeronet" ]]; then
    file="AERONET_L15_${YYYY_PREV}${MM_PREV}${DD_PREV}_${YYYY_END}${MM_END}${DD_END}.nc"
    fout="aeronet.${YYYYMMDD}.nc"
    rm -f ${file}
    ${MM_BIN} get-aeronet -s "${DATEF_PREV}" -e "${DATEF_END}" -n 12 --interp-to 0.55 --debug --verbose --no-compress
    checkstatus
    
    mv ${file} ${fout}
    ${SRUN_PYTHON} ${USH_DIR}/add_region_flags_to_obs.py "${fout}" aeronet
    checkstatus
    
    exit 0
fi

#--------------------------------------------------
# AIRNOW  -----------------------------------------
#--------------------------------------------------
if [[ "${DATASET}" == "airnow" ]]; then
    file="AirNow_${YYYY_PREV}${MM_PREV}${DD_PREV}_${YYYY_END}${MM_END}${DD_END}.nc"
    fout="airnow.${YYYYMMDD}.nc"
    rm -f ${file}
    ${MM_BIN} get-airnow -s "${DATEF_PREV}" -e "${DATEF_END}" -n 12 --debug --verbose --no-compress
    checkstatus
    
    mv ${file} ${fout}
    ${SRUN_PYTHON} ${USH_DIR}/add_region_flags_to_obs.py "${fout}" airnow
    checkstatus
    
    
    # Create baseline removal file:
    # baselinesrcpath="${MELODIES_MONET_DIR}/src/MM_Huiying_BaselineRemoval"
    # locsetpath="${baselinesrcpath}/airnow_sites_epalist_20240716.csv"
    # basefname="${baselinesrcpath}/IODA_hourlyPM25baseline_v2r1.nc"
    # ${SRUN_PYTHON} ${SCRIPTS_DIR}/MONETobs_debkg_v2.py ${WORKDIR}/test5.airnow.${YYYYMMDD}.nc ${locsetpath} ${basefname}

    # if [[ -e cur_aqi_ozone.kml ]]; then
    #     mv cur_aqi_ozone.kml cur_aqi_ozone.${YYYYMMDD}.kml
    #     source ${SCRIPTS_DIR}/mm_split_aqi_files.sh ${YYYYMMDD} ${WORKDIR} ozone
    # else
    #     echo "Did not download ozone AQI data!"
    # fi
    
    # if [[ -e cur_aqi_pm25.kml ]]; then
    #     mv cur_aqi_pm25.kml cur_aqi_pm25.${YYYYMMDD}.kml
    #     source ${SCRIPTS_DIR}/mm_split_aqi_files.sh ${YYYYMMDD} ${WORKDIR} pm25
    # else
    #     echo "Did not download PM.25 AQI data!"
    # fi
    # if [[ -e cur_aqi_combined.kml ]]; then
    #     mv cur_aqi_combined.kml cur_aqi_combined.${YYYYMMDD}.kml
    #     source ${SCRIPTS_DIR}/mm_split_aqi_files.sh ${YYYYMMDD} ${WORKDIR} combined
    # else
    #     echo "Did not download combined AQI data!"
    # fi

    exit 0
fi

#--------------------------------------------------
# ISH-LITE  ---------------------------------------
#--------------------------------------------------
if [[ "${DATASET}" == "ish-lite" ]]; then
    rm -f test5.ish-lite*.nc
    file="ISH-Lite_${YYYY_PREV}${MM_PREV}${DD_PREV}_${YYYY_END}${MM_END}${DD_END}.nc"
    ${MM_BIN} get-ish-lite -s ${DATEF_PREV} -e ${DATEF_END} -n 12 --box 0 -179 90 -50 --debug --verbose --no-compress
    if [[ -e "${file}" ]]; then
        echo "obs file created for ISH-Lite"
        echo "moving ${file} to test5.ish.${YYYYMMDD}.nc"
        mv ${file} test5.ish-lite.${YYYYMMDD}.nc
        cp ${SCRIPTS_DIR}/make_westoreastof97_ish-lite.ncl .
        ncl make_westoreastof97_ish-lite.ncl
    else
        echo "test5 is missing for ISH-Lite!"
        exit 1
    fi
    exit 0
fi

#--------------------------------------------------
# ISH (visibility, cloud ceilin)  -----------------
#--------------------------------------------------
if [[ "${DATASET}" == "ish" ]]; then
    rm -f test5.ish.*.nc
    file="ISH_${YYYY_PREV}${MM_PREV}${DD_PREV}_${YYYY_END}${MM_END}${DD_END}.nc"
    ${MM_BIN} get-ish -s ${DATEF_PREV} -e ${DATEF_END} --box 0 -179 90 -50 -n 12 --no-compress --verbose --debug
    if [[ -e ${file} ]]; then
        echo "obs file created for ISH/ISD"
        echo "moving ${file} to test5.ish.${YYYYMMDD}.nc"
        mv ${file} test5.ish.${YYYYMMDD}.nc
        cp ${SCRIPTS_DIR}/make_westoreastof97_ish.ncl .
        ncl make_westoreastof97_ish.ncl
    else
        echo "test5 is missing for ISH!"
        exit 1
    fi
    exit 0
fi

#--------------------------------------------------
# OPEN-AQ  ----------------------------------------
#--------------------------------------------------
if [[ "${DATASET}" == "openaq" ]]; then
    rm -f test5.openaq.*.nc
    export MM_LOGGING_LEVEL=debug
    # export OPENAQ_API_KEY=dff4aa0d178fdb763b65d72ed81f21da28de5907a9a2f74bd7bbf85d2cbeb98d   # noaa
    export OPENAQ_API_KEY=2ddf96ae51ef9ba9ce53832092be4a171cdc9f9c43877e82ed3a613acf1d44d7    # gmail
    file="OpenAQ_${YYYY_PREV}${MM_PREV}${DD_PREV}_${YYYYMMDD}.nc"
    ${MM_BIN} get-openaq -s ${DATEF_PREV} -e ${DATEF} --params pm25 -c US --low-cost --verbose --debug

    if [[ -e ${file} ]]; then
        echo "obs file created for OPEN-AQ"
        mv ${file} test5.openaq.${YYYYMMDD}.nc
        cp ${SCRIPTS_DIR}/make_westoreastof97_openaq.ncl .
        ncl make_westoreastof97_openaq.ncl
        ncrename -v pm25,PM2.5 test5.openaq.${YYYYMMDD}.nc 
    else
        echo "test5 is missing for openaq"
        exit 1
    fi
    exit 0
fi

#--------------------------------------------------
# HMS FIRE/SMOKE  ---------------------------------
#--------------------------------------------------
if [[ "${DATASET}" == "hms" ]]; then
    
    [[ ${YYYYMMDD} -lt 20030401 ]] && echo "ERROR: No HMS data available before 2003-04-01. Aborting" && exit 1
    
    day=${YYYYMMDD_PREV}
    while [[ ${day} -le ${YYYYMMDD_END} ]]; do
        URL="https://satepsanone.nesdis.noaa.gov/pub/FIRE/web/HMS/Fire_Points/Text/${day:0:4}/${day:4:2}"
        file="hms_fire${day}.txt"
        wget ${URL}/${file}
        isfile ${file}
        day=$(date -u -d "${day} + 1 day" +%Y%m%d)
    done
    echo "done!";exit 0
fi

#--------------------------------------------------
# MODIS  ------------------------------------------
#--------------------------------------------------
if [[ "${DATASET}" == "modis_aqua" ]]; then
    hsi get -N /BMC/fdr/Permanent/${YYYY_PREV}/${MM_PREV}/${DD_PREV}/data/sat/nasa/modis/aerosol/${YYYY_PREV}${MM_PREV}${DD_PREV}0000.zip .
    hsi get -N /BMC/fdr/Permanent/${YYYY}/${MM}/${DD}/data/sat/nasa/modis/aerosol/${YYYYMMDD}0000.zip .
    hsi get -N /BMC/fdr/Permanent/${YYYY_END}/${MM_END}/${DD_END}/data/sat/nasa/modis/aerosol/${YYYY_END}${MM_END}${DD_END}0000.zip .
    hsi get -N /BMC/fdr/Permanent/${YYYY_PREV}/${MM_PREV}/${DD_PREV}/data/sat/nasa/modis/aerosol/${YYYY_PREV}${MM_PREV}${DD_PREV}1200.zip .
    hsi get -N /BMC/fdr/Permanent/${YYYY}/${MM}/${DD}/data/sat/nasa/modis/aerosol/${YYYYMMDD}1200.zip .
    hsi get -N /BMC/fdr/Permanent/${YYYY_END}/${MM_END}/${DD_END}/data/sat/nasa/modis/aerosol/${YYYY_END}${MM_END}${DD_END}1200.zip .
    for file in *.zip
    do
        unzip ${file}
        rm -f ${file}
    done
    # Remove corrupt files
    for file in MY*
    do
        ncdump -hv AOD_550_Dark_Target_Deep_Blue_Combined ${file}
        [[ $? -ne 0 ]] && rm -f ${file}
    done
fi

if [[ "${DATASET}" == "modis_terra" ]]; then
    hsi get -N /BMC/fdr/Permanent/${YYYY_PREV}/${MM_PREV}/${DD_PREV}/data/sat/nasa/modis/aerosol/${YYYY_PREV}${MM_PREV}${DD_PREV}0000.zip .
    hsi get -N /BMC/fdr/Permanent/${YYYY}/${MM}/${DD}/data/sat/nasa/modis/aerosol/${YYYYMMDD}0000.zip .
    hsi get -N /BMC/fdr/Permanent/${YYYY_END}/${MM_END}/${DD_END}/data/sat/nasa/modis/aerosol/${YYYY_END}${MM_END}${DD_END}0000.zip .
    hsi get -N /BMC/fdr/Permanent/${YYYY_PREV}/${MM_PREV}/${DD_PREV}/data/sat/nasa/modis/aerosol/${YYYY_PREV}${MM_PREV}${DD_PREV}1200.zip .
    hsi get -N /BMC/fdr/Permanent/${YYYY}/${MM}/${DD}/data/sat/nasa/modis/aerosol/${YYYYMMDD}1200.zip .
    hsi get -N /BMC/fdr/Permanent/${YYYY_END}/${MM_END}/${DD_END}/data/sat/nasa/modis/aerosol/${YYYY_END}${MM_END}${DD_END}1200.zip .
    for file in *.zip
    do
        unzip ${file}
        rm -f ${file}
    done
    # Remove corrupt files
    for file in MO*
    do
        ncdump -hv AOD_550_Dark_Target_Deep_Blue_Combined ${file}
        [[ $? -ne 0 ]] && rm -f ${file}
    done
fi

#--------------------------------------------------
# TROPOMI  ----------------------------------------
#--------------------------------------------------
if [[ "${DATASET}" == "tropomi" ]]; then
    ln -s ${DIR_TROPOMI}/NO2/* .
    ln -s ${DIR_TROPOMI}/CO/* .
    ln -s ${DIR_TROPOMI}/HCHO/* .
fi

#--------------------------------------------------
# VIIRS  ------------------------------------------
#--------------------------------------------------
if [[ "${DATASET}" == "viirs" ]]; then
    VIIRSDIR="${WORKDIR}/viirs"
    ismkdir "${VIIRSDIR}"
    GCS="https://storage.googleapis.com"
    URLS="${VIIRSDIR}/urls.txt"

    [[ ${YYYYMMDD} -lt 20120119 ]] && echo "ERROR: There's no VIIRS data available before 2012-01-19. Aborting" && exit 1

    # List every object under a bucket prefix as a full URL.
    # The XML API caps a response at 1000 keys, so loop on NextMarker while IsTruncated=true.
    gcs_list() {
        local bucket=$1 prefix=$2 marker="" xml
        while :; do
            xml=$(wget -qO- "${GCS}/${bucket}?prefix=${prefix}&marker=${marker}") || return 1
            grep -o '<Key>[^<]*</Key>' <<< "${xml}" \
                | sed -e 's|</\?Key>||g' -e "s|^|${GCS}/${bucket}/|"          # key -> full URL
            grep -q '<IsTruncated>true</IsTruncated>' <<< "${xml}" || break   # last page
            marker=$(grep -o '<NextMarker>[^<]*</NextMarker>' <<< "${xml}" | sed 's|</\?NextMarker>||g')
        done
    }

    # Build the URL list for all days in [YYYYMMDD, YYYYMMDD_END]
    day=${YYYYMMDD_PREV}
    while [[ ${day} -le ${YYYYMMDD_END} ]]; do
        ymd="${day:0:4}/${day:4:2}/${day:6:2}"

        # Reprocessed archive through 2022-12-31, operational (NRT) afterwards
        [[ ${day} -le 20221231 ]] && sub="reprocessed/VIIRS-JRR-AOD" || sub="VIIRS-JRR-AOD"

        # Satellite availability for this specific day (bucket suffixes: snpp, n20, n21)
        sats=()
        [[ ${day} -le 20261102 ]] && sats+=(snpp)    # S-NPP decommissioned 2026-11-02
        [[ ${day} -ge 20180101 ]] && sats+=(n20)
        [[ ${day} -ge 20230906 ]] && sats+=(n21)

        for sat in "${sats[@]}"; do
            gcs_list "gcp-noaa-nesdis-${sat}" "${sub}/${ymd}/"
        done

        day=$(date -u -d "${day} + 1 day" +%Y%m%d)
    done > "${URLS}"

    echo "Found $(wc -l < "${URLS}") VIIRS files"

    # Parallel download:
    #   -nc        skip files already present, so a rerun only fetches what's missing
    #   --retries  retry a failed wget up to 3 times
    #   --joblog   per-file exit status, so failures are easy to find
    ${PARALLEL_BIN} -j 16 --retries 3 --joblog "${VIIRSDIR}/wget_joblog.txt" \
        wget -q -nc -P "${VIIRSDIR}" {} < "${URLS}"
        
    # Create hourly file:
    RESOLUTION=0.05
    DELTA_HOUR=1
    file="${WORKDIR}/viirs_all_aggregated_${DELTA_HOUR}h_${RESOLUTION//./p}deg_${YYYYMMDD}.nc"
    ${SRUN_PYTHON} "${USH_DIR}/viirs_l2_aggregation_uniform_grid.py" \
        --sat all --start "${DATEF} 00:00:00" --end "${DATEF_END} 00:00:00" \
        --dt "${DELTA_HOUR}" --res "${RESOLUTION}" \
        --viirs-dir "${VIIRSDIR}" \
        --file-out "${file}" \
        --qcall-threshold 0
    checkstatus
    isfile "${file}" "${WORKDIR}/viirs_${YYYYMMDD}.nc"
    ln -sf ${file} 
    exit 0
fi

#--------------------------------------------------
# TEMPO  ------------------------------------------
#--------------------------------------------------
if [[ "${DATASET}" == "tempo" ]]; then
    TEMPODIR="${OBS_DIR}/${CDATE}/tempo"
    ZIPDIR="${TEMPODIR}/zip"
    mkdir -p "${ZIPDIR}"
    HPSS_ROOT="/BMC/fdr/Permanent"

    [[ ${YYYYMMDD} -lt 20250601 ]] && echo "ERROR: No TEMPO data in HPSS before 2025-06-01. Aborting" && exit 1

    # Build "local : remote" pairs for every day in the range
    pairs=()
    day=${YYYYMMDD_PREV}
    while [[ ${day} -le ${YYYYMMDD_END} ]]; do
        zip="${day}0000.zip"
        remote="${HPSS_ROOT}/${day:0:4}/${day:4:2}/${day:6:2}/data/grids/nesdis/tempo_aod/${zip}"
        # Skip zips already retrieved
        [[ -s "${ZIPDIR}/${zip}" ]] || pairs+=("${ZIPDIR}/${zip} : ${remote}")
        day=$(date -u -d "${day} + 1 day" +%Y%m%d)
    done

    # One hsi session, one get command with all files, so HPSS queues all tape recalls together
    if (( ${#pairs[@]} > 0 )); then
        # module load hpss 2>/dev/null
        hsi -q "get ${pairs[*]}" || echo "WARNING: hsi reported errors (missing days?)"
    fi

    # Unzip all retrieved days in parallel:
    #   -o  overwrite without prompting
    #   -j  flatten any internal folders so every granule lands directly in TEMPODIR
    ls "${ZIPDIR}"/*.zip >/dev/null 2>&1 || { echo "ERROR: no TEMPO zips retrieved"; exit 1; }
    
    ${PARALLEL_BIN} -j 8 unzip -q -o -j {} -d "${TEMPODIR}" ::: "${ZIPDIR}"/*.zip

    echo "TEMPO: $(ls "${ZIPDIR}"/*.zip | wc -l) days, $(ls "${TEMPODIR}"/*.nc 2>/dev/null | wc -l) granules"
    
    # Aggregate:
    RESOLUTION=0.1
    DELTA_HOUR=1
    file="${WORKDIR}/tempo_aodalh_aggregated_${DELTA_HOUR}h_${RESOLUTION//./p}deg_${YYYYMMDD}.nc"
    
    ${SRUN_PYTHON} "${USH_DIR}/tempo_aggregation_uniform_grid.py" \
        --start "${DATEF} 00:00:00" --end "${DATEF_END} 00:00:00" \
        --dt "${DELTA_HOUR}" \
        --res "${RESOLUTION}" \
        --tempo-dir "${TEMPODIR}" \
        --file-out "${file}" \
        --dqf-max 0
    
    checkstatus
    isfile "${file}"
    exit 0
    
fi






# if [[ "${DATASET}" == "tempo" ]]; then

# fi

#--------------------------------------------------
# PollenSense  ------------------------------------
#--------------------------------------------------
if [[ "${DATASET}" == "PollenSense" ]]; then
    # Because the PollenSense data changes from obs to forecast, we grab the data
    # two days earlier than other data
    APIKEY=j3xWLmABCaI9cn9PoDyqkXLktPW1mohpDF10v0FBKdxYdsl6Z7kOy8ref9PBn9kD1128e6aisl3TABkoY7Fy3O
    URL="https://model.pollensense.com/api"
    region=NA
    agg=A
    unit=PPM3
    format=tiff
    
    categories=(POL TRE GRA WEE OTHPAR)
    ncat_loop=$((${#categories[@]}-1))
    rm -f ${WORKDIR}/*.tiff

    for icat in $(seq 0 ${ncat_loop})
    do
        category=${categories[${icat}]}

        # GET POLLEN
        # Today is 4/22, the obs directory is 2025042212
        # Grab the interpolated obs for yesterday, 4/21
        # Grab today, tomorrow, and 2-day forecast, 4/22, 4/23, 4/24
        #
        # COMPARE POLLEN
        # Today is 4/22, we are running analysis for 4/20 (4/19-4/21)
        # 1st plot compares yesterday's interpolated obs to the day 1 RAP-Chem forecast
        # - Model file: 2025042006/aqm_RAP-Chem_2025042006.nc (Times = 0-23)
        # - Obs   file: 2025042112/POL_20250420.nc
        #
        # 2nd plot compares today's PS forecast to day 1 forecast of the following day
        # - Model file: 2025042106/aqm_RAP-Chem_2025042106.nc (Times = 0-23 of full file)
        # - Obs (today fcst)   file: 2025042112/POL_20250421.nc
        #
        # 3rd plot compares tomorrow's forecast to the day2 forecast of previous day
        # - Model file: Model file: 2025042006/aqm_RAP-Chem_2025042106.nc (Times = 24-47)
        # - Obs (tomorrow fcst)file: 2025041912/POL_20250420.nc
        
        # Yesterday Obs
        curl -X 'GET' "${URL}/region?region=${region}&starting=${DATEF_PREV}&category=${category}&agg=${agg}&unit=${unit}&format=${format}&key=${APIKEY}" -H "accept: image/${format}" >> ${category}_${YYYYMMDD_PREV}.tiff
        python ${SCRIPTS_DIR}/convert_tiff_to_netcdf.py ${WORKDIR} ${category} ${YYYYMMDD_PREV}

        # Today Forecast
        curl -X 'GET' "${URL}/region?region=${region}&starting=${DATEF}&category=${category}&agg=${agg}&unit=${unit}&format=${format}&key=${APIKEY}" -H "accept: image/${format}" >> ${category}_${YYYYMMDD}.tiff
        python ${SCRIPTS_DIR}/convert_tiff_to_netcdf.py ${WORKDIR} ${category} ${YYYYMMDD}

        # Tomorrow forecast
        curl -X 'GET' "${URL}/region?region=${region}&starting=${DATEF_END}&category=${category}&agg=${agg}&unit=${unit}&format=${format}&key=${APIKEY}" -H "accept: image/${format}" >> ${category}_${YYYYMMDD_END}.tiff
        python ${SCRIPTS_DIR}/convert_tiff_to_netcdf.py ${WORKDIR} ${category} ${YYYYMMDD_END}

        # Two-day forecast
        curl -X 'GET' "${URL}/region?region=${region}&starting=${DATEF_END2}&category=${category}&agg=${agg}&unit=${unit}&format=${format}&key=${APIKEY}" -H "accept: image/${format}" >> ${category}_${YYYYMMDD_END2}.tiff
        python ${SCRIPTS_DIR}/convert_tiff_to_netcdf.py ${WORKDIR} ${category} ${YYYYMMDD_END2}

    done
fi

#--------------------------------------------------
# Google Pollen  ----------------------------------
#--------------------------------------------------
if [[ "${DATASET}" == "Google_Pollen" ]]; then
    scp -o 'ProxyJump jschnell@gate.al.noaa.gov' jschnell@meiyu:/wrk/csd4/rahmadov/RAP-Chem/google_pollen/${YYYYMMDD}00/TRE_google_${YYYYMMDD}.png .
    scp -o 'ProxyJump jschnell@gate.al.noaa.gov' jschnell@meiyu:/wrk/csd4/rahmadov/RAP-Chem/google_pollen/${YYYYMMDD}00/GRA_google_${YYYYMMDD}.png .
    scp -o 'ProxyJump jschnell@gate.al.noaa.gov' jschnell@meiyu:/wrk/csd4/rahmadov/RAP-Chem/google_pollen/${YYYYMMDD}00/WEE_google_${YYYYMMDD}.png .
    exit 0
fi

#--------------------------------------------------
# Pollen.com  -------------------------------------
#--------------------------------------------------
if [[ "${DATASET}" == "pollen_dot_com" ]]; then
    wget https://pollenapps.com/DF/pollen/map/pollenmapvideo.mp4
    python ${SCRIPTS_DIR}/convert_mp4_to_images.py ${WORKDIR}
fi

#--------------------------------------------------
# Pollen  -----------------------------------------
#--------------------------------------------------
if [[ "${DATASET}" == "NAB_POLLEN" ]]; then
    scp -o 'ProxyJump jschnell@gate.al.noaa.gov' jschnell@meiyu:/wrk/csd4/rahmadov/RAP-Chem/nab_pollen/${YYYYMMDD}00/scraped_pollen_data_${YYYYMMDD}.csv .
    cp scraped_pollen_data_${YYYYMMDD}.csv ../scraped_pollen/scraped_pollen_data_${YYYYMMDD}.csv
fi

exit 0
