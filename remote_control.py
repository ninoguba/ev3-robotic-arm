#!/usr/bin/env python3
# ev3-robot-arm 6dof, originally by Nino Guba.
# v2 improved by Marno van der Molen;
# - bugfixes
# - don't require grabber attachment to run
# - more debug output for troubleshooting
# - improved gamepad responsiveness
# - proportional control for some motors
# - code cleanup / simplify
# v2.1 refinements by Marno van der Molen:
# - simlify code
# - allow changing speed of movement by holding d-pad up/down
# - optionally support a color sensor to align waist by pressing d-pad left/right
# v2.2 minor improvements by Marno van der Molen
# - maintain grabber grip during spin
# - increase joystick deadzone a bit to prevent unintended movement while pressing L3/R3
# - start work on calibration support using touch sensors
# - prevent calculate_speed() returning values over 100 which causes exceptions

__author__ = 'Nino Guba'

import logging
import os
import sys
import threading
import time

import evdev
import rpyc
from signal import signal, SIGINT
from ev3dev2 import DeviceNotFound
from ev3dev2.led import Leds
from ev3dev2.sensor import INPUT_1, INPUT_2, INPUT_3, INPUT_4
from ev3dev2.sensor.lego import ColorSensor, TouchSensor
from ev3dev2.motor import OUTPUT_A, OUTPUT_B, OUTPUT_C, OUTPUT_D, LargeMotor, MoveTank
from ev3dev2.power import PowerSupply

# from ev3dev2.sound import Sound
from evdev import InputDevice

from math_helper import scale_stick


# Config
REMOTE_HOST = '10.42.0.3'
JOYSTICK_DEADZONE = 20

# Define speeds
FULL_SPEED = 100
FAST_SPEED = 75
NORMAL_SPEED = 50
SLOW_SPEED = 25
VERY_SLOW_SPEED = 10

# Setup logging
os.system('setfont Lat7-Terminus12x6')
logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                    format='%(message)s')
logger = logging.getLogger(__name__)


def reset_motors():
    """ reset motor positions to default """
    logger.info("Resetting motors...")
    waist_motor.reset()
    shoulder_motors.reset()
    elbow_motor.reset()
    roll_motor.reset()
    pitch_motor.reset()
    spin_motor.reset()
    if grabber_motor:
        grabber_motor.reset()


# Initial setup

# RPyC
# Setup on slave EV3: https://ev3dev-lang.readthedocs.io/projects/python-ev3dev/en/stable/rpyc.html
# Create a RPyC connection to the remote ev3dev device.
# Use the hostname or IP address of the ev3dev device.
# If this fails, verify your IP connectivty via ``ping X.X.X.X``
logger.info("Connecting RPyC to {}...".format(REMOTE_HOST))
# change this IP address for your slave EV3 brick
conn = rpyc.classic.connect(REMOTE_HOST)
# remote_ev3 = conn.modules['ev3dev.ev3']
remote_power_mod = conn.modules['ev3dev2.power']
remote_motor = conn.modules['ev3dev2.motor']
remote_led = conn.modules['ev3dev2.led']
logger.info("RPyC started succesfully")

# Gamepad
# If bluetooth is not available, check https://github.com/ev3dev/ev3dev/issues/1314
logger.info("Connecting wireless controller...")
gamepad = InputDevice(evdev.list_devices()[0])
if gamepad.name != 'Wireless Controller':
    logger.error('Failed to connect to wireless controller')
    sys.exit(1)

# LEDs
leds = Leds()
remote_leds = remote_led.Leds()

# Power
power = PowerSupply(name_pattern='*ev3*')
remote_power = remote_power_mod.PowerSupply(name_pattern='*ev3*')

# Sound
# sound = Sound()

# Primary EV3
# Sensors
try:
    color_sensor = ColorSensor(INPUT_1)
    color_sensor.mode = ColorSensor.MODE_COL_COLOR
    logger.info("Color sensor detected!")
except DeviceNotFound:
    logger.info("Color sensor not detected (primary EV3, input 1) - running without it...")
    color_sensor = False

try:
    shoulder_touch = TouchSensor(INPUT_3)
    logger.info("Shoulder touch sensor detected!")
except DeviceNotFound:
    logger.info("Shoulder touch sensor not detected (primary EV3, input 3) - running without it...")
    shoulder_touch = False

