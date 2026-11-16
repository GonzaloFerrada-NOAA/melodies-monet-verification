msg()
{
    echo "$(date '+%F %T')    $1"
}

# =============================================================================
# =============================================================================

checkstatus() {
    local status=$?
    if (( status != 0 )); then
        echo "Command failed with exit status $status" >&2
        exit "$status"
    fi
}

# =============================================================================
# =============================================================================

isfile()
{
    local file="$1"
    if [[ ! -s "$file" ]]; then
        echo "ERROR: File does not exist or is empty: $file" >&2
        exit 1
    fi
}

# =============================================================================
# =============================================================================

ismkdir()
{
    local diri="$1"
    if [[ ! -d "$diri" ]]; then
        mkdir -p $diri
    fi
}

# =============================================================================
# =============================================================================
get_group_leads()
{
    local fcst_length_hours="$1"
    local model_dt="$2"
    local ngroups="$3"
    local group="$4"

    local n_times base remainder n_group_times start_idx end_idx

    n_times=$(( fcst_length_hours / model_dt + 1 ))
    base=$(( n_times / ngroups ))
    remainder=$(( n_times % ngroups ))

    if (( group <= remainder )); then
        n_group_times=$(( base + 1 ))
    else
        n_group_times=$base
    fi

    start_idx=$(( (group - 1) * base + (group - 1 < remainder ? group - 1 : remainder) ))
    end_idx=$(( start_idx + n_group_times - 1 ))

    LEAD_HH_START=$(( start_idx * model_dt ))
    LEAD_HH_END=$(( end_idx * model_dt ))
}

# =============================================================================
# =============================================================================
# ---- Date helpers -----------------------------------------------------------

# shift_cdate CDATE NDAYS -> prints CDATE shifted by NDAYS (negative = earlier)
# Works through epoch seconds, so month/year/leap-year boundaries are handled; UTC has no DST confusion
shift_cdate() {
  local cdate=$1 ndays=$2 base
  base=$(date -ud "${cdate:0:8} ${cdate:8:2}:00:00" +%s)   # CDATE -> seconds since 1970
  date -ud "@$(( base + ndays*86400 ))" +%Y%m%d%H          # shifted seconds -> YYYYMMDDHH
}

# set_date_vars CDATE [SUFFIX] -> exports YYYYMMDD${SUFFIX}, YYYY${SUFFIX}, ... as global variables
set_date_vars() {
  local cdate=$1 sfx=${2:-}
  [[ ${cdate} =~ ^[0-9]{10}$ ]] || { echo "set_date_vars: invalid date '${cdate}'" >&2; return 1; }
  declare -gx "CDATE${sfx}=${cdate}" \
              "YYYYMMDD${sfx}=${cdate:0:8}" \
              "YYYY${sfx}=${cdate:0:4}" \
              "YY${sfx}=${cdate:2:2}" \
              "MM${sfx}=${cdate:4:2}" \
              "DD${sfx}=${cdate:6:2}" \
              "HH${sfx}=${cdate:8:2}" \
              "JJJ${sfx}=$(date -ud "${cdate:0:8}" +%j)" \
              "DATEF${sfx}=${cdate:0:4}-${cdate:4:2}-${cdate:6:2}" \
              "DATET${sfx}=${cdate:8:2}:00:00" \
              "CYCLE_DATE${sfx}=${cdate:0:4}-${cdate:4:2}-${cdate:6:2} ${cdate:8:2}:00:00"
  # declare -g: set the variable globally even though we're inside a function; -x: export it
}

# Previous / next day relative to CDATE (defaults to $CDATE if no argument is given)
set_prev_dates() { set_date_vars "$(shift_cdate "${1:-${CDATE}}" -1)" _PREV; }
set_next_dates() { set_date_vars "$(shift_cdate "${1:-${CDATE}}" +1)" _NEXT; }
