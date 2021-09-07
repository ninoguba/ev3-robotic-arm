# Math helpers in separate file for easy unit testing
def scale(val, src, dst):
    return (float(val - src[0]) / (src[1] - src[0])) * (dst[1] - dst[0]) + dst[0]


def scale_stick(value, deadzone=10, scale_to=80, invert=False):
    """ scale a range of input to a range of output, optionally applying a deadzone or inverting the result """
    result = scale(value, (0, 255), (-scale_to, scale_to))

    if deadzone and result < deadzone and result > -deadzone:
        result = 0

    if invert:
        result *= -1

    return result
