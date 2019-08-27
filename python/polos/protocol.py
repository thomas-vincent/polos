import time
from threading import Thread
import logging
import numpy as np
import re

logger = logging.getLogger('polos')

class DppDecodeError(Exception): pass

def mark_bins(bin_str, imark, padding=0, col_width=80):
    # TODO: enable multiple markings
    pad = ' ' * padding
    # bin_str = ''.join([str(int(v)) for v in bins])
    mark_str = ' ' * (imark) + '^' + ' ' * (len(bin_str)-imark)
    from textwrap import wrap
    return '\n'.join(['%s%s\n%s%s' % (pad, b, pad, m) \
                      for b,m in zip(wrap(bin_str, col_width),
                                     wrap(mark_str, col_width,
                                          replace_whitespace=False,
                                          drop_whitespace=False))])

class DiscretePwmProtocol:
    """
    DiscretePwmProtocol (Dpp) transmits float values using only pulses, 
    in a (slow) fail-safe manner.
    Encoding is done by pulse width modulation. The sending interface
    is expected to be a logic on/off emitter. 

    The encoding depends on the sampling rate of the recieving interface, so
    that sent pulses are wide enough to be caught. Any constant NB_SAMPLES...
    stands for a multiple of the recording pace in the receiving end.
    
    The Dpp segment consists of:
    SEP.            : a pulse of width NB_SAMPLES_SEPARATOR
    FIXED_OPENING   : a pulse of width NB_SAMPLES_DELIMITER
    SEP.            : a pulse of width NB_SAMPLES_SEPARATOR
    PRECISION_BITS  : a binary encoding of the number of decimal digits in the output
                      value. TODO
    SEP.            : a pulse of width NB_SAMPLES_SEPARATOR
    VALUE_ENCODING  : a binary encoding of the output value (without decimals)
    SEP.            : a pulse of width NB_SAMPLES_SEPARATOR
    FIXED_CLOSING   : a pulse of width NB_SAMPLES_DELIMITER (same as FIXED_OPENING)
    SEP.            : a pulse of width NB_SAMPLES_SEPARATOR

    A "binary encoding" segment is a pulse representation of a binary sequence,
    where:
        The sent pulse for a bit of value 1 spans NB_SAMPLES_BIT1 samples 
        The sent pulse for a bit of value 0 spans NB_SAMPLES_BIT0 samples 
        The constants NB_SAMPLES_BIT1 and NB_SAMPLES_BIT0 are set so that decoding 
    on the receiving end will be robust up to one sample drop or one extra sample.

    >>> mock = PulseEmulator(1000, 1) # record at 1000Hz, for 1 second
    >>> pwm_transmitter = DiscretePwmProtocol(precision=6) #6 decimals rounding
    >>> mock.start()
    >>> pwm_transmitter.send_value(4.3, mock.sampling_rate, mock.on, mock.off) #doctest: +ELLIPSIS
    (4.3, ...)
    >>> pwm_transmitter.decode_values_from_signal(mock.get_signal()) #doctest: +ELLIPSIS
    [(..., 4.3)]
    """

    NB_SAMPLES_DELIMITER = 7 # used to define begining and end of an encoding 
    NB_SAMPLES_BIT0 = 5 
    NB_SAMPLES_BIT1 = 2
    NB_SAMPLES_SEP = 2
    NB_BITS_PRECISION = 4 

    SIG_THRESH_FACTOR = 0.5 # faction of signal maximum for thresholding
    
    def __init__(self, precision=6):
        """
        argument:
            precision (int): number of decimal digits to keep (between 0 and 9)
        """
        assert(int(precision)==precision and precision <= 9 and precision >= 0)
        self.precision = precision
        
    def value_to_bits(self, value):
        """ 
        Convert given value to a binary sequence using given precision. 
    
        arguments:
            value (float): to convert
    
        output:
            binary sequence as a string (without prefix '0b')
        """
        return bin(int(round(value * (10**self.precision))))[2:]

    @staticmethod
    def bits_to_value(bin_seq, precision):
        """ 
        Convert given binary sequence to a value using given precision. 
    
        arguments:
            - bin_seq (string): binary sequence (without prefix '0b')
            - precision (int): number of decimal digits to extract.
                               Must be between 0 and 9.
        """
        return int('0b' + bin_seq, 2) / 10**precision

    @classmethod
    def decode_values_from_signal(cls, sig):
        """ 
        Decode all timestamps from given analog signal.
    
        Conversion from analog to binary values is done by thresholding 
        above 50% of the max amplitude.
        
        argument:
            - sig (np.array of float): pulse signal from which to extract 
                                       encoded values
    
        output: list of tuple
             Each entry of this list is:
             (sample index in sig where coded value was found, 
              decoded float value)        
        """
        
        assert(sig.ndim==1)
    
        bin_sig = sig > (sig.max() * cls.SIG_THRESH_FACTOR)
        # return self._decode_chunk_by_chunk(bin_sig)
        bin_sig_str = ''.join([str(int(e)) for e in bin_sig])
        
        return cls._decode_regexp(bin_sig_str)

    @classmethod
    def _decode_regexp(cls, bin_seq_str):
        print('decoding:\n' + mark_bins(bin_seq_str, 0))
        values = []
        re_seqs = '1{6,8}0{1,3}(?:(?:1{1,3}|1{4,6})0{1,3}){4,}1{6,8}'
        # seqs = re.findall(re_seqs, bin_seq_str)

        def decode_val(code):
            tmp = re.sub('1{1,2}0{1,3}','o',re.sub('1{4,6}0{1,3}','z',code))
            bins = tmp.replace('0', '').replace('z', '0').replace('o', '1')
            return int(bins, 2)

        for seq_match in re.finditer(re_seqs, bin_seq_str):
            re_segs = '1{6,8}0{1,3}' \
                      '(?P<precision>(?:(?:1{1,3}|1{4,6})0{1,3}){4})' \
                      '(?P<value>(?:(?:1{1,3}|1{4,6})0{1,3})+)'\
                      '1{6,8}'
            rr_segs = re.search(re_segs, seq_match.string)
            if rr_segs is not None:
                segs_groups = rr_segs.groupdict()
                precision = decode_val(segs_groups['precision'])
                value = decode_val(segs_groups['value']) / 10**precision
            else:
                warning('Could not decode sequence at pos %d: %s',
                        seq_match.start(), seq_match.string)
                continue
            values.append((seq_match.start(), value))
        return values
        
    def send_value(self, value, sampling_rate, on_func=None, off_func=None):
        """
        Transmit the given value using pulse width modulation. Pulse emission 
        is done via the given functions on_func and off_func.
        Transmission is time-critical so that the pulse sequence can be 
        synchronized with value resolving. Indeed, value can be passed 
        in a delayed manner via a callable (see argument documentation).

        The function returns the overhead delay between the function call and
        when the first encoded bit is transmitted.
        
        argument:
            - value (float or callable): value to transmit. If it's a callable, 
                                         it must return a single float value.
            - sampling_rate (float): sampling rate of the receiving interface,
                                     in Hz.
            - on_func (callable): set the pulse on. Default prints "ON"
            - off_func (callable): set the pulse off. Default prints "OFF"

        output: tuple
            (transmitted value,
             delay in second (float) between the function call and the start 
             of the encoded sequence transmission,
             transmission time in second (float))
        """
        t_call = time.perf_counter()

        if not callable(value):
            get_value = lambda : value
        else:
            get_value = value
            
        if on_func is None:
            on_func = lambda : print('ON')
    
        if off_func is None:
            off_func = lambda : print('OFF')

        bin_seq_precision = "{0:04b}".format(self.precision)
        
        dt = 1/sampling_rate
    
        # Insure there is some zero before starting the encoding sequence:
        t_start_send = time.perf_counter()
        off_func()
        time.sleep(dt * DiscretePwmProtocol.NB_SAMPLES_SEP - \
                   time.perf_counter() + t_start_send)
        
        tic = time.perf_counter()
        # Value resolving is done right before the start of the delimiting sequence
        value_tr = get_value()
        delay = time.perf_counter() - t_call # Overhead time
        on_func() # Pulse start for the delimiting sequence 
        bin_seq_value = self.value_to_bits(value_tr) # Convert to binary 
        time.sleep(dt * DiscretePwmProtocol.NB_SAMPLES_DELIMITER - \
                   time.perf_counter() + tic)
    
        # Send a separator
        tic = time.perf_counter()
        logger.debug('Sending value %f...', value_tr)
        off_func()
        time.sleep(dt * DiscretePwmProtocol.NB_SAMPLES_SEP - \
                   time.perf_counter() + tic)
        
        def send_bits(bits):
            # Loop through all given bits and send pulses using width encoding
            for b in bits:
                tic = time.perf_counter()
                if b == '1':
                    on_func()
                    time.sleep(dt * DiscretePwmProtocol.NB_SAMPLES_BIT1 - \
                               time.perf_counter() + tic)
                else:
                    on_func()
                    time.sleep(dt * DiscretePwmProtocol.NB_SAMPLES_BIT0 - \
                               time.perf_counter() + tic)
                tic = time.perf_counter()
                off_func()
                time.sleep(dt * DiscretePwmProtocol.NB_SAMPLES_SEP - \
                           time.perf_counter() + tic)
        # Send precision bits:
        send_bits(bin_seq_precision)
        # Send value bits:
        send_bits(bin_seq_value)
        
        # Send the delimiting sequence to tag the end:
        tic = time.perf_counter()
        on_func()
        time.sleep(dt * DiscretePwmProtocol.NB_SAMPLES_DELIMITER - \
                   time.perf_counter() + tic)
        tic = time.perf_counter()
        off_func()
        time.sleep(dt * DiscretePwmProtocol.NB_SAMPLES_SEP - \
                   time.perf_counter() + tic)
        
        send_duration = time.perf_counter() - t_start_send
        logger.info('Value %f sent in %1.3f s', value_tr, send_duration)
        return value_tr, delay, send_duration
    
    def send_timestamp_gpio(self, gpio_id, sampling_rate):
        """ 
        Helper function to send a timestamp using GPIO on a 
        raspberry pi.
        """
        from RPi import GPIO
        try:
            gpio_chan = GPIO.gpio_function(gpio_id)
        except Exception:
            raise Exception('Error in GPIO setup. Make sure to set ' \
                            'GPIO numbering mode ' \
                            'and setup GPIO channel %d before calling ' \
                            'this function.')
        if gpio_chan != GPIO.OUT:
            raise Exception('Given GPIO channel %d must be setup ' \
                            'to GPIO.OUT before calling this function.')
        logger.info('Sending timestamp through GPIO %d at %1.2f Hz...',
                    gpio_id, sampling_rate)
        self.send_value(time.time, sampling_rate,
                        lambda: GPIO.output(gpio_id, GPIO.HIGH),
                        lambda: GPIO.output(gpio_id, GPIO.LOW))
        
        
