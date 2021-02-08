#!/bin/bash

function dt_now {
    date "+%d.%m.%Y %H:%M:%S"
}

fname="/var/www/html/piaware-status.txt"

#cnt=3
#while [ $cnt -gt 0 ]
#do
    title="Status from: $(dt_now)\n---"

    echo -e $title > $fname
    piaware-status >> $fname
    echo "---" >> $fname

    #((cnt--))
    #sleep 5
#done
