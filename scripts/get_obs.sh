#!/bin/bash

source mm.settings

# Determine the maximum simulation_length_hr from the experiments:
max_sim_len=0
while IFS= read -r exp; do
    len=$(yaml_get "${YAML_EXPERIMENTS}" "${exp}" simulation_length_hr) || { echo "ERROR: simulation_length_hr missing for ${exp}" >&2; exit 1; }
    len=$((10#${len})) # force base 10 (e.g. "072")
    if (( len > max_sim_len )); then max_sim_len=${len}; fi
done < <(yaml_experiments "${YAML_EXPERIMENTS}")

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

# Logic for each DATASET:
case "${DATASET}" in
aeronet)
    file="AERONET_L15_${YYYYMMDD}_${YYYYMMDD_END}.nc"
    fout="aeronet.${YYYYMMDD}.nc"
    rm -f ${file}
    
    ${MM_BIN} get-aeronet -s "${DATEF}" -e "${DATEF_END}" -n 12 --interp-to 0.55 --debug --verbose --no-compress
    checkstatus
    
    mv ${file} ${fout}
    ${SRUN_PYTHON} ${USH_DIR}/add_region_flags_to_obs.py "${fout}" aeronet
    checkstatus
    ;;
    
airnow)
    file="AirNow_${YYYYMMDD}_${YYYYMMDD_END}.nc"
    fout="airnow.${YYYYMMDD}.nc"
    rm -f ${file}
    ${MM_BIN} get-airnow -s "${DATEF}" -e "${DATEF_END}" -n 12 --debug --verbose --no-compress
    checkstatus
    
    mv ${file} ${fout}
    ${SRUN_PYTHON} ${USH_DIR}/add_region_flags_to_obs.py "${fout}" airnow
    checkstatus
    ;;
        
viirs)
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
    isfile "${file}" 
    ln -sf ${file} "${WORKDIR}/viirs_${YYYYMMDD}.nc"
    ;;
    
tempo)
    TEMPODIR="${OBS_DIR}/tempo"
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
    ln -sf ${file} "${WORKDIR}/tempo_${YYYYMMDD}.nc"
    ;;
    
hms)
    [[ ${YYYYMMDD} -lt 20030401 ]] && echo "ERROR: No HMS data available before 2003-04-01. Aborting" && exit 1
    
    day=${YYYYMMDD}
    while [[ ${day} -le ${YYYYMMDD_END} ]]; do
        URL="https://satepsanone.nesdis.noaa.gov/pub/FIRE/web/HMS/Fire_Points/Text/${day:0:4}/${day:4:2}"
        file="hms_fire${day}.txt"
        wget ${URL}/${file}
        isfile ${file}
        day=$(date -u -d "${day} + 1 day" +%Y%m%d)
    done
    ;;
    
metar)
    BASE_URL="https://madis-data.ncep.noaa.gov/madisPublic1/data/archive"
    [[ ${YYYYMMDD} -lt 20010701 ]] && echo "ERROR: No METAR data available before 2001-07-01. Aborting" && exit 1
    
    mkdir -p metar; cd metar
    vars="latitude,longitude,temperature,dewpoint,precip1Hour,visibility,windSpeed,windDir,windGust,stationName"
    
    day=${YYYYMMDD}
    while [[ ${day} -le ${YYYYMMDD_END} ]]; do
        ymd="${day:0:4}/${day:4:2}/${day:6:2}"
        wget -r -np -nd -A "*.gz" "${BASE_URL}/${ymd}/point/metar/netcdf/"
        day=$(date -u -d "${day} + 1 day" +%Y%m%d)
    done
    
    gunzip *.gz # decompress
    
    # extract only variables we need:
    # for f in *; do
    #     ncks -O -h -v ${vars} ${f} ${f}.nc
    #     checkstatus; rm ${f}
    # done
    cd ..
    fout="${WORKDIR}/metar.${YYYYMMDD}.nc"
    
    ${SRUN_PYTHON} ${USH_DIR}/metar2mm.py "${WORKDIR}/metar" \
        "${YYYYMMDD}00" "${YYYYMMDD_END}00" "${fout}"
    
    rm -rf metar/
    ;;
    
