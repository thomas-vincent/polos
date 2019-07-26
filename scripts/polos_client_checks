#!/usr/bin/env python3

import polos
from polos import print_status

if __name__=='__main__':
    # print_status('Local IP', polos.client_get_ntp_source_ip())
    print_status('NTP running', polos.client_is_ntp_running())
    # print_status('NTP sync', polos.client_is_ntp_synced()) # necessary?
    print_status('NTP query', polos.is_ntp_queryable(polos.client_get_ntp_source_ip()))
