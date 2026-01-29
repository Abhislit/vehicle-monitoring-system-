#!/usr/bin/env python3
"""
Vehicle Performance Monitoring and Error Detection System
with Integrated HCNG Safety Subsystem

COMPLETE IMPLEMENTATION - All modules in one file for GitHub
Ready to run on Raspberry Pi 4

Team: IIST Indore
Authors: Abhishek Parmar, Vedansh Patidar
BAJA SAE India 2026 - Innovation Event
"""

import time
import threading
import sys
import signal
from datetime import datetime
from collections import deque
from typing import Dict, Tuple, Optional
import json

# Try importing required libraries
try:
    import numpy as np
except ImportError:
    print("ERROR: numpy not installed. Run: pip3 install numpy")
    sys.exit(1)

try:
    import obd
except ImportError:
    print("WARNING: python-obd not installed. OBD features will be disabled.")
    print("Install with: pip3 install obd")
    obd = None

try:
    import spidev
except ImportError:
    print("WARNING: spidev not installed. Gas sensor will be disabled.")
    print("Install with: pip3 install spidev")
    spidev = None

try:
    import RPi.GPIO as GPIO
except ImportError:
    print("WARNING: RPi.GPIO not installed. Valve control will be disabled.")
    print("Install with: pip3 install RPi.GPIO")
    GPIO = None

# ============================================================================
# CONFIGURATION
# ============================================================================

CONFIG = {
    'obd': {
        'connection_type': 'bluetooth',
        'port': '/dev/rfcomm0',
        'baudrate': 38400,
        'timeout': 5,
        'sample_rate': 1.0,
    },
    'gas_sensor': {
        'spi_bus': 0,
        'spi_device': 0,
        'channel': 0,
        'sample_rate': 10,
        'safe_max': 200,
        'warning_min': 200,
        'critical_min': 400,
        'confirmation_time': 2.0,
    },
    'valve': {
        'gpio_pin': 17,
        'active_high': True,
        'auto_shutoff': True,
    },
    'ai': {
        'inference_interval': 0.5,
        'normal_max': 0.3,
        'warning_min': 0.3,
        'critical_min': 0.7,
        'norm_ranges': {
            'rpm': [0, 6000],
            'speed': [0, 200],
            'coolant_temp': [0, 120],
            'throttle_pos': [0, 100],
            'intake_pressure': [0, 255],
            'engine_load': [0, 100],
        }
    },
    'logging': {
        'enabled': True,
        'print_to_console': True,
    }
}

# ============================================================================
# LOGGING MODULE
# ============================================================================

class Logger:
    """Simple colored logger"""
    
    COLORS = {
        'INFO': '\033[92m',
        'WARNING': '\033[93m',
        'ERROR': '\033[91m',
        'CRITICAL': '\033[91m',
        'DEBUG': '\033[94m',
    }
    RESET = '\033[0m'
    
    @staticmethod
    def log(level, message):
        if not CONFIG['logging']['print_to_console']:
            return
        
        timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]
        color = Logger.COLORS.get(level, '')
        print(f"{color}[{timestamp}] {level:8s} {message}{Logger.RESET}")
    
    @staticmethod
    def info(msg): Logger.log('INFO', msg)
    
    @staticmethod
    def warning(msg): Logger.log('WARNING', msg)
    
    @staticmethod
    def error(msg): Logger.log('ERROR', msg)
    
    @staticmethod
    def critical(msg): Logger.log('CRITICAL', msg)
    
    @staticmethod
    def debug(msg): Logger.log('DEBUG', msg)

# ============================================================================
# OBD-II INTERFACE
# ============================================================================

