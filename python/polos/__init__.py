from ._polos import STATUS_ERROR, STATUS_OK, STATUS_WARNING, STATUS_LABELS
from ._polos import get_rtc_battery_status, get_rtc_run_status, get_rtc_installation_status, get_rtc_dtoverlay_id
from ._polos import is_fake_hwclock_removed, is_systemd_ntp_disabled, ref_time_server_is_ntp_running, ref_time_server_is_ntp_synced, ref_time_server_is_ntp_queryable
from ._polos import client_get_ntp_source_ip, client_is_ntp_running

from ._polos import get_local_ip, print_status
