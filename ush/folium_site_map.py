#!/usr/bin/env python
# coding: utf-8


import folium
import base64
from folium import IFrame
from PIL import Image
import numpy as np
import pandas as pd
import xarray as xr
import sys
import glob
import os.path
from folium import plugins
import subprocess


def convert_to_webp(input_path, output_path, quality=80):
	try:
		with Image.open(input_path) as img:
			# Convert to RGB if the image has an alpha channel for WebP conversion
			if img.mode == 'RGBA':
				img = img.convert('RGB')
			img.save(output_path, 'webp', quality=quality)
		print(f"Successfully converted {input_path} to {output_path}")
	except Exception as e:
		print(f"Error converting {input_path}: {e}")


popups = list()
lats   = list() #np.empty(0)
lons   = list() #np.empty(0)
coords = []
ipopup = 0
mkr1 = list() # list of the individual markers
mkrc = list() # list of the individual markers for the marker cluster (in case we need to access them separately)
#
#path to plots
path = './'
# Styles for the AQI contours
style_mod = {'fillColor': '#FFFF00', 'lineColor': '#FFFF00', 'color' : '#FFFF00'}
style_uns = {'fillColor': '#ff7e00', 'lineColor': '#ff7e00', 'color' : '#ff7e00'}
style_unh = {'fillColor': '#FF0000', 'lineColor': '#FF0000', 'color' : '#FF0000'}
style_vuh = {'fillColor': '#8f3f97', 'lineColor': '#8f3f97', 'color' : '#8f3f97'}
style_haz = {'fillColor': '#7e0023', 'lineColor': '#7e0023', 'color' : '#7e0023'}
style_hms_light = {'fillColor': '#D3D3D3', 'lineColor': '#D3D3D3', 'color' : '#D3D3D3'}
style_hms_medium = {'fillColor': '#A9A9A9', 'lineColor': '#A9A9A9', 'color' : '#A9A9A9'}
style_hms_heavy = {'fillColor': '#6A6A6A', 'lineColor': '#6A6A6A', 'color' : '#6A6A6A'}
markerClusterOptions = {'chunkedLoading': True}
#defining the resolution, width, and height of the popup
resolution, width, height = 45, 11, 6 # original
marker_radius=8
#defining plot type
plot_type = "grp1.timeseries"
#import variables from the bash file
start_date=sys.argv[1]
end_date=sys.argv[2]
todays_date=sys.argv[3]
endday_date=sys.argv[4]
#species_list = ['PM25' , 'AOD550', 'ws', 'temp','PM10','vsb']
species_list = ['PM25', 'OZONE', 'PM10', 'CO', 'NO2', 'AOD550', 'temp', 'dew_pt_temp', 'ws', 'wdir', 'precip_1hr','vsb','HMS','ceiling']
color_list   = ['purple', 'blue', 'red', 'green' , 'yellow', 'orange', 'cyan', 'blueviolet', 'limegreen', 'maroon', 'gray', 'sienna','magenta','goldenrod']
network_list = ['EPA AirNOW', 'EPA AirNOW','EPA AirNOW','EPA AirNOW','EPA AirNOW','EPA AirNOW','NASA AERONET','NOAA ISD','NOAA ISD','NOAA ISD','NOAA ISD','NOAA ISD','NOAA_ISD','NOAA_ISD']
#species_list = ['temp', 'ws', 'precip_1hr']
#color_list   = ['cyan', 'limegreen', 'gray']
#network_list = ['NOAA ISD','NOAA ISD','NOAA ISD']
airnow_species = ['PM2.5', 'OZONE', 'PM10', 'CO', 'NO2']
ish_species = ['vsb','ceiling']
ish_dict = dict()
ish_lite_species = ['temp', 'dew_pt_temp', 'ws', 'wdir', 'precip_1hr']
ish_lite_dict = dict()
fmap_dict = dict()
# First create the BASE folium map centered over the US
fmap = folium.Map(location=[40, -95],zoom_start=4,min_zoom=3,control=False,chunkedLoading=True)
# Next create the feature groups that will hold the AIRNOW and ISH-LITE PLOTS
fmap_airnow = folium.FeatureGroup(name='EPA AirNOW AQI',overlay=True,control=True,show=True,chunkedLoading=True)
fmap_hms = folium.FeatureGroup(name='NOAA HMS',overlay=True,control=True,show=False)
# Now create the marker cluster group (not a feature group or featuregroupsubgroup)
fmap_marker_cluster = folium.plugins.MarkerCluster(name='Station Decluster Tool (Experimental)',control=False,show=True,overlay=True,options=markerClusterOptions) #disableClusterAtZoom=16
# Add the Feature Groups and the Marker Cluster Groups as children to the main map
fmap.add_child(fmap_airnow)
fmap.add_child(fmap_hms)
#fmap.add_child(fmap_ish_lite)
for s in range(len(species_list)):
	print("now working on "+species_list[s])
	species=species_list[s]
	#
	# Create feature groups for each species
	if species == "PM25":
		# Check to see if files exists before creating the layer
		if len(glob.glob('plot_grp1.timeseries.PM2.5*')) < 12:
			print("not enough files present")
			continue
		else:
			fmap_pm25_aqi = folium.plugins.FeatureGroupSubGroup(fmap_airnow,name='PM2.5 AQI',overlay=True,control=True,show=True)
			if 'EPA AirNOW' in fmap_dict:
				fmap_dict['EPA AirNOW'].append(fmap_pm25_aqi)
			else:
				fmap_dict['EPA AirNOW'] = [fmap_pm25_aqi]
			for filename in glob.glob(path + 'Moderate.pm25*json'):
				fmap_pm25_aqi.add_child(folium.GeoJson(data=(open(filename,"r",encoding="utf-8-sig")).read(),style_function=lambda x:style_mod))
			for filename in glob.glob(path + 'UnhealthySG.pm25*json'):
				fmap_pm25_aqi.add_child(folium.GeoJson(data=(open(filename,"r",encoding="utf-8-sig")).read(),style_function=lambda x:style_uns))
			for filename in glob.glob(path + 'Unhealthy.pm25*json'):
				fmap_pm25_aqi.add_child(folium.GeoJson(data=(open(filename,"r",encoding="utf-8-sig")).read(),style_function=lambda x:style_unh))
			for filename in glob.glob(path + 'VeryUnhealthy.pm25*json'):
				fmap_pm25_aqi.add_child(folium.GeoJson(data=(open(filename,"r",encoding="utf-8-sig")).read(),style_function=lambda x:style_vuh))
			for filename in glob.glob(path + 'Hazardous.pm25*json'):
				fmap_pm25_aqi.add_child(folium.GeoJson(data=(open(filename,"r",encoding="utf-8-sig")).read(),style_function=lambda x:style_haz))
			fmap_pm25 = folium.plugins.FeatureGroupSubGroup(fmap_marker_cluster,name='PM2.5',overlay=True,control=True,show=True)
			if 'EPA AirNOW' in fmap_dict:
				fmap_dict['EPA AirNOW'].append(fmap_pm25)
			else:
				fmap_dict['EPA AirNOW'] = [fmap_pm25]
	if species == 'HMS':
		if os.path.exists("hms_fire_"+todays_date+".txt") and os.path.exists("hms_smoke_"+todays_date+".kml"):
			fmap_hms_smoke = folium.plugins.FeatureGroupSubGroup(fmap_hms,name='HMS Smoke Polygons',overlay=True,control=True,show=False)
			if 'NOAA HMS' in fmap_dict:
				fmap_dict['NOAA HMS'].append(fmap_hms_smoke)
			else:
				fmap_dict['NOAA HMS'] = [fmap_hms_smoke]
			for filename in glob.glob(path + 'Light.hms*json'):
				fmap_hms_smoke.add_child(folium.GeoJson(data=(open(filename,"r",encoding="utf-8-sig")).read(),style_function=lambda x:style_hms_light))
			for filename in glob.glob(path + 'Medium.hms*json'):
				fmap_hms_smoke.add_child(folium.GeoJson(data=(open(filename,"r",encoding="utf-8-sig")).read(),style_function=lambda x:style_hms_medium))
			for filename in glob.glob(path + 'Heavy.hms*json'):
				fmap_hms_smoke.add_child(folium.GeoJson(data=(open(filename,"r",encoding="utf-8-sig")).read(),style_function=lambda x:style_hms_heavy))
			fmap_hms_fire = folium.plugins.FeatureGroupSubGroup(fmap_hms,name='HMS Fire Locations',overlay=True,control=True,show=False)
			if 'NOAA HMS' in fmap_dict:
				fmap_dict['NOAA HMS'].append(fmap_hms_fire)
			else:
				fmap_dict['NOAA HMS'] = [fmap_hms_fire]
	if species == "OZONE":
		if len(glob.glob('plot_grp1.timeseries.OZONE*')) < 12:
			continue
		else:
			#fmap_ozone_aqi = folium.plugins.FeatureGroupSubGroup(fmap_airnow,name='Ozone AQI',overlay=True,control=True,show=True)
			#if 'EPA AirNOW' in fmap_dict:
			#	fmap_dict['EPA AirNOW'].append(fmap_ozone_aqi)
			#else:
			#	fmap_dict['EPA AirNOW'] = [fmap_ozone_aqi]
			#for filename in glob.glob(path + 'Moderate.ozone*json'):
			#	fmap_ozone_aqi.add_child(folium.GeoJson(data=(open(filename,"r",encoding="utf-8-sig")).read(),style_function=lambda x:style_mod))
			#for filename in glob.glob(path + 'UnhealthySG.ozone*json'):
			#	fmap_ozone_aqi.add_child(folium.GeoJson(data=(open(filename,"r",encoding="utf-8-sig")).read(),style_function=lambda x:style_uns))
			#for filename in glob.glob(path + 'Unhealthy.ozone*json'):
			#	fmap_ozone_aqi.add_child(folium.GeoJson(data=(open(filename,"r",encoding="utf-8-sig")).read(),style_function=lambda x:style_unh))
			#for filename in glob.glob(path + 'VeryUnhealthy.ozone*json'):
			#	fmap_ozone_aqi.add_child(folium.GeoJson(data=(open(filename,"r",encoding="utf-8-sig")).read(),style_function=lambda x:style_vuh))
			#for filename in glob.glob(path + 'Hazardous.ozone*json'):
			#	fmap_ozone_aqi.add_child(folium.GeoJson(data=(open(filename,"r",encoding="utf-8-sig")).read(),style_function=lambda x:style_haz))
			#fmap_combined_aqi = folium.plugins.FeatureGroupSubGroup(fmap_airnow,name='Ozone + PM2.5 AQI',overlay=True,control=True,show=True)
			#if 'EPA AirNOW' in fmap_dict:
			#	fmap_dict['EPA AirNOW'].append(fmap_combined_aqi)
			#else:
			#	fmap_dict['EPA AirNOW'] = [fmap_ozone_aqi]
			#for filename in glob.glob(path + 'Moderate.combined*json'):
			#	fmap_combined_aqi.add_child(folium.GeoJson(data=(open(filename,"r",encoding="utf-8-sig")).read(),style_function=lambda x:style_mod))
			#for filename in glob.glob(path + 'UnhealthySG.combined*json'):
			#	fmap_combined_aqi.add_child(folium.GeoJson(data=(open(filename,"r",encoding="utf-8-sig")).read(),style_function=lambda x:style_uns))
			#for filename in glob.glob(path + 'Unhealthy.combined*json'):
			#	fmap_combined_aqi.add_child(folium.GeoJson(data=(open(filename,"r",encoding="utf-8-sig")).read(),style_function=lambda x:style_unh))
			#for filename in glob.glob(path + 'VeryUnhealthy.combined*json'):
			#	fmap_combined_aqi.add_child(folium.GeoJson(data=(open(filename,"r",encoding="utf-8-sig")).read(),style_function=lambda x:style_vuh))
			#for filename in glob.glob(path + 'Hazardous.combined*json'):
			#	fmap_combined_aqi.add_child(folium.GeoJson(data=(open(filename,"r",encoding="utf-8-sig")).read(),style_function=lambda x:style_haz))
			fmap_ozone = folium.plugins.FeatureGroupSubGroup(fmap_marker_cluster,name='Ozone',overlay=True,control=True,show=True)
			if 'EPA AirNOW' in fmap_dict:
				fmap_dict['EPA AirNOW'].append(fmap_ozone)
			else:
				fmap_dict['EPA AirNOW'] = [fmap_ozone]
	if species == "PM10":
		if len(glob.glob('plot_grp1.timeseries.PM10*')) < 12:
			continue
		else:
			fmap_pm10 = folium.plugins.FeatureGroupSubGroup(fmap_marker_cluster,name='PM10',overlay=True,control=True,show=True)
			if 'EPA AirNOW' in fmap_dict:
				fmap_dict['EPA AirNOW'].append(fmap_pm10)
			else:
				fmap_dict['EPA AirNOW'] = [fmap_pm10]
	if species == "CO":
		if len(glob.glob('plot_grp1.timeseries.CO*')) < 12:
			continue
		else:
			fmap_co = folium.plugins.FeatureGroupSubGroup(fmap_marker_cluster,name='CO',overlay=True,control=True,show=True)
			if 'EPA AirNOW' in fmap_dict:
				fmap_dict['EPA AirNOW'].append(fmap_co)
			else:
				fmap_dict['EPA AirNOW'] = [fmap_co]
	if species == "NO2":
		if len(glob.glob('plot_grp1.timeseries.NO2*')) < 12:
			continue
		else:
			fmap_no2 = folium.plugins.FeatureGroupSubGroup(fmap_marker_cluster,name='NO2',overlay=True,control=True,show=True)
			if 'EPA AirNOW' in fmap_dict:
				fmap_dict['EPA AirNOW'].append(fmap_no2)
			else:
				fmap_dict['EPA AirNOW'] = [fmap_no2]
	if species == "TEMP":
		if len(glob.glob('plot_grp1.timeseries.TEMP*')) < 12:
			continue
		else:
			fmap_TEMP = folium.plugins.FeatureGroupSubGroup(fmap_marker_cluster,name='2-m Temperature',overlay=True,control=True,show=True)
			if 'EPA AirNOW' in fmap_dict:
				fmap_dict['EPA AirNOW'].append(fmap_TEMP)
			else:
				fmap_dict['EPA AirNOW'] = [fmap_TEMP]
	if species == "AOD550":
		if len(glob.glob('plot_grp1.timeseries.aod_550nm*')) < 2:
			continue
		else:
			fmap_aod = folium.plugins.FeatureGroupSubGroup(fmap_marker_cluster,name='AERONET AOD @ 550nm',overlay=True,control=True,show=True)
			if 'NASA AERONET' in fmap_dict:
				fmap_dict['NASA AERONET'].append(fmap_aod)
			else:
				fmap_dict['NASA AERONET'] = [fmap_aod]
	if species == "temp":
		if len(glob.glob('plot_grp1.timeseries.temp*')) < 1:
			continue
		else:
			fmap_temp = folium.plugins.FeatureGroupSubGroup(fmap_marker_cluster,name='2-m Temperature (ISD)',overlay=True,control=True,show=True)
			if 'NOAA ISD' in fmap_dict:
				fmap_dict['NOAA ISD'].append(fmap_temp)
			else:
				fmap_dict['NOAA ISD'] = [fmap_temp]
	if species == "dew_pt_temp":
		if len(glob.glob('plot_grp1.timeseries.dew_pt_temp*')) < 1:
			continue
		else:
			fmap_dpt = folium.plugins.FeatureGroupSubGroup(fmap_marker_cluster,name='Dew Pt. Temp',overlay=True,control=True,show=True)
			if 'NOAA ISD' in fmap_dict:
				fmap_dict['NOAA ISD'].append(fmap_dpt)
			else:
				fmap_dict['NOAA ISD'] = [fmap_dpt]
	if species == "ws":
		if len(glob.glob('plot_grp1.timeseries.ws*')) < 1:
			continue
		else:
			fmap_ws = folium.plugins.FeatureGroupSubGroup(fmap_marker_cluster,name='Wind Speed',overlay=True,control=True,show=True)
			if 'NOAA ISD' in fmap_dict:
				fmap_dict['NOAA ISD'].append(fmap_ws)
			else:
				fmap_dict['NOAA ISD'] = [fmap_ws]
	if species == "wdir":
		if len(glob.glob('plot_grp1.timeseries.wdir*')) < 1:
			continue
		else:
			fmap_wdir = folium.plugins.FeatureGroupSubGroup(fmap_marker_cluster,name='Wind Direction',overlay=False,control=True,show=True)
			if 'NOAA ISD' in fmap_dict:
				fmap_dict['NOAA ISD'].append(fmap_wdir)
			else:
				fmap_dict['NOAA ISD'] = [fmap_wdir]
	if species == "precip_1hr":
		if len(glob.glob('plot_grp1.timeseries.precip_1hr*')) < 1:
			continue
		else:
			fmap_precip1hr = folium.plugins.FeatureGroupSubGroup(fmap_marker_cluster,name='Precip (1hr)',overlay=False,control=True,show=True)
			if 'NOAA ISD' in fmap_dict:
				fmap_dict['NOAA ISD'].append(fmap_precip1hr)
			else:
				fmap_dict['NOAA ISD'] = [fmap_precip1hr]

	if species == "ceiling":
		if len(glob.glob('plot_grp1.timeseries.ceiling*')) < 1:
			continue
		else:
			fmap_ceiling = folium.plugins.FeatureGroupSubGroup(fmap_marker_cluster,name='Cloud Ceiling',overlay=False,control=True,show=True)
			if 'NOAA ISD' in fmap_dict:
				fmap_dict['NOAA ISD'].append(fmap_ceiling)
			else:
				fmap_dict['NOAA ISD'] = [fmap_ceiling]

	if species == "vsb":
		if len(glob.glob('plot_grp1.timeseries.vsb*')) < 1:
			continue
		else:
			fmap_vsb = folium.plugins.FeatureGroupSubGroup(fmap_marker_cluster,name='Visibility',overlay=False,control=True,show=True)
			if 'NOAA ISD' in fmap_dict:
				fmap_dict['NOAA ISD'].append(fmap_vsb)
			else:
				fmap_dict['NOAA ISD'] = [fmap_vsb]

	if species != 'HMS':
		#get the site names from the txt file
		fileObj = open("sitefile.txt."+species+"."+todays_date, "r") #opens the file in read mode
		site_name_list = fileObj.read().splitlines() #puts the file into an array
		fileObj.close()
		##remove the extra characters from either end
		site_name_list = [site[1:-1] for site in site_name_list]
		##replace the spaces in the site names with underscores
		site_name = [site.replace(' ','_') for site in site_name_list]

	#have to change the PM variable name
	if species == 'PM25':
		species = 'PM2.5'
	if species == 'AOD550':
		species = 'aod_550nm'

	#defining arrays for the lat and lon
	##reading in the airnow file
	if species == "aod_550nm":
		data = xr.open_dataset("test5.aeronet."+todays_date+"-"+endday_date+".nc")
		latitude = data['latitude'].values
		longitude = data['longitude'].values
		site = data['siteid'].values
	if str(species) in airnow_species:
		data = xr.open_dataset("test5.airnow."+todays_date+"-"+endday_date+".nc")
		latitude = data['latitude'].values
		longitude = data['longitude'].values
		site = data['site'].values[0]
	if str(species) in ish_lite_species:
		print("adding an ish-lite species")
		data = xr.open_dataset("test5.ish-lite."+todays_date+"-"+endday_date+".nc")
		latitude = data['latitude'].values
		longitude = data['longitude'].values
		site = data['siteid'].values
	if str(species) in ish_species:
		data = xr.open_dataset("test5.ish."+todays_date+"-"+endday_date+".nc")
		latitude = data['latitude'].values
		longitude = data['longitude'].values
		site = data['siteid'].values
	if species == "HMS" and os.path.exists("hms_fire_"+todays_date+".txt"):
		columns = ['Lon','Lat','YearDay','Time','Satellite','Method','Ecosystem','FRP']
		data = pd.read_csv("hms_fire_"+todays_date+".txt",names=columns,skiprows=1,header=None)
		latitude = data['Lat'].values
		longitude = data['Lon'].values
		frp = data['FRP'].values
		sat = data['Satellite'].values
	if species == "HMS" and os.path.exists("hms_fire_"+todays_date+".txt"):
		for i in range(len(latitude)):
			tooltip = folium.Tooltip("FRP = "+str(frp[i]) + " MW, detected by "+str(sat[i]))
			#x = folium.Marker(location=[latitude[i], longitude[i]], icon=folium.Icon(color="red",icon_size=(14, 14),icon='fire-flame-simple',prefix='fa'),tooltip=tooltip)
			x = folium.CircleMarker(location=[latitude[i], longitude[i]], tooltip=tooltip, radius=4,color = 'red', fill_color="red", fill_opacity=0.5)
			fmap_hms_fire.add_child(x)
	else:
		for i in range(len(site_name)):
	
			#boolean to identify the index of each site
			sitename_bool = site == site_name_list[i]
	
             		#use the boolean to select the lat and lon corresponding to each site
			site_lat = latitude[sitename_bool]
			site_lon = longitude[sitename_bool]
			if len(site_lat) == 1: # and site_lat < 65.0 and site_lat > 15.0 and site_lon > -135.0 and site_lon < -55.0:
				#define the plot name
				if species == "aod_550nm":
					plot_name="plot_" + plot_type + "." + species + "." + start_date + "_00." + end_date + "_00.siteid." + site_name_list[i]
				if str(species) in airnow_species:
					plot_name="plot_" + plot_type + "." + species + "." + start_date + "_00." + end_date + "_00.site." + site_name_list[i]
				if str(species) in ish_species:
					plot_name="plot_" + plot_type + "." + species + "." + start_date + "_00." + end_date + "_00.siteid." + site_name_list[i]
					print("working on plot = " + plot_name)
				if str(species) in ish_lite_species:
					plot_name="plot_" + plot_type + "." + species + "." + start_date + "_00." + end_date + "_00.siteid." + site_name_list[i]
				#print(plot_name)	
				#resizing the image to fit the popup window size and saving as another file
				##needs to be in loop
				if os.path.exists(path + plot_name + ".png") and os.path.getsize(path+plot_name + ".png") != 0:
					#image = Image.open(path + plot_name + ".png") #path to original image file
					#image = image.resize(((width*resolution),(height*resolution)),Image.ANTIALIAS)
					#image.save(fp = path + plot_name + "_resized.png") #save back to the original directory? or ne
					png = path + plot_name + ".png"
					png2= path + plot_name + "_resized.png"
					webp= path + plot_name + ".webp"
					if not os.path.exists(png2):
						subprocess.run(['convert',png,'-resize', '495x270',png2])
					if not os.path.exists(webp):
						convert_to_webp(png2, webp)
					#	subprocess.run(['convert',png2,'-quality','100',webp])
					# Encoding it
					encoded = base64.b64encode(open(webp, 'rb').read())
		
					# formating the encoded plot as html
					html = '<img src="data:image/webp;base64,{}">'.format
		
					# decoding the plot into an iframe
					iframe = IFrame(html(encoded.decode('UTF-8')), width=(width*resolution), height=(height*resolution))
		
					#creating a popup of the iframe
					popup = folium.Popup(iframe,sticky=True)
					popups.append(popup)
					#lats.append(site_lat[0])
					#lons.append(site_lon[0])
					ipopup = ipopup + 1
					tooltip = folium.Tooltip(str(site_name[i]))
					#
					if species == "OZONE":
						x = folium.CircleMarker(location=[site_lat[-1], site_lon[-1]], popup=popup, tooltip=tooltip, radius=marker_radius,color = 'blue', fill_color="blue", fill_opacity=0.5)
						fmap_ozone.add_child(x)
					if species == "PM10":
						x = folium.CircleMarker(location=[site_lat[-1], site_lon[-1]], popup=popup, tooltip=tooltip, radius=marker_radius,color = 'red', fill_color="red", fill_opacity=0.5)
						fmap_pm10.add_child(x)
					if species == "CO":
						x = folium.CircleMarker(location=[site_lat[-1], site_lon[-1]], popup=popup, tooltip=tooltip, radius=marker_radius,color = 'green', fill_color="green", fill_opacity=0.5)
						fmap_co.add_child(x)
					if species == "NO2":
						x = folium.CircleMarker(location=[site_lat[-1], site_lon[-1]], popup=popup, tooltip=tooltip, radius=marker_radius,color = 'yellow', fill_color="yellow", fill_opacity=0.5)
						fmap_no2.add_child(x)
					if species == "TEMP":
						x = folium.CircleMarker(location=[site_lat[-1], site_lon[-1]], popup=popup, tooltip=tooltip, radius=marker_radius,color = 'orange', fill_color="orange", fill_opacity=0.5)
						fmap_TEMP.add_child(x)
					if species == "aod_550nm":
						x = folium.CircleMarker(location=[site_lat[-1], site_lon[-1]], popup=popup, tooltip=tooltip, radius=marker_radius,color = 'cyan', fill_color="cyan", fill_opacity=0.5)
						fmap_aod.add_child(x)
					if species == "PM2.5":
						x = folium.CircleMarker(location=[site_lat[-1], site_lon[-1]], popup=popup, tooltip=tooltip, radius=marker_radius,color = 'purple', fill_color="purple", fill_opacity=0.5)
						fmap_pm25.add_child(x)
					if species == "temp":
						x = folium.CircleMarker(location=[site_lat[-1], site_lon[-1]], popup=popup, tooltip=tooltip, radius=marker_radius,color = 'blueviolet', fill_color="blueviolet", fill_opacity=0.5)
						fmap_temp.add_child(x)
					if species == "dew_pt_temp":
						x = folium.CircleMarker(location=[site_lat[-1], site_lon[-1]], popup=popup, tooltip=tooltip, radius=marker_radius,color = 'limegreen', fill_color="limegreen", fill_opacity=0.5)
						fmap_dpt.add_child(x)
					if species == "ws":
						x = folium.CircleMarker(location=[site_lat[-1], site_lon[-1]], popup=popup, tooltip=tooltip, radius=marker_radius,color = 'maroon', fill_color="maroon", fill_opacity=0.5)
						fmap_ws.add_child(x)
					if species == "wdir":
						x = folium.CircleMarker(location=[site_lat[-1], site_lon[-1]], popup=popup, tooltip=tooltip, radius=marker_radius,color = 'gray', fill_color="gray", fill_opacity=0.5)
						fmap_wdir.add_child(x)
					if species == "precip_1hr":
						x = folium.CircleMarker(location=[site_lat[-1], site_lon[-1]], popup=popup, tooltip=tooltip, radius=marker_radius,color = 'sienna', fill_color="sienna", fill_opacity=0.5)
						fmap_precip1hr.add_child(x)
						print("adding a marker for precip_1hr")
						print(x)
						print(site_lat[-1])
						print(site_lon[-1])
					if species == "vsb":
						x = folium.CircleMarker(location=[site_lat[-1], site_lon[-1]], popup=popup, tooltip=tooltip, radius=marker_radius,color = 'magenta', fill_color="magenta", fill_opacity=0.5)
						fmap_vsb.add_child(x)
					if species == "ceiling":
						x = folium.CircleMarker(location=[site_lat[-1], site_lon[-1]], popup=popup, tooltip=tooltip, radius=marker_radius,color = 'goldenrod', fill_color="goldenrod", fill_opacity=0.5)
						fmap_ceiling.add_child(x)
					mkr1.append(x)
