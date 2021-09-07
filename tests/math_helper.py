import unittest
from math_helper import scale, scale_stick


VALID_DEFAULT_INPUT = [
    (0, -80),
    (136, 0),
    (255, 80),
    (128, 0)
]

VALID_DEADZONE_INPUT = [
    (0, 0, -80),
    (255, 0, 80),
    (128, 0, 0),
    (136, 10, 0),
    (140, 10, 0),
    (140, 5, 7),
    (150, 10, 14),
    (255, 0, 80),
    (128, 0, 0),
]


class TestMathHelper(unittest.TestCase):

    def test_scale_stick_default(self):
        for input_set in VALID_DEFAULT_INPUT:
            with self.subTest(data=input_set):
                self.assertEqual(scale_stick(input_set[0]), input_set[1])

    def test_scale_stick_default_inverted(self):
        for input_set in VALID_DEFAULT_INPUT:
            with self.subTest(data=input_set):
                self.assertEqual(scale_stick(input_set[0], invert=True), -input_set[1])

    def test_scale_stick_deadzone(self):
        for input_set in VALID_DEADZONE_INPUT:
            with self.subTest(data=input_set):
                self.assertEqual(int(scale_stick(input_set[0], deadzone=input_set[1])), input_set[2])