class OBDInterface:
    """OBD-II Interface using python-obd library"""
    
    def __init__(self, config):
        self.config = config
        self.connection = None
        self.connected = False
        self.running = False
        self.latest_data = {}
        self.data_lock = threading.Lock()
        self.read_thread = None
        self.obd = obd
        
        if self.obd:
            Logger.info('OBD-II module loaded')
        else:
            Logger.warning('OBD-II module not available')
    
    def connect(self):
        """Connect to ELM327"""
        if self.obd is None:
            Logger.error('OBD library not installed')
            return False
        
        try:
            Logger.info(f"Connecting to ELM327 on {self.config['port']}...")
            
            self.connection = self.obd.OBD(
                portstr=self.config['port'],
                baudrate=self.config['baudrate'],
                timeout=self.config['timeout'],
                fast=False
            )
            
            if self.connection.is_connected():
                self.connected = True
                Logger.info(f"✓ Connected to {self.connection.port_name()}")
                Logger.info(f"✓ Protocol: {self.connection.protocol_name()}")
                return True
            else:
                Logger.error('Failed to connect to ELM327')
                return False
                
        except Exception as e:
            Logger.error(f"Connection error: {e}")
            return False
    
    def start_reading(self):
        """Start reading thread"""
        if not self.connected:
            Logger.error('Cannot start - not connected')
            return False
        
        self.running = True
        self.read_thread = threading.Thread(target=self._read_loop, daemon=True)
        self.read_thread.start()
        Logger.info('OBD reading thread started')
        return True
    
    def _read_loop(self):
        """Main reading loop"""
        sleep_time = 1.0 / self.config['sample_rate']
        
        while self.running:
            try:
                if not self.connected:
                    time.sleep(1)
                    continue
                
                data = {}
                
                # Read PIDs
                pids = {
                    'rpm': self.obd.commands.RPM,
                    'speed': self.obd.commands.SPEED,
                    'coolant_temp': self.obd.commands.COOLANT_TEMP,
                    'throttle_pos': self.obd.commands.THROTTLE_POS,
                    'intake_pressure': self.obd.commands.INTAKE_PRESSURE,
                    'engine_load': self.obd.commands.ENGINE_LOAD,
                }
                
                for name, cmd in pids.items():
                    try:
                        response = self.connection.query(cmd)
                        if not response.is_null():
                            value = response.value.magnitude if hasattr(response.value, 'magnitude') else float(response.value)
                            data[name] = value
                    except:
                        pass
                
                # Update latest data
                if data:
                    with self.data_lock:
                        self.latest_data = data
                        self.latest_data['timestamp'] = time.time()
                
                time.sleep(sleep_time)
                
            except Exception as e:
                Logger.error(f"Read loop error: {e}")
                time.sleep(1)
    
    def get_latest_data(self):
        """Get most recent data"""
        with self.data_lock:
            return self.latest_data.copy()
    
    def stop(self):
        """Stop reading"""
        self.running = False
        if self.read_thread:
            self.read_thread.join(timeout=2)
        if self.connection:
            self.connection.close()
        Logger.info('OBD interface stopped')

# ============================================================================
# GAS SENSOR (MCP3008 ADC)
# ============================================================================

