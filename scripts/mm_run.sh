#!/bin/bash
#
source /home/Jordan.Schnell/miniconda/bin/activate melodies-monet-develop
module load nco

set -x
#
DATE=/bin/date
#Set up the date strings
todays_date=${START_TIME}
endday_date=${END_TIME}
#
# Today variables 
YYYY_today=`${DATE} +%Y -d "${todays_date}"`
MM_today=`${DATE} +%m -d "${todays_date}"`
DD_today=`${DATE} +%d -d "${todays_date}"`
HH_today=`${DATE} +%H -d "${todays_date}"`
# End day variables
YYYY_endday=`${DATE} +%Y -d "${END_TIME}"`
MM_endday=`${DATE} +%m -d "${END_TIME}"`
DD_endday=`${DATE} +%d -d "${END_TIME}"`
HH_endday=`${DATE} +%H -d "${END_TIME}"`
#
# start/end time is for yesterday, day N-1
start_time_yaml=${YYYY_today}-${MM_today}-${DD_today}-00:00:00
end_time_yaml=${YYYY_endday}-${MM_endday}-${DD_endday}-00:00:00
#
workdir=${WORKDIR}
mkdir -p ${workdir}
cd ${workdir}
#
scriptsdir=${SCRIPTS_DIR} #/home/role.rap-chem/scripts/melodies-monet_offline

output_directory=${PLOT_OUTPUT_DIR}
mkdir -p ${output_directory}

# Remove any old files
rm -rf tmp_list_*

cp ${namelist} ./monet_namelist
file="./monet_namelist"
    listmodeloutputdir=()
    listmodel=()
    listtype=()
    listnt=()   # use for init/run length/relative day/color/marker/linestyle
    listmodvar=()
    listobsvar=()
    listspecies=() 
    list_PM10=()
    list_PM25=()
    list_TEMP=()
    list_CO=()
    list_NO2=()
    list_AOD550=()
    list_OZONE=()
    list_PRECIP=()
    list_temp=()
    list_dew_pt_temp=()
    list_wdir=()
    list_ws=()
    list_precip_1hr=()
    list_vsb=()
    list_ceiling=()
    echo "Here is what the namelist input:"
