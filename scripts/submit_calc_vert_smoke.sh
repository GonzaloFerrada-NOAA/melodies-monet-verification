#!/bin/bash 

ulimit -s unlimited

source ~/.bashrc
conda deactivate
conda deactivate
conda activate melodies-monet

cd ${WORKDIR}
for iframe in $(seq 0 24)
do
if [[ ${iframe} -lt 10 ]];then
frame="00"${iframe}
else
frame="0"${iframe}
fi
python calc_vertically_integrated_smoke.py ${WORKDIR} ${frame}
done
