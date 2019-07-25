"""
See documentation on how to assemble and setup the reference time server.
This will run several checks to ensure that the server is functional.
"""
import sys
import os
import subprocess
import re
import socket

DT_OVERLAY_RTC_RE = re.compile('^dtoverlay=(.*-rtc).*$', flags=re.MULTILINE)
DATE_ISO_FORMAT_RE = re.compile('\d{4}-[01]\d-[0-3]\d [0-2]\d:[0-5]\d:[0-5]\d\.\d+([+-][0-2]\d:[0-5]\d|Z?)')
DMESG_LOW_BAT_ENTRY_RE = re.compile('^[\][ 0-9]+[.][0-9]+[]].*: (.*low voltage.*RTC.*|.*RTC.*low voltage.*)$', flags=re.MULTILINE)

STATUS_ERROR = 0
STATUS_OK = 1
STATUS_WARNING = 2
STATUS_LABELS = ['ERROR', 'Ok', 'Warning']

def except_to_status(func):
    def _func(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            return STATUS_ERROR, str(e)
    return _func

def get_local_ip():
    try:
        local_ip = [l for l in ([ip for ip in socket.gethostbyname_ex(socket.gethostname())[2] if not ip.startswith("127.")][:1], [[(s.connect(('8.8.8.8', 53)), s.getsockname()[0], s.close()) for s in [socket.socket(socket.AF_INET, socket.SOCK_DGRAM)]][0][1]]) if l][0][0]
    except Exception as e:
        return (STATUS_ERROR, e.msg) 
    else:
        return (STATUS_OK, local_ip)
    
#@except_to_status
def get_rtc_battery_status():
    cmd = ['dmesg']
    dmesg_output = subprocess.run(cmd, stdout=subprocess.PIPE)
    dmesg_stdout = dmesg_output.stdout.decode('utf-8')
    match_low_bat = DMESG_LOW_BAT_ENTRY_RE.findall(dmesg_stdout)
    if match_low_bat is not None and len(match_low_bat) > 0:
        status_msg = match_low_bat[0]
        status = STATUS_WARNING
    else:
        status = STATUS_OK
        status_msg = 'No low voltage warning'
    return status, status_msg

@except_to_status
def get_rtc_run_status():
    cmd = ['hwclock', '-r']
    hw_clock_output = subprocess.run(cmd, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE)
    hw_clock_stdout = hw_clock_output.stdout.decode('utf-8')
    if len(hw_clock_output.stderr):
        status_msg = 'Error: %s' % hw_clock_output.stderr.decode('utf-8')
        status = STATUS_ERROR
    elif DATE_ISO_FORMAT_RE.match(hw_clock_stdout) is not None:
        status = STATUS_OK
        status_msg = 'RTC time: %s' % hw_clock_stdout.rstrip('\n')
    else:
        status = STATUS_ERROR
        status_msg = 'Invalid RTC time: %s' % hw_clock_stdout
    return status, status_msg

@except_to_status
def get_rtc_installation_status():
    """ 
    Return True if RTC is present in the device tree,
    as indicated by vcdbg.
    Else return False
    
    Supported RTC: RasClock on Raspberry pi
    """
    rtc_id = get_rtc_dtoverlay_id()
    loaded_msg = "Loaded overlay '%s'" % rtc_id
    log_entry_re = '[0-9][.][0-9]+: %s' % loaded_msg
    cmd = ['vcdbg', 'log', 'msg']
    log_output = subprocess.run(cmd, stderr=subprocess.PIPE).stderr.decode('utf-8')
    if len(log_output) == 0:
        status = STATUS_ERROR
        status_msg = 'Empty log from vcdbg'
    elif re.search(log_entry_re, log_output) is not None:
        status = STATUS_OK
        status_msg = 'RTC found as %s' % rtc_id
    else:
        status = STATUS_ERROR
        status_msg = 'Empty log from vcdbg'
    return status, status_msg 

def get_rtc_dtoverlay_id():
    """ Read /boot/config.txt and return the RTC DT identifier """
    rtc_dt_id = None
    with open('/boot/config.txt') as fin:
        re_result = DT_OVERLAY_RTC_RE.findall(fin.read())
        if len(re_result) == 0:
            raise Exception('Cannot find RTC device in /boot/config.txt')
        elif len(re_result) > 1:
            raise Exception('Multiple RTC devices found in /boot/config.txt: %s' % \
                            ', '.join(re_result))
        else:
            rtc_dt_id = re_result[0]
    return rtc_dt_id

def run_as_root():
    return os.getuid() == 0

def print_status(intro, status):
    PADDING_INTRO = 20
    PADDING_ST = 7
    print('%s %s -- %s' %((intro+'...').ljust(PADDING_INTRO), 
                          STATUS_LABELS[status[0]].rjust(PADDING_ST), 
                             status[1]))
if __name__=='__main__':
    if not run_as_root():
        print('This script must be run as root')
        sys.exit(1)
    print_status('RTC installation', get_rtc_installation_status())
    print_status('RTC running', get_rtc_run_status())
    print_status('RTC battery', get_rtc_battery_status())
    print_status('Local IP', get_local_ip())
    # check fake-hwclock.service not present
    # check sudo timedatectl set-ntp false
    # check ntp running
    # check ntp is polling RTC (ntpq -p) 
    # check ntp is synchronized ntpstat
    # check that ntp is reachable from LAN 
