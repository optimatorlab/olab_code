from olab_camera.cv_features import _Calibrate


def test_calibration_stop_preserves_terminal_result():
    feature = object.__new__(_Calibrate)
    feature.deque = __import__('collections').deque([{'state': 'success', 'resolution': '640x480'}], maxlen=1)
    feature.idName = 'default'
    feature.isThreadActive = True

    class Logger:
        def log(self, *_args, **_kwargs):
            pass

    camera = type('Camera', (), {'calibrate': {'default': feature}, 'dec': {'dequeRemove': []}, 'logger': Logger()})()
    feature.camObject = camera
    feature.decorationID = None
    feature.stop()
    assert feature.deque[-1]['state'] == 'success'