sed 1d $file | while IFS=, read -r model_name model_type init run_len relative_day color marker linestyle rawspecies modelstmpdir
do
    ###############################################################
    listmodel+=($model_name)
    ###############################################################
    listtype+=($model_type)
    ###############################################################
    nt=$(echo "$init" | awk -F "/" '{print NF-1}')
    ntime=$(echo "$nt" \+ 1 | bc)
    listnt+=($ntime)
    #echo "$ntime"
    listinit=()
    for ((i=1; i<=ntime; i++)); do 
    subinit=$(echo $init| cut -d'/' -f $i)
    listinit+=($subinit)
    done
    ###############################################################
    listrunlen=()
    for ((i=1; i<=ntime; i++)); do 
    subrun_len=$(echo $run_len| cut -d'/' -f $i)
    listrunlen+=($subrun_len)
    done
    ###############################################################
    listrelday=()
    for ((i=1; i<=ntime; i++)); do 
    subrel_day=$(echo $relative_day| cut -d'/' -f $i)
    listrelday+=($subrel_day)
    done
    ###############################################################
    listcolor=()
    for ((i=1; i<=ntime; i++)); do 
    subcolor=$(echo $color| cut -d'/' -f $i)
    listcolor+=($subcolor)
    done
    ###############################################################
    listmarker=()
    for ((i=1; i<=ntime; i++)); do 
    submarker=$(echo $marker| cut -d'/' -f $i)
    listmarker+=($submarker)
    done
    ###############################################################
    listlinestyle=()
    for ((i=1; i<=ntime; i++)); do 
    sublinestyle=$(echo $linestyle| cut -d'/' -f $i)
    listlinestyle+=($sublinestyle)
    done
    ###############################################################
    allspecies=$(echo "$rawspecies" | awk '{gsub(/:/,"/")}1')
    ns=$(echo "$allspecies" | awk -F "/" '{print NF-1}')
    nspecies2=$(echo "$ns" \+ 1 | bc)
    nspecies=$(echo "$nspecies2" \/ 2 | bc)
    listspecies+=($nspecies)
    for ((i=2; i<=nspecies2; i+=2)); do
    iminus=$(expr $i - 1)
    submodvar=$(echo $allspecies| cut -d'/' -f $iminus)
    listmodvar+=($submodvar)
    subobsvar=$(echo $allspecies| cut -d'/' -f $i)
    listobsvar+=($subobsvar)
    listmodeloutputdir+=($modelstmpdir)
    if [[ "${subobsvar}" == "PM10" ]]; then
      list_PM10=($ntime $model_name $model_type ${listinit[*]} ${listrunlen[*]} ${listrelday[*]} ${listcolor[*]} ${listmarker[*]} ${listlinestyle[*]} $submodvar $subobsvar $modelstmpdir)
      echo  ${list_PM10[*]} >> tmp_list_PM10
    elif [[ "${subobsvar}" == "PM2.5" ]]; then
      list_PM25=($ntime $model_name $model_type ${listinit[*]} ${listrunlen[*]} ${listrelday[*]} ${listcolor[*]} ${listmarker[*]} ${listlinestyle[*]} $submodvar $subobsvar $modelstmpdir)
      echo  ${list_PM25[*]} >> tmp_list_PM25
    elif [[ "${subobsvar}" == "NO2" ]]; then
      list_NO2=($ntime $model_name $model_type ${listinit[*]} ${listrunlen[*]} ${listrelday[*]} ${listcolor[*]} ${listmarker[*]} ${listlinestyle[*]} $submodvar $subobsvar $modelstmpdir)
      echo  ${list_NO2[*]} >> tmp_list_NO2
    elif [[ "${subobsvar}" == "CO" ]]; then
      list_CO=($ntime $model_name $model_type ${listinit[*]} ${listrunlen[*]} ${listrelday[*]} ${listcolor[*]} ${listmarker[*]} ${listlinestyle[*]} $submodvar $subobsvar $modelstmpdir)
      echo  ${list_CO[*]} >> tmp_list_CO
    elif [[ "${subobsvar}" == "TEMP" ]]; then
      list_TEMP=($ntime $model_name $model_type ${listinit[*]} ${listrunlen[*]} ${listrelday[*]} ${listcolor[*]} ${listmarker[*]} ${listlinestyle[*]} $submodvar $subobsvar $modelstmpdir)
      echo  ${list_TEMP[*]} >> tmp_list_TEMP
    elif [[ "${subobsvar}" == "aod_550nm" ]]; then
      list_AOD550=($ntime $model_name $model_type ${listinit[*]} ${listrunlen[*]} ${listrelday[*]} ${listcolor[*]} ${listmarker[*]} ${listlinestyle[*]} $submodvar $subobsvar $modelstmpdir)
      echo  ${list_AOD550[*]} >> tmp_list_AOD550
    elif [[ "${subobsvar}" == "OZONE" ]]; then
      list_OZONE=($ntime $model_name $model_type ${listinit[*]} ${listrunlen[*]} ${listrelday[*]} ${listcolor[*]} ${listmarker[*]} ${listlinestyle[*]} $submodvar $subobsvar $modelstmpdir)
      echo  ${list_OZONE[*]} >> tmp_list_OZONE
    elif [[ "${subobsvar}" == "PRECIP" ]]; then
      list_PRECIP=($ntime $model_name $model_type ${listinit[*]} ${listrunlen[*]} ${listrelday[*]} ${listcolor[*]} ${listmarker[*]} ${listlinestyle[*]} $submodvar $subobsvar $modelstmpdir)
      echo  ${list_PRECIP[*]} >> tmp_list_PRECIP
    elif [[ "${subobsvar}" == "temp" ]]; then
      list_temp=($ntime $model_name $model_type ${listinit[*]} ${listrunlen[*]} ${listrelday[*]} ${listcolor[*]} ${listmarker[*]} ${listlinestyle[*]} $submodvar $subobsvar $modelstmpdir)
      echo ${list_temp[*]} >> tmp_list_temp
    elif [[ "${subobsvar}" == "dew_pt_temp" ]]; then
      list_dew_pt_temp=($ntime $model_name $model_type ${listinit[*]} ${listrunlen[*]} ${listrelday[*]} ${listcolor[*]} ${listmarker[*]} ${listlinestyle[*]} $submodvar $subobsvar $modelstmpdir)
      echo ${list_dew_pt_temp[*]} >> tmp_list_dew_pt_temp
    elif [[ "${subobsvar}" == "wdir" ]]; then
      list_wdir=($ntime $model_name $model_type ${listinit[*]} ${listrunlen[*]} ${listrelday[*]} ${listcolor[*]} ${listmarker[*]} ${listlinestyle[*]} $submodvar $subobsvar $modelstmpdir)
      echo ${list_wdir[*]} >> tmp_list_wdir
    elif [[ "${subobsvar}" == "ws" ]]; then
      list_ws=($ntime $model_name $model_type ${listinit[*]} ${listrunlen[*]} ${listrelday[*]} ${listcolor[*]} ${listmarker[*]} ${listlinestyle[*]} $submodvar $subobsvar $modelstmpdir)
      echo ${list_ws[*]} >> tmp_list_ws
    elif [[ "${subobsvar}" == "precip_1hr" ]]; then
      list_precip_1hr=($ntime $model_name $model_type ${listinit[*]} ${listrunlen[*]} ${listrelday[*]} ${listcolor[*]} ${listmarker[*]} ${listlinestyle[*]} $submodvar $subobsvar $modelstmpdir)
      echo ${list_precip_1hr[*]} >> tmp_list_precip_1hr
    elif [[ "${subobsvar}" == "vsb" ]]; then
      list_vsb=($ntime $model_name $model_type ${listinit[*]} ${listrunlen[*]} ${listrelday[*]} ${listcolor[*]} ${listmarker[*]} ${listlinestyle[*]} $submodvar $subobsvar $modelstmpdir)
      echo ${list_vsb[*]} >> tmp_list_vsb
    elif [[ "${subobsvar}" == "ceiling" ]]; then
      list_ceiling=($ntime $model_name $model_type ${listinit[*]} ${listrunlen[*]} ${listrelday[*]} ${listcolor[*]} ${listmarker[*]} ${listlinestyle[*]} $submodvar $subobsvar $modelstmpdir)
      echo ${list_ceiling[*]} >> tmp_list_ceiling
    else
      echo "ATTENTION!!!!! Need to add this new specie for $model_name : $subobsvar"
    fi

    done
    species=$(echo $rawspecies | tr '/' ' ')
    echo "Model Name: $model_name Model Type: $model_type Initial Times: ${listinit[*]} Run Length: ${listrunlen[*]} Relative Day to Obs: ${listrelday[*]} Marker Color: ${listcolor[*]} Marker Shape: ${listmarker[*]} Line Style: ${listlinestyle[*]} Species: $species"
done #read file

# .. Other namelist
mdl_lw=1.5

airnow_species=("NO2" "CO" "OZONE" "PM10" "PM25" "TEMP")
ish_lite_species=("temp" "dew_pt_temp" "ws" "wdir" "precip_1hr")
ish_species=("vsb" "ceiling")

species=${SPECIES_TO_VERIFY} #${species_list[$is]}
echo "Now working on analysis for $species"
if [[ "${species}" == "PM10" ]]; then
        CONUS=1; R1=1; R2=0; R3=0; R4=1; R5=1; R6=1; R7=1; R8=1; R9=1; R10=1; site=1
        np=9 # Sum of domains
elif [[ "${species}" == "NO2" ]]; then
        CONUS=1; R1=0; R2=1; R3=1; R4=0; R5=1; R6=0; R7=0; R8=0; R9=1; R10=0; site=1
        np=5
elif [[ "${species}" == "CO" ]]; then
        CONUS=1; R1=0; R2=0; R3=1; R4=0; R5=1; R6=0; R7=0; R8=0; R9=1; R10=0; site=1
        np=4
elif [[ "${species}" == "OZONE" ]] ; then
        CONUS=1; R1=1; R2=1; R3=1; R4=1; R5=1; R6=1; R7=1; R8=1; R9=1; R10=1; site=1
        np=11
elif [[ "${species}" == "PM25" ]] ; then
        CONUS=1; R1=1; R2=1; R3=1; R4=1; R5=1; R6=1; R7=1; R8=1; R9=1; R10=1; site=1
        np=11
