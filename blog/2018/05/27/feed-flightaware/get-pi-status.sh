#!/bin/bash

function dt_now {
    date "+%d.%m.%Y %H:%M:%S"
}

fname="/var/www/html/pi-status.txt"

title="Status from: $(dt_now)\n---"

echo -e $title > $fname

# CPU temperature
echo "CPU temperature: $(cat /sys/class/thermal/thermal_zone*/temp)" >> $fname
echo -e >> $fname

# CPU workload
ps -Ao user,comm,pcpu --sort=-pcpu | head -n 6 >> $fname
echo -e "" >> $fname

# Free RAM
free -m >> $fname

echo "---" >> $fname