fmap.add_child(fmap_marker_cluster)
#if 'fmap_ozone_aqi' in locals():
#	fmap.add_child(fmap_ozone_aqi)
if 'fmap_pm25_aqi' in locals():
	fmap.add_child(fmap_pm25_aqi)
#if 'fmap_combined_aqi' in locals():
#	fmap.add_child(fmap_combined_aqi)
if 'fmap_ozone' in locals():
	fmap.add_child(fmap_ozone)
	fmap.keep_in_front(fmap_ozone)
if 'fmap_temp' in locals():
	fmap.add_child(fmap_temp)
	fmap.keep_in_front(fmap_temp)
if 'fmap_dpt' in locals():
	fmap.add_child(fmap_dpt)
#	fmap.keep_in_front(fmap_dpt)
if 'fmap_ws' in locals():
	fmap.add_child(fmap_ws)
	fmap.keep_in_front(fmap_ws)
if 'fmap_wdir' in locals():
	fmap.add_child(fmap_wdir)
#	fmap.keep_in_front(fmap_wdir)
if 'fmap_precip1hr' in locals():
	fmap.add_child(fmap_precip1hr)
	fmap.keep_in_front(fmap_precip1hr)
if 'fmap_vsb' in locals():
	fmap.add_child(fmap_vsb)
