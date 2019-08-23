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
from polos.server import STServerProcess, STServerThread, ST_NTPClient, STClient
from polos.server import TimestampSaver

# TODO: implement tennis

class StatusHolder:
    def __init__(self):
        self.status = None
        self.status_message = ''

    def set_status(self, status, message):
        self.status = status
        self.status_message = message

    def get_status(self):
        return self.status, self.status_message

class SyncTriggerTest(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = op.join(tempfile.gettempdir(), 'polos_server_test')
        if op.exists(self.tmp_dir):
            shutil.rmtree(self.tmp_dir)
        os.makedirs(self.tmp_dir)
        self.threads = []
        self.processes = []
        self.to_close = []
        self.procs = []
        
        if '-v' in sys.argv:
            logger.setLevel(10)

    def tearDown(self):
        for tc in self.to_close:
            tc.close()

        for pr in self.processes:
            # print('TearDown: Calling join')
            if pr.is_alive():
                pr.join(timeout=0.2)
                if pr.exitcode is None:
                    pr.terminate()

        for pr in self.procs:
            pr.terminate()
            pr.wait(timeout=0.2)
            if pr.poll() is None:
                pr.kill()
                pr.wait(timeout=0.2)
                if pr.poll() is None:
                    print('Cannot terminate subprocess', pr)
                    
        for th in self.threads:
            # print('TearDown: Calling join')
            if th.is_alive():
                th.join(timeout=0.2)
                if th.is_alive():
                    print('Cannot terminate thread', th)
            
        # print('Teardown complete')

        shutil.rmtree(self.tmp_dir)
        

    def check_status(self, obj, expected_status, expected_message):
        status, message = obj.get_status()
        if status != expected_status:
            raise AssertionError('Got %s, expected %s' % \
                                 (polos.STATUS_LABELS[status],
                                  polos.STATUS_LABELS[expected_status]))
        
        self.assertIn(expected_message.lower(), message.lower())

            
    def test_ntp_query_with_callback(self):
        
        class Callback:
            def __init__(self):
                self.nb_calls = 0
                
            def __call__(self):
                self.nb_calls += 1

        call_counter = Callback()

        sts_status_tracker = StatusHolder()
        
        # use receive_timeout to allow server to check termination status
        # instead of blocking while waiting for request:
        server = STServerThread(callback1=call_counter, receive_timeout=0.5,
                                status_handler=sts_status_tracker)
        self.threads.append(server)
        self.check_status(sts_status_tracker, polos.STATUS_ERROR, 'not started')
        
        server.start()
        time.sleep(0.05) # wait a bit to let server update
        self.check_status(sts_status_tracker, polos.STATUS_WARNING,
                          'waiting connection')

        client = ST_NTPClient()
        self.to_close.append(client)
        
        self.check_status(client, polos.STATUS_ERROR, 'not connected')
        client.connect('localhost', server.get_port())
        self.check_status(client, polos.STATUS_WARNING, 'no query')
        time.sleep(0.05) # wait a bit to let server update
        self.check_status(sts_status_tracker, polos.STATUS_OK, 'connected')
        # except AssertionError:
        # TODO: enable check_status to check between several given options
        #       here server might be connecting or connected
        #     self.check_status(server, polos.STATUS_WARNING, 'connecting')
        #     time.sleep(0.1)
        #     self.check_status(server, polos.STATUS_OK, 'connected')
            
        nb_request_trials = 10
        client.request(nb_trials=nb_request_trials)
        self.check_status(client, polos.STATUS_OK, '')
        self.check_status(sts_status_tracker, polos.STATUS_OK, 'connected')

        client.shutdown_server()
        client.close()
        self.check_status(client, polos.STATUS_ERROR, 'closed')
        time.sleep(0.05) # let server come out of main loop
        self.check_status(sts_status_tracker, polos.STATUS_ERROR, 'finished')

        self.assertIsNotNone(client.offset)
        self.assertTrue(abs(client.offset) < ST_NTPClient.CLOCK_OFFSET_TOLERANCE)

        self.assertEqual(call_counter.nb_calls, nb_request_trials)
                
    def test_remote_trigger_process(self):
        """
        The goal is to emit two *synchronized* triggers:
          - one local
          - one remote, fired by a distant server
        To do so, the transmission lag to the distant server is measured
        during 'test' calls. Then the remote triggered is resquested while
        the local triggered is fired after the estimated lag.
        """
        
        class EventRecorder:
            def __init__(self):
                self.ts = None

            def __call__(self):
                self.ts = time.time()

            def get_ts(self):
                return self.ts
                    
        trigger_server_tracker = TimestampSaver(self.tmp_dir, 'server')
        dummy_trigger_server = TimestampSaver(self.tmp_dir, 'dummy')
        # use receive_timeout to allow server to check termination status
        # instead of blocking while waiting for request:
        trigger_server = STServerProcess(server_name='TriggerServer', port=8889,
                                         callback1=trigger_server_tracker,
                                         callback2=dummy_trigger_server,
                                         receive_timeout=1)
        self.processes.append(trigger_server)

        trigger_tracker = EventRecorder()
        trigger_sender = STClient(trigger_callback=trigger_tracker)
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
        remote_delay, remote_delay_std = trigger_sender.request()

        trigger_sender.shutdown_server()
        
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
        # message += '          adjusted to  %f\n' % \
        #             (trigger_server_tracker.get_ts() - \
        #              trigger_sender.trigger_delay_error)
        message += '  - local trigger at   %f\n' % trigger_tracker.get_ts()
        message += '  - synchronization error:      %f sec\n' % sync_error
        message += '  - delay std:                  %f sec' % \
                   remote_delay_std

        sync_error_tol = 1e-3 # more than 1ms is too large
        too_large = abs(sync_error) > sync_error_tol

        if too_large:
            message += '\nSynchronization error too large (more than %f s)' % \
                       sync_error_tol
            
        if inacurate or too_large:
            raise Exception(message)
        else:
            print(message)

        if too_large:
            raise Exception
        
    def test_trigger_with_scripts_file(self):
        from subprocess import Popen
        
        cmd_server = ['polos_sync_trigger_server', 'TRIGGER_FILE',
                      '-d', self.tmp_dir, '-v', '10']
        server_proc = Popen(cmd_server, stdout=sys.stdout, stderr=sys.stderr)
        self.procs.append(server_proc)
        
        time.sleep(0.1) # wait for server to be ready
                        # todo: use server status output to now when it's ready
        
        cmd_client = ['polos_sync_trigger_request', 'localhost',
                      '-d', self.tmp_dir, '-v', '10']
        client_proc = Popen(cmd_client, stdout=sys.stdout, stderr=sys.stderr)
        self.procs.append(client_proc)
        client_proc.wait()

        sys.path.insert(0, op.join('..', 'scripts'))
        from polos.server import server_trigger_fn_prefix, server_dummy_fn_prefix
        from polos.server import client_trigger_fn_prefix as client_prefix

        pat = op.join(self.tmp_dir, server_dummy_fn_prefix + '*')
        server_dummy_fn = glob(pat)[0]
        pat = op.join(self.tmp_dir, server_trigger_fn_prefix + '*')
        server_trigger_fn = glob(pat)[0]

        client_trigger_fn = glob(op.join(self.tmp_dir, client_prefix + '*'))[0]

        client_ts = TimestampSaver.get_ts_from_filename(client_trigger_fn)
        server_ts = TimestampSaver.get_ts_from_filename(server_trigger_fn)

        tolerance = 1e-3
        print('client ts:', client_ts)
        print('server ts:', server_ts)
        self.assertLess(abs(client_ts-server_ts), tolerance)
        
    def test_trigger_with_scripts_gpio(self):
        pass
