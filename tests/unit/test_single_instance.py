import pytest
from voice_commander.single_instance import SingleInstanceLock, AlreadyRunning


def test_first_acquire_succeeds(tmp_path):
    lock = SingleInstanceLock(tmp_path / "daemon.lock")
    lock.acquire()
    lock.release()


def test_second_acquire_raises(tmp_path):
    a = SingleInstanceLock(tmp_path / "daemon.lock")
    a.acquire()
    b = SingleInstanceLock(tmp_path / "daemon.lock")
    with pytest.raises(AlreadyRunning):
        b.acquire()
    a.release()
    b.acquire()
    b.release()


def test_stale_lock_is_reclaimed(tmp_path):
    """If lock file has dead PID, new instance takes over."""
    lock_path = tmp_path / "daemon.lock"
    lock_path.write_text("999999")  # almost certainly dead
    lock = SingleInstanceLock(lock_path)
    lock.acquire()
    lock.release()