elif [[ "${species}" == "TEMP" ]] ; then
        CONUS=1; R1=1; R2=1; R3=1; R4=1; R5=1; R6=1; R7=1; R8=1; R9=1; R10=1; site=1
        np=11
elif [[ "${species}" == "AOD550" ]]; then
	CONUS=1; R1=0; R2=0; R3=0; R4=0; R5=0; R6=0; R7=0; R8=0; R9=0; R10=0; site=1
        np=1
elif [[ ${ish_lite_species[*]} =~ "${species}" ]]; then 
        CONUS=1; R1=0; R2=0; R3=0; R4=0; R5=0; R6=0; R7=0; R8=0; R9=0; R10=0; site=1
        np=1
elif [[ ${ish_species[*]} =~ "${species}" ]]; then
        CONUS=1; R1=0; R2=0; R3=0; R4=0; R5=0; R6=0; R7=0; R8=0; R9=0; R10=0; site=1
        np=1
else
	echo "Unknown regions to evaluate for species: ${species}"
	exit 1
fi
##############################################################################
if [[ ${species} == "AOD550" ]]; then
do_stats=1
ts_select_time="'time'"
ln -sf ../test5.aeronet.${todays_date}-${endday_date}.nc test5.nc
if [[ ! -e ${output_directory}/test5.aeronet.${todays_date}-${endday_date}.nc ]]; then
	cp ../test5.aeronet.${todays_date}-${endday_date}.nc ${output_directory}/test5.aeronet.${todays_date}-${endday_date}.nc
fi
fi
# If the species is airnow
if [[ ${airnow_species[*]} =~ "${species}" ]]; then
do_stats=1
ts_select_time="'time'"

ln -sf ../test5.airnow.${todays_date}-${endday_date}.nc test5.nc   #creating symbolic test5.nc file pointing to test5.nc.{todays_date}
if [[ ! -e ${output_directory}/test5.airnow.${todays_date}-${endday_date}.nc ]]; then
        cp ../test5.airnow.${todays_date}-${endday_date}.nc ${output_directory}/test5.airnow.${todays_date}-${endday_date}.nc
fi
fi
#
if [[ ${ish_lite_species[*]} =~ "${species}" ]]; then
do_stats=1
ts_select_time="time"
ln -sf ../test5.ish-lite.${todays_date}-${endday_date}.nc test5.nc
if [[ ! -e ${output_directory}/test5.ish-lite.${todays_date}-${endday_date}.nc ]]; then
        cp ../test5.ish-lite.${todays_date}-${endday_date}.nc ${output_directory}/test5.ish-lite.${todays_date}-${endday_date}.nc
fi
fi
#
if [[ ${ish_species[*]} =~ "${species}" ]]; then
do_stats=1
ts_select_time="time"
ln -sf ../test5.ish.${todays_date}-${endday_date}.nc test5.nc
if [[ ! -e ${output_directory}/test5.ish.${todays_date}-${endday_date}.nc ]]; then
        cp ../test5.ish.${todays_date}-${endday_date}.nc ${output_directory}/test5.ish.${todays_date}-${endday_date}.nc
fi
fi
#
if [[ ${species} == "OZONE" ]]; then
cp ../cur_aqi_ozone.${todays_date}-${endday_date}.kml ${output_directory}/cur_aqi_ozone.${todays_date}-${endday_date}.kml
cp ../cur_aqi_combined.${todays_date}-${endday_date}.kml ${output_directory}/cur_aqi_combined.${todays_date}-${endday_date}.kml
fi
if [[ ${species} == "PM25" ]]; then
cp ../cur_aqi_pm25.${todays_date}-${endday_date}.kml ${output_directory}/cur_aqi_pm25.${todays_date}-${endday_date}.kml
cp ../hms_fire_${YYYY_today}${MM_today}${DD_today}-${endday_date}.txt ${output_directory}/hms_fire_${YYYY_today}${MM_today}${DD_today}-${endday_date}.txt
cp ../hms_smoke_${YYYY_today}${MM_today}${DD_today}-${endday_date}.kml ${output_directory}/hms_smoke_${YYYY_today}${MM_today}${DD_today}-${endday_date}.kml
fi
#
# Loop over the plot types, the spatial plots take  a lot of time and may fail 
#ip=0
for ip in $( seq 0 2 )
do
  if [[ ${ip} == 0 ]]; then
  	timeseries=1
  	taylor=0
  	spatial_bias=0
  	spatial_overlay=0
  	boxplot=0
        do_stats_run=${do_stats}
  elif [[ ${ip} == 1 ]]; then
  	timeseries=0
  	taylor=0
  	spatial_bias=0
  	spatial_overlay=1
  	boxplot=0
        do_stats_run=0
  elif [[ ${ip} == 2 ]]; then
  	timeseries=0
  	taylor=0
  	spatial_bias=1
  	spatial_overlay=0
  	boxplot=0
        do_stats_run=0
  elif [[ ${ip} == 3 ]]; then
        timeseries=0
        taylor=0
        spatial_bias=0
        spatial_overlay=0
        boxplot=0
        do_stats_run=${do_stats}
  fi