*)
    echo "ERROR: dataset $DATASET is not implemented or deprecated!"
    exit 1
    ;;
esac

# Options no longer available/deprecated:
# openaq)
#     rm -f test5.openaq.*.nc
#     export MM_LOGGING_LEVEL=debug
#     # export OPENAQ_API_KEY=dff4aa0d178fdb763b65d72ed81f21da28de5907a9a2f74bd7bbf85d2cbeb98d   # noaa
#     export OPENAQ_API_KEY=2ddf96ae51ef9ba9ce53832092be4a171cdc9f9c43877e82ed3a613acf1d44d7    # gmail
#     file="OpenAQ_${YYYY_PREV}${MM_PREV}${DD_PREV}_${YYYYMMDD}.nc"
#     ${MM_BIN} get-openaq -s ${DATEF_PREV} -e ${DATEF} --params pm25 -c US --low-cost --verbose --debug

#     if [[ -e ${file} ]]; then
#         echo "obs file created for OPEN-AQ"
#         mv ${file} test5.openaq.${YYYYMMDD}.nc
#         cp ${SCRIPTS_DIR}/make_westoreastof97_openaq.ncl .
#         ncl make_westoreastof97_openaq.ncl
#         ncrename -v pm25,PM2.5 test5.openaq.${YYYYMMDD}.nc 
#     else
#         echo "test5 is missing for openaq"
#         exit 1
#     fi
#     ;;
    
# surfrad)
#     baseurl="https://gml.noaa.gov/aftp/data/radiation/surfrad/realtime"
#     declare -a sites=("bon" "dra" "fpk" "gwn" "psu" "red" "rut" "slv" "sxf" "tbl" "was")
#     for isite in "${sites[@]}"
#     do
#         filename="${isite}${YY}${JJJ}.dat"
#         rm -f ${filename}*
#         wget "${baseurl}/${isite}/${filename}"
#     done
#     ;;
    
# ish-lite)
#     rm -f test5.ish-lite*.nc
#     file="ISH-Lite_${YYYY_PREV}${MM_PREV}${DD_PREV}_${YYYY_END}${MM_END}${DD_END}.nc"
#     ${MM_BIN} get-ish-lite -s ${DATEF_PREV} -e ${DATEF_END} -n 12 --box 0 -179 90 -50 --debug --verbose --no-compress
#     if [[ -e "${file}" ]]; then
#         echo "obs file created for ISH-Lite"
#         echo "moving ${file} to test5.ish.${YYYYMMDD}.nc"
#         mv ${file} test5.ish-lite.${YYYYMMDD}.nc
#         cp ${SCRIPTS_DIR}/make_westoreastof97_ish-lite.ncl .
#         ncl make_westoreastof97_ish-lite.ncl
#     else
#         echo "test5 is missing for ISH-Lite!"
#         exit 1
#     fi
#     ;;

# ish)
#     rm -f test5.ish.*.nc
#     file="ISH_${YYYY_PREV}${MM_PREV}${DD_PREV}_${YYYY_END}${MM_END}${DD_END}.nc"
#     ${MM_BIN} get-ish -s ${DATEF_PREV} -e ${DATEF_END} --box 0 -179 90 -50 -n 12 --no-compress --verbose --debug
#     if [[ -e ${file} ]]; then
#         echo "obs file created for ISH/ISD"
#         echo "moving ${file} to test5.ish.${YYYYMMDD}.nc"
#         mv ${file} test5.ish.${YYYYMMDD}.nc
#         cp ${SCRIPTS_DIR}/make_westoreastof97_ish.ncl .
#         ncl make_westoreastof97_ish.ncl
#     else
#         echo "test5 is missing for ISH!"
#         exit 1
#     fi
#     ;;


exit 0