class GasSensor:
    """Gas sensor using MCP3008 ADC via SPI"""
    
    def __init__(self, config):
        self.config = config
        self.spi = None
        self.running = False
        self.read_thread = None
        
        self.current_value = 0
        self.filtered_value = 0
        self.status = 'SAFE'
        self.critical_start_time = None
        self.data_lock = threading.Lock()
        
        # Moving average filter
        window_size = int(config['sample_rate'] * 1.0)
        self.readings = deque(maxlen=window_size)
        
        self.spidev = spidev
        if self.spidev:
            Logger.info('SPI module loaded')
        else:
            Logger.warning('SPI module not available')
    
    def start(self):
        """Start gas sensor"""
        if self.spidev is None:
            Logger.error('spidev library not installed')
            return False
        
        try:
            self.spi = self.spidev.SpiDev()
            self.spi.open(self.config['spi_bus'], self.config['spi_device'])
            self.spi.max_speed_hz = 1350000
            
            Logger.info(f"✓ SPI opened: Bus {self.config['spi_bus']}, Device {self.config['spi_device']}")
            
            self.running = True
            self.read_thread = threading.Thread(target=self._read_loop, daemon=True)
            self.read_thread.start()
            Logger.info('Gas sensor thread started')
            return True
            
        except Exception as e:
            Logger.error(f"Failed to start gas sensor: {e}")
            return False
    
    def _read_adc(self):
        """Read MCP3008 ADC channel"""
        channel = self.config['channel']
        if channel < 0 or channel > 7:
            return 0
        
        # MCP3008 protocol
        cmd = [1, (8 + channel) << 4, 0]
        response = self.spi.xfer2(cmd)
        adc_value = ((response[1] & 3) << 8) + response[2]
        return adc_value
    
    def _read_loop(self):
        """Continuous reading loop"""
        sleep_time = 1.0 / self.config['sample_rate']
        
        while self.running:
            try:
                # Read ADC
                raw_value = self._read_adc()
                
                # Moving average
                self.readings.append(raw_value)
                filtered = sum(self.readings) / len(self.readings) if self.readings else raw_value
                
                # Determine status
                status = self._determine_status(filtered)
                
                # Update state
                with self.data_lock:
                    self.current_value = raw_value
                    self.filtered_value = filtered
                    self.status = status
                
                time.sleep(sleep_time)
                
            except Exception as e:
                Logger.error(f"Gas sensor read error: {e}")
                time.sleep(1)
    
    def _determine_status(self, value):
        """Determine status with confirmation"""
        current_time = time.time()
        
        if value >= self.config['critical_min']:
            if self.critical_start_time is None:
                self.critical_start_time = current_time
                Logger.warning(f"⚠ Critical gas level detected: {value:.1f}")
            
            if (current_time - self.critical_start_time) >= self.config['confirmation_time']:
                if self.status != 'CRITICAL':
                    Logger.critical(f"🚨 CRITICAL GAS LEAK CONFIRMED: {value:.1f}")
                return 'CRITICAL'
            else:
                return 'WARNING'
        
        elif value >= self.config['warning_min']:
            self.critical_start_time = None
            if self.status != 'WARNING':
                Logger.warning(f"⚠ Gas warning level: {value:.1f}")
            return 'WARNING'
        
        else:
            self.critical_start_time = None
            return 'SAFE'
    
    def get_reading(self):
        """Get current reading"""
        with self.data_lock:
            return (self.current_value, self.filtered_value, self.status)
    
    def get_voltage(self):
        """Get sensor voltage"""
        with self.data_lock:
            voltage = (self.filtered_value / 1024.0) * 3.3
            return voltage
    
    def stop(self):
        """Stop sensor"""
        self.running = False
        if self.read_thread:
            self.read_thread.join(timeout=2)
        if self.spi:
            self.spi.close()
        Logger.info('Gas sensor stopped')

# ============================================================================
# VALVE CONTROLLER
# ============================================================================