# Get the list of sites for this species
  if [[ ${site} -eq 1 ]] && [[ ${timeseries} -eq 1 ]]; then
      rm -f sitefile.txt
      rm -f sitefile.txt.${species}.${todays_date}
    if [[ "${species}" == "AOD550" ]]; then
      echo "running site analysis for AOD"
      python ${scriptsdir}/site_analysis_aeronet.py ${species} > sitefile.txt
    elif [[ ${airnow_species[*]} =~ "${species}" ]]; then
      python ${scriptsdir}/site_analysis_airnow.py ${species} > sitefile.txt
    elif [[ ${ish_lite_species[*]} =~ "${species}" ]]; then
      python ${scriptsdir}/site_analysis_ish.py ${species} > sitefile.txt
    elif [[ ${ish_species[*]} =~ "${species}" ]]; then
      python ${scriptsdir}/site_analysis_ish.py ${species} > sitefile.txt
    fi
    readarray -t site_list < sitefile.txt
    nsite=${#site_list[@]}
    mv sitefile.txt sitefile.txt.${species}.${todays_date}
    cp sitefile.txt.${species}.${todays_date} ${output_directory}/sitefile.txt.${species}.${todays_date}
  fi

#build array of region choices to go into control.yaml as RGN_list 
if [ ${CONUS} -eq 1 ]; then
        echo "Adding plots/stats for CONUS"
  if [ ${#rgn_list[@]} -eq 0 ]; then
         rgn_list[0]="'CONUS'"
         rgn_type[0]="'all'"
  else
         rgn_list[ ${#rgn_list[@]} +1 ]=",'CONUS'"
         rgn_type[ ${#rgn_type[@]} +1 ]=",'all'"
  fi
fi

if [[ ${airnow_species[*]} =~ "${species}" ]]; then # No EPA Regions for AERONET
for r in $(seq 1 10)
do
        key="R${r}"
        eval do_region='$'$key
        if [ ${do_region} -eq 1 ]; then
        if [ ${#rgn_list[@]} -eq 0 ]; then
         rgn_list[0]="'$key'"
         rgn_type[0]="'epa_region'"
         else
         rgn_list[ ${#rgn_list[@]} +1 ]=",'$key'"
         rgn_type[ ${#rgn_type[@]} +1 ]=",'epa_region'"
        fi
        fi
done
fi
#add the sites to the region list
if [[ ${site} -eq 1 ]] && [[ ${timeseries} -eq 1 ]];  then
        echo "Adding plots/stats for sites"
        if [[ "${species}" == "AOD550" ]] || [[ ${ish_species[*]} =~ "${species}" ]] || [[ ${ish_lite_species[*]} =~ "${species}" ]]; then
        echo "adding sites for AOD"
        for isite in $( seq 0 $(((${nsite} - 1))) )
        do
                 echo "site_list[isite] = ${site_list[$isite]}"
                 echo "rgn_list[@] = ${#rgn_list[@]}"
                 if [ ${#rgn_list[@]} -eq 0 ]; then
                        rgn_list[0]="${site_list[$isite]}"
                        rgn_type[0]="'siteid'"
                 else
                        rgn_list[ ${#rgn_list[@]} +1 ]=",${site_list[$isite]}"
                        rgn_type[ ${#rgn_type[@]} +1 ]=",'siteid'"
                 fi
        done
        elif [[ ${airnow_species[*]} =~ "${species}" ]]; then
        for isite in $( seq 0 $(((${nsite} - 1))) )
        do
                 if [ ${#rgn_list[@]} -eq 0 ]; then
                         rgn_list[0]="${site_list[$isite]}"
                         rgn_type[0]="'site'"
                else
                        # echo ${site_list[$isite]} 
                        rgn_list[ ${#rgn_list[@]} +1 ]=",${site_list[$isite]}"
                        rgn_type[ ${#rgn_type[@]} +1 ]=",'site'"
                 fi
        done
        fi
fi

####################################################
####################################################
####################################################
####################################################
####################################################
# ... Construct the control.yaml file/namelist
echo "Bulding control.yaml"
rm -f tmp.control.yaml.${species}.${todays_date}
cat << EOF > tmp.control.yaml.${species}.${todays_date}
analysis:
  start_time: '${start_time_yaml}'
  end_time:  '${end_time_yaml}'    #UTC #
  output_dir: ${output_directory}
  debug: True
EOF

# Now insert which models
cat << EOF >> tmp.control.yaml.${species}.${todays_date}
model:
EOF
####################################################
####################################################
####################################################
####################################################
####################################################
####################################################
#echo "Including regions: ${rgn_list[*]}"
knt=0

bb="tmp_list_${species}"
#KYecho "${bb}"
#ncol=()
#let c=0
#while read -r myLine; do 
#  a=($myLine) 
#  #echo "Line $((++c)) has ${#a[*]} columns"
#  nrow=$((++c))
#  ncol+=(${#a[*]})
#done < $bb
#echo ${ncol[*]}
#echo ${ncol[0]}
#echo ${ncol[1]}
#echo ${nrow[*]}

#for ((j=1; j<=${nrow[*]}; j++)); do
#echo "j= ${j}"
#fieldlist=()
#for ((i=1; i<=${ncol[j-1]}; i++)); do
#done
for ((i=1; i<=30; i++)); do
fieldlist+="f"${i}" "
done


#####f1=Number of Initial Time (Fixed)
#####f2=Model Name (Fixed)
#####f3=Model Type (Fixed)
#####f4-f30=Initial Time,Run Length,Relative Time,Color,Marker,Linestyle,model specie name,obs specie name
while IFS=" " read -r $fieldlist
do
##########
for ((j=1; j<=$f1; j++)); do
n1=$(echo "$j" \+ 3 | bc)
#echo "n1 ${n1}"
initime=$(eval "echo f${n1}")
#echo "initime $initime"
#echo "f1=$f1"
n2=$(echo "$f1" \+ $n1 | bc)
#echo $n2
runlen=$(eval "echo f${n2}")
#echo "runlen $runlen"
#echo "runlen ${!runlen}"
n3=$(echo "$f1" \+ $n2 | bc)
#echo $n3
relday=$(eval "echo f${n3}")
#echo "relday $relday"
n4=$(echo "$f1" \+ $n3 | bc)
#echo $n4
color2=$(eval "echo f${n4}")
#echo "color2 $color2 ${!color2}"
n5=$(echo "$f1" \+ $n4 | bc)
#echo $n5
marker2=$(eval "echo f${n5}")
#echo "marker2 $marker2 ${!marker2}"
n6=$(echo "$f1" \+ $n5 | bc)
#echo $n6
linestyle2=$(eval "echo f${n6}")
#echo "linestyle2 $linestyle2 ${!linestyle2}"
#n7=$(echo "$f1" \+ $n6 | bc)
n7=$(echo "$f1" \* 6 \+ 4 | bc)
#echo $n7
modname=$(eval "echo f${n7}")
#echo "modname $modname ${!modname}"
n8=$(echo $n7 \+ 1 | bc)
#n8=$(echo "$f1" \+ $n7 | bc)
#echo $n8
obsname=$(eval "echo f${n8}")
#echo "obsname $obsname ${!obsname}"
n9=$(echo $n8 \+ 1 | bc)
modeloutputdir=$(eval "echo f${n9}")
echo "modeloutputdir $modeloutputdir ${!modeloutputdir}"
echo "listmodeloutputdir $listmodeloutputdir ${!listmodeloutputdir}"
 
YYYY=`${DATE} +%Y -d "${todays_date}"` # - ${!relday} days"`
MM=`${DATE} +%m -d "${todays_date}"`   # - ${!relday} days"`
DD=`${DATE} +%d -d "${todays_date}"`   # - ${!relday} days"`
start_time=${YYYY}${MM}${DD}${!initime}  

datadir=${f12}/monet
testfile=${datadir}/*.nc
echo "data directory is " $datadir
echo "model files are " $testfile
######## Check for data
has_data=1
nfiles=`ls ${testfile} | wc -l`
if [[ ${nfiles} == 0 ]]; then
   echo "NO MODEL FILES!!!"
   exit 1
fi


################
#... Create model list arrays dependent on model choices 
if [[ ${has_data} -eq 1 ]]; then    # if data exists
mdl_list[0]=${f2}"_"${YYYY}${MM}${DD}"-"${!initime}"Z"
# .. Observations ..
#for now we just have airnow, can create namelist and code similar to above to add in more i.e balloon soundings 
if [[ ${species} == "AOD550" ]]; then
      obs_list[0]="aeronet"
elif [[ ${airnow_species[*]} =~ "${species}" ]]; then
      obs_list[0]="airnow"
elif [[ ${ish_species[*]} =~ "${species}" ]]; then
      obs_list[0]="ish"
elif [[ ${ish_lite_species[*]} =~ "${species}" ]]; then
      obs_list[0]="ish_lite"
fi
#
# check that at least one model is being used 
if [ ${#mdl_list[@]} -eq 0 ]; then
        echo "Model list empty, please choose at least one (e.g., rapchem)"
        exit 1
elif [ ${#obs_list[@]} -eq 0 ]; then
        echo "Obs list empty, please choose at least one (e.g., airnow)"
        exit 1
else
        echo "Proceeding using the obs: ${obs_list[*]} and models ${mdl_list[*]}"
fi
#
# Construct obs-mdl pair for plot groups
for iobs in ${obs_list[@]}; do
for imdl in ${mdl_list[@]}; do
        if [ $knt -eq 0 ]; then
                obs_mdl_list[knt++]="'${iobs}_${imdl}'"
        else
                obs_mdl_list[knt++]=",'${iobs}_${imdl}'"
        fi
done
done
###################################################################################################################
cat << EOF >> tmp.control.yaml.${species}.${todays_date}
  ${mdl_list}: # model label
    files: ${testfile}
    mod_type: '${f3}'
EOF
# Model specific options
if [[ ${f3} == "hrrr" ]] || [[ ${f3} == "wrfchem" ]];then
cat << EOF >> tmp.control.yaml.${species}.${todays_date}
    mod_kwargs:
      surf_only_nc: True
EOF
fi
if [[ ${f3} == "rrfs" ]] || [[ ${f3} == 'raqms' ]]; then
cat << EOF >> tmp.control.yaml.${species}.${todays_date}
    mod_kwargs:
      surf_only: True
EOF
fi
#if [[ ${f3} == "rrfs" ]]; then
#cat << EOF >> tmp.control.yaml.${species}.${todays_date}
#      convert_pm25: False
#EOF
#fi
cat << EOF >> tmp.control.yaml.${species}.${todays_date}
    radius_of_influence: 12000 #meters
    mapping: #model species name : obs species name
EOF
if [[ "${species}" == "AOD550" ]]; then
cat << EOF >> tmp.control.yaml.${species}.${todays_date}
      aeronet:
EOF
elif [[ ${airnow_species[*]} =~ "${species}" ]]; then
cat << EOF >> tmp.control.yaml.${species}.${todays_date}
      airnow:
EOF
elif [[ ${ish_species[*]} =~ "${species}" ]]; then
cat << EOF >> tmp.control.yaml.${species}.${todays_date}
      ish:
EOF
elif [[ ${ish_lite_species[*]} =~ "${species}" ]]; then
cat << EOF >> tmp.control.yaml.${species}.${todays_date}
      ish_lite:
EOF
fi
cat << EOF >> tmp.control.yaml.${species}.${todays_date}
        ${!modname}: "${!obsname}"
    projection: None
    plot_kwargs: #Opt
      color: '${!color2}'
      marker: '${!marker2}'
      linestyle: '${!linestyle2}'
      markersize: 8
      linewidth: ${mdl_lw}
EOF

fi # end if data exist 
done #end j loop
done < ${bb} #end read loop
###################################################################################################################
#Observation Types 
if [[ ${species} == "AOD550" ]]; then
cat << EOF >> tmp.control.yaml.${species}.${todays_date}

obs:
  aeronet: # obs label
    filename: test5.nc
    obs_type: pt_sfc
    variables: #Opt 
EOF
elif [[ ${airnow_species[*]} =~ "${species}" ]]; then
cat << EOF >> tmp.control.yaml.${species}.${todays_date}

obs:
  airnow: # obs label
    use_airnow: True
    filename: test5.nc
    obs_type: pt_sfc
    variables: #Opt
EOF
elif [[ ${ish_species[*]} =~ "${species}" ]]; then
cat << EOF >> tmp.control.yaml.${species}.${todays_date}

obs:
  ish: # obs label
    filename: test5.nc
    obs_type: pt_sfc
    variables: #Opt
EOF
elif [[ ${ish_lite_species[*]} =~ "${species}" ]]; then
cat << EOF >> tmp.control.yaml.${species}.${todays_date}

obs:
  ish_lite: # obs label
    filename: test5.nc
    obs_type: pt_sfc
    variables: #Opt
EOF
fi
###################################################################################################################
if [[ ${species} == "AOD550" ]]; then
cat << EOF >> tmp.control.yaml.${species}.${todays_date}
      aod_550nm:
        unit_scale: 1
        unit_scale_method: '*' # Multiply = '*' , Add = '+', subtract = '-', divide = '/'
        nan_value: -1.0 # Set this value to NaN
        ylabel_plot: 'Aeronet 550nm AOD' #Optional to set ylabel so can include units and/or instr etc.
        vmin_plot: 0.0 #Opt Min for y-axis during plotting. To apply to a plot, change restrict_yaxis = True.
        vmax_plot: 1.0 #Opt Max for y-axis during plotting. To apply to a plot, change restrict_yaxis = True.
        vdiff_plot: 0.2 #Opt +/- range to use in bias plots. To apply to a plot, change restrict_yaxis = True.
        nlevels_plot: 23 #Opt number of levels used in colorbar for contourf plot.
EOF
fi
###################################################################################################################
if [[ "${species}" == "PM25" ]]; then
vmin="0.0"; vmax="100.0";
cat << EOF >> tmp.control.yaml.${species}.${todays_date}

      PM2.5:
        unit_scale: 1
        unit_scale_method: '*' # Multiply = '*' , Add = '+', subtract = '-', divide = '/'
        nan_value: -1.0 # Set this value to NaN
        #The obs_min, obs_max, and nan_values are set to NaN first and then the unit conversion is applied.
        ylabel_plot: 'PM2.5 (ug/m3)' #Optional to set ylabel so can include units and/or instr etc.
        ty_scale: 2.0 #Opt
        vmin_plot: ${vmin} #Opt Min for y-axis during plotting. To apply to a plot, change restrict_yaxis = True.
        vmax_plot: ${vmax} #Opt Max for y-axis during plotting. To apply to a plot, change restrict_yaxis = True.
        vdiff_plot: 15.0 #Opt +/- range to use in bias plots. To apply to a plot, change restrict_yaxis = True.
        nlevels_plot: 21 #Opt number of levels used in colorbar for contourf plot.

EOF
fi
###################################################################################################################
if [[ "${species}" == "PM10" ]]; then
cat << EOF >> tmp.control.yaml.${species}.${todays_date}

      PM10:
        unit_scale: 1
        unit_scale_method: '*' # Multiply = '*' , Add = '+', subtract = '-', divide = '/'
        #obs_min: 0 # set all values less than this value to NaN
        #obs_max: 100 # set all values greater than this value to NaN
        nan_value: -1.0 # Set this value to NaN
        #The obs_min, obs_max, and nan_values are set to NaN first and then the unit conversion is applied.
        ylabel_plot: 'PM10 (ug/m3)' #Optional to set ylabel so can include units and/or instr etc.
        ty_scale: 2.0 #Opt
        vmin_plot: ${vmin} #Opt Min for y-axis during plotting. To apply to a plot, change restrict_yaxis = True.
        vmax_plot: ${vmax} #Opt Max for y-axis during plotting. To apply to a plot, change restrict_yaxis = True.
        vdiff_plot: 15.0 #Opt +/- range to use in bias plots. To apply to a plot, change restrict_yaxis = True.
        nlevels_plot: 21 #Opt number of levels used in colorbar for contourf plot.

EOF
fi
###################################################################################################################
if [[ "${species}" == "OZONE" ]]; then
cat << EOF >> tmp.control.yaml.${species}.${todays_date}

      OZONE:
        unit_scale: 1 #Opt Scaling factor 
        unit_scale_method: '*' #Opt Multiply = '*' , Add = '+', subtract = '-', divide = '/'
        nan_value: -1.0 # Opt Set this value to NaN
        ylabel_plot: 'Ozone (ppbv)'
        vmin_plot: 15.0 #Opt Min for y-axis during plotting. To apply to a plot, change restrict_yaxis = True.
        vmax_plot: 55.0 #Opt Max for y-axis during plotting. To apply to a plot, change restrict_yaxis = True.
        vdiff_plot: 20.0 #Opt +/- range to use in bias plots. To apply to a plot, change restrict_yaxis = True.
        nlevels_plot: 21 #Opt number of levels used in colorbar for contourf plot.


EOF
fi
###################################################################################################################
if [[ "${species}" == "WS" ]]; then
cat << EOF >> tmp.control.yaml.${species}.${todays_date}

      WS:
        unit_scale: 0.514  # convert obs knots-->m/s
        unit_scale_method: '*'
        obs_min: 0.2 # m/s

EOF
fi
###################################################################################################################
if [[ "${species}" == "PRSFC" ]]; then
cat << EOF >> tmp.control.yaml.${species}.${todays_date}

      PRSFC:
        unit_scale: 0.01  # convert model Pascals-->millibars
        unit_scale_method: '*'

EOF
fi
###################################################################################################################
if [[ "${species}" == "PRECIP" ]]; then
cat << EOF >> tmp.control.yaml.${species}.${todays_date}

      PRECIP:
        unit_scale: 0.1  # convert obs mm-->cm
        unit_scale_method: '*'

EOF
fi
###################################################################################################################
if [[ "${species}" == "TEMP" ]]; then
cat << EOF >> tmp.control.yaml.${species}.${todays_date}

      TEMP:
        #unit_scale: 273.16
        #unit_scale_method: '+'
        nan_value: -1.0
        ylabel_plot: '2-m Temperature (K)'

EOF
fi
###################################################################################################################
if [[ "${species}" == "CO" ]]; then
cat << EOF >> tmp.control.yaml.${species}.${todays_date}

      CO:
        unit_scale: 1000. #Convert from ppmv to ppbv.
        unit_scale_method: '*' # Multiply = '*' , Add = '+', subtract = '-', divide = '/'
        nan_value: -1.0 # Set this value to NaN
        #The obs_min, obs_max, and nan_values are set to NaN first and then the unit conversion is applied.
        ylabel_plot: 'CO (ppbv)' #Optional to set ylabel so can include units and/or instr etc.
        vmin_plot: 50.0 #Opt Min for y-axis during plotting. To apply to a plot, change restrict_yaxis = True.
        vmax_plot: 750.0 #Opt Max for y-axis during plotting. To apply to a plot, change restrict_yaxis = True.
        vdiff_plot: 400.0 #Opt +/- range to use in bias plots. To apply to a plot, change restrict_yaxis = True
        nlevels_plot: 15 #Opt number of levels used in colorbar for contourf plot.

EOF
fi
###################################################################################################################
if [[ "${species}" == "SO2" ]]; then
cat << EOF >> tmp.control.yaml.${species}.${todays_date}

      SO2:
        nan_value: -1.0 # Set this value to NaN
        ylabel_plot: 'SO2 (ppbv)' #Optional to set ylabel so can include units and/or instr etc.

EOF
fi
###################################################################################################################
if [[ "${species}" == "NO" ]]; then
cat << EOF >> tmp.control.yaml.${species}.${todays_date}

      'NO':
        nan_value: -1.0 # Set this value to NaN
        ylabel_plot: 'NO (ppbv)' #Optional to set ylabel so can include units and/or instr etc.
        vmin_plot: 0.0 #Opt Min for y-axis during plotting. To apply to a plot, change restrict_yaxis = True.
        vmax_plot: 20.0 #Opt Max for y-axis during plotting. To apply to a plot, change restrict_yaxis = True.
        vdiff_plot: 15.0 #Opt +/- range to use in bias plots. To apply to a plot, change restrict_yaxis = True.
        nlevels_plot: 21 #Opt number of levels used in colorbar for contourf plot.

EOF
fi
###################################################################################################################
if [[ "${species}" == "NO2" ]]; then
cat << EOF >> tmp.control.yaml.${species}.${todays_date}

      NO2:
        #obs_max: 1 # ppbv
        nan_value: -1.0 # Set this value to NaN
        ylabel_plot: 'NO2 (ppbv)' #Optional to set ylabel so can include units and/or instr etc.
        vmin_plot: 0.0 #Opt Min for y-axis during plotting. To apply to a plot, change restrict_yaxis = True.
        vmax_plot: 20.0 #Opt Max for y-axis during plotting. To apply to a plot, change restrict_yaxis = True.
        vdiff_plot: 15.0 #Opt +/- range to use in bias plots. To apply to a plot, change restrict_yaxis = True.
        nlevels_plot: 21 #Opt number of levels used in colorbar for contourf plot.

EOF
fi
###################################################################################################################
if [[ "${species}" == "ws" ]]; then
cat << EOF >> tmp.control.yaml.${species}.${todays_date}

      ws:
        nan_value: -1.0
        ylabel_plot: 'Wind Speed (m/s)'
EOF
fi
###################################################################################################################
if [[ "${species}" == "wdir" ]]; then
cat << EOF >> tmp.control.yaml.${species}.${todays_date}

      wdir:
        nan_value: -1.0
        ylabel_plot: 'Wind Direction (-)'
EOF
fi
###################################################################################################################
if [[ "${species}" == "temp" ]]; then
cat << EOF >> tmp.control.yaml.${species}.${todays_date}

      temp:
        nan_value: -1.0
        ylabel_plot: 'Temperature (C)'
EOF
fi
###################################################################################################################
if [[ "${species}" == "dew_pt_temp" ]]; then
cat << EOF >> tmp.control.yaml.${species}.${todays_date}

      dew_pt_temp:
        nan_value: -1.0
        ylabel_plot: 'Dew Point Temperature (C)'
EOF
fi
###################################################################################################################
if [[ "${species}" == "precip_1hr" ]]; then
cat << EOF >> tmp.control.yaml.${species}.${todays_date}

      precip_1hr:
        nan_value: -1.0
        ylabel_plot: 'Precipitation (mm)'
EOF
fi
if [[ "${species}" == "vsb" ]]; then
cat << EOF >> tmp.control.yaml.${species}.${todays_date}
      vsb:
        nan_value: -1.0
        obs_min: 0.01
        unit_scale: 0.000625
        unit_scale_method: '*'
        ylabel_plot: 'Visibility (mi)'
EOF
fi
if [[ "${species}" == "ceiling" ]]; then
cat << EOF >> tmp.control.yaml.${species}.${todays_date}
      ceiling:
        nan_value: -1.0
        obs_max: 21999.99
        ylabel_plot: 'Cloud Ceiling (m)'
EOF
fi
###################################################################################################################
#Plot Types
#if [[ ${do_stats_run} -eq 0 ]]; then
cat << EOF >> tmp.control.yaml.${species}.${todays_date}
plots:
EOF
#fi
# Now insert which plot groups  
#Timeseries plot group 1 
if [[ ${timeseries} -eq 1 ]]; then
ndomains=$((${np}+${nsite}))
echo "Now doing timeseries, plot group 1" 
cat << EOF >> tmp.control.yaml.${species}.${todays_date}
  plot_grp1:
    type: 'timeseries' # plot type
    fig_kwargs: #Opt to define figure options
      figsize: [12,6] # figure size if multiple plots
    default_plot_kwargs: # Opt to define defaults for all plots. Model kwargs overwrite these.
      linewidth: 3.0
      markersize: 10.
    text_kwargs: #Opt
      fontsize: 18.
    domain_type: [${rgn_type[@]:0:$ndomains}] #List of domain types: 'all' or any domain in obs file. (e.g., airnow: epa_region, state_name, siteid, etc.)
    domain_name: [${rgn_list[@]:0:$ndomains}] #List of domain names. If domain_type = all domain_name is used in plot title.
    data: [${obs_mdl_list[*]}]
    data_proc:
      rem_obs_nan: False # True: Remove all points where model or obs variable is NaN. False: Remove only points where model variable is NaN.
      ts_select_time: ${ts_select_time} #Time used for avg and plotting: Options: 'time' for UTC or 'time_local'
      ts_avg_window: 'H' # Options: None for no averaging or list pandas resample rule (e.g., 'H', 'D')
      set_axis: False #If select True, add vmin_plot and vmax_plot for each variable in obs.

EOF
fi
#Taylor Plot Group 2 
if [[ ${taylor} -eq 1 ]]; then
echo "Now doing Taylor plot, plot group 2"
cat << EOF >> tmp.control.yaml.${species}.${todays_date}
  plot_grp2:
    type: 'taylor' # plot type
    fig_kwargs: #Opt to define figure options
      figsize: [8,8] # figure size if multiple plots
    default_plot_kwargs: # Opt to define defaults for all plots. Model kwargs overwrite these.
      linewidth: 2.0
      markersize: 10.
    text_kwargs: #Opt
      fontsize: 16.
    domain_type: [${rgn_type[@]:0:$np}] #List of domain types: 'all' or any domain in obs file. (e.g., airnow: epa_region, state_name, siteid, etc.)
    domain_name: [${rgn_list[@]:0:$np}] #List of domain names. If domain_type = all domain_name is used in plot title.
    data: [${obs_mdl_list[*]}]
    data_proc:
      rem_obs_nan: True # True: Remove all points where model or obs variable is NaN. False: Remove only points where model variable is NaN.
      set_axis: True #If select True, add ty_scale for each variable in obs.

EOF
fi
#Spatial Bias Plot Group 3 
if [[ ${spatial_bias} -eq 1 ]]; then
echo "Now doing spatial bias, plot group 3"
cat << EOF >> tmp.control.yaml.${species}.${todays_date}

  plot_grp3:
    type: 'spatial_bias' # plot type
    fig_kwargs: #For all spatial plots, specify map_kwargs here too.
      states: True
      figsize: [10, 5] # figure size 
    text_kwargs: #Opt
      fontsize: 16.
    domain_type: [${rgn_type[@]:0:$np}] #List of domain types: 'all' or any domain in obs file. (e.g., airnow: epa_region, state_name, siteid, etc.) 
    domain_name: [${rgn_list[@]:0:$np}] #List of domain names. If domain_type = all domain_name is used in plot title.
    data: [${obs_mdl_list[*]}]
    data_proc:
      rem_obs_nan: True # True: Remove all points where model or obs variable is NaN. False: Remove only points where model variable is NaN.
      set_axis: True #If select True, add vdiff_plot for each variable in obs.

EOF
fi
#Spatial Overlay 
if [[ ${spatial_overlay} -eq 1 ]]; then
echo "Now doing spatial overlay, plot group 4"
cat << EOF >> tmp.control.yaml.${species}.${todays_date}
  plot_grp4:
    type: 'spatial_overlay' # plot type
    fig_kwargs: #For all spatial plots, specify map_kwargs here too.
      states: True
      figsize: [10, 5] # figure size
    text_kwargs: #Opt
      fontsize: 16.
    domain_type: [${rgn_type[@]:0:$np}] #List of domain types: 'all' or any domain in obs file. (e.g., airnow: epa_region, state_name, siteid, etc.)
    domain_name: [${rgn_list[@]:0:$np}] #List of domain names. If domain_type = all domain_name is used in plot title.
    data: [${obs_mdl_list[*]}]
    data_proc:
      rem_obs_nan: True # True: Remove all points where model or obs variable is NaN. False: Remove only points where model variable is NaN.
      set_axis: True #If select True, add vmin_plot and vmax_plot for each variable in obs.

EOF
fi
#Boxplot Plot Group 5 
if [[ ${boxplot} -eq 1 ]]; then
echo "Now doing boxplot, plot group 5"
cat << EOF >> tmp.control.yaml.${species}.${todays_date}
  plot_grp5:
    type: 'boxplot' # plot type
    fig_kwargs: #Opt to define figure options
      figsize: [8, 6] # figure size 
    text_kwargs: #Opt
      fontsize: 10.
    domain_type: [${rgn_type[@]:0:$np}] #List of domain types: 'all' or any domain in obs file. (e.g., airnow: epa_region, state_name, siteid, etc.) 
    domain_name: [${rgn_list[@]:0:$np}] #List of domain names. If domain_type = all domain_name is used in plot title.
    data: [${obs_mdl_list[*]}]
    data_proc:
      rem_obs_nan: True # True: Remove all points where model or obs variable is NaN. False: Remove only points where model variable is NaN.
      set_axis: False #If select True, add vmin_plot and vmax_plot for each variable in obs.
EOF
fi

if [ ${do_stats_run} -eq 1 ]; then
echo "Adding stats"
cat << EOF >> tmp.control.yaml.${species}.${todays_date}
stats:
  #Stats require positive numbers, so if you want to calculate temperature use Kelvin!
  #Wind direction has special calculations for AirNow if obs name is 'WD'
  #stat_list: ['STDO', 'STDP', 'MdnNB', 'NO','NOP','NP','MO','MP', 'MdnO', 'MdnP', 'RM', 'RMdn', 'MB', 'MdnB', 'NMB', 'NMdnB', 'FB', 'NME', 'R2', 'RMSE', 'IOA', 'AC'] #List stats to calculate. Dictionary of definitions included in plots/proc_stats.py Only stats listed below are currently working.
  stat_list: ['MB','MdnB','NMB','R2','RMSE','NOP']
  #Full calc list ['STDO', 'STDP', 'MdnNB', 'NO','NOP','NP','MO','MP', 'MdnO', 'MdnP', 'RM', 'RMdn', 'MB', 'MdnB', 'NMB', 'NMdnB', 'FB', 'NME', 'R2', 'RMSE', 'IOA', 'AC']
  round_output: 1 #Opt, defaults to rounding to 3rd decimal place.
  output_table: True #Always outputs a .txt file. Optional to also output as a table.
  output_table_kwargs: #Opt For all spatial plots, specify map_kwargs here too.
    figsize: [15, 4] # figure size 
    fontsize: 6.
    xscale: 1.1
    yscale: 1.1
    edges: 'horizontal'
  domain_type: [${rgn_type[@]:0:$np}] #List of domain types: 'all' or any domain in obs file. (e.g., airnow: epa_region, state_name, siteid, etc.) 
  domain_name: [${rgn_list[@]:0:$np}] #List of domain names. If domain_type = all domain_name is used in plot title.
  data: [${obs_mdl_list[*]}] # make this a list of pairs in obs_model where the obs is the obs label and model is the model_label
EOF
fi
# Remove any old control.yaml files and link to the current on
rm -f control.yaml
cp tmp.control.yaml.${species}.${todays_date} ../../control.yaml.${species}.${todays_date}.all_${ip}
ln -sf ../../control.yaml.${species}.${todays_date}.all_${ip} control.yaml
# Unset/delete unnecesary variables
unset obs_mdl_list
unset fieldlist

#this will pair the observational data with model data and generate the chosen plots with chosen models and species
if [[ "${species}" == "AOD550" ]]; then
   python ${scriptsdir}/Monet-analysis-example-plots-wrf-rapchemtest_aeronet.py ${do_stats_run}
elif [[ ${airnow_species[*]} =~ "${species}" ]]; then
   python ${scriptsdir}/Monet-analysis-example-plots-wrf-rapchemtest.py ${do_stats_run}
elif [[ ${ish_lite_species[*]} =~ "${species}" ]]; then
   python ${scriptsdir}/Monet-analysis-example-plots-wrf-rapchemtest_ish-lite.py ${do_stats_run}
elif  [[ ${ish_species[*]} =~ "${species}" ]]; then
   python ${scriptsdir}/Monet-analysis-example-plots-wrf-rapchemtest_ish.py ${do_stats_run}
fi

done #end ip loop
#done #end is loop
