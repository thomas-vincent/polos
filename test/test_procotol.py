
import unittest
import time

import numpy as np

from polos.protocol import DiscretePwmProtocol, Recorder

import logging
import sys
logging.basicConfig(stream=sys.stdout)
logger = logging.getLogger('polos')

class DiscretePwmProtocolTest(unittest.TestCase):
        
    def setUp(self):
        if '-v' in sys.argv:
            logger.setLevel(10)
            self.verbose = True
        else:
            self.verbose = False  
             
    def test_decode_isolated_signal(self):
        bin_seq = '001' # 1
        precision = 9
        expected_value = 1*10**(-precision)
        
        sig = np.array([1,1,1,1,1,1,1,0,             # delimiter
                        1,0,1,1,1,1,0,1,1,1,1,0,1,0,  # precision
                        1,1,1,1,0,1,1,1,1,0,0,1,0,   # value
                        1,1,1,1,1,1,1])              # delimiter
        sig = sig + np.random.rand(*sig.shape) * 0.5
        found = DiscretePwmProtocol.decode_values_from_signal(sig)
        self.assertEqual(found[0][1], expected_value)
        self.assertEqual(found[0][0], 0)

    def test_decode_signal(self):
        bin_seq = '101' # 5
        precision = 0
        expected_value = 5
            
        sig = np.array(
            [0,0,0,1,0,                                       # unrelated
             1,1,1,1,1,1,1,1,0,0,                             # delimiter
             1,1,1,1,1,0,1,1,1,1,1,0,1,1,1,1,1,0,1,1,1,1,1,0, # precision
             1,1,0,1,1,1,1,1,0,0,1,0,0,                       # value
             1,1,1,1,1,1,0,                                   # delimiter
             0,0,0,0,0,0,0,0,0,0,1,1,1,0,0,0,0,0,0,0])        # unrelated
        sig = sig + np.random.rand(*sig.shape) * 0.5
        found = DiscretePwmProtocol.decode_values_from_signal(sig)
        self.assertEqual(found[0][1], expected_value)
        self.assertEqual(found[0][0], 5)

    def test_decode_timestamp(self):
        current_time = time.time()
        precision = 6 # microsecond precision
        sender = DiscretePwmProtocol(precision=precision)
        bin_seq = sender.value_to_bits(current_time)
        decoded_value = DiscretePwmProtocol.bits_to_value(bin_seq, precision)
        self.assertTrue(abs(decoded_value - current_time) < 10**(-precision))

    def test_send_timestamp(self):
        sampling_rate = 300 # Hz
        recording_max_duration = 0.5 # second
        gpio_state = [0]
        
        def gpio_on():
            print('gpio on')
            gpio_state[0] = 1
    
        def gpio_off():
            print('gpio off')
            gpio_state[0] = 0
    
        recorder = Recorder(gpio_state, sampling_rate, recording_max_duration)
        precision = 6 # microsecond precision
        sender = DiscretePwmProtocol(precision=precision) 
        
        recorder.start()
        onset, send_delay, tr_time = sender.send_value(time.time, sampling_rate,
                                                       on_func=gpio_on,
                                                       off_func=gpio_off)
    
        recorder.stop()
            
        found = sender.decode_values_from_signal(recorder.get_signal())
        self.assertTrue(abs(found[0][1] - onset) < 10**(-precision))
        delay_record_trigger = send_delay + recorder.get_record_start_delay()
        self.assertTrue(found[0][0] <= np.ceil((delay_record_trigger) * sampling_rate))
            
if __name__ == "__main__":
    # logger.setLevel(0) #TODO: add command option?
    unittest.main()