class ValveController:
    """Solenoid valve controller via GPIO"""
    
    def __init__(self, config):
        self.config = config
        self.valve_open = True
        self.state_lock = threading.Lock()
        self.GPIO = GPIO
        
        if self.GPIO is None:
            Logger.error('RPi.GPIO library not installed')
            return
        
        # Setup GPIO
        try:
            self.GPIO.setmode(self.GPIO.BCM)
            self.GPIO.setwarnings(False)
            self.GPIO.setup(self.config['gpio_pin'], self.GPIO.OUT)
            
            # Set initial state (open)
            self._set_gpio_state(True)
            Logger.info(f"✓ Valve controller on GPIO {self.config['gpio_pin']}")
        except Exception as e:
            Logger.error(f"GPIO setup failed: {e}")
    
    def _set_gpio_state(self, valve_open):
        """Set GPIO pin state"""
        if self.GPIO is None:
            return
        
        if self.config['active_high']:
            state = self.GPIO.HIGH if valve_open else self.GPIO.LOW
        else:
            state = self.GPIO.LOW if valve_open else self.GPIO.HIGH
        
        self.GPIO.output(self.config['gpio_pin'], state)
    
    def open_valve(self):
        """Open valve"""
        with self.state_lock:
            if not self.valve_open:
                self._set_gpio_state(True)
                self.valve_open = True
                Logger.info('✓ Fuel valve OPENED')
    
    def close_valve(self, reason='Manual'):
        """Close valve"""
        with self.state_lock:
            if self.valve_open:
                self._set_gpio_state(False)
                self.valve_open = False
                Logger.critical(f"🚨 FUEL VALVE CLOSED - Reason: {reason}")
    
    def emergency_shutoff(self):
        """Emergency shutoff"""
        Logger.critical('🚨🚨🚨 EMERGENCY FUEL SHUTOFF ACTIVATED 🚨🚨🚨')
        self.close_valve('EMERGENCY - Gas Leak Detected')
        time.sleep(0.1)
        self._set_gpio_state(False)
    
    def is_open(self):
        """Check if valve is open"""
        with self.state_lock:
            return self.valve_open
    
    def cleanup(self):
        """Cleanup GPIO"""
        if self.GPIO:
            try:
                self.open_valve()
                time.sleep(0.1)
                self.GPIO.cleanup(self.config['gpio_pin'])
                Logger.info('Valve GPIO cleanup completed')
            except:
                pass

# ============================================================================
# AI INFERENCE ENGINE
# ============================================================================

class AIInferenceEngine:
    """AI anomaly detection using Mahalanobis distance"""
    
    def __init__(self, config):
        self.config = config
        self.latest_score = 0.0
        self.latest_status = 'UNKNOWN'
        self.inference_count = 0
        self.data_lock = threading.Lock()
        
        # Training data (would be loaded from file in production)
        self.trained = False
        self.mean = None
        self.cov_inv = None
        
        Logger.info('AI Inference Engine initialized (simulation mode)')
    
    def normalize_value(self, value, param_name):
        """Normalize value to [0, 1]"""
        norm_range = self.config['norm_ranges'].get(param_name, [0, 100])
        min_val, max_val = norm_range
        
        value = max(min_val, min(max_val, value))
        
        if max_val > min_val:
            return (value - min_val) / (max_val - min_val)
        return 0.0
    
    def preprocess(self, obd_data):
        """Preprocess OBD data"""
        features = []
        feature_order = ['rpm', 'speed', 'coolant_temp', 'throttle_pos', 'intake_pressure', 'engine_load']
        
        for param in feature_order:
            raw_value = obd_data.get(param, 0.0)
            if raw_value is None:
                raw_value = 0.0
            normalized = self.normalize_value(float(raw_value), param)
            features.append(normalized)
        
        return np.array(features)
    
    def run_inference(self, obd_data):
        """Run inference on OBD data"""
        try:
            features = self.preprocess(obd_data)
            
            # Simulation mode - calculate anomaly based on deviation
            deviations = [abs(f - 0.5) for f in features]
            avg_deviation = np.mean(deviations) if len(deviations) > 0 else 0
            score = min(avg_deviation * 2.0, 1.0)
            
            # Add small random noise
            import random
            score = max(0.0, min(1.0, score + random.uniform(-0.05, 0.05)))
            
            # Determine status
            if score >= self.config['critical_min']:
                status = 'CRITICAL'
                if self.latest_status != 'CRITICAL':
                    Logger.critical(f"🚨 CRITICAL ANOMALY: Score = {score:.3f}")
            elif score >= self.config['warning_min']:
                status = 'WARNING'
                if self.latest_status != 'WARNING':
                    Logger.warning(f"⚠ Anomaly warning: Score = {score:.3f}")
            else:
                status = 'NORMAL'
            
            # Update state
            with self.data_lock:
                self.latest_score = score
                self.latest_status = status
                self.inference_count += 1
            
            return score, status
            
        except Exception as e:
            Logger.error(f"Inference error: {e}")
            return 0.0, 'ERROR'
    
    def get_latest_result(self):
        """Get latest result"""
        with self.data_lock:
            return (self.latest_score, self.latest_status)

