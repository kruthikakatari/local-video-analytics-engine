"""
ByteTrack multi-object tracker.

Implementation of the ByteTrack algorithm (Zhang et al., "ByteTrack: Multi-Object
Tracking by Associating Every Detection Box", ECCV 2022), written with only
NumPy + SciPy so it runs the same on any laptop.

Key idea: most trackers throw away low-confidence boxes. ByteTrack keeps them and
uses them in a SECOND matching stage, which recovers people who are partly
occluded (their confidence drops) instead of losing their ID.

Per frame:
  1. Predict every track's new position with a Kalman filter.
  2. Match HIGH-confidence boxes to tracks (IoU + Hungarian algorithm).
  3. Match LOW-confidence boxes to the tracks still unmatched.
  4. Start new tracks from unmatched high-confidence boxes.
  5. Tracks unmatched for `track_buffer` frames are removed.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import linear_sum_assignment

from .object_detector import Detection


# --------------------------------------------------------------------------- Kalman
class KalmanFilterXYAH:
    """Constant-velocity Kalman filter on (center x, center y, aspect ratio, height)."""

    def __init__(self):
        ndim, dt = 4, 1.0
        self._motion = np.eye(2 * ndim)
        for i in range(ndim):
            self._motion[i, ndim + i] = dt
        self._update = np.eye(ndim, 2 * ndim)
        self._std_pos = 1.0 / 20
        self._std_vel = 1.0 / 160

    def initiate(self, measurement):
        mean = np.r_[measurement, np.zeros(4)]
        h = measurement[3]
        std = [2 * self._std_pos * h, 2 * self._std_pos * h, 1e-2, 2 * self._std_pos * h,
               10 * self._std_vel * h, 10 * self._std_vel * h, 1e-5, 10 * self._std_vel * h]
        return mean, np.diag(np.square(std))

    def predict(self, mean, cov):
        h = mean[3]
        std = [self._std_pos * h, self._std_pos * h, 1e-2, self._std_pos * h,
               self._std_vel * h, self._std_vel * h, 1e-5, self._std_vel * h]
        mean = self._motion @ mean
        cov = self._motion @ cov @ self._motion.T + np.diag(np.square(std))
        return mean, cov

    def update(self, mean, cov, measurement):
        h = mean[3]
        std = [self._std_pos * h, self._std_pos * h, 1e-1, self._std_pos * h]
        proj_mean = self._update @ mean
        proj_cov = self._update @ cov @ self._update.T + np.diag(np.square(std))
        gain = np.linalg.solve(proj_cov, (cov @ self._update.T).T).T
        mean = mean + (measurement - proj_mean) @ gain.T
        cov = cov - gain @ proj_cov @ gain.T
        return mean, cov


def xyxy_to_xyah(b):
    w, h = b[2] - b[0], b[3] - b[1]
    return np.array([b[0] + w / 2, b[1] + h / 2, w / max(h, 1e-6), h], dtype=float)


def xyah_to_xyxy(m):
    w = m[2] * m[3]
    return np.array([m[0] - w / 2, m[1] - m[3] / 2, m[0] + w / 2, m[1] + m[3] / 2])


def iou_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)))
    x1 = np.maximum(a[:, None, 0], b[None, :, 0])
    y1 = np.maximum(a[:, None, 1], b[None, :, 1])
    x2 = np.minimum(a[:, None, 2], b[None, :, 2])
    y2 = np.minimum(a[:, None, 3], b[None, :, 3])
    inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
    area_a = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    area_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    return inter / (area_a[:, None] + area_b[None, :] - inter + 1e-9)


def linear_assignment(cost: np.ndarray, thresh: float):
    """Hungarian matching; pairs costing more than `thresh` are rejected."""
    if cost.size == 0:
        return [], list(range(cost.shape[0])), list(range(cost.shape[1]))
    rows, cols = linear_sum_assignment(cost)
    matches = [(r, c) for r, c in zip(rows, cols) if cost[r, c] <= thresh]
    mr = {r for r, _ in matches}
    mc = {c for _, c in matches}
    return (matches,
            [r for r in range(cost.shape[0]) if r not in mr],
            [c for c in range(cost.shape[1]) if c not in mc])


# --------------------------------------------------------------------------- tracks
NEW, TRACKED, LOST, REMOVED = range(4)


@dataclass
class TrackedObject:
    """What the rest of the pipeline sees for each confirmed track."""
    track_id: int
    box: tuple[float, float, float, float]
    confidence: float
    class_name: str


class STrack:
    def __init__(self, det: Detection, kf: KalmanFilterXYAH):
        self.kf = kf
        self.mean, self.cov = None, None
        self._box = np.asarray(det.box, dtype=float)
        self.score = det.confidence
        self.class_name = det.class_name
        self.track_id = 0
        self.state = NEW
        self.is_activated = False
        self.frame_id = 0
        self.start_frame = 0

    @property
    def box(self) -> np.ndarray:
        return self._box if self.mean is None else xyah_to_xyxy(self.mean[:4])

    def predict(self):
        if self.state != TRACKED:
            self.mean = self.mean.copy()
            self.mean[7] = 0  # freeze height velocity while lost
        self.mean, self.cov = self.kf.predict(self.mean, self.cov)

    def activate(self, frame_id: int):
        self.mean, self.cov = self.kf.initiate(xyxy_to_xyah(self._box))
        self.state = TRACKED
        self.is_activated = frame_id == 1  # tracks on the very first frame are trusted immediately
        self.frame_id = self.start_frame = frame_id

    def update(self, det: "STrack", frame_id: int):
        self.mean, self.cov = self.kf.update(self.mean, self.cov, xyxy_to_xyah(det._box))
        self.score = det.score
        self.class_name = det.class_name
        self.state = TRACKED
        self.is_activated = True
        self.frame_id = frame_id


class ByteTracker:
    def __init__(self, high_thresh: float = 0.5, low_thresh: float = 0.1,
                 new_track_thresh: float = 0.5, match_thresh: float = 0.8,
                 track_buffer: int = 30):
        self.high_thresh = high_thresh
        self.low_thresh = low_thresh
        self.new_track_thresh = new_track_thresh
        self.match_thresh = match_thresh
        self.track_buffer = track_buffer
        self.kf = KalmanFilterXYAH()
        self.frame_id = 0
        self._next_id = 0
        self.tracked: list[STrack] = []
        self.lost: list[STrack] = []

    def _new_id(self) -> int:
        self._next_id += 1
        return self._next_id

    @staticmethod
    def _cost(tracks, dets, fuse_score=True):
        if not tracks or not dets:
            return np.zeros((len(tracks), len(dets)))
        ious = iou_matrix(np.array([t.box for t in tracks]), np.array([d._box for d in dets]))
        if fuse_score:  # ByteTrack "fuse_score": weight IoU by detection confidence
            ious = ious * np.array([d.score for d in dets])[None, :]
        return 1 - ious

    def update(self, detections: list[Detection]) -> list[TrackedObject]:
        self.frame_id += 1
        dets = [STrack(d, self.kf) for d in detections if d.confidence >= self.low_thresh]
        high = [d for d in dets if d.score >= self.high_thresh]
        low = [d for d in dets if d.score < self.high_thresh]

        unconfirmed = [t for t in self.tracked if not t.is_activated]
        active = [t for t in self.tracked if t.is_activated]
        pool = active + [t for t in self.lost if t not in active]
        for t in pool + unconfirmed:
            t.predict()

        activated, refound, lost_now, removed = [], [], [], []

        # Stage 1: high-confidence detections vs all active + lost tracks.
        matches, u_track, u_det = linear_assignment(self._cost(pool, high), self.match_thresh)
        for ti, di in matches:
            t = pool[ti]
            was_lost = t.state == LOST
            t.update(high[di], self.frame_id)
            (refound if was_lost else activated).append(t)

        # Stage 2: low-confidence detections vs remaining TRACKED tracks.
        remaining = [pool[i] for i in u_track if pool[i].state == TRACKED]
        matches, u_track2, _ = linear_assignment(
            self._cost(remaining, low, fuse_score=False), 0.5)
        for ti, di in matches:
            remaining[ti].update(low[di], self.frame_id)
            activated.append(remaining[ti])
        for i in u_track2:
            t = remaining[i]
            t.state = LOST
            lost_now.append(t)

        # Unconfirmed tracks (seen once) need a second high-confidence match.
        left_high = [high[i] for i in u_det]
        matches, u_unconf, u_det = linear_assignment(self._cost(unconfirmed, left_high), 0.7)
        for ti, di in matches:
            unconfirmed[ti].update(left_high[di], self.frame_id)
            activated.append(unconfirmed[ti])
        for i in u_unconf:
            unconfirmed[i].state = REMOVED
            removed.append(unconfirmed[i])

        # Stage 3: new tracks from confident, unmatched detections.
        for i in u_det:
            d = left_high[i]
            if d.score >= self.new_track_thresh:
                d.activate(self.frame_id)
                activated.append(d)

        # Drop tracks lost for too long.
        for t in self.lost:
            if t.state == LOST and self.frame_id - t.frame_id > self.track_buffer:
                t.state = REMOVED
                removed.append(t)

        self.tracked = [t for t in self.tracked if t.state == TRACKED]
        self.tracked += [t for t in activated + refound if t not in self.tracked]
        self.lost = [t for t in self.lost + lost_now
                     if t.state == LOST and t not in self.tracked]

        # IDs are handed out only once a track is confirmed, so the IDs users see
        # stay consecutive (Person #1, #2, #3 ...) instead of skipping numbers.
        for t in self.tracked:
            if t.is_activated and t.track_id == 0:
                t.track_id = self._new_id()

        return [TrackedObject(t.track_id, tuple(map(float, t.box)), float(t.score), t.class_name)
                for t in self.tracked if t.is_activated]
