import socket
import time
import timeit
import select
#from threading import Thread, Timer
from multiprocessing import Process

import logging

import numpy as np

from ._polos import STATUS_OK, STATUS_ERROR, STATUS_WARNING

logger = logging.getLogger('polos')


class SNTPServer(Process):
    """ 
    NTP-like server using TCP, returning receive / transmit
    timestamps. A callback can be executed before replying,
    depending on a query flag.

    TODO: rework specification... no longer a simple NTP...
          timestamping scheme is based on NTP but main feature is 
          *time-critical callbacking*
          -> that's why using Process is mandatory. threading has too much
          overhead and uncertainty. 
          Accurate timestamping is not necessary -> maybe use perf_counter
    """
    
    BUFFER_SIZE = 2**6
    DEFAULT_PORT = 8888
    CONNECTION_TIMEOUT = 1

    EXECUTE_CALLBACK = b'1'
    SKIP_CALLBACK = b'0'
    QUIT = b'2'
    
    def __init__(self, port=DEFAULT_PORT, callback=None,
                 callback_dummy=None,
                 receive_timeout=None,
                 server_name='SNTPServer'):
        super().__init__()

        if callback is None:
            self.callback = lambda: None
        else:
            assert(callable(callback))
            self.callback = callback

        if callback_dummy is None:
            self.callback_dummy = lambda: None
        else:
            assert(callable(callback_dummy))
            self.callback_dummy = callback_dummy

        self.port = port
        self.server_name = server_name

        self.receive_timeout = receive_timeout
        
        nb_trials = 10000
        code = '(str(ts)+" "+str(ts)+" "+str(ts)).encode()'
        self.ts_encode_time = timeit.timeit(code, setup='ts=time.time()',
                                            number=nb_trials) / nb_trials
        
        self.status = STATUS_ERROR
        self.status_message = 'Idle'
        self.finished = True

    def get_port(self):
        return self.port

    def get_status(self):
        return self.status, self.status_message
    
    def close(self):
        if self.socket is not None:
            logger.info('%s closing %s', self.server_name, self.socket)
            self.socket.shutdown(socket.SHUT_RDWR)
            self.socket.close()
            self.socket = None

            self.status = STATUS_ERROR
            self.status_message = 'Closed'

    def run(self):
        #TODO: put everything in a function and wrap it in Process
        #      -> could be called as is in a script
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, True)
        self.socket.bind(('', self.port))
        self.socket.listen(1)

        logger.info('%s listening on %s', self.server_name, self.socket)
        
        self.status = STATUS_WARNING
        self.status_message = 'Waiting connection...'
        logger.info('%s waiting connection on %s', self.server_name,
                    self.socket)

        self.finished = False
        while not self.finished and self.socket is not None:
            self.socket.settimeout(SNTPServer.CONNECTION_TIMEOUT)
            try:
                connection, conn_address = self.socket.accept() #wait
                self.status = STATUS_WARNING
                self.status_message = 'Connecting to %s' % str(conn_address)
                
                # listen for some time then check if something else should
                # be done instead, like terminating the server.
                # Will come back here if nothing needed to be done.
            except socket.timeout:
                logger.info('%s connection timeout', self.server_name)
                connection = None
                
            if connection is not None:
                logger.info('%s waiting for request from %s',
                            self.server_name, conn_address)
                logger.info('%s waiting for request using %s',
                            self.server_name, connection)
                self.status = STATUS_OK
                self.status_message = 'Connected to %s' % str(conn_address)
                      
                connection.setblocking(False)
                while not self.finished and self.socket is not None:
                    ready = select.select([connection], [], [],
                                          self.receive_timeout)
                    if ready[0]:
                        data = connection.recv(SNTPServer.BUFFER_SIZE) #wait
                        ts_receive = time.time()
                        if not data:
                            break
                        elif data == SNTPServer.EXECUTE_CALLBACK:
                            self.callback()
                            ts_callback = time.time()
                        elif data == SNTPServer.SKIP_CALLBACK:
                            ts_callback = time.time()
                            self.callback_dummy()                            
                        elif data == SNTPServer.QUIT:
                            self.finished = True
                            break
                        else:
                            self.finished = True
                            logger.error('%s received bad request %s',
                                         self.server_name, data)
                            break
                        # else:
                        #     logger.info('%s received: %s', self.server_name, data)
                        ts_transmit = time.time() + self.ts_encode_time
                        connection.sendall((str(ts_receive) + ' ' + \
                                            str(ts_callback) + ' ' + \
                                            str(ts_transmit)).encode())
                        
                logger.info('%s last callback at %s', self.server_name,
                            ts_callback)
                logger.info('%s closing connection %s', self.server_name,
                            connection)
                connection.close()
        self.close()
        
    def stop(self):
        self.finished = True
            
