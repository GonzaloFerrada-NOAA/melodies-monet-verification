#!/bin/bash
#==============================================================================
# website.sh -- build the verification website for one cycle (CDATE):
#   ${OUTPUT_DIR}/website/index.html             shared layout (rebuilt each run, lists all cycles)
#   ${OUTPUT_DIR}/website/${CDATE}/<page>.html    one page per dataset (airnow, aeronet, hms,
#                                                 viirs, tempo, viirs-models-bias, ...)
# Inputs (earlier tasks):
#   ${OUTPUT_DIR}/<platform>/<species>_${CDATE}_<platform>_<exp>.nc4   melodies-monet (paired)
#   ${OBS_DIR}/hms_fireYYYYMMDD.txt                                    get_obs hms
#   ${OBS_DIR}/{viirs,tempo}_YYYYMMDD.nc                               get_obs viirs/tempo
#   ${OUTPUT_DIR}/<viirs|tempo>/aod_<ds>_<exp>_${CDATE}.nc             satellite_aod
# Environment (Rocoto): CDATE
#==============================================================================
source mm.settings

YAML="${MELODIES_MONET_DIR}/experiments.yaml"
TEMPLATE="${YAML_DIR}/html_dashboard.yaml"
WEB_DIR="${OUTPUT_DIR}/website_${CDATE}"
RUN_WEB="${RUN_DIR}/website/${CDATE}"
CONFIG="${RUN_WEB}/website.yaml"

isfile "${YAML_EXPERIMENTS}"
isfile "${TEMPLATE}"
ismkdir "${WEB_DIR}/${CDATE}"
ismkdir "${RUN_WEB}"

# Experiments (order = colour order on the website) and the longest simulation (window length)
EXPS=()
MAX_SIM_LENGTH=0
while IFS= read -r exp; do
    EXPS+=( "${exp}" )
    len=$(yaml_get "${YAML_EXPERIMENTS}" "${exp}" simulation_length_hr) \
        || { echo "ERROR: ${exp}: simulation_length_hr missing in ${YAML_EXPERIMENTS}"; exit 1; }
    len=$((10#${len}))                                # force base 10 (e.g. "072")
    if (( len > MAX_SIM_LENGTH )); then MAX_SIM_LENGTH=${len}; fi
done < <(yaml_experiments "${YAML_EXPERIMENTS}")

if (( ${#EXPS[@]} == 0 )); then
    echo "ERROR: no experiments in ${YAML_EXPERIMENTS}"
    exit 1
fi
echo "Experiments: ${EXPS[*]}  (window ${MAX_SIM_LENGTH} h)"

# Fill the template for this cycle
sed -e "s|__WEB_DIR__|${WEB_DIR}|g" \
    -e "s|__OUTPUT_DIR__|${OUTPUT_DIR}|g" \
    -e "s|__OBS_DIR__|${OBS_DIR}|g" \
    -e "s|__WINDOW_HOURS_AFTER__|${MAX_SIM_LENGTH}|g" \
    "${TEMPLATE}" > "${CONFIG}"

# Pages of this cycle + index.html
cd "${RUN_WEB}"
${SRUN_PYTHON} "${USH_DIR}/generate_website.py" "${CONFIG}" \
    --cycle            "${CDATE}"      \
    --experiments      "${EXPS[@]}"    \
    --experiments-yaml "${YAML_EXPERIMENTS}"
checkstatus

isfile "${WEB_DIR}/index.html"
chmod a+rx "${WEB_DIR}" "${WEB_DIR}/${CDATE}"        # files are already a+r

exit 0