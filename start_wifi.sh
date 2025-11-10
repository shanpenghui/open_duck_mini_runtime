
#!/bin/bash

rfkill unblock wifi

sleep 2

ip link set wlan0 up

sleep 2

wpa_supplicant -B -i wlan0 -c /etc/wpa_supplicant/wpa_supplicant.conf

sleep 1

dhclient wlan0

exit 0