class SNTPClient:

    CLOCK_OFFSET_TOLERANCE = 10e-3 # second
    
    def __init__(self, client_name='SNTPClient'):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.status = STATUS_ERROR
        self.status_message = 'Not connected'

        self.offset = None
        self.roundtrip_delay = None

        self.client_name = client_name
        logger.info('%s created, socket: %s', self.client_name, self.socket)
        
    def connect(self, host, port=SNTPServer.DEFAULT_PORT):
        logger.info('%s connecting to %s:%d..., socket: %s', self.client_name,
                    host, port, self.socket)
        self.socket.connect((host, port))
        logger.info('%s connected to %s:%d', self.client_name, host, port)
        self.status = STATUS_WARNING
        self.status_message = 'Connected to %s:%d, but no query yet' % \
                              (host, port)

    def close(self):
        if self.socket is not None:
            logger.info('%s closing %s', self.client_name, self.socket)

            self.socket.close()
            self.socket = None
            
            self.status = STATUS_ERROR
            self.status_message = 'Closed'

    def get_status(self):
        return self.status, self.status_message

    def single_request(self):
        ts_orig = time.time()
        self.socket.send(SNTPServer.SKIP_CALLBACK)
        print('Sent took %f' % (time.time() - ts_orig))
        time.sleep(0)
        ready = select.select([self.socket], [], [], 1)
        if ready[0]:
            rdata = self.socket.recv(SNTPServer.BUFFER_SIZE)
            ts_destination = time.time()
        else:
            raise Exception('Timeout during waiting for server answer')
        
        rts1,rts2,rts3 = rdata.decode().split(' ')
        ts_receive = float(rts1)
        ts_callback = float(rts2)
        ts_transmit = float(rts3)
        
        return ts_orig, ts_destination, ts_transmit, ts_receive, ts_callback
        
    def request(self, nb_trials=10):
        assert(nb_trials > 0)
        self.socket.setblocking(False)
        offsets = np.zeros(nb_trials)
        delays = np.zeros(nb_trials)
        for itrial in range(nb_trials):
            ts_orig, ts_dest, ts_transmit, ts_receive, ts_cbk = self.single_request()
            offsets[itrial] = ((ts_receive - ts_orig) - \
                               (ts_transmit - ts_dest)) / 2
            delays[itrial] = (ts_dest - ts_orig) - \
                             (ts_transmit - ts_receive)

        to_keep = np.argsort(delays)[nb_trials//2]
        self.offset = offsets[to_keep].mean() # rather trust requests with
                                              # shorter round-trip delays
        self.round_trip_delay = np.median(delays)
        self.round_trip_delay_max = delays.max()
        self.round_trip_delay_min = delays.min()
        self.round_trip_delay_std = delays.std()

        logger.info('%s estimated round-trip delay: %f (%f) sec',
                    self.client_name, self.round_trip_delay,
                    self.round_trip_delay_std)
        
        if abs(self.offset) < SNTPClient.CLOCK_OFFSET_TOLERANCE:
            self.status = STATUS_OK
            self.status_message = 'Time offset with server: %1.3f s' % \
                                  self.offset
        else:
            self.status = STATUS_WARNING
            self.status_message = 'LARGE Time offset with server: %1.3f s' % \
                                  self.offset

def faint(delay):
    end = time.perf_counter() + delay
    while time.perf_counter() < end:
        continue

class EventLogger:
    def __init__():
        self.size = 10000
        self.events = [None] * self.size
        self.i_event = 0

    def log(msg):
        self.events[self.i_event] = (time.time(), msg)
        self.i_event += 1

    def to_string():
        '\n'.join(['%f : %s' %self.events[i] for i in range(self.i_event)])
    
class SyncTriggerClient(SNTPClient):

    def __init__(self, trigger_callback=None):

        super().__init__(client_name='SyncTriggerClient')
        
        assert(callable(trigger_callback))
        self.trigger_callback = trigger_callback
                
    def trigger(self):
                
        # Request remote trigger
        self.socket.setblocking(False)
        nb_trials = 50
        self.delays = np.zeros(nb_trials)
        
        # TODO: discard prior step with dedicated pingpong client/server
        # TODO: Warmup loop
        trigger_bytes = [SNTPServer.SKIP_CALLBACK, SNTPServer.EXECUTE_CALLBACK]
        for itrial in range(nb_trials):
            ts_orig = time.time()
            self.socket.send(trigger_bytes[itrial==nb_trials-1])
            ts_send = time.time()
            if itrial==nb_trials-1:
                trigger_delay = estimated_delay - (ts_send - ts_orig)
                end = time.perf_counter() + trigger_delay
                while time.perf_counter() < end:
                    continue

                ts_pre_callback = time.time()
                self.trigger_callback()
                ts_end_callback = time.time()
                # time.sleep(1e-6)
            ready = select.select([self.socket], [], [], 1)
            if ready[0]:
                rdata = self.socket.recv(SNTPServer.BUFFER_SIZE)
                ts_destination = time.time()
            else:
                raise Exception('Timeout during waiting for server answer')
            
            rts1,rts2,rts3 = rdata.decode().split(' ')
            ts_receive = float(rts1)
            ts_remote_callback = float(rts2)
            ts_transmit = float(rts3)

            # if itrial==nb_trials-1:
            #     self.delays[itrial] = ((ts_destination - ts_orig) - \
            #                            (ts_end_callback - ts_send) - \
            #                            (ts_transmit - ts_receive)) / 2 + \
            #                            ts_remote_callback - ts_receive
            # else:
            self.delays[itrial] = ((ts_destination - ts_orig) - \
                                   (ts_transmit - ts_receive)) / 2 + \
                                   ts_remote_callback - ts_receive
            if itrial==nb_trials-2:
                estimated_delay = self.delays[-10:-1].mean()

        # Aftermaths
        logger.info('%s request for remote trigger sent at %f',
                    self.client_name, ts_orig)
        logger.info('%s socket.send returned at %f',
                    self.client_name, ts_send)
        logger.info('%s estimated remote delay : %f',
                    self.client_name, estimated_delay)
        logger.info('%s planned to wait %f after socket.send returned',
                    self.client_name, trigger_delay)
        logger.info('%s actually waited %f after socket.send returned',
                    self.client_name, ts_pre_callback - ts_send)
        logger.info('%s delay between socket.send and pre_callback: %f',
                    self.client_name, ts_pre_callback - ts_orig)
        logger.info('%s ts pre callback: %f', self.client_name,
                    ts_pre_callback)
        logger.info('%s ts end callback: %f', self.client_name,
                    ts_end_callback)
        logger.info('%s ts remote callback (server time): %f', self.client_name,
                    ts_remote_callback)

        # logger.info('%s trigger time: %f', self.client_name,
        #             self.trigger_time)
                
        self.remote_trigger_sent_at = ts_orig
        logger.info('%s remote trigger issued btwn %f and %f (server time)',
                    self.client_name, ts_receive, ts_transmit)

        # remote_delay = self.delays[-10:-1].mean()
        print('all delays:\n', self.delays)
        remote_delay_std = self.delays.std()
        self.trigger_delay_error = estimated_delay - self.delays[-1]

        return estimated_delay, remote_delay_std

    def shutdown_server(self):
        self.socket.send(SNTPServer.QUIT)
