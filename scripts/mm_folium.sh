#!/bin/bash --login

module load imagemagick

DATE=/bin/date

#source /mnt/lfs4/BMC/rtwbl/melodies-monet/miniconda3/bin/activate monet
source /scratch1/BMC/wrf-chem/Jordan/miniconda3/bin/activate melodies-monet

todays_date=${START_TIME}

# Today variables 
YYYY_today=`${DATE} +%Y -d "${todays_date}"`
MM_today=`${DATE} +%m -d "${todays_date}"`
DD_today=`${DATE} +%d -d "${todays_date}"`
HH_today=`${DATE} +%H -d "${todays_date}"`

YYYY_endday=`${DATE} +%Y -d "${END_TIME}"`
MM_endday=`${DATE} +%m -d "${END_TIME}"`
DD_endday=`${DATE} +%d -d "${END_TIME}"`
HH_endday=`${DATE} +%H -d "${END_TIME}"`

# Dates also need to be for yesterday
start_time_reformat=`${DATE} -d "${YYYY_today}${MM_today}${DD_today}" +%Y-%m-%d`
end_time_reformat=`${DATE} -d "${YYYY_endday}${MM_endday}${DD_endday}" +%Y-%m-%d`

output_directory=${PLOT_OUTPUT_DIR}
scriptsdir=${SCRIPTS_DIR} #/home/role.rap-chem/scripts/melodies-monet_offline

cd ${output_directory}
# Remove any old plots
cp ${scriptsdir}/folium_site_map.py .

#aqi_types=(Good Moderate UnhealthySG Unhealthy VeryUnhealthy Hazardous)
# Split up KML files into their individual PlaceMarks
csplit -ksf pm25_aqi. cur_aqi_pm25.${todays_date}.kml /\<Placemark\>/ "{500}" 2>/dev/null
#csplit -ksf ozone_aqi. cur_aqi_ozone.${todays_date}.kml /\<Placemark\>/ "{500}" 2>/dev/null
#csplit -ksf combined_aqi. cur_aqi_combined.${todays_date}.kml /\<Placemark\>/ "{500}" 2>/dev/null
#
# Convert smoke polygons to JSON
csplit -k hms_smoke_${todays_date}.kml /\<Placemark\>/ "{500}" 2>/dev/null
nfiles=`ls xx* | wc -l`
nfiles1=$((${nfiles}-1))
for file in xx*
do
if [[ ${file} == "xx00" ]]; then
continue
else
#Look for smoke type
grep "Light" ${file}
if [[ $? == 0 ]]; then
str="Light"
fi
grep "Medium" ${file}
if [[ $? == 0 ]]; then
str="Medium"
fi
grep "Heavy" ${file}
if [[ $? == 0 ]]; then
str="Heavy"
fi
# add header
cat xx00 ${file} > $$.tmp && mv $$.tmp ${str}.${file}
if [[ ${file} != "xx${nfiles1}" ]]; then
# Add footer
echo "</Folder></Document></kml>" >> ${str}.${file}
fi
# Convert to json
k2g ${str}.${file} ./
# Rename
mv style.json ${str}.hms_smoke.${file}.json
fi
done
#
#
#
nfiles=`ls pm25_aqi* | wc -l`
nfiles1=$((${nfiles}-1))
# Loop over the files, rename based on AQI type and convert each to Geojson
for file in pm25_aqi*
do
if [[ ${file} == "pm25_aqi.00" ]]; then
continue
else
# Look for aqi type
grep "Good<" ${file}
if [[ $? == 0 ]]; then
str="Good"
fi
grep "Moderate<" ${file}
if [[ $? == 0 ]]; then
str="Moderate"
fi
grep "UnhealthySG<" ${file}
if [[ $? == 0 ]]; then
str="UnhealthySG"
fi
grep "Unhealthy<" ${file}
if [[ $? == 0 ]]; then
str="Unhealthy"
fi
grep "Very Unhealthy<" ${file}
if [[ $? == 0 ]]; then
str="VeryUnhealthy"
fi
grep "Hazardous<" ${file}
if [[ $? == 0 ]]; then
str="Hazardous"
fi
# add header
cat pm25_aqi.00 ${file} > $$.tmp && mv $$.tmp ${str}.${file}
if [[ ${file} != "pm25_aqi.${nfiles1}" ]]; then
# Add footer
echo "</Folder></Document></kml>" >> ${str}.${file}
fi
# Convert to json
k2g ${str}.${file} ./
# Rename
mv style.json ${str}.${file}.json
fi
done
#
nfiles=`ls ozone_aqi* | wc -l`
nfiles1=$((${nfiles}-1))
if [[ ${nfiles} -gt 1 ]]; then
for file in ozone_aqi*
do
if [[ ${file} == "ozone_aqi.00" ]]; then
continue
else
# Look for aqi type
grep "Good<" ${file}
if [[ $? == 0 ]]; then
str="Good"
fi
grep "Moderate<" ${file}
if [[ $? == 0 ]]; then
str="Moderate"
fi
grep "UnhealthySG<" ${file}
if [[ $? == 0 ]]; then
str="UnhealthySG"
fi
grep "Unhealthy<" ${file}
if [[ $? == 0 ]]; then
str="Unhealthy"
fi
grep "Very Unhealthy<" ${file}
if [[ $? == 0 ]]; then
str="VeryUnhealthy"
fi
grep "Hazardous<" ${file}
if [[ $? == 0 ]]; then
str="Hazardous"
fi
# add header
cat ozone_aqi.00 ${file} > $$.tmp && mv $$.tmp ${str}.${file}
if [[ ${file} != "ozone_aqi.${nfiles1}" ]]; then
# Add footer
echo "</Folder></Document></kml>" >> ${str}.${file}
fi
# Convert to json
k2g ${str}.${file} ./
# Rename
mv style.json ${str}.${file}.json
fi
done
#
#
nfiles=`ls combined_aqi* | wc -l`
nfiles1=$((${nfiles}-1))
for file in combined_aqi*
do
if [[ ${file} == "combined_aqi.00" ]]; then
continue
else
# Look for aqi type
grep "Good" ${file}
if [[ $? == 0 ]]; then
str="Good"
fi
grep "Moderate<" ${file}
if [[ $? == 0 ]]; then
str="Moderate"
fi
grep "UnhealthySG<" ${file}
if [[ $? == 0 ]]; then
str="UnhealthySG"
fi
grep "Unhealthy<" ${file}
if [[ $? == 0 ]]; then
str="Unhealthy"
fi
grep "Very Unhealthy<" ${file}
if [[ $? == 0 ]]; then
str="Very Unhealthy"
fi
grep "Hazardous<" ${file}
if [[ $? == 0 ]]; then
str="Hazardous"
fi
# add header
cat combined_aqi.00 ${file} > $$.tmp && mv $$.tmp ${str}.${file}
if [[ ${file} != "combined_aqi.${nfiles1}" ]]; then
# Add footer
echo "</Folder></Document></kml>" >> ${str}.${file}
fi
# Convert to json
k2g ${str}.${file} ./
# Rename
mv style.json ${str}.${file}.json
fi
done
fi # nfiles > 1
# ADD CODE FOR HMS FIRE AND SMOKE

python folium_site_map.py ${start_time_reformat} ${end_time_reformat} ${todays_date} ${END_TIME}
