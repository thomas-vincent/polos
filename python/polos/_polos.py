""" 
Draft vesion of the core module. Should be moved to proper module 
folder, outside of script
""" 
import os.path as op
import subprocess
import re
import socket
import warnings
import ntplib
import sys

DT_OVERLAY_RTC_RE = re.compile('^dtoverlay=(.*-rtc).*$', flags=re.MULTILINE)
DATE_ISO_FORMAT_RE = re.compile('\d{4}-[01]\d-[0-3]\d [0-2]\d:[0-5]\d:[0-5]\d\.\d+([+-][0-2]\d:[0-5]\d|Z?)')
DMESG_LOW_BAT_ENTRY_RE = re.compile('^[\][ 0-9]+[.][0-9]+[]].*: (.*low voltage.*RTC.*|.*RTC.*low voltage.*)$', flags=re.MULTILINE)

STATUS_ERROR = 0
STATUS_OK = 1
STATUS_WARNING = 2
STATUS_LABELS = ['ERROR', 'Ok', 'Warning']

def get_local_ip():
    try:
        local_ip = [l for l in ([ip for ip in socket.gethostbyname_ex(socket.gethostname())[2] if not ip.startswith("127.")][:1], [[(s.connect(('8.8.8.8', 53)), s.getsockname()[0], s.close()) for s in [socket.socket(socket.AF_INET, socket.SOCK_DGRAM)]][0][1]]) if l][0][0]
    except Exception as e:
        return (STATUS_ERROR, str(e)) 
    else:
        return (STATUS_OK, local_ip)
    
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

def is_fake_hwclock_removed():
    # TODO: check that files are absent: /etc/cron.hourly/fake-hwclock,
    #                                    /etc/init.d/fake-hwclock
    
    cmd = ['systemctl', 'is-enabled', 'fake-hwclock']
    sys_output = subprocess.run(cmd, stderr=subprocess.PIPE,
                                stdout=subprocess.PIPE)
    output = sys_output.stdout.decode('utf-8')
    if sys_output.returncode:
        err_msg = sys_output.stderr.decode('utf-8')
        if err_msg.startswith('Failed to get unit') or \
           output.strip() == 'masked':
            status = STATUS_OK
            status_msg = 'fake-hwclock service not present'                
        else:
            status = STATUS_ERROR
            status_msg = 'Cannot check service fake-hwclock. Error: ' + \
                         err_msg + 'Output: ' + output
    else:
        if 'disabled' in output:
            status = STATUS_WARNING
            status_msg = 'fake-hwclock disabled but present. Should be removed.'
        elif 'enabled' in output or 'running' in output:
            status = STATUS_ERROR
            status_msg = 'fake-hwclock enabled or running. Should be removed.'
        else:
            status = STATUS_ERROR
            status_msg = 'Cannot check service fake-hwclock'
            
    return status, status_msg

def is_systemd_ntp_disabled():
    tdc_output = subprocess.run('timedatectl', stderr=subprocess.PIPE,
                                stdout=subprocess.PIPE)
    
    if tdc_output.returncode:
        status = STATUS_ERROR
        status_msg = 'Failed to query timedatectl. Error: ' + \
                     tdc_output.stderr.decode('utf-8')
    else:
        output = tdc_output.stdout.decode('utf-8')
        if 'systemd-timesyncd.service active: no' in output or \
           'NTP service: inactive' in output:
            status = STATUS_OK
            status_msg = 'NTP from systemd service inactive'
        else:
            status = STATUS_ERROR
            status_msg = 'NTP service from systemd should be disabled'
    return status, status_msg    

def ref_time_server_is_ntp_running():
    cmd = ['ntpq', '-p']
    ntpq_output = subprocess.run(cmd, stderr=subprocess.PIPE,
                                 stdout=subprocess.PIPE)
    if ntpq_output.returncode:
        status = STATUS_ERROR
        status_msg = 'Failed to query NTP. Error: ' + \
                     ntpq_output.stderr.decode('utf-8')
    else:
        output = ntpq_output.stdout.decode('utf-8').split('\n')
        if len(output) > 3:
            status = STATUS_WARNING
            status_msg = 'There should only one time source (found %d)' % \
                         len(output)
        source_re = '^(?P<selected>\s|[*])(?P<hostname>.+?)\s+.*$'
        source_match = re.search(source_re, output[2])
        if source_match is None:
            status = STATUS_ERROR
            status_msg = 'Cannot parse source line "%s"' % output[2]
        else:
            selected = source_match.group('selected') == '*'
            hostname = source_match.group('hostname')
            if 'LOCAL' not in hostname:
                status = STATUS_WARNING
                status_msg = 'Using %s, may not be RTC (should be "LOCAL")' % \
                             hostname
            else:
                if selected:
                    status = STATUS_OK
                    status_msg = 'Connected to %s' % hostname
                else:
                    status = STATUS_WARNING
                    status_msg = 'Using %s, but it is not connected' % hostname
    return status, status_msg    