if 'fmap_ceiling' in locals():
	fmap.add_child(fmap_ceiling)
if 'fmap_pm25' in locals():
	fmap.add_child(fmap_pm25)
	fmap.keep_in_front(fmap_pm25)
if 'fmap_pm10' in locals():
	fmap.add_child(fmap_pm10)
	fmap.keep_in_front(fmap_pm10)
if 'fmap_co' in locals():
	fmap.add_child(fmap_co)
	fmap.keep_in_front(fmap_co)
if 'fmap_no2' in locals():
	fmap.add_child(fmap_no2)
	fmap.keep_in_front(fmap_no2)
if 'fmap_TEMP' in locals():
	fmap.add_child(fmap_TEMP)
#	fmap.keep_in_front(fmap_TEMP)
if 'fmap_aod' in locals():
	fmap.add_child(fmap_aod)
#	fmap.keep_in_front(fmap_aod)
#if 'fmap_hms_smoke' in locals():
#	fmap.add_child(fmap_hms_smoke)
#if 'fmap_hms_fire' in locals():
#	fmap.add_child(fmap_hms_fire)
#
#fmap.keep_in_front(fmap_hms)
fmap.keep_in_front(fmap_airnow)
fmap.keep_in_front(fmap_marker_cluster)
# Add the layer controls
folium.plugins.GroupedLayerControl(fmap_dict, exclusive_groups=False).add_to(fmap)
folium.LayerControl().add_to(fmap)
#saving the map to an html file
output_name = "fullhtml_sfc_f000.html"
fmap.save(output_name)
