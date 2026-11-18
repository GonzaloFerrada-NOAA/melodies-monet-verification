#!/bin/bash --login
#
# Build the verification dashboard: ONE self-contained html per cycle and model_type,
# from the MELODIES-MONET time-series CSVs (mm_run task) and the HMS fire text files.
#
# Environment (Rocoto XML):
#   START_TIME          cycle day, YYYYMMDD
#   MELODIES_MONET_DIR  main directory of the real-time system
#   model_type          regional_smoke | regional_chemistry | regional_aerosols | global | all
#   WORKDIR             working directory of this task
#   OBSDIR              observations of this cycle (not used by the dashboard)
#   SCRIPTS_DIR         real-time scripts (generate_dashboard.py, yaml_templates/)
#   PLOT_OUTPUT_DIR     <species>/<platform>/plot_grp1.timeseries.*.csv of this cycle
#   HMS_TXT_DIR         HMS fire text files (hms_fireYYYYMMDD.txt)
#   CONDA_ENV           conda environment with pandas + pyyaml
#
source mm.settings

# Cycle 00Z of START_TIME; the dashboard shows START_TIME-24h .. START_TIME+24h
cycle="${DATEF} 00:00"

# Paths without a trailing slash
plot_dir="${PLOT_OUTPUT_DIR}/${CDATE}/sites"
hms_dir="${DIR_HMS_TXT}"
out_dir="${plot_dir}/website"
out_html="${out_dir}/index.html"
run_dir="${RUN_DIR}/${DATE}/html"
mkdir -p ${out_dir} ${run_dir}
cd ${run_dir}

# Hourly AOD of this cycle (48 h). ADJUST the directories to where the aggregation task
# writes them; with or without the .nc extension.
viirs_file="${OBS_DIR}/viirs_${YYYYMMDD}.nc"
tempo_file="${OBS_DIR}/tempo_${YYYYMMDD}.nc"

[[ ! -s ${viirs_file} ]] && viirs_file=None
[[ ! -s ${tempo_file} ]] && tempo_file=None

echo "VIIRS AOD file: ${viirs_file}"
echo "TEMPO AOD file: ${tempo_file}"

# Fill the yaml template for this cycle / model_type
template="${SCRIPTS_DIR}/yaml_templates/html_dashboard.yaml"
config="${run_dir}/dashboard.yaml"
if [[ ! -f ${template} ]] ; then
    echo "FATAL: yaml template not found: ${template}"
    exit 1
fi

sed -e "s|__CYCLE__|${cycle}|g" \
    -e "s|__PLOT_OUTPUT_DIR__|${plot_dir}|g" \
    -e "s|__HMS_TXT_DIR__|${hms_dir}|g" \
    -e "s|__OUTPUT_HTML__|${out_html}|g" \
    ${template} > ${config}

# Build the html
${SRUN_PYTHON} ${SCRIPTS_DIR}/generate_website.py ${config} --viirs-file "${viirs_file}" --tempo-file "${tempo_file}"
checkstatus
isfile ${out_html}

chmod a+r ${out_html}

exit 0