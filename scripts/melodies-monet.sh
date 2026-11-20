#!/bin/bash
#==============================================================================
# mm_plots.sh -- MELODIES-MONET verification for ONE platform (DATASET).
# Loops over all SPECIES of the platform, and for each over PLOT_TYPES.
# control.yaml is assembled from static templates in ${YAML_DIR};
# only cycle-dependent parts are generated:
#   - analysis dates/output dirs     (@START_TIME@ @END_TIME@, output_dir[_save])
#   - model block                    (built from experiments.yaml: which
#                                     experiments have data this cycle)
#   - obs-model pairs / model names  (@DATA@ @MODEL_NAMES@)
#   - domain lists                   (@DOMAIN_TYPE@ @DOMAIN_NAME@)
#   - CSI thresholds                 (@CSI_THRESHOLDS@, from csi_thresholds.txt)
#   - paired-file prefix             (@SAVE_PREFIX@ = <species>_<CDATE>)
#
# Directories:
#   ${FIG_DIR}/<platform>/<species>/          PNG figures + stats tables
#   ${OUTPUT_DIR}/<platform>/                 paired netCDF (<species>_<CDATE>_<pair>.nc4)
#   ${RUN_DIR}/mm_plots/<platform>/<CDATE>/   scratch: control.yaml, obs link, status
#                                             (removed at the end if nothing failed)
#
# Failure handling: each species and each plot type runs in its own subshell,
# so a crash (or 'exit') only ends that species/plot type; the rest continue.
#
# Environment (from Rocoto): CDATE DATASET
# From mm.settings: FIG_DIR OUTPUT_DIR RUN_DIR OBS_DIR YAML_DIR USH_DIR PYTHON
#==============================================================================

source mm.settings                         # also sources workflow_tools.sh + yaml_helpers.sh

platform="${DATASET}"
fig_platform_dir="${FIG_DIR}/${platform}"            # + /<species> below
save_dir="${OUTPUT_DIR}/${platform}"                 # paired netCDF, all species
run_root="${RUN_DIR}/${platform}/${CDATE}"  # scratch (per cycle: safe for concurrent cycles)
TEMPLATE_DIR="${YAML_DIR}"
MODEL_OUTPUT_DIR="${OUTPUT_DIR}/model_data"

# PLOT_TYPES=( "ts_regional" "spatial_overlay" "spatial_bias" "boxplot" "taylor" "scorecard" "csi" "stats" )
PLOT_TYPES=( "stats" )

# Species evaluated for each platform (obs names, as in the obs/variables templates)
case ${platform} in
    airnow)   SPECIES=( "PM2.5"  "PM10"  "OZONE"  "NO2"  "CO"  "TEMP" ) ;;
    openaq)   SPECIES=( "PM2.5"  "PM10"  "OZONE"  "NO2"  "CO" ) ;;
    aeronet)  SPECIES=( "aod_550nm" ) ;;
    metar)    SPECIES=( "temperature" "dewpoint" "windDir" "windSpeed" "precip1Hour" "visibility" ) ;;
    # ish)      SPECIES=( "vsb"  "ceiling" ) ;;
    # ish-lite) SPECIES=( "temp"  "dew_pt_temp"  "wdir"  "ws"  "precip_1hr" ) ;;
    *)        echo "ERROR: unsupported platform ${platform}"; exit 1 ;;
esac

