#!/usr/bin/env python3
"""
See documentation on how to assemble hardware and setup the reference time server.
This script will run several checks to ensure that the server is functional.
"""
import sys
import os

import warnings 
if 'SUDO_USER' in os.environ and \
  not os.environ['HOME'].strip(os.sep).endswith(os.environ['SUDO_USER']):
    warnings.warn('User environment may not be preserved.\n\n' \
                  '========================================================\n' \
                  '  If there are import errors, consider using "sudo -E"  \n' \
                  '========================================================\n')

import polos
from polos import print_status
from sys import platform
if sys.platform != "linux" and platform != "linux2":
    print('This script is only compatible with linux')
    sys.exit(1)

if sys.version_info[0] < 3:
    raise Exception('Python 3 or newer is required.')

def run_as_root():
    return os.getuid() == 0
    
if not run_as_root():
    print('This script must be run as root')
    sys.exit(1)
            
if __name__=='__main__':
    print_status('RTC installation', polos.get_rtc_installation_status())
    print_status('RTC running', polos.get_rtc_run_status())
    print_status('RTC battery', polos.get_rtc_battery_status())
    print_status('Local IP', polos.get_local_ip())
    print_status('fake-hwclock absent', polos.is_fake_hwclock_removed())
    print_status('Systemd-NTP off', polos.is_systemd_ntp_disabled())
    print_status('NTP running', polos.ref_time_server_is_ntp_running())
    print_status('NTP sync', polos.ref_time_server_is_ntp_synced())
    print_status('NTP query', polos.ref_time_server_is_ntp_queryable())