def ref_time_server_is_ntp_synced():
    try:
        ntpst_output = subprocess.run('ntpstat', stderr=subprocess.PIPE,
                                      stdout=subprocess.PIPE)
    except FileNotFoundError:
        return STATUS_ERROR, 'ntpstat command not found'
    
    output = ntpst_output.stdout.decode('utf-8').split('\n')
    if output[0].startswith('unsynchronised'):
        status = STATUS_WARNING
        status_msg = 'system time not synchronised (yet?)' 
    elif output[0].startswith('synchronised to local net'):
        status = STATUS_OK
        status_msg = 'Synchronised to local net'
    elif output[0].startswith('synchronised'):
        status = STATUS_WARNING
        status_msg = 'Synchronised but maybe not to local source: %s' % \
                     output[0]
    else:
        status = STATUS_ERROR
        status_msg = 'Cannot parse output of ntpstat: %s' % \
                     output[0]
    return status, status_msg    

def ref_time_server_is_ntp_queryable(ip=None):
    if ip is None:
        ip_status, ip = get_local_ip()
        if ip_status == STATUS_ERROR or ip_status == STATUS_WARNING:
            return STATUS_ERROR, 'Cannot get local IP to query NTP'
        
    ntp_client = ntplib.NTPClient()
    try: 
        response = ntp_client.request(ip, version=4)    
        if response.offset < 0.001:
            return (STATUS_OK,
                    'Queried NTP at %s\nTime offset=%1.6f s\nNetwork delay=%1.6f s' % \
                    (ip, response.offset, response.delay))
        else:
            return (STATUS_WARNING,
                    'Queried NTP at %s\nLARGE time offset=%1.6f s\nNetwork delay=%1.6f s'%\
                    (ip, response.offset, response.delay))
    except Exception as e:
        return STATUS_ERROR, str(e) 

    
#HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Services\W32Time\TimeProviders\NtpServer
# -> value set as 1 if NTP is enabled

# from winreg import OpenKey
# try:
#     reg_entry = r'HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\DateTime\Servers'
#     kservers = OpenKey(HKEY_CURRENT_USER, reg_entry)
#     t = (EnumValue(n,0))    
# except WindowsError:
#     status_msg = 'Failed to read reg entry %s' reg_entry

# https://docs.microsoft.com/en-us/windows-server/networking/windows-time-service/windows-time-service-tools-and-settings

def client_get_ntp_source_ip():
    if sys.platform.startswith('linux'):
        raise NotImplementedError('Getting NTP source for linux system.')
    elif sys.platform == 'win32':
        w32tm_out = subprocess.run(['w32tm', '/query', '/source'],
                                   stderr=subprocess.PIPE,
                                   stdout=subprocess.PIPE)
        # TODO: Handle when NTP is not running / no source 
        return w32tm_out.stdout.decode('utf-8').strip()
    else:
        raise Exception('Unsupported platform: %s', sys.platform)

def client_is_ntp_running():
    if sys.platform.startswith('linux'):
        raise NotImplementedError('Check NTP running for linux system.')
    elif sys.platform == 'win32':
        w32tm_out = subprocess.run(['w32tm', '/query', '/configuration'],
                                   stderr=subprocess.PIPE,
                                   stdout=subprocess.PIPE)
        config_re = 'NtpClient (Local)\n.*?\nEnabled: *(?P:<enabled>0|1)' \
                    '.*?Type: *(?P<type>.*?\n'
        cfg_match = re.search(config_re, w32tm_out.stdout.decode('utf-8'))
        enabled = cfg_match.groups('enabled')=='1'
        ntp_type_is_NTP = cfg_match.groups('type')=='NTP'
        if enabled:
            if ntp_type_is_NTP:
                status = STATUS_WARNING
                status_msg = 'NTP client enabled but type is not NTP: %s' % \
                             cfg_match.groups('type')
            else:
                status = STATUS_OK
                status_msg = 'NTP client enabled and type is NTP.'
        else:
            status = STATUS_ERROR
            status_msg = 'NTP client not enabled.'
            
        return status, status_msg    
    else:
        raise Exception('Unsupported platform: %s', sys.platform)    
    
def print_status(intro, status):
    PADDING_INTRO = 22
    PADDING_ST = 7
    SEP = ' -- '
    status_msg_lines = pad_lines(status[1], PADDING_INTRO+len(SEP)+PADDING_ST+1)
    print('%s %s%s%s' %((intro+'...').ljust(PADDING_INTRO), 
                          STATUS_LABELS[status[0]].rjust(PADDING_ST),
                          SEP, status_msg_lines))

def pad_lines(s, padding):
    return s.replace('\n', '\n' + ' ' * padding)

def except_to_status(func):
    def _func(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            return STATUS_ERROR, str(e)
    return _func
