#!/bin/bash -l

source mm.settings

YAML="${MELODIES_MONET_DIR}/experiments.yaml"

# CDATE must be YYYYMMDDHH (catches unsubstituted Rocoto strings)
[[ ${CDATE} =~ ^[0-9]{10}$ ]] || { echo "ERROR: invalid CDATE='${CDATE}'" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Experiment settings
# ---------------------------------------------------------------------------
model_name=$(yaml_get "${YAML}" "${EXP}" model_name)
raw_output_directory=$(yaml_get "${YAML}" "${EXP}" raw_output_directory)
raw_output_filenames=$(yaml_get "${YAML}" "${EXP}" raw_output_filenames)
raw_output_format=$(yaml_get "${YAML}" "${EXP}" raw_output_format)
simulation_length_hr=$(yaml_get "${YAML}" "${EXP}" simulation_length_hr)
output_interval_hr=$(yaml_get "${YAML}" "${EXP}" output_interval_hr) || output_interval_hr=1

# Species: two parallel arrays (same index = same species, file order kept)
SPECIES_MODEL=(); SPECIES_MM=()
while IFS=$'\t' read -r k v; do
  SPECIES_MODEL+=("${k}"); SPECIES_MM+=("${v}")
done < <(yaml_map "${YAML}" "${EXP}" species)
(( ${#SPECIES_MODEL[@]} > 0 )) || { echo "ERROR: no species for ${EXP}" >&2; exit 1; }
species_csv=$(IFS=,; echo "${SPECIES_MODEL[*]}")     # aod550,pm25,... for python

# ---------------------------------------------------------------------------
# Recipe: optional 'recipe:' key in experiments.yaml overrides the default
# ---------------------------------------------------------------------------
recipe=$(yaml_get "${YAML}" "${EXP}" recipe) || recipe=""
if [[ -z ${recipe} ]]; then
  shopt -s nocasematch                                # case insensitive
  case "${model_name}" in
    rrfs-sd|rrfs|rrfsv1)                                    base=rrfs-sd ;;
    mpas-aerosols|chempas-fire|cmf|fire-weather-nest|fwx-nest|fwxnest) base=mpas-aerosols ;;
    hrrr-smoke)                                             base=hrrr-smoke ;;
    gefs-aerosols|gcafs|ufs-aerosols)                       base=gefs-aerosols ;;
    rap-smoke|rap-chem)                                     base=rap-chem ;;
    *) echo "ERROR: model_name='${model_name}' not supported." >&2; exit 1 ;;
  esac
  shopt -u nocasematch
  recipe="${AUX_DIR}/recipes/${base}_${raw_output_format}.yaml"
fi
[[ -f ${recipe} ]] || { echo "ERROR: recipe not found: ${recipe}" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Build job list: one line per frame ->  <out_frame> TAB <in1,in2,...>
# raw_output_filenames may hold several space-separated templates (RRFS dyn+phy)
# ---------------------------------------------------------------------------
path_out="${OUTPUT_DIR}/model_data"
frames_dir="${path_out}/frames_${EXP}_${CDATE}"
ismkdir "${path_out}"
ismkdir "${frames_dir}"

read -ra templates <<< "${raw_output_filenames}"
jobs="${frames_dir}/jobs.txt"
: > "${jobs}"
for (( lead=0; lead<=simulation_length_hr; lead+=output_interval_hr )); do
  inputs=()
  for tpl in "${templates[@]}"; do
    f=$(fill_template "${raw_output_directory}/${tpl}" "${CDATE}" "${lead}")
    isfile "${f}"
    inputs+=("${f}")
  done
  printf -v out '%s/frame_f%03d.nc' "${frames_dir}" "${lead}"   # zero-padded -> sorted glob
  printf '%s\t%s\t%d\n' "${out}" "$(IFS=,; echo "${inputs[*]}")" "${lead}" >> "${jobs}"
done

# ---------------------------------------------------------------------------
# Extract frames in parallel ({1}=output, {2}=comma-separated inputs)
# --halt now,fail=1 : stop everything at the first failure
# ---------------------------------------------------------------------------
# command -v parallel >/dev/null || { echo "ERROR: GNU parallel not in PATH" >&2; exit 1; }
# Concurrent frames: limited by CPUs AND memory.
#   MEM_PER_FRAME_GB: peak memory of one extract_frame.py job (measure once, see notes)
#   SLURM_MEM_PER_NODE is in MB when <memory> is set in the XML
MEM_PER_FRAME_GB=${MEM_PER_FRAME_GB:-4}
ncpu=${SLURM_NTASKS:-${SLURM_CPUS_ON_NODE:-8}}   # tasks requested (ppn), not whole-node CPUs
nmem=$(( ${SLURM_MEM_PER_NODE:-32768} / 1024 / MEM_PER_FRAME_GB ))
(( nmem < 1 )) && nmem=1
NPROC=${NPROC:-$(( ncpu < nmem ? ncpu : nmem ))}

# Validate recipe + species on one frame before launching all of them (seconds)
IFS=$'\t' read -r chk_out chk_in chk_lead < <(tail -n 1 "${jobs}")
"${PYTHON}" "${USH_DIR}/extract_frame_${raw_output_format}.py" --check \
    --recipe "${recipe}" --species "${species_csv}" \
    --out "${chk_out}" --lead "${chk_lead}" "${chk_in}" \
  || { echo "ERROR: recipe check failed for ${EXP} (see message above)" >&2; exit 1; }


echo "Running ${NPROC} frames at a time (cpus=${ncpu}, mem-limited=${nmem})"

${PARALLEL_BIN} --colsep '\t' -a "${jobs}" -j "${NPROC}" --halt now,fail=1 \
         --joblog "${frames_dir}/joblog.txt" \
         "${PYTHON}" "${USH_DIR}/extract_frame_${raw_output_format}.py" \
         --recipe "${recipe}" --species "${species_csv}" --out {1} --lead {3} {2} \
  || { echo "ERROR: frame extraction failed (see ${frames_dir}/joblog.txt)" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Concatenate frames along time and clean up
# ---------------------------------------------------------------------------
aqm_file="${path_out}/aqm_${EXP}_${CDATE}.nc"
ncrcat -O -h "${frames_dir}"/frame_f*.nc "${aqm_file}" 
checkstatus
isfile "${aqm_file}"

rm -rf "${frames_dir}"
echo "Done: ${aqm_file}"

# ---------------------------------------------------------------------------
# Regrid AOD data for VIIRS and TEMPO evaluation
# ---------------------------------------------------------------------------
tmp_aod="${path_out}/aod_${EXP}_${CDATE}.nc"
file_tempo="${path_out}/aod_tempo_regridded_${EXP}_${CDATE}.nc"
file_viirs="${path_out}/aod_viirs_regridded_${EXP}_${CDATE}.nc"
cdo_tempo="${AUX_DIR}/cdo_regrid_tempo_regional_0p1.txt"
cdo_viirs="${AUX_DIR}/cdo_regrid_viirs_regional_0p05.txt"
ncks -h -O -v aod550 "${aqm_file}" "${tmp_aod}"; isfile "${tmp_aod}"
cdo remapbil,${cdo_tempo} "${tmp_aod}" "${file_tempo}"
cdo remapbil,${cdo_viirs} "${tmp_aod}" "${file_viirs}"

isfile "${file_tempo}"
isfile "${file_viirs}"


exit 0