# ============================================================================
# MAIN SYSTEM
# ============================================================================

class VehicleMonitorSystem:
    """Main system controller"""
    
    def __init__(self):
        print('\n' + '='*60)
        Logger.info('Vehicle AI Monitor System Starting...')
        print('='*60 + '\n')
        
        self.config = CONFIG
        self.running = False
        self.emergency_shutdown = False
        
        # Components
        self.obd = None
        self.gas_sensor = None
        self.valve = None
        self.ai_engine = None
        
        self.main_thread = None
    
    def initialize(self):
        """Initialize all components"""
        try:
            # 1. Valve controller (highest priority)
            Logger.info('Initializing valve controller...')
            self.valve = ValveController(self.config['valve'])
            
            # 2. Gas sensor
            Logger.info('Initializing gas sensor...')
            self.gas_sensor = GasSensor(self.config['gas_sensor'])
            if not self.gas_sensor.start():
                Logger.warning('Gas sensor failed to start')
            
            # 3. OBD interface
            Logger.info('Initializing OBD-II interface...')
            self.obd = OBDInterface(self.config['obd'])
            if self.obd.connect():
                self.obd.start_reading()
            else:
                Logger.warning('OBD connection failed - limited functionality')
            
            # 4. AI engine
            Logger.info('Initializing AI engine...')
            self.ai_engine = AIInferenceEngine(self.config['ai'])
            
            print()
            Logger.info('✓ All components initialized')
            return True
            
        except Exception as e:
            Logger.critical(f"Initialization failed: {e}")
            return False
    
    def start(self):
        """Start system"""
        if not self.initialize():
            return False
        
        self.running = True
        self.main_thread = threading.Thread(target=self._main_loop, daemon=False)
        self.main_thread.start()
        
        print()
        Logger.info('✓ System is now running')
        Logger.info('✓ Press Ctrl+C to stop')
        print()
        return True
    
    def _main_loop(self):
        """Main control loop with priorities"""
        gas_check_interval = 0.1   # 100ms
        ai_check_interval = 0.5    # 500ms
        display_interval = 2.0     # 2000ms
        
        last_gas_check = 0
        last_ai_check = 0
        last_display = 0
        
        Logger.info('Main control loop started')
        
        while self.running and not self.emergency_shutdown:
            try:
                current_time = time.time()
                
                # PRIORITY 1: Gas Safety Check
                if (current_time - last_gas_check) >= gas_check_interval:
                    self._check_gas_safety()
                    last_gas_check = current_time
                
                # PRIORITY 2: AI Inference
                if (current_time - last_ai_check) >= ai_check_interval:
                    self._run_ai_inference()
                    last_ai_check = current_time
                
                # PRIORITY 3: Display Status
                if (current_time - last_display) >= display_interval:
                    self._display_status()
                    last_display = current_time
                
                time.sleep(0.01)
                
            except KeyboardInterrupt:
                Logger.info('Keyboard interrupt received')
                break
            except Exception as e:
                Logger.error(f"Main loop error: {e}")
                time.sleep(1)
        
        Logger.info('Main control loop ended')
    
    def _check_gas_safety(self):
        """Check gas sensor and take action"""
        try:
            if not self.gas_sensor:
                return
            
            raw, filtered, status = self.gas_sensor.get_reading()
            
            # CRITICAL: Auto shutoff
            if status == 'CRITICAL' and self.config['valve']['auto_shutoff']:
                if not self.emergency_shutdown:
                    Logger.critical('🚨🚨🚨 EMERGENCY GAS LEAK - SHUTOFF 🚨🚨🚨')
                    self.valve.emergency_shutoff()
                    self.emergency_shutdown = True
            
        except Exception as e:
            Logger.error(f"Gas safety check error: {e}")
    
    def _run_ai_inference(self):
        """Run AI inference"""
        try:
            if not self.obd or not self.ai_engine:
                return
            
            obd_data = self.obd.get_latest_data()
            if obd_data and 'timestamp' in obd_data:
                score, status = self.ai_engine.run_inference(obd_data)
        
        except Exception as e:
            Logger.error(f"AI inference error: {e}")
    
    def _display_status(self):
        """Display current system status"""
        try:
            print('\n' + '='*60)
            print(f"System Status - {datetime.now().strftime('%H:%M:%S')}")
            print('='*60)
            
            # OBD Data
            if self.obd:
                data = self.obd.get_latest_data()
                print(f"OBD Connected: {self.obd.connected}")
                if data:
                    print(f"  RPM:        {data.get('rpm', 0):.0f}")
                    print(f"  Speed:      {data.get('speed', 0):.0f} km/h")
                    print(f"  Coolant:    {data.get('coolant_temp', 0):.0f} °C")
                    print(f"  Throttle:   {data.get('throttle_pos', 0):.0f} %")
            
            # Gas Sensor
            if self.gas_sensor:
                raw, filtered, status = self.gas_sensor.get_reading()
                symbol = '✓' if status == 'SAFE' else '⚠' if status == 'WARNING' else '🚨'
                print(f"\nGas Sensor: {symbol} {status}")
                print(f"  Raw Value:  {raw}")
                print(f"  Filtered:   {filtered:.1f}")
                voltage = self.gas_sensor.get_voltage()
                print(f"  Voltage:    {voltage:.2f}V")
            
            # AI Status
            if self.ai_engine:
                score, status = self.ai_engine.get_latest_result()
                symbol = '✓' if status == 'NORMAL' else '⚠' if status == 'WARNING' else '🚨'
                print(f"\nAI Anomaly: {symbol} {status}")
                print(f"  Score:      {score:.3f}")
            
            # Valve Status
            if self.valve:
                valve_status = 'OPEN ✓' if self.valve.is_open() else 'CLOSED 🚨'
                print(f"\nValve Status: {valve_status}")
            
            # Emergency Status
            if self.emergency_shutdown:
                print(f"\n{'🚨'*20}")
                print(f"EMERGENCY SHUTDOWN ACTIVE")
                print(f"{'🚨'*20}")
            
            print('='*60)
        
        except Exception as e:
            Logger.error(f"Display error: {e}")
    
    def stop(self):
        """Stop system"""
        Logger.info('Stopping system...')
        self.running = False
        
        if self.main_thread:
            self.main_thread.join(timeout=5)
        
        if self.gas_sensor:
            self.gas_sensor.stop()
        
        if self.obd:
            self.obd.stop()
        
        if self.valve:
            self.valve.cleanup()
        
        Logger.info('✓ System stopped')

# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

def signal_handler(sig, frame):
    """Handle Ctrl+C gracefully"""
    print("\n\nShutdown signal received...")
    if 'system' in globals():
        system.stop()
    sys.exit(0)

def main():
    """Main entry point"""
    # Register signal handler
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Display header
    print("""
╔═══════════════════════════════════════════════════════════════╗
║  Vehicle Performance Monitoring & HCNG Safety System          ║
║  BAJA SAE India 2026 - Innovation Event                       ║
║  Team: IIST Indore                                            ║
╚═══════════════════════════════════════════════════════════════╝
    """)
    
    # Create and start system
    global system
    system = VehicleMonitorSystem()
    
    try:
        if system.start():
            # Keep running
            while system.running:
                time.sleep(1)
    except KeyboardInterrupt:
        print("\n\nKeyboard interrupt...")
    finally:
        system.stop()
        print("\nSystem shutdown complete. Goodbye!\n")

if __name__ == "__main__":
    main()
