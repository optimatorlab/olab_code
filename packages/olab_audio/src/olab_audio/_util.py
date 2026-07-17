import numpy as np


def defaultFromNone(val, default, test=None):
    """If user doesn't specify a value, return a default."""
    try:
        if val is None:
            val = default

        if test in (int, float, str):
            return test(val)
        else:
            return val
    except Exception as e:
        raise Exception(f'Error in defaultFromNone: {e}')


def convert_to_db(data):
    try:
        amp = np.max(np.abs(data))
        if amp == 0:
            return -60
        else:
            return 20 * np.log10(amp)  # or 20*log10(amp/ref), where ref = 1
    except Exception:
        return -61


def bytes2np(bytesarray, dtype):
    """bytesarray is raw sound data. dtype is an np data type, e.g. 'int16', 'float32'."""
    return np.frombuffer(bytesarray, dtype=dtype)


def np2bytes(nparray):
    return nparray.tobytes()


def np2np(ys, newDtype, scaling=1):
    """Convert a given np array (`ys`) to type `newDtype`, optionally scaling (normalizing) it."""
    return ys.astype(newDtype) * scaling