#------------------------------------------------------------------------------
# Helpers
#------------------------------------------------------------------------------
# fill FILE TOKEN VALUE : replace every @TOKEN@ in FILE with VALUE
fill() {
    local v=${3//\\/\\\\}; v=${v//&/\\&}; v=${v//|/\\|}   # escape sed specials
    sed -i "s|@${2}@|${v}|g" "$1"
}
# setkey FILE KEY VALUE : set an 'analysis:' key (2-space indent) to VALUE,
# whatever the template has there (token or literal path)
setkey() {
    local v=${3//\\/\\\\}; v=${v//&/\\&}; v=${v//|/\\|}
    sed -i "s|^\(  ${2}:\).*|\1 ${v}|" "$1"
}
# lookup FILE KEY : print everything after KEY on its line (empty if not found)
lookup() {
    awk -v k="$2" '$1==k { $1=""; sub(/^ +/,""); print; exit }' "$1"
}
# join SEP ITEMS... : join items with SEP
join() { local IFS="$1"; shift; echo "$*"; }

# yaml_opt FILE EXP KEY DEFAULT : like yaml_get, but DEFAULT if key missing/empty
yaml_opt() { local v; v=$(yaml_get "$1" "$2" "$3") || v=""; echo "${v:-$4}"; }

# model_file EXP RUN_CDATE : path of the (pre-processed) model file for one run.
# Change here if the model_data layout changes.
model_file() { echo "${MODEL_OUTPUT_DIR}/aqm_${1}_${2}.nc"; }

# record SPECIES PLOT_TYPE STATUS : append one line to the status file.
# Subshells can't change parent arrays, so results are passed through a file.
STATUS_FILE=${run_root}/plot_status.txt
record() { printf "%-12s %-18s %s\n" "$1" "$2" "$3" >> ${STATUS_FILE}; }

#------------------------------------------------------------------------------
# Experiments: read experiments.yaml ONCE into associative arrays keyed by
# experiment name (subshells below inherit them).
#   X_SPC["exp|obsname"] = model variable  (obsname lower-cased so that the
#   same model var matches e.g. airnow 'TEMP' and ish-lite 'temp')
#------------------------------------------------------------------------------
[[ -r ${YAML_EXPERIMENTS} ]] || { echo "ERROR: missing ${YAML_EXPERIMENTS}"; exit 1; }

EXPS=()
declare -A X_TYPE X_INIT X_LEN X_RELDAY X_COLOR X_MARKER X_LSTYLE X_MSIZE X_LWIDTH X_SPC
MAX_SIM_LENGTH=0

while IFS= read -r exp; do
    EXPS+=("${exp}")
    X_LEN[$exp]=$(yaml_get "${YAML_EXPERIMENTS}" "${exp}" simulation_length_hr) \
        || { echo "ERROR: ${exp}: simulation_length_hr missing in ${YAML_EXPERIMENTS}"; exit 1; }
    X_TYPE[$exp]=$(  yaml_opt "${YAML_EXPERIMENTS}" "${exp}" model_type          standard)
    X_INIT[$exp]=$(  yaml_opt "${YAML_EXPERIMENTS}" "${exp}" initialization_hr   0)
    X_RELDAY[$exp]=$(yaml_opt "${YAML_EXPERIMENTS}" "${exp}" relative_day_to_obs 0)
    X_COLOR[$exp]=$( yaml_opt "${YAML_EXPERIMENTS}" "${exp}" color               black)
    X_MARKER[$exp]=$(yaml_opt "${YAML_EXPERIMENTS}" "${exp}" marker              x)
    X_LSTYLE[$exp]=$(yaml_opt "${YAML_EXPERIMENTS}" "${exp}" linestyle           -)
    X_MSIZE[$exp]=$( yaml_opt "${YAML_EXPERIMENTS}" "${exp}" markersize          8)
    X_LWIDTH[$exp]=$(yaml_opt "${YAML_EXPERIMENTS}" "${exp}" linewidth           1.5)

    # species: map  ->  "modvar<TAB>obsname" lines
    while IFS=$'\t' read -r modvar obsname; do
        X_SPC["${exp}|${obsname,,}"]=${modvar}
    done < <(yaml_map "${YAML_EXPERIMENTS}" "${exp}" species)

    if (( X_LEN[$exp] > MAX_SIM_LENGTH )); then MAX_SIM_LENGTH=${X_LEN[$exp]}; fi
done < <(yaml_experiments "${YAML_EXPERIMENTS}")

if (( ${#EXPS[@]} == 0 )); then
    echo "ERROR: no experiments found in ${YAML_EXPERIMENTS}"
    exit 1
fi
echo "Experiments: ${EXPS[*]}  (max simulation length ${MAX_SIM_LENGTH} h)"

#------------------------------------------------------------------------------
# Dates: analysis window is CYCLE_DATE to CYCLE_DATE + longest simulation
#------------------------------------------------------------------------------
start_time_yaml="${DATEF}-${DATET}"
end_time_yaml=$(date -ud "${CYCLE_DATE} ${MAX_SIM_LENGTH} hours" "+%F-%T")

#------------------------------------------------------------------------------
# Checks that apply to ALL species (platform-level: fail once)
#------------------------------------------------------------------------------
for f in analysis.yaml obs/${platform}.yaml; do
    if [[ ! -r ${TEMPLATE_DIR}/${f} ]]; then
        echo "ERROR: missing template ${TEMPLATE_DIR}/${f}"
        exit 1
    fi
done

obsfile="${OBS_DIR}/${platform}.${YYYYMMDD}.nc"
if [[ ! -e ${obsfile} ]]; then
    echo "No ${platform} data (${obsfile}), nothing to plot..."
    exit 1
fi

rm -rf ${run_root}                           # fresh scratch for this cycle (reruns too)
mkdir -p ${run_root} ${save_dir} ${fig_platform_dir}
: > ${STATUS_FILE}                           # start with an empty status file

#==============================================================================
# SPECIES loop. Each species runs in a subshell: an 'exit' inside (missing
# template, no model data, crash) only ends THIS species.
# NOTE: mm.settings sets 'set -e'; '( ... ) || rc=$?' keeps a failed subshell
# from killing the whole task.
#==============================================================================
for species in "${SPECIES[@]}"; do

  echo ""
  echo "##################################################################"
  echo "### ${species} (${platform}): starting $(date -u)"
  echo "##################################################################"

  rc=0
  (
    rundir=${run_root}/${species}        # scratch files per species
    figdir=${fig_platform_dir}/${species}  # PNGs per species
    mkdir -p ${rundir} ${figdir}
    cd ${rundir} || exit 1

    if [[ ! -r ${TEMPLATE_DIR}/variables/${species}.yaml ]]; then
      echo "ERROR: missing template ${TEMPLATE_DIR}/variables/${species}.yaml"
      record "${species}" "ALL" "FAILED (no variables template)"
      exit 1
    fi

    #--------------------------------------------------------------------------
    # Model block: one entry per experiment that has data and the variable.
    #--------------------------------------------------------------------------
    model_block=${rundir}/model_block.yaml
    echo "model:" > ${model_block}
    pairs=()                                 # 'platform_label' entries for data:
    models=()                                # 'label' entries for model_name_list:

    for exp in "${EXPS[@]}"; do
      modvar=${X_SPC["${exp}|${species,,}"]:-}   # model var mapped to this obs species
      if [[ -z ${modvar} ]]; then continue; fi   # experiment doesn't provide it

      # Run (init) date = obs day + relative_day_to_obs (negative = earlier)
      printf -v hh "%02d" "$(( 10#${X_INIT[$exp]} ))"
      run=$(shift_cdate "${YYYYMMDD}${hh}" "${X_RELDAY[$exp]}")
      testfile=$(model_file "${exp}" "${run}")

      if [[ ! -r ${testfile} ]]; then
        echo "Missing ${testfile}, skipping ${exp}"; continue
      fi
      if ! ncdump -hv ${modvar} ${testfile} > /dev/null 2>&1; then
        echo "Variable ${modvar} not in ${testfile}, skipping ${exp}"; continue
      fi

      label=${exp}
      pairs+=("'${platform}_${label}'")
      models+=("'${label}'")
      echo "Using model ${label} (${modvar} -> ${species})"

      {                                      # append this model's YAML entry
        echo "  ${label}:"
        echo "    files: ${testfile}"
        # All model output is pre-converted to a generic surface netCDF, so any
        # non-native mod_type makes MELODIES-MONET read it with plain xarray.
        # No mod_kwargs: they'd be passed straight to xr.open_dataset().
        echo "    mod_type: '${X_TYPE[$exp]}'"
        if [[ ${platform} == airnow ]]; then echo "    projection: ~"; fi   # YAML null
        echo "    radius_of_influence: 12000 #meters"
        echo "    mapping: #model species name : obs species name"
        echo "      ${platform}:"
        echo "        ${modvar}: \"${species}\""
        echo "    plot_kwargs:"
        echo "      color: '${X_COLOR[$exp]}'"
        echo "      marker: '${X_MARKER[$exp]}'"
        echo "      linestyle: '${X_LSTYLE[$exp]}'"
        echo "      markersize: ${X_MSIZE[$exp]}"
        echo "      linewidth: ${X_LWIDTH[$exp]}"
      } >> ${model_block}
    done

    # No experiment provides this species (e.g. NO2 for smoke-only runs): skip
    if (( ${#pairs[@]} == 0 )); then
      echo "NO model data for ${species} on ${YYYYMMDD}, skipping species"
      record "${species}" "ALL" "SKIPPED (no model data)"
      rmdir ${figdir} 2>/dev/null || true    # don't leave an empty figure dir
      exit 0
    fi

    # Base YAML shared by every plot type: analysis + model + obs + variables
    base_yaml=${rundir}/base.control.yaml
    cat ${TEMPLATE_DIR}/analysis.yaml \
        ${model_block} \
        ${TEMPLATE_DIR}/obs/${platform}.yaml \
        ${TEMPLATE_DIR}/variables/${species}.yaml > ${base_yaml}
    fill ${base_yaml} START_TIME "${start_time_yaml}"
    fill ${base_yaml} END_TIME   "${end_time_yaml}"
    fill ${base_yaml} SAVE_PREFIX "${species}_${CDATE}"
    fill ${base_yaml} OUTPUT_DIR      "${figdir}"     # PNGs + stats tables
    fill ${base_yaml} OUTPUT_DIR_SAVE "${save_dir}"   # paired netCDF

    data_list=$(join , "${pairs[@]}")
    model_list=$(join , "${models[@]}")
    csi_thr=$(lookup ${TEMPLATE_DIR}/csi_thresholds.txt "${species}")

    # Regional domains: CONUS + EPA regions (AirNow only)
    if [[ ${platform} == "airnow" ]]; then
        rgn_names=("'CONUS'"); rgn_types=("'isinconus'")
        regions=$(lookup ${TEMPLATE_DIR}/epa_regions.txt "${species}")
        for r in ${regions:-R1 R2 R3 R4 R5 R6 R7 R8 R9 R10}; do
            rgn_names+=("'${r}'"); rgn_types+=("'epa_region'")
        done
    else
        rgn_names=("'all'"); rgn_types=("'all'")      # whole domain, no region flags needed
    fi

    #--------------------------------------------------------------------------
    # PLOT_TYPE loop (nested subshell per plot type)
    #--------------------------------------------------------------------------
    for plot_type in "${PLOT_TYPES[@]}"; do

      if [[ ! -r ${TEMPLATE_DIR}/plots/${plot_type}.yaml ]]; then
        echo "=== ${species} ${plot_type}: no template"
        record "${species}" "${plot_type}" "FAILED (no plot template)"
        continue
      fi
      echo "=== ${species} ${plot_type}: starting $(date -u)"

      prc=0
      (
        workdir=${rundir}/${plot_type}
        mkdir -p ${workdir}
        cd ${workdir} || exit 1
        rm -f tmp.control.yaml* control.yaml
        ln -sf ${obsfile} test5.nc

        dnames=("${rgn_names[@]}"); dtypes=("${rgn_types[@]}")

        # Assemble control.yaml = base + plot template, then fill tokens
        ctl=tmp.control.yaml.${species}.${YYYYMMDD}.${plot_type}
        cat ${base_yaml} ${TEMPLATE_DIR}/plots/${plot_type}.yaml > ${ctl}
        fill ${ctl} DOMAIN_TYPE "$(join , "${dtypes[@]}")"
        fill ${ctl} DOMAIN_NAME "$(join , "${dnames[@]}")"
        fill ${ctl} DATA        "${data_list}"
        fill ${ctl} MODEL_NAMES "${model_list}"
        if [[ -n ${csi_thr} ]]; then
          fill ${ctl} CSI_THRESHOLDS "${csi_thr}"
        else
          sed -i '/@CSI_THRESHOLDS@/d' ${ctl}    # species without thresholds
        fi
        ln -sf ${ctl} control.yaml

        ${PYTHON} ${USH_DIR}/melodies_monet_analysis.py ${platform} ${plot_type}  # subshell exit status = this
      ) || prc=$?

      if (( prc != 0 )); then
        echo "=== ${species} ${plot_type}: FAILED (exit ${prc}), continuing"
        record "${species}" "${plot_type}" "FAILED (exit ${prc})"
      else
        echo "=== ${species} ${plot_type}: done $(date -u)"
        record "${species}" "${plot_type}" "SUCCESS"
      fi

    done # PLOT_TYPES
  ) || rc=$?

  # Species subshell died before recording anything (unexpected crash)
  if (( rc != 0 )) && ! grep -q "^${species} " ${STATUS_FILE}; then
    record "${species}" "ALL" "FAILED (exit ${rc})"
  fi
  echo "### ${species}: finished $(date -u)"

done # SPECIES

# -------------------------------------------------------
# Summary report (from the status file)
# grep -c exits 1 on zero matches -> '|| true' so set -e doesn't kill us
# -------------------------------------------------------
n_success=$(grep -c ' SUCCESS$' ${STATUS_FILE} || true)
n_failed=$( grep -c ' FAILED'   ${STATUS_FILE} || true)
n_skipped=$(grep -c ' SKIPPED'  ${STATUS_FILE} || true)

echo ""
echo "=========================================================="
echo "     SUMMARY: ${platform} / ${CDATE}"
echo "=========================================================="
printf "%-12s %-18s %s\n" "SPECIES" "PLOT_TYPE" "STATUS"
echo "----------------------------------------------------------"
cat ${STATUS_FILE}
echo "=========================================================="
echo "Succeeded: ${n_success}   Failed: ${n_failed}   Skipped: ${n_skipped}"
echo "Figures : ${fig_platform_dir}/<species>"
echo "Paired  : ${save_dir}"
echo "=========================================================="

# -------------------------------------------------------
# Scratch cleanup: keep the run directory only if something failed (debugging)
# -------------------------------------------------------
if (( n_failed == 0 )); then
    rm -rf ${run_root}
else
    echo "Run files kept for debugging: ${run_root}"
fi

# -------------------------------------------------------
# Final Rocoto task exit status: DEAD only if everything attempted failed
# -------------------------------------------------------
if (( n_failed > 0 && n_success == 0 )); then
    echo "All attempted plots failed"
    exit 1
fi
if (( n_failed > 0 )); then
    echo "Some plots failed (see summary above)"
fi
exit 0