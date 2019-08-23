import os.path as op
import time
import timeit
from threading import Thread
from multiprocessing import Process
import socket as socket_module
import select
import logging

import numpy as np

from ._polos import STATUS_OK, STATUS_ERROR, STATUS_WARNING

logger = logging.getLogger('polos')


## Synchronised Trigger Server (STS) ##
STS_BUFFER_SIZE = 2**6
STS_DEFAULT_PORT = 8888
STS_CONNECTION_TIMEOUT = 1

STS_CALLBACK_1 = b'0'
STS_CALLBACK_2 = b'1'
STS_QUIT = b'2'

STS_DEFAULT_NAME = 'STServer'

client_trigger_fn_prefix = 'st-client'
server_trigger_fn_prefix = 'st-server'
server_dummy_fn_prefix = 'sts-dummy'

class NoStatus:
    def set_status(self, st, m):
        pass
    
    def get_status():
        return None, ''
    
class TimestampSaver:
    def __init__(self, tmp_dir, fn_prefix):
        self.fn_prefix = op.join(tmp_dir, fn_prefix)

    def __call__(self):
        ts = time.time()
        open(self.fn_prefix + '_' + str(ts), 'a').close()

    def get_ts(self):
        ts_fn = glob(self.fn_prefix + '_' + '*')[0]
        return TimestampSaver.get_ts_from_filename(ts_fn)

    @staticmethod
    def get_ts_from_filename(ts_fn):
        return float(op.split(ts_fn)[1].split('_')[1])    
    
def sync_trigger_server(port=STS_DEFAULT_PORT, callback1=None,
                        callback2=None, server_name=STS_DEFAULT_NAME,
                        receive_timeout=None, status_handler=None):
    """
    TODO: add finished callback?
    """
    
    ## Setup
    def init_callback(cb):
        if cb is None:
            return lambda: None
        else:
            assert(callable(cb))
            return cb

    callback1 = init_callback(callback1)
    callback2 = init_callback(callback2)

    if status_handler is None:
        status_handler = NoStatus()
        
    status_handler.set_status(STATUS_ERROR, 'Idle')

    # Evaluate reply encoding overhead
    nb_trials = 10000
    code = '(str(ts)+" "+str(ts)+" "+str(ts)).encode()'
    ts_encode_time = timeit.timeit(code, setup='ts=time.time()',
                                   number=nb_trials) / nb_trials
    

    # Setup socket
    socket = socket_module.socket(socket_module.AF_INET,
                                  socket_module.SOCK_STREAM)
    socket.setsockopt(socket_module.SOL_SOCKET, socket_module.SO_REUSEADDR, True)
    socket.bind(('', port))
    socket.listen(1)

    status_handler.set_status(STATUS_WARNING, 'Waiting connection...')
    logger.info('%s waiting connection on %s', server_name, socket)
    
    ## Main loop
    finished = False
    while not finished and socket is not None:
        socket.settimeout(STS_CONNECTION_TIMEOUT)
        try:
            connection, conn_address = socket.accept() #wait
            status_handler.set_status(STATUS_WARNING,
                                      'Connecting to %s' % str(conn_address))
            
            # listen for some time then check if something else should
            # be done instead, like terminating the server.
            # Will come back here if nothing needed to be done.
        except socket_module.timeout:
            logger.debug('%s connection timeout', server_name)
            connection = None

        # Use a dict to get the same call delay for all callbacks
        actions = {STS_CALLBACK_1 : callback1,
                   STS_CALLBACK_2 : callback2}
        if connection is not None:
            logger.info('%s waiting for request from %s',
                        server_name, conn_address)
            logger.info('%s waiting for request using %s',
                        server_name, connection)
            
            status_handler.set_status(STATUS_OK,
                                      'Connected to %s' % str(conn_address))

            connection.setblocking(False)
            while not finished and socket is not None:
                ready = select.select([connection], [], [], receive_timeout)
                if ready[0]:
                    data = connection.recv(STS_BUFFER_SIZE) #wait
                    ts_receive = time.time()
                    # There is still the overhead of "dict.get" here:
                    action_result = actions.get(data, lambda: 1)()
                    ts_callback = time.time()
                    if action_result == 1:
                        if not data:
                            break
                        elif data == STS_QUIT:
                            finished = True
                            break
                        else:
                            finished = True
                            msg = 'Shutting down because of bad request: %s' % \
                                  data
                            status_handler.set_status(STATUS_ERROR, msg)
                            logger.error('%s %s', server_name, msg)
                            break
                    ts_transmit = time.time() + ts_encode_time
                    connection.sendall((str(ts_receive) + ' ' + \
                                        str(ts_callback) + ' ' + \
                                        str(ts_transmit)).encode())
                    
            logger.info('%s last callback at %s', server_name, ts_callback)
            logger.info('%s closing connection %s', server_name, connection)
            connection.close()
            
    ## Close
    if socket is not None:
        logger.info('%s closing %s', server_name, socket)
        socket.shutdown(socket_module.SHUT_RDWR)
        socket.close()
        status_handler.set_status(STATUS_ERROR, 'Finished')

class STServerProcess(Process):
    """ 
    Synchronized Trigger Server encapsulated in a multiprocessing.Process

    NTP-like server using TCP, returning receive / transmit timestamps. 
    A callback can be executed before replying, depending on a query flag.

    Note: Process is used to minimize thread switching overhead, hopefully
          using a dedicated CPU to be as precise as possible.
          For more flexibility, but potentially less precision, 
          use STServerThread.
          
    TODO: Accurate timestamping may not necessary -> maybe use perf_counter
    """    
    
    def __init__(self, port=STS_DEFAULT_PORT, callback1=None,
                 callback2=None, receive_timeout=None,
                 server_name=STS_DEFAULT_NAME, status_handler=None):
        super().__init__()

        self.callback1 = callback1
        self.callback2 = callback2
        self.port = port
        self.server_name = server_name
        self.receive_timeout = receive_timeout

        if status_handler is None:
            status_handler = NoStatus()
        self.status_handler = status_handler
        
        self.status_handler.set_status(STATUS_ERROR, 'Not started')
        
    def get_port(self):
        return self.port

    def run(self):
        sync_trigger_server(self.port, self.callback1, self.callback2,
                            self.server_name, self.receive_timeout,
                            self.status_handler)
        
    def run_old(self):
        #TODO: put everything in a function and wrap it in Process
        #      -> could be called as is in a script
        self.socket = socket_module.socket(socket_module.AF_INET,
                                           socket_module.SOCK_STREAM)
        self.socket.setsockopt(socket_module.SOL_SOCKET,
                               socket_module.SO_REUSEADDR, True)
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
            except socket_module.timeout:
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
        

class STServerThread(Thread):
    """ 
    Synchronized Trigger Server encapsulated in a threading.Thread

    NTP-like server using TCP, returning receive / transmit timestamps. 
    A callback can be executed before replying, depending on a query flag.

    Note: Thread can have large overhead and uncertainty.
          If time-critical is required, use STServerProcess.
          
    """
    
    
    def __init__(self, port=STS_DEFAULT_PORT, callback1=None,
                 callback2=None, receive_timeout=None,
                 server_name=STS_DEFAULT_NAME, status_handler=None):
        super().__init__()

        self.callback1 = callback1
        self.callback2 = callback2
        self.port = port
        self.server_name = server_name
        self.receive_timeout = receive_timeout

        if status_handler is None:
            status_handler = NoStatus()
        self.status_handler = status_handler

        self.status_handler.set_status(STATUS_ERROR, 'Not started')
        self.finished = False
        
    def get_port(self):
        return self.port
    
    def run(self):
        sync_trigger_server(self.port, self.callback1, self.callback2,
                            self.server_name, self.receive_timeout,
                            self.status_handler)
        
class STBaseClient:

    CLOCK_OFFSET_TOLERANCE = 10e-3 # second
    
    def __init__(self, client_name):
        self.socket = socket_module.socket(socket_module.AF_INET,
                                           socket_module.SOCK_STREAM)
        self.status = STATUS_ERROR
        self.status_message = 'Not connected'

        self.client_name = client_name
        logger.info('%s created, socket: %s', self.client_name, self.socket)
        
    def connect(self, host, port=STS_DEFAULT_PORT):
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

    def shutdown_server(self):
        self.socket.setblocking(True)
        self.socket.send(STS_QUIT)
        
    def request(self):
        raise NotImplementedError()
    

class ST_NTPClient(STBaseClient):
    DEFAULT_NAME = 'ST_NTPClient'
    CLOCK_OFFSET_TOLERANCE = 10e-3 # second
    
    def __init__(self, client_name=DEFAULT_NAME):
        super().__init__(client_name)
        
        self.offset = None
        self.roundtrip_delay = None
        
    def single_request(self):
        ts_orig = time.time()
        self.socket.send(STS_CALLBACK_1)
        ready = select.select([self.socket], [], [], 1)
        if ready[0]:
            rdata = self.socket.recv(STS_BUFFER_SIZE)
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
            ts_orig, ts_dest, ts_tr, ts_receive, ts_cbk = self.single_request()
            offsets[itrial] = ((ts_receive - ts_orig) - (ts_tr - ts_dest)) / 2
            delays[itrial] = (ts_dest - ts_orig) - (ts_tr - ts_receive)

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
        
        if abs(self.offset) < STClient.CLOCK_OFFSET_TOLERANCE:
            self.status = STATUS_OK
            self.status_message = 'Time offset with server: %1.3f s' % \
                                  self.offset
        else:
            self.status = STATUS_WARNING
            self.status_message = 'LARGE Time offset with server: %1.3f s' % \
                                  self.offset
    
            
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
    
class STClient(STBaseClient):

    DEFAULT_NAME = 'STClient'
    
    def __init__(self, trigger_callback=None):

        super().__init__(client_name=STClient.DEFAULT_NAME)
        
        assert(callable(trigger_callback))
        self.trigger_callback = trigger_callback
                
    def request(self, nb_trials=100):
                
        # Request remote trigger
        self.socket.setblocking(False)
        self.delays = np.zeros(nb_trials)
        
        trigger_bytes = [STS_CALLBACK_2, STS_CALLBACK_1]
        for itrial in range(nb_trials):
            ts_orig = time.time()
            self.socket.send(trigger_bytes[itrial==nb_trials-1])
            ts_send = time.time()
            
            if itrial==nb_trials-1: # last trial -> trigger
                trigger_delay = estimated_delay - (ts_send - ts_orig)
                # Use CPU-intensive wait loop to be as precise as possible
                # Don't use time.sleep() (imprecise)
                end = time.perf_counter() + trigger_delay
                while time.perf_counter() < end:
                    continue
                ts_pre_callback = time.time()
                self.trigger_callback()
                ts_end_callback = time.time()
                
            ready = select.select([self.socket], [], [], 1)
            if ready[0]:
                rdata = self.socket.recv(STS_BUFFER_SIZE)
                ts_destination = time.time()
            else:
                raise Exception('Timeout during waiting for server answer')
            
            rts1,rts2,rts3 = rdata.decode().split(' ')
            ts_receive = float(rts1)
            ts_remote_callback = float(rts2)
            ts_transmit = float(rts3)

            self.delays[itrial] = ((ts_destination - ts_orig) - \
                                   (ts_transmit - ts_receive)) / 2 # + \
                                   # (ts_remote_callback - ts_receive)/2
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
                
        self.remote_trigger_sent_at = ts_orig
        logger.info('%s remote trigger issued btwn %f and %f (server time)',
                    self.client_name, ts_receive, ts_remote_callback)

        # remote_delay = self.delays[-10:-1].mean()
        print('all delays:\n', self.delays)
        remote_delay_std = self.delays.std()
        self.trigger_delay_error = estimated_delay - self.delays[-1]

        return estimated_delay, remote_delay_std
