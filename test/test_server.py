import unittest
import time
import socket
import sys
import shutil
import os
import os.path as op
import tempfile
from glob import glob

import logging
logging.basicConfig(stream=sys.stdout)
logger = logging.getLogger('polos')

from multiprocessing import Value
import numpy as np

import polos
from polos.server import SNTPServer, SNTPClient, SyncTriggerClient

# TODO: implement tennis

class SntpTest(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = op.join(tempfile.gettempdir(), 'polos_server_test')
        os.makedirs(self.tmp_dir)
        self.servers = []
        self.to_close = []

        if '-v' in sys.argv:
            logger.setLevel(10)

    def tearDown(self):
        for tc in self.to_close:
            tc.close()

        for server in self.servers:
            print('TearDown: Calling join')
            server.join(timeout=0.2)
            
        print('Teardown complete')

        shutil.rmtree(self.tmp_dir)
        

    def check_status(self, obj, expected_status, expected_message):
        status, message = obj.get_status()
        if status != expected_status:
            raise AssertionError('Got %s, expected %s' % \
                                 (polos.STATUS_LABELS[status],
                                  polos.STATUS_LABELS[expected_status]))
        
        self.assertIn(expected_message.lower(), message.lower())

            
    def test_query_with_callback(self):
        
        class Callback:
            def __init__(self):
                self.nb_calls = 0
                
            def __call__(self):
                self.nb_calls += 1


        call_counter = Callback()

        # use receive_timeout to allow server to check termination status
        # instead of blocking while waiting for request:
        server = SNTPServer(callback=call_counter, receive_timeout=0.5)
        self.servers.append(server)
        self.check_status(server, polos.STATUS_ERROR, 'idle')
        
        server.start()
        
        self.check_status(server, polos.STATUS_WARNING, 'waiting connection')

        client = SNTPClient()
        self.to_close.append(client)
        
        self.check_status(client, polos.STATUS_ERROR, 'not connected')
        client.connect('localhost')
        self.check_status(client, polos.STATUS_WARNING, 'no query')
        time.sleep(0.05) # wait a bit to let server update
        self.check_status(server, polos.STATUS_OK, 'connected')
        # except AssertionError:
        # TODO: enable check_status to check between several given options
        #       here server might be connecting or connected
        #     self.check_status(server, polos.STATUS_WARNING, 'connecting')
        #     time.sleep(0.1)
        #     self.check_status(server, polos.STATUS_OK, 'connected')
            
        nb_request_trials = 10
        client.request(nb_trials=nb_request_trials)
        self.check_status(client, polos.STATUS_OK, '')
        self.check_status(server, polos.STATUS_OK, 'connected')
        self.assertIsNotNone(client.offset)
        self.assertTrue(abs(client.offset) < SNTPClient.CLOCK_OFFSET_TOLERANCE)
        # print('client.offset: ', client.offset)

        client.close()
        self.check_status(client, polos.STATUS_ERROR, 'closed')
        # print('Calling stop on server')
        server.stop()
        time.sleep(0.05) # let server come out of main loop
        self.check_status(server, polos.STATUS_ERROR, 'closed')

        self.assertEqual(call_counter.nb_calls, nb_request_trials)
                
    def test_remote_trigger_emulated(self):
        """
        The goal is to emit two *synchronized* triggers:
          - one local
          - one remote, issued by a distant server
        To do so, the transmission lag to the distant server is taken 
        into account.

        How it works:
        A pingpong server is used to evaluate the round-trip delay.
        A trigger server is used to send the remote trigger.
        The trigger client first calls the pingpong server several times to
        estimate the round-trip delay. Then it calls the trigger server and
        schedule a local trigger within a time equals to half the estimated
        round-trip delay.
        """
        
        class EventDumper:
            def __init__(self, tmp_dir, fn_prefix):
                self.fn_prefix = op.join(tmp_dir, fn_prefix)

            def __call__(self):
                ts = time.time()
                open(self.fn_prefix + '_' + str(ts), 'a').close()

            def get_ts(self):
                ts_fn = glob(self.fn_prefix + '_' + '*')[0]
                return float(op.split(ts_fn)[1].split('_')[1])

        class EventRecorder:
            def __init__(self):
                self.ts = None

            def __call__(self):
                self.ts = time.time()

            def get_ts(self):
                return self.ts
                    
        trigger_server_tracker = EventDumper(self.tmp_dir, 'server')
        dummy_trigger_server = EventDumper(self.tmp_dir, 'dummy')
        # use receive_timeout to allow server to check termination status
        # instead of blocking while waiting for request:
        trigger_server = SNTPServer(server_name='TriggerServer', port=8889,
                                    callback=trigger_server_tracker,
                                    callback_dummy=dummy_trigger_server,
                                    receive_timeout=1)
        self.servers.append(trigger_server)

        trigger_tracker = EventRecorder()
        trigger_sender = SyncTriggerClient(trigger_callback=trigger_tracker)
        self.to_close.append(trigger_sender)

        trigger_server.start()
        from polos import get_local_ip
        local_ip = get_local_ip()
        if local_ip[0] == 0:
            server_adress = 'localhost'
        else:
            server_adress = local_ip[1]
        time.sleep(0.2) 
        trigger_sender.connect(server_adress, trigger_server.get_port())
        time.sleep(0.2) 
        remote_delay, remote_delay_std = trigger_sender.trigger()

        trigger_sender.shutdown_server()
        
        # self.assertIsNotNone(dummy_server_tracker.record_ts)
        server_trigger_ts = trigger_server_tracker.get_ts()
        self.assertIsNotNone(server_trigger_ts)
        local_trigger_ts = trigger_tracker.get_ts()
        self.assertIsNotNone(local_trigger_ts)

        sync_error = local_trigger_ts - server_trigger_ts # - \
                     #trigger_sender.trigger_delay_error
        inacurate = not (abs(sync_error) < remote_delay_std*1.5)
        
        message = '%s trigger synchronization:\n' % \
                  ['Acurate', 'Inacurate'][int(inacurate)] 
        message += '  - request sent at    %f\n' % \
                   trigger_sender.remote_trigger_sent_at            
        message += '    estimated delay:            %f sec, \n' % \
                   remote_delay
        message += '  - remote expected at %f sec, \n' % \
                   (trigger_sender.remote_trigger_sent_at + \
                    remote_delay)

        message += '  - remote trigger at  %f, \n' % \
                   trigger_server_tracker.get_ts()
        message += '          adjusted to  %f\n' % \
                    (trigger_server_tracker.get_ts() - \
                     trigger_sender.trigger_delay_error)
        message += '  - local trigger at   %f\n' % trigger_tracker.get_ts()
        message += '  - synchronization error:      %f sec\n' % sync_error
        message += '  - delay std:                  %f sec' % \
                   remote_delay_std
        
        if inacurate:
            raise Exception(message)
        else:
            print(message)
        
    def test_remote_trigger_over_network(self):
        pass
