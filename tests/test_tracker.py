"""ByteTrack: IDs must stay the same while objects move, and differ between objects."""
from analytics.object_detector import Detection
from analytics.tracker import ByteTracker, iou_matrix
import numpy as np


def person(x, y, conf=0.9):
    return Detection((x, y, x + 50, y + 120), conf, "person")


def test_iou():
    a = np.array([[0, 0, 10, 10]], float)
    b = np.array([[0, 0, 10, 10], [5, 0, 15, 10], [20, 20, 30, 30]], float)
    assert np.allclose(iou_matrix(a, b), [[1.0, 1 / 3, 0.0]], atol=1e-6)


def test_ids_persist_while_moving():
    tr = ByteTracker()
    ids = []
    for step in range(15):
        out = tr.update([person(100 + step * 4, 100), person(400 - step * 4, 120)])
        ids.append(sorted(o.track_id for o in out))
    assert ids[-1] == [1, 2]
    assert all(i == [1, 2] for i in ids[1:])


def test_low_confidence_box_keeps_track_alive():
    """ByteTrack's second stage: an occluded person (low score) keeps the same ID."""
    tr = ByteTracker(high_thresh=0.5)
    for step in range(5):
        tr.update([person(100 + step * 3, 100, 0.9)])
    out = tr.update([person(115, 100, 0.3)])      # confidence drops (occlusion)
    assert [o.track_id for o in out] == [1]
    out = tr.update([person(118, 100, 0.9)])
    assert [o.track_id for o in out] == [1]


def test_lost_track_recovered_and_new_person_gets_new_id():
    tr = ByteTracker(track_buffer=10)
    for step in range(5):
        tr.update([person(100 + step * 2, 100)])
    for _ in range(3):
        assert tr.update([]) == []                # person hidden for 3 frames
    out = tr.update([person(112, 100)])
    assert [o.track_id for o in out] == [1]       # same person, same ID
    tr.update([person(114, 100), person(500, 300)])
    out = tr.update([person(116, 100), person(502, 300)])
    assert sorted(o.track_id for o in out) == [1, 2]


def test_single_low_confidence_detection_does_not_create_track():
    tr = ByteTracker(new_track_thresh=0.5)
    for _ in range(3):
        assert tr.update([person(100, 100, 0.3)]) == []