#### Some mock recording interfaces to emulate receivers ####

class RecordTerminated(Exception): pass
    
class Recorder(Thread):
    """ 
    Emulate an analog signal recorder that makes periodic readings, for tests.
    """
    
    def __init__(self, tracked, sampling_rate, max_duration):
        """
        arguments:
            - tracked (list of float): number to track encapsulated in a list 
                                       (list length=1)
            - sampling_rate (float), in Hz
            - max_duration (float), in sec
        """
        Thread.__init__(self)
        self.tracked = tracked
        self.sampling_rate = sampling_rate
        self.record_pace = round(1/sampling_rate * 1e6) / 1e6 #round to microsec
        self.buffer_size = int(round(max_duration / self.record_pace))
        self.buffer = np.zeros(self.buffer_size, dtype=type(tracked[0]))
        self.i_sample = 0

        self.finished = False

        self.thread_start_ts = None
        self.record_start_ts = None

    def get_record_start_delay(self):
        """ 
        Get delay between call of Thread.start() and recording of first value

        Return None if not started.
        """
        if self.thread_start_ts is None or self.record_start_ts is None:
            return None
        return self.record_start_ts - self.thread_start_ts
    
    def record(self):
        """ Record a single value """
        if self.i_sample < self.buffer_size:
            # print('recording:', self.tracked[0], 'i_sample:', self.i_sample)
            self.buffer[self.i_sample] = self.tracked[0]
            self.i_sample += 1
        else:
            raise RecordTerminated()
        
    def run(self):
        """ 
        Record loop, insuring no temporal drift and accounting for
        overhead delay (see get_record_start_delay).
        """
        if not self.finished:
            # Do first step taking into account thread starting delay
            t0 = time.perf_counter()
            self.record_start_ts = time.time()
            try:
                self.record()
                overhead_time = time.perf_counter() - t0 + \
                                self.record_start_ts - self.thread_start_ts
                time.sleep(self.record_pace - overhead_time)
            except RecordTerminated:
                self.finished = True
                return
            except ValueError:
                raise Exception('Recorder frequency too high. ' \
                                'Maximum seems to be %1.3 Hz' % \
                                1/overhead_time)
                
            
        while not self.finished:
            t0 = time.perf_counter()
            try:
                self.record()
            except RecordTerminated:
                break
            time.sleep(self.record_pace - time.perf_counter() + t0)

        self.finished = True
        
    def start(self):
        """ Override Thread.start """
        self.thread_start_ts = time.time()
        super().start()
        
    def stop(self):
        """ Override Thread.stop """
        self.finished = True
        
    def get_signal(self):
        """ 
        Return the recorded signal. 
        Return only zeros when the recording has not been done.
        """
        return self.buffer

class PulseEmulator(Recorder):
    """ Pulse emulator holding a binary flag to represent pulses. """
    
    def __init__(self, sampling_rate, max_duration):
        self.pulse_state = [0]
        super().__init__(self.pulse_state, sampling_rate, max_duration)

    def on(self):
        self.pulse_state[0] = 1

    def off(self):
        self.pulse_state[0] = 0