try:
    elbow_touch = TouchSensor(INPUT_4)
    logger.info("Elbow touch sensor detected!")
except DeviceNotFound:
    logger.info("Elbow touch sensor not detected (primary EV3, input 4) - running without it...")
    elbow_touch = False

# Motors
waist_motor = LargeMotor(OUTPUT_A)
shoulder_motors = MoveTank(OUTPUT_B, OUTPUT_C)
elbow_motor = LargeMotor(OUTPUT_D)

# Secondary EV3
# Motors
roll_motor = remote_motor.MediumMotor(remote_motor.OUTPUT_A)
pitch_motor = remote_motor.MediumMotor(remote_motor.OUTPUT_B)
pitch_motor.stop_action = remote_motor.MediumMotor.STOP_ACTION_COAST
spin_motor = remote_motor.MediumMotor(remote_motor.OUTPUT_C)

try:
    grabber_motor = remote_motor.MediumMotor(remote_motor.OUTPUT_D)
    grabber_motor.stop_action = remote_motor.MediumMotor.STOP_ACTION_COAST
    logger.info("Grabber motor detected!")
except DeviceNotFound:
    logger.info("Grabber motor not detected (secondary EV3, port D) - running without it...")
    grabber_motor = False


# Not sure why but resetting all motors before doing anything else seems to improve reliability
reset_motors()


# Variables for stick input
shoulder_speed = 0
elbow_speed = 0

# Variables for button input
waist_left = False
waist_right = False
roll_left = False
roll_right = False
pitch_up = False
pitch_down = False
spin_left = False
spin_right = False
grabber_open = False
grabber_close = False

# We are running!
running = True

def log_power_info():
    logger.info('Local battery power: {}V / {}A'.format(round(power.measured_volts,2 ), round(power.measured_amps, 2)))
    logger.info('Remote battery power: {}V / {}A'.format(round(remote_power.measured_volts, 2), round(remote_power.measured_amps, 2)))


speed_modifier = 0
def calculate_speed(speed, max=100):
    if speed_modifier == 0:
        return min(speed, max)
    elif speed_modifier == -1:  # dpad up
        return min(speed * 1.5, max)
    elif speed_modifier == 1:  # dpad down
        return min(speed / 1.5, max)
    

waist_target_color = 0
aligning_waist = False
def align_waist_to_color(waist_target_color):
    if waist_target_color == -1:
        target_color = ColorSensor.COLOR_RED
    elif waist_target_color == 1:
        target_color = ColorSensor.COLOR_BLUE
    else:
        # if someone asks us to move to an unknown/unmapped
        # color, just make this a noop.
        return
    
    # Set a flag for the MotorThread to prevent stopping the waist motor while
    # we're trying to align it
    global aligning_waist
    aligning_waist = True

    # If we're not on the correct color, start moving but make sure there's a 
    # timeout to prevent trying forever.
    if color_sensor.color != target_color:
        logger.info('Moving to color {}...'.format(target_color))
        waist_motor.on(NORMAL_SPEED)

        max_iterations = 100
        iterations = 0
        while color_sensor.color != target_color:
            # wait a bit between checks. Ideally there would be a wait_for_color() 
            # method or something, but as far as I know that's not possible with the 
            # current libraries, so we do it like this.
            time.sleep(0.1)
            
            # prevent running forver
            iterations += 1
            if iterations >= max_iterations:
                logger.info('Failed to align waist to requested color {}'.format(target_color))
                break
        
        # we're either aligned or reached a timeout. Stop moving.
        waist_motor.stop()

    # update flag for MotorThead so waist control works again.
    aligning_waist = False


def clean_shutdown(signal_received=None, frame=None):
    """ make sure all motors are stopped when stopping this script """
    logger.info('Shutting down...')

    global running
    running = False
    
    logger.info('waist..')
    waist_motor.stop()
    logger.info('shoulder..')
    shoulder_motors.stop()
    logger.info('elbow..')
    elbow_motor.stop()
    logger.info('pitch..')
    # For some reason the pitch motor sometimes gets stuck here, and a reset helps?
    # pitch_motor.reset()
    pitch_motor.stop()
    logger.info('roll..')
    roll_motor.stop()
    logger.info('spin..')
    spin_motor.stop()

    if grabber_motor:
        logger.info('grabber..')
        grabber_motor.stop()

    # See https://github.com/gvalkov/python-evdev/issues/19 if this raises exceptions, but it seems 
    # stable now.
    gamepad.close()

    logger.info('Shutdown completed.')
    sys.exit(0)


