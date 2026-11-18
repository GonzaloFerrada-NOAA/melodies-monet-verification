#!/bin/bash

source mm.settings
YAML="${MELODIES_MONET_DIR}/experiments.yaml"

WORKDIR="${OUTPUT_DIR}/${DATASET}"
ismkdir ${WORKDIR}
ismkdir ${FIG_DIR}/${DATASET}

FILE_OBS="${OBS_DIR}/${DATASET}_${YYYYMMDD}.nc"
LAMBERT_CSV_FILE="${AUX_DIR}/lambert_map_settings.csv"

DATE_START="${CYCLE_DATE}"
MAX_SIM_LENGTH=0

EXPERIMENTS=( )
FILES_EXPERIMENTS=( )

while IFS= read -r EXP; do
    
    FILE_AOD_MODEL="${OUTPUT_DIR}/model_data/aod_${DATASET}_regridded_${EXP}_${CDATE}.nc"
    FILE_OUT="${OUTPUT_DIR}/${DATASET}/aod_${DATASET}_${EXP}_${CDATE}.nc"
    
    simulation_length_hr=$(yaml_get "${YAML}" "${EXP}" simulation_length_hr)
    [[ ${simulation_length_hr} -gt ${MAX_SIM_LENGTH} ]] && MAX_SIM_LENGTH=${simulation_length_hr}
    DATE_END=$( date -ud "${CYCLE_DATE} ${simulation_length_hr} hours" "+%F %T" )
    
    # Do the comparison:
    ${SRUN_PYTHON} ${USH_DIR}/satellite_aod_verification.py --save-netcdf \
        --obs-name      "${DATASET}"        \
        --date-start    "${DATE_START}"     \
        --date-end      "${DATE_END}"       \
        --model-name    "${EXP}"            \
        --file-model    "${FILE_AOD_MODEL}" \
        --obs-var       "aod550"            \
        --obs-file      "${FILE_OBS}"       \
        --file-out      "${FILE_OUT}"
    
    checkstatus
    
    EXPERIMENTS+=( "${EXP}" )
    FILES_EXPERIMENTS+=( "${FILE_OUT}" )
    
    
done < <(yaml_experiments "${YAML}")

# Ready to plot
DATE_END=$( date -ud "${CYCLE_DATE} ${MAX_SIM_LENGTH} hours" "+%F %T" )
${SRUN_PYTHON} ${USH_DIR}/satellite_aod_verification.py --make-figure \
    --obs-name      "${DATASET}"        \
    --date-start    "${DATE_START}"     \
    --date-end      "${DATE_END}"       \
    --model-names   "${EXPERIMENTS[@]}" \
    --plot-files    "${FILES_EXPERIMENTS[@]}" \
    --regions-csv   "${LAMBERT_CSV_FILE}"   \
    --path-fig      "${FIG_DIR}/${DATASET}"

checkstatus


exit 0


