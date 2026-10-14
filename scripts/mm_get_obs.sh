#!/bin/bash
#
source /home/Jordan.Schnell/miniconda/bin/activate melodies-monet-develop

module load ncl
#
DATE=/bin/date
#
#Set up the date strings
todays_date=`${DATE} +%Y%m%d -d ${START_TIME}`
endday_date=`${DATE} +%Y%m%d -d ${END_TIME}`
end_date=`${DATE} +%Y%m%d -d ${END_TIME}`

# Today variables 
YYYY_today=`${DATE} +%Y -d "${todays_date}"` # with activation_offset = -24, this brings us to the current time
MM_today=`${DATE} +%m -d "${todays_date}"`
DD_today=`${DATE} +%d -d "${todays_date}"`
HH_today=`${DATE} +%H -d "${todays_date}"`

YYYY_endday=`${DATE} +%Y -d "${END_TIME}"` # with activation_offset = -24, this brings us to the current time
MM_endday=`${DATE} +%m -d "${END_TIME}"`
DD_endday=`${DATE} +%d -d "${END_TIME}"`
HH_endday=`${DATE} +%H -d "${END_TIME}"`

# Dates also need to be for yesterday
start_time_reformat=`${DATE} -d "${YYYY_today}${MM_today}${DD_today}" +%Y-%m-%d`
end_time_reformat=`${DATE} -d "${YYYY_endday}${MM_endday}${DD_endday}" +%Y-%m-%d`
end_time_reformat_openaq=`${DATE} -d "${YYYY_endday}${MM_endday}${DD_endday} + 23 hours" "+%Y-%m-%d %H:00"`

workdir=${WORKDIR}
mkdir -p ${workdir}
cd ${workdir}

scriptsdir=${SCRIPTS_DIR} #/home/role.rap-chem/scripts/melodies-monet_offline

if [[ "${OBSTYPE}" == "AERONET" ]]; then
melodies-monet get-aeronet -s ${start_time_reformat} -e ${end_time_reformat} --interp-to '0.55' 
outfile=AERONET_L15_${YYYY_today}${MM_today}${DD_today}_${YYYY_endday}${MM_endday}${DD_endday}.nc
if [[ -e ${outfile} ]]; then
  mv ${outfile} test5.aeronet.${todays_date}-${endday_date}.nc
  cp ${scriptsdir}/make_westoreastof97_aeronet.ncl .
  ncl make_westoreastof97_aeronet.ncl
else
  echo "test5.nc is missing for aeronet!"
  exit 1
fi
fi

if [[ "${OBSTYPE}" == "AIRNOW" ]]; then
melodies-monet get-airnow -s ${start_time_reformat} -e ${end_time_reformat}
outfile=AirNow_${YYYY_today}${MM_today}${DD_today}_${YYYY_endday}${MM_endday}${DD_endday}.nc
if [[ -e ${outfile} ]]; then
  mv ${outfile} test5.airnow.${todays_date}-${endday_date}.nc   #moving the test5.nc generated from reformat to test5.nc.{todays_date}
  cp ${scriptsdir}/make_westoreastof97_airnow.ncl .
  ncl make_westoreastof97_airnow.ncl
else
  echo "test5.nc is missing for airnow!"
  exit 1
fi
python ${scriptsdir}/get_airnow_kml.py

if [[ -e cur_aqi_ozone.kml ]]; then
   mv cur_aqi_ozone.kml cur_aqi_ozone.${todays_date}-${endday_date}.kml
else
  echo "Did not download ozone AQI data!"
fi
if [[ -e cur_aqi_pm25.kml ]]; then
   mv cur_aqi_pm25.kml cur_aqi_pm25.${todays_date}-${endday_date}.kml
else
  echo "Did not download PM.25 AQI data!"
fi
if [[ -e cur_aqi_combined.kml ]]; then
   mv cur_aqi_combined.kml cur_aqi_combined.${todays_date}-${endday_date}.kml
else
  echo "Did not download combined AQI data!"
fi
fi

if [[ "${OBSTYPE}" == "AQS" ]]; then	
echo "getting aqs"
melodies-monet get-aqs -s ${start_time_reformat} -e ${end_time_reformat}
outfile=AQS_${YYYY_today}${MM_today}${DD_today}_${YYYY_endday}${MM_endday}${DD_endday}.nc
if [[ -e ${outfile} ]]; then
  mv ${outfile} test5.aqs.${todays_date}-${endday_date}.nc   #moving the test5.nc generated from reformat to test5.nc.{todays_date}
  cp ${scriptsdir}/make_westoreastof97_aqs.ncl .
  ncl make_westoreastof97_aqs.ncl
else
  echo "test5.nc is missing for aqs!"
  exit 1
fi
fi
#


if [[ "${OBSTYPE}" == "ISH-LITE" ]]; then
melodies-monet get-ish-lite -s ${start_time_reformat} -e ${end_time_reformat} --box 0 -140 90 -50 --verbose --debug --num-workers=13
outfile=ISH-Lite_${todays_date}_${endday_date}.nc
if [[ -e ${outfile} ]]; then
   echo "obs file created for ISH/ISD"
   mv ${outfile} test5.ish-lite.${todays_date}-${endday_date}.nc
   cp ${scriptsdir}/make_westoreastof97_ish-lite.ncl .
   ncl make_westoreastof97_ish-lite.ncl
else
   echo "test5 is missing for ISH!"
   exit 1
fi
fi
#
#
# TODO.. openAQ
#melodies-monet get-openaq -s ${start_time_reformat} -e ${end_time_reformat_openaq} --verbose --debug --num-workers=13

if [[ "${OBSTYPE}" == "ISH" ]]; then
melodies-monet get-ish -s ${start_time_reformat} -e ${end_time_reformat} --box 0 -140 90 -50 --verbose --debug --num-workers=13
outfile=ISH_${todays_date}_${endday_date}.nc
if [[ -e ${outfile} ]]; then
   echo "obs file created for ISH/ISD"
   mv ${outfile} test5.ish.${todays_date}-${endday_date}.nc
   cp ${scriptsdir}/make_westoreastof97_ish-lite.ncl .
   ncl make_westoreastof97_ish-lite.ncl
else
   echo "test5 is missing for ISH!"
   exit 1
fi
fi

if [[ "${OBSTYPE}" == "HMS" ]]; then
## Get the HMS smoke polygons and fire locations
wget https://satepsanone.nesdis.noaa.gov/pub/FIRE/web/HMS/Fire_Points/Text/${YYYY_today}/${MM_today}/hms_fire${todays_date}.txt
if [[ -e hms_fire${todays_date}.txt ]]; then
   mv hms_fire${todays_date}.txt hms_fire_${todays_date}-${endday_date}.txt 
else
    echo "HMS Fire locations are unavailable"
fi
wget https://satepsanone.nesdis.noaa.gov/pub/FIRE/web/HMS/Smoke_Polygons/KML/${YYYY_today}/${MM_today}/hms_smoke${YYYY_today}${MM_today}${DD_today}.kml
if [[ -e hms_smoke${YYYY_today}${MM_today}${DD_today}.kml ]]; then
   mv hms_smoke${YYYY_today}${MM_today}${DD_today}.kml hms_smoke_${YYYY_today}${MM_today}${DD_today}.kml
else
   echo "HMS Smoke polygons are unavailable"
fi
fi

exit 0