class WaistAlignThread(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)

    def run(self):
        logger.info("WaistAlignThread running!")
        while running:
            if waist_target_color != 0 and not aligning_waist:
                align_waist_to_color(waist_target_color)
            time.sleep(2)  # prevent performance impact, drawback is you need to hold the button for a bit before it registers
        logger.info("WaistAlignThread stopping!")


class MotorThread(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)

    def run(self):
        logger.info("MotorThread running!")
        # os.system('setfont Lat7-Terminus12x6')
        leds.set_color("LEFT", "BLACK")
        leds.set_color("RIGHT", "BLACK")
        remote_leds.set_color("LEFT", "BLACK")
        remote_leds.set_color("RIGHT", "BLACK")
        # sound.play_song((('C4', 'e'), ('D4', 'e'), ('E5', 'q')))
        leds.set_color("LEFT", "GREEN")
        leds.set_color("RIGHT", "GREEN")
        remote_leds.set_color("LEFT", "GREEN")
        remote_leds.set_color("RIGHT", "GREEN")

        logger.info("Starting main loop...")
        while running:
            # Proportional control
            if shoulder_speed != 0:
                shoulder_motors.on(shoulder_speed, shoulder_speed)
            elif shoulder_motors.is_running:
                shoulder_motors.stop()
            
            # Proportional control
            if elbow_speed != 0:
                elbow_motor.on(elbow_speed)
            elif elbow_motor.is_running:
                elbow_motor.stop()

            # on/off control
            if not aligning_waist:
                if waist_left:
                    waist_motor.on(calculate_speed(-SLOW_SPEED))
                elif waist_right:
                    waist_motor.on(calculate_speed(SLOW_SPEED))
                elif waist_motor.is_running:
                    waist_motor.stop()

            # on/off control
            if roll_left:
                roll_motor.on(calculate_speed(-SLOW_SPEED))
            elif roll_right:
                roll_motor.on(calculate_speed(SLOW_SPEED))
            elif roll_motor.is_running:
                roll_motor.stop()

            # on/off control
            #
            # Pitch affects grabber as well, but to a lesser degree. We could improve this 
            # in the future to adjust grabber based on pitch movement as well.
            if pitch_up:
                pitch_motor.on(calculate_speed(VERY_SLOW_SPEED))
            elif pitch_down:
                pitch_motor.on(calculate_speed(-VERY_SLOW_SPEED))
            elif pitch_motor.is_running:
                pitch_motor.stop()

            # on/off control
            #
            # If we keep spinning, the grabber motor can get stuck because it remains stationary
            # but is forced to move around the worm gear. We need to adjust it while spinning.
            # 
            # spin motor: 7:1 (=23.6RPM) 
            # grabber motor: 1:1 (=165RPM) untill the worm gear which we need to keep steady
            # 
            # So, I think the grabber_motor needs to move 7 times slower than the spin_motor 
            # to maintain it's position.
            # 
            # NOTE: I'm using knob wheels to control the grabber, which is not smoothly rotating 
            # at these low speeds. Therefor the grabber has to move a bit quicker for me, but I 
            # think when using regular gears the 7 ratio should be sufficient.
            GRABBER_SPIN_RATIO = 6.5
            if spin_left:
                spin_motor_speed = calculate_speed(-SLOW_SPEED)
                spin_motor.on(spin_motor_speed)
                if grabber_motor:
                    # determine grabber_motor speed based on spin_motor speed & invert
                    grabber_sync_speed = (spin_motor_speed / GRABBER_SPIN_RATIO) * -1
                    grabber_motor.on(grabber_sync_speed, False)
                    # logger.info('Spin motor {}, grabber {}'.format(spin_motor_speed, grabber_sync_speed))
            elif spin_right:
                spin_motor_speed = calculate_speed(SLOW_SPEED)
                spin_motor.on(spin_motor_speed)
                if grabber_motor:
                    # determine grabber_motor speed based on spin_motor speed & invert
                    grabber_sync_speed = (spin_motor_speed / GRABBER_SPIN_RATIO) * -1
                    grabber_motor.on(grabber_sync_speed, False)
                    # logger.info('Spin motor {}, grabber {}'.format(spin_motor_speed, grabber_sync_speed))
            elif spin_motor.is_running:
                spin_motor.stop()
                if grabber_motor:
                    grabber_motor.stop()

            # on/off control - can only control this directly if we're not currently spinning
            elif grabber_motor:
                if grabber_open:
                    grabber_motor.on(calculate_speed(NORMAL_SPEED), False)
                elif grabber_close:
                    grabber_motor.on(calculate_speed(-NORMAL_SPEED), False)
                elif grabber_motor.is_running:
                    grabber_motor.stop()
        
        logger.info("MotorThread stopping!")


