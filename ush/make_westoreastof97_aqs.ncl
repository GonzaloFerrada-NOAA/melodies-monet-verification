begin
;
START_TIME=getenv("START_TIME")
END_TIME=getenv("END_TIME")
;
f=addfile("test5.aqs."+START_TIME+"-"+END_TIME+".nc","w")
;
siteid=f->siteid
longitude=f->longitude
;
westoreastof97=siteid
;
westoreastof97 = where(longitude .le. -97.0,"west","east")
;
f->westoreastof97=westoreastof97
;
end
