""" 
Core module.
""" 
import os.path as op
import subprocess
import re
import socket
import warnings
import ntplib
import sys

import configparser
from io import StringIO

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

def linux_get_ntp_sources():
    cmd = ['ntpq', '-p']
    ntpq_output = subprocess.run(cmd, stderr=subprocess.PIPE,
                                 stdout=subprocess.PIPE)

    i_selected_source = None
    sources = []
    
    if ntpq_output.returncode:
        raise Exception('Failed to query NTP. Error: ' + \
                        ntpq_output.stderr.decode('utf-8'))
    else:
        output = ntpq_output.stdout.decode('utf-8').split('\n')
        source_re = '^(?P<selected>\s|[*])(?P<hostname>.+?)\s+.*$'
        for iline, line in enumerate([l for l in output[2:] if len(l)>0]):
            source_match = re.search(source_re, line)
            if source_match is None:
                warnings.warn('Cannot parse ntpq report line: %s' % line)
            else:
                sources.append(source_match.group('hostname'))
                if source_match.group('selected') == '*':
                    i_selected_source = iline
                    
    return sources, i_selected_source

def ref_time_server_is_ntp_running():
    try:
        sources, i_selected = linux_get_ntp_sources()
    except Exception as e:
        status = STATUS_ERROR
        status_msg = e.message
    
    src_local = [(isrc, src) for (isrc, src) in enumerate(sources) \
                 if 'LOCAL' in src]
    if len(src_local) == 1:
        if src_local[0][0]==i_selected:
            status = STATUS_OK
            status_msg = 'Connected to %s' % src_local[0][1]
        else:
            status = STATUS_ERROR
            status_msg = '%s is listed but not connected' % src_local[0][1]
    elif len(src_local) == 0:
            status = STATUS_ERROR
            status_msg = 'RTC (LOCAL) source not found'
            if len(sources) > 1:
                status_msg += ' but multiple non-RTC sources listed: %s' \
                              ', '.join(sources) + \
                              '. There should be only one LOCAL source listed.'
            else:
                status_msg += '.'
    else: # more than one LOCAL source
        isrc_local_sel = [isrc for src, isrc in src_local if isrc==i_selected]
        if len(isrc_local_sel)==1:
            status = STATUS_WARNING
            status_msg = 'Connected to %s, but other LOCAL sources found. ' \
                         'There should be only one LOCAL source.' % \
                         src_local[isrc_local_sel][0]
        else:
            status = STATUS_ERROR
            status_msg = 'Multiple LOCAL sources listed but connected ' \
                         'to none: %s' ', '.join([s for s,i in src_local])
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

def is_ntp_queryable(ip=None):
    if ip is None:
        ip_status, ip = get_local_ip()
        if ip_status == STATUS_ERROR or ip_status == STATUS_WARNING:
            return STATUS_ERROR, 'Cannot get local IP to query NTP'
        
    if ip == '':
        return STATUS_ERROR, 'No NTP source found'
    
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
        
        sources, i_selected = linux_get_ntp_sources()        
        if i_selected is None:
            return ''
        else:
            return sources[i_selected]
        
    elif sys.platform == 'win32':
        w32tm_out = subprocess.run(['w32tm', '/query', '/source'],
                                   stderr=subprocess.PIPE,
                                   stdout=subprocess.PIPE)
        # TODO: Handle when NTP is not running / no source 
        return w32tm_out.stdout.decode('utf-8').strip()
    else:
        raise Exception('Unsupported platform: %s', sys.platform)

def client_is_ntp_running():

    status = STATUS_ERROR
    status_msg = 'Cannot check if NTP is running.'
    
    if sys.platform.startswith('linux'):
        sources, i_selected = linux_get_ntp_sources()
        if len(sources) == 1 and i_selected is not None:
            status = STATUS_OK
            status_msg = 'NTP client connected to %s.' % (sources[i_selected])
        elif len(sources) > 1 and i_selected is not None:
            status = STATUS_WARNING
            status_msg = 'NTP client connected to %s but other sources ' \
                         'listed (%s). There should be only one.' % \
                         (sources[i_selected],
                          ', '.join([s for s in sources \
                                     if s!=sources[i_selected]]))
    elif sys.platform == 'win32':
        w32tm_out = subprocess.run(['w32tm', '/query', '/configuration'],
                                   stderr=subprocess.PIPE,
                                   stdout=subprocess.PIPE)
        # config_re = r'.*NtpClient (Local)\s+.*?\sEnabled:\s*(?P<enabled>0|1).*?Type:\s*(?P<type>.*?)(?:\n|\r).*'
        # config_re = r'^.*Ntp.*$^Enabled:\s*(?P<enabled>0|1)\s*$.*'
        w32tm_stdout = w32tm_out.stdout.decode('utf-8')
        # print('w32tm_stdout: ', w32tm_stdout) 
        # cfg_match = re.search(config_re, w32tm_stdout, flags=re.MULTILINE)
        
        w32tm_stdout = w32tm_stdout[:w32tm_stdout.index('NtpServer')].replace('NtpClient (Local)', '')
        w32tm_cfg = configparser.ConfigParser()
        w32tm_cfg.readfp(StringIO(w32tm_stdout))
        
        # print('w32tm_cfg: ', w32tm_cfg)
        
        if 0:
            enabled = cfg_match.groups('enabled')=='1'
            ntp_type_is_NTP = cfg_match.groups('type')=='NTP'
        else:
            enabled = w32tm_cfg.get('TimeProviders', 'Enabled').strip().startswith('1')
            ntp_type_is_NTP = 'NTP' in w32tm_cfg.get('TimeProviders', 'Type')
		        
        if enabled:
            if not ntp_type_is_NTP:
                status = STATUS_WARNING
                status_msg = 'NTP client enabled but type is not NTP: %s' % \
                             w32tm_cfg.get('TimeProviders', 'Type')
            else:
                status = STATUS_OK
                status_msg = 'NTP client enabled and type is NTP.'
        else:
            status = STATUS_ERROR
            status_msg = 'NTP client not enabled.'
    else:
        raise Exception('Unsupported platform: %s', sys.platform)    

    return status, status_msg

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
