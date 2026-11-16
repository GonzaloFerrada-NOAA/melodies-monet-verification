#!/usr/bin/env bash
# yaml_helpers.sh — minimal YAML reader for the verification experiments file.
# Source it:  source yaml_helpers.sh
#
# Supports the structure used in experiments.yaml:
#   <experiment>:            (top level, no indent)
#     key: value             (scalars)
#     map_key:               (one nested mapping level, e.g. species)
#       sub_key: value
# Handles single/double quotes and trailing "# comments".
# Not a full YAML parser (no lists, anchors, multi-line strings).
# Portable: works with macOS bash 3.2 and awk/gawk on RDHPCS.

# --- xtrace guard -------------------------------------------------------
# Callers often run with `set -x`; silence tracing inside the helpers so the
# awk code isn't dumped to the log. The call line itself is still traced.
#   _yaml_xoff  : remember current xtrace state, then disable it
#   _yaml_xon   : restore xtrace if it was on
# The `{ ...; } 2>/dev/null` wrapper hides the guard's own trace line.
_yaml_xoff() { case $- in *x*) _YAML_XT=1 ;; *) _YAML_XT=0 ;; esac; set +x; }
_yaml_xon()  { [ "${_YAML_XT:-0}" = 1 ] && set -x; return 0; }

# Core awk parser. Emits one record per line, tab-separated:
#   E <exp>                      top-level experiment
#   S <exp> <key> <value>        scalar
#   M <exp> <map> <sub> <value>  entry of a nested mapping
_yaml_parse() {
  awk -v SQ="'" '
    # Strip a trailing comment, ignoring "#" inside quotes
    function strip_comment(s,   i, c, q) {
      q = ""
      for (i = 1; i <= length(s); i++) {
        c = substr(s, i, 1)
        if (q == "" && (c == "\"" || c == SQ)) q = c
        else if (q != "" && c == q) q = ""
        else if (q == "" && c == "#" && (i == 1 || substr(s, i-1, 1) ~ /[ \t]/))
          return substr(s, 1, i-1)
      }
      return s
    }
    function trim(s) { sub(/^[ \t]+/, "", s); sub(/[ \t]+$/, "", s); return s }
    # Remove one pair of surrounding quotes
    function unquote(s) {
      if (s ~ /^".*"$/ || substr(s,1,1) == SQ && substr(s,length(s),1) == SQ) s = substr(s, 2, length(s) - 2)
      return s
    }
    {
      line = strip_comment($0)
      if (line ~ /^[ \t]*$/) next              # skip blank/comment-only lines
      match(line, /^ */); ind = RLENGTH        # indentation (spaces)
      body = trim(line)
      k = body; sub(/:.*/, "", k); k = trim(k) # key = text before first ":"
      v = body; sub(/^[^:]*:/, "", v); v = unquote(trim(v))

      if (ind == 0) {                          # new experiment
        ex = k; lvl1 = -1; map = ""
        print "E\t" ex; next
      }
      if (lvl1 < 0) lvl1 = ind                 # first child sets level-1 indent
      if (ind == lvl1) {
        if (v == "") map = k                   # "species:" opens a nested map
        else { map = ""; print "S\t" ex "\t" k "\t" v }
      } else if (ind > lvl1 && map != "") {
        print "M\t" ex "\t" map "\t" k "\t" v
      }
    }' "$1"
}

# List experiment names, one per line.
#   yaml_experiments FILE
yaml_experiments() {
  { _yaml_xoff; } 2>/dev/null
  _yaml_parse "$1" | awk -F'\t' '$1 == "E" { print $2 }'
  local rc=$?; { _yaml_xon; return $rc; } 2>/dev/null
}

# Print a scalar value (empty + exit code 1 if missing).
#   yaml_get FILE EXPERIMENT KEY
yaml_get() {
  { _yaml_xoff; } 2>/dev/null
  _yaml_parse "$1" | awk -F'\t' -v e="$2" -v k="$3" '
    $1 == "S" && $2 == e && $3 == k { print $4; found = 1; exit }
    END { exit !found }'
  local rc=$?; { _yaml_xon; return $rc; } 2>/dev/null       # keep "missing key" exit code
}

# Print a nested mapping as "subkey<TAB>value" lines, in file order.
#   yaml_map FILE EXPERIMENT MAPKEY
yaml_map() {
  { _yaml_xoff; } 2>/dev/null
  _yaml_parse "$1" | awk -F'\t' -v e="$2" -v m="$3" '
    $1 == "M" && $2 == e && $3 == m { print $4 "\t" $5 }'
  local rc=$?; { _yaml_xon; return $rc; } 2>/dev/null
}


# template_helpers.sh — fill METplus-style filename templates.
# Source it:  source template_helpers.sh
#
# Tokens (fmt is a strftime string, as in METplus):
#   {init?fmt=%Y%m%d%H}   initialization (cycle) time
#   {valid?fmt=%Y-%m-%d_%H}  valid time = init + lead
#   {lead?fmt=%3H}        forecast lead in hours, zero-padded to N digits
#                         (%H -> 2 digits, %3H -> 3 digits, ...)
# Examples:
#   rrfs.t{init?fmt=%H}z.2dfld.3km.f{lead?fmt=%3H}.na.grib2
#   mpassit_{valid?fmt=%Y-%m-%d_%H}.00.00.nc
#
# Works with GNU date (RDHPCS) and BSD date (macOS). Quiet under `set -x`.

# Format a time: _tpl_date YYYYMMDDHH OFFSET_HR STRFTIME_FMT
_tpl_date() {
  local t=$1 off=$2 fmt=$3
  if date --version >/dev/null 2>&1; then          # GNU date
    date -u -d "${t:0:8} ${t:8:2}:00 UTC ${off} hours" +"$fmt"
  else                                             # BSD/macOS date
    date -u -j -v"${off}"H -f "%Y%m%d%H%M%S" "${t}0000" +"$fmt"
  fi
}

# Expand a template.
#   fill_template TEMPLATE INIT_YYYYMMDDHH LEAD_HR
# Prints the filled string; exit 1 on an unknown token.
fill_template() {
  { case $- in *x*) local _xt=1;; *) local _xt=0;; esac; set +x; } 2>/dev/null
  local s=$1 init=$2 lead=$((10#$3)) out="" kind fmt val n rc=0
  local re='\{(init|valid|lead)\?fmt=([^}]*)\}'
  while [[ $s =~ $re ]]; do
    kind=${BASH_REMATCH[1]}; fmt=${BASH_REMATCH[2]}
    out+=${s%%"${BASH_REMATCH[0]}"*}               # text before the token
    s=${s#*"${BASH_REMATCH[0]}"}                   # text after the token
    case $kind in
      init)  val=$(_tpl_date "$init" 0 "$fmt") ;;
      valid) val=$(_tpl_date "$init" "+$lead" "$fmt") ;;
      lead)  n=${fmt//[^0-9]/}; n=${n:-2}          # %3H -> 3, %H -> 2
             printf -v val "%0${n}d" "$lead" ;;
    esac
    out+=$val
  done
  out+=$s
  [[ $out == *'{'*'}'* ]] && { echo "ERROR: unparsed token in '$1'" >&2; rc=1; }
  echo "$out"
  { [ "$_xt" = 1 ] && set -x; return $rc; } 2>/dev/null
}