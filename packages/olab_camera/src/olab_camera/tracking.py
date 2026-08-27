"""Detector-agnostic local multi-object tracking helpers.

The public payload deliberately contains plain Python/NumPy values.  The
optional ``trackers``/``supervision`` dependencies are an internal adapter
detail so a detector is never coupled to their object types.
"""

import threading
import time
from collections import deque
from copy import deepcopy

import cv2
import numpy as np

import olab_utils


_BACKENDS = {
    'sort': 'SORTTracker',
    'bytetrack': 'ByteTrackTracker',
    'ocsort': 'OCSORTTracker',
    'botsort': 'BoTSORTTracker',
}


def _normalise_payload(payload):
    """Validate one public detection mapping and return defensive NumPy copies."""
    if not isinstance(payload, dict) or 'xyxy' not in payload:
        raise ValueError('detections must be a mapping containing xyxy')
    xyxy = np.asarray(payload['xyxy'], dtype=float)
    if xyxy.ndim == 1 and xyxy.shape[0] == 0:
        xyxy = np.empty((0, 4), dtype=float)
    if xyxy.ndim != 2 or xyxy.shape[1] != 4 or not np.isfinite(xyxy).all():
        raise ValueError('xyxy must be a finite N x 4 array')
    result = {'xyxy': xyxy.copy()}
    count = len(xyxy)
    optional = {'class_id', 'class', 'class_conf', 'masks'}
    unknown = set(payload) - optional - {'xyxy'}
    if unknown:
        raise ValueError(f'unsupported detection fields: {sorted(unknown)}')
    for name in optional:
        if name not in payload:
            continue
        value = payload[name]
        if value is None or len(value) != count:
            raise ValueError(f'{name} must be an N-length sequence when supplied')
        if name == 'class_id':
            values = np.asarray(value)
            if count and not np.issubdtype(values.dtype, np.integer):
                raise ValueError('class_id must contain integral values')
            result[name] = values.astype(int, copy=True)
        elif name == 'class_conf':
            values = np.asarray(value, dtype=float)
            if not np.isfinite(values).all() or np.any(values < 0) or np.any(values > 1):
                raise ValueError('class_conf must contain finite values in [0, 1]')
            result[name] = values.copy()
        elif name == 'class':
            if not all(isinstance(item, str) for item in value):
                raise ValueError('class must contain strings')
            result[name] = list(value)
        else:
            result[name] = list(value)
    return result


def _copy_payload(payload):
    """Return an independent payload for one backend invocation."""
    copied = {'xyxy': payload['xyxy'].copy()}
    for name, value in payload.items():
        if name == 'xyxy':
            continue
        copied[name] = value.copy() if isinstance(value, np.ndarray) else deepcopy(value)
    return copied


def _make_backend(algorithm, fps_target):
    """Construct a pinned-package tracker lazily, retaining optional imports."""
    if algorithm not in _BACKENDS:
        raise ValueError(f'algorithm must be one of {sorted(_BACKENDS)}')
    try:
        import trackers
    except Exception as exc:
        raise RuntimeError('tracking support requires olab-camera[tracking]') from exc
    return _BackendAdapter(getattr(trackers, _BACKENDS[algorithm])(frame_rate=float(fps_target)))


def _to_backend_detections(payload):
    try:
        import supervision as sv
    except Exception as exc:
        raise RuntimeError('tracking support requires olab-camera[tracking]') from exc
    data = {}
    if 'class' in payload:
        data['class'] = np.asarray(payload['class'], dtype=object)
    if 'masks' in payload:
        data['masks'] = np.asarray(payload['masks'], dtype=object)
    return sv.Detections(
        xyxy=payload['xyxy'],
        confidence=payload.get('class_conf'),
        class_id=payload.get('class_id'),
        data=data,
    )