# Ensure clean shutdown on CTRL+C
signal(SIGINT, clean_shutdown)

log_power_info()

# Main motor control thread
motor_thread = MotorThread()
motor_thread.setDaemon(True)
motor_thread.start()

# We only need the WaistAlignThread if we detected a color sensor
if color_sensor:
    waist_align_thread = WaistAlignThread()
    waist_align_thread.setDaemon(True)
    waist_align_thread.start()

# Handle gamepad input
for event in gamepad.read_loop():  # this loops infinitely
    if event.type == 3:  # stick input
        if event.code == 0:  # Left stick X-axis
            shoulder_speed = scale_stick(event.value, deadzone=JOYSTICK_DEADZONE, invert=True)
        elif event.code == 3:  # Right stick X-axis
            elbow_speed = scale_stick(event.value, deadzone=JOYSTICK_DEADZONE)
        elif event.code == 17:  # dpad up/down
            speed_modifier = event.value
        elif event.code == 16:  # dpad left/right
            waist_target_color = event.value

    elif event.type == 1:  # button input

        if event.code == 310:  # L1
            if event.value == 1:
                waist_right = False
                waist_left = True
            elif event.value == 0:
                waist_left = False

        elif event.code == 311:  # R1
            if event.value == 1:
                waist_left = False
                waist_right = True
            elif event.value == 0:
                waist_right = False

        elif event.code == 308:  # Square
            if event.value == 1:
                roll_right = False
                roll_left = True
            elif event.value == 0:
                roll_left = False

        elif event.code == 305:  # Circle
            if event.value == 1:
                roll_left = False
                roll_right = True
            elif event.value == 0:
                roll_right = False

        elif event.code == 307:  # Triangle
            if event.value == 1:
                pitch_down = False
                pitch_up = True
            elif event.value == 0:
                pitch_up = False

        elif event.code == 304:  # X
            if event.value == 1:
                pitch_up = False
                pitch_down = True
            elif event.value == 0:
                pitch_down = False

        elif event.code == 312:  # L2
            if event.value == 1:
                spin_right = False
                spin_left = True
            elif event.value == 0:
                spin_left = False

        elif event.code == 313:  # R2
            if event.value == 1:
                spin_left = False
                spin_right = True
            elif event.value == 0:
                spin_right = False

        elif event.code == 317:  # L3
            if event.value == 1:
                grabber_close = False
                grabber_open = True
            elif event.value == 0:
                grabber_open = False

        elif event.code == 318:  # R3
            if event.value == 1:
                grabber_open = False
                grabber_close = True
            elif event.value == 0:
                grabber_close = False

        elif event.code == 314 and event.value == 1:  # Share
            # debug info
            log_power_info()
         
        elif event.code == 315 and event.value == 1:  # Options
            # debug info
            logger.info('Elbow motor state: {}'.format(elbow_motor.state))
            logger.info('Elbow motor duty cycle: {}'.format(elbow_motor.duty_cycle))
            logger.info('Elbow motor speed: {}'.format(elbow_motor.speed))

        elif event.code == 316 and event.value == 1:  # PS
            # stop control loop
            running = False

            # Move motors to default position
            # motors_to_center()

            # sound.play_song((('E5', 'e'), ('C4', 'e')))
            leds.set_color("LEFT", "BLACK")
            leds.set_color("RIGHT", "BLACK")
            remote_leds.set_color("LEFT", "BLACK")
            remote_leds.set_color("RIGHT", "BLACK")

            time.sleep(1)  # Wait for the motor thread to finish
            break

clean_shutdown()