def _from_backend_detections(detections, original):
    count = len(detections.xyxy)
    result = {
        'xyxy': np.asarray(detections.xyxy, dtype=float).tolist(),
        'track_id': (np.full(count, -1, dtype=int) if getattr(detections, 'tracker_id', None) is None
                     else np.asarray(detections.tracker_id, dtype=int)).tolist(),
    }
    data = getattr(detections, 'data', {}) or {}
    if 'class_id' in original and getattr(detections, 'class_id', None) is not None:
        result['class_id'] = np.asarray(detections.class_id, dtype=int).tolist()
    if 'class_conf' in original and getattr(detections, 'confidence', None) is not None:
        result['class_conf'] = np.asarray(detections.confidence, dtype=float).tolist()
    if 'class' in original:
        values = data.get('class')
        if values is not None:
            result['class'] = np.asarray(values, dtype=object).tolist()
    if 'masks' in original:
        values = data.get('masks')
        if values is not None:
            result['masks'] = list(values)
    return result


class _BackendAdapter:
    """Keep vendor detection objects inside the real optional backend boundary."""

    def __init__(self, backend):
        self._backend = backend

    def update(self, payload, frame=None, timestamp=None):
        detections = _to_backend_detections(payload)
        output = self._backend.update(detections, frame=frame, timestamp=timestamp)
        return _from_backend_detections(output, payload)


class _TrackerFeature:
    """One camera-owned, synchronous tracker with a latest-result deque."""

    def __init__(self, camObject, idName, algorithm, fps_target, postFunction,
                 postFunctionArgs, color, drawBox, drawLabel, maskOutline, decorate):
        self.camObject = camObject
        self.idName = idName
        self.algorithm = algorithm
        self.color = color
        self.drawBox = True if drawBox is None else drawBox
        self.drawLabel = self.drawBox if drawLabel is None else drawLabel
        self.maskOutline = maskOutline
        self.decorate = decorate
        self.postFunction = postFunction or olab_utils._passFunction
        self.postFunctionArgs = dict(postFunctionArgs or {})
        self.postFunctionArgs['idName'] = idName
        self.deque = deque([{'xyxy': [], 'track_id': []}], maxlen=1)
        self._lock = threading.Lock()
        self._active = True
        self._last_timestamp = None
        self.decorationID = None
        self.backend = _make_backend(algorithm, fps_target)

    @property
    def isThreadActive(self):
        """Compatibility lifecycle flag; this feature intentionally owns no thread."""
        return self._active

    def start(self):
        if self.decorate and self.decorationID is None:
            self.decorationID = int(time.time() * 1000)
            self.camObject.dec['dequeAdd'].append({
                'function': self._decorate, 'idName': self.idName,
                'decorationID': self.decorationID,
            })

    def update(self, payload, frame=None, timestamp=None):
        with self._lock:
            if not self._active:
                raise RuntimeError('tracker is stopped')
            if timestamp is not None and self._last_timestamp is not None and timestamp < self._last_timestamp:
                raise ValueError('timestamp is earlier than this tracker\'s previous update')
            result = self.backend.update(_copy_payload(payload), frame=frame, timestamp=timestamp)
            self.deque.append(result)
            if timestamp is not None:
                self._last_timestamp = timestamp
        args = dict(self.postFunctionArgs)
        args['result'] = result
        self.postFunction(args)
        return result

    def _decorate(self, img, **kwargs):
        with self._lock:
            result = dict(self.deque[0]) if self.deque else {'xyxy': [], 'track_id': []}
        for index, box in enumerate(result.get('xyxy', [])):
            x1, y1, x2, y2 = (int(value) for value in box)
            if self.drawBox:
                cv2.rectangle(img, (x1, y1), (x2, y2), self.color, 2)
            if self.drawLabel:
                label = str(result.get('track_id', [-1])[index])
                if 'class' in result:
                    label = f"{result['class'][index]} #{label}"
                cv2.putText(img, label, (x1, max(0, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX,
                            0.5, self.color, 1, cv2.LINE_AA)

    def stop(self):
        with self._lock:
            if not self._active and not self.deque:
                return
            self._active = False
            self.deque.clear()
        if self.decorationID is not None:
            self.camObject.dec['dequeRemove'].append(self.decorationID)
            self.decorationID = None
