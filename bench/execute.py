"""Isolated execution oracle.

One (probe, program-variant) observation == one forked child. The child writes
the variant source to a temp file, imports it, and calls the probe with the
variant bound to `_m`. Fork keeps crashes, hangs and global state pollution out
of the parent, and costs about a millisecond.

No worker pool: a method under test executes its probes sequentially, which is
what the declared per-probe cost model prices. Bulk work (ground truth) uses
`fanout` below, which forks sibling workers that each run jobs sequentially.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import sys
import tempfile

REPR_CAP = 20000


def _canonical(value) -> str:
    """Byte-stable, *type-faithful* rendering for the types probes may return.

    Types are tagged because the un-tagged form is ambiguous: without a tag,
    the integer 1 and the string "1" render identically, so a mutation that
    changed a probe's return from 1 to "1" would be scored as invisible -- a
    false negative in the measurement itself, which is the one place a silent
    error is least acceptable. A tagged form makes every distinct value distinct.
    """
    if isinstance(value, str):
        return f"s:{value}"
    if isinstance(value, bool):
        return f"b:{value}"
    if isinstance(value, int):
        return f"i:{value}"
    if isinstance(value, float):
        return f"f:{value!r}"
    if value is None:
        return "n:None"
    if isinstance(value, (list, tuple)):
        open_c, close_c = ("l", "]") if isinstance(value, list) else ("t", ")")
        return open_c + ",".join(_canonical(v) for v in value) + close_c
    if isinstance(value, dict):
        return "d{" + ",".join(
            f"{_canonical(k)}:{_canonical(v)}" for k, v in value.items()
        ) + "}"
    return f"r:{value!r}"


def _child(conn, src_text: str, probe_src: str) -> None:
    try:
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "variant.py")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(src_text)
            spec = importlib.util.spec_from_file_location("regpt_variant", path)
            module = importlib.util.module_from_spec(spec)
            sys.modules["regpt_variant"] = module
            spec.loader.exec_module(module)

            scope: dict = {}
            exec(compile(probe_src, "<probe>", "exec"), scope)
            value = scope["__regpt_probe__"](module)

        text = _canonical(value)
        payload = {
            "status": "ok",
            "text": text[:REPR_CAP],
            "hash": hashlib.sha256(text[:REPR_CAP].encode("utf-8", "replace")).hexdigest(),
        }
    except BaseException as exc:  # noqa: BLE001 - the exception IS the observation
        text = f"{type(exc).__name__}: {exc}"[:REPR_CAP]
        payload = {
            "status": "exc",
            "text": text,
            "hash": hashlib.sha256(text.encode("utf-8", "replace")).hexdigest(),
        }
    try:
        conn.send(payload)
        conn.close()
    except BaseException:  # noqa: BLE001
        pass
    finally:
        os._exit(0)


def execute(src_text: str, probe_src: str, timeout: float = 2.0) -> dict:
    """Run one probe against one program variant. Never raises."""
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:  # child
        try:
            os.close(read_fd)
            conn = _Conn(write_fd)
            _child(conn, src_text, probe_src)
        except BaseException:  # noqa: BLE001
            pass
        finally:
            os._exit(0)

    os.close(write_fd)
    try:
        payload = _read_conn(read_fd, timeout)
    finally:
        os.close(read_fd)
        _reap(pid)
    if payload is None:
        return {"status": "timeout", "text": "", "hash": "TIMEOUT"}
    return payload


class _Conn:
    """Minimal writer over a raw fd, avoiding multiprocessing's daemon rules."""

    def __init__(self, fd: int) -> None:
        self._fd = fd

    def send(self, obj) -> None:
        import pickle

        data = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
        header = len(data).to_bytes(8, "little")
        os.write(self._fd, header)
        view = memoryview(data)
        while view:
            written = os.write(self._fd, view)
            view = view[written:]

    def close(self) -> None:
        try:
            os.close(self._fd)
        except OSError:
            pass


def _read_conn(read_fd: int, timeout: float):
    """Read a length-prefixed pickle from a pipe, killing on timeout."""
    import pickle
    import select
    import signal
    import struct

    deadline_header = _wait_readable(read_fd, timeout)
    if not deadline_header:
        return None
    header = _read_exact(read_fd, 8)
    if header is None:
        return None
    (length,) = struct.unpack("<Q", header)
    if length > 8 * 1024 * 1024:
        return None
    body = _read_exact(read_fd, length, timeout)
    if body is None:
        return None
    try:
        return pickle.loads(body)
    except BaseException:  # noqa: BLE001
        return None


def _wait_readable(fd: int, timeout: float) -> bool:
    import select

    ready, _, _ = select.select([fd], [], [], timeout)
    return bool(ready)


def _read_exact(fd: int, count: int, timeout: float | None = None):
    import struct

    buf = bytearray()
    while len(buf) < count:
        if timeout is not None:
            remaining = timeout
            if not _wait_readable(fd, remaining):
                return None
        chunk = os.read(fd, count - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def _reap(pid: int, budget: float = 0.1) -> None:
    """Wait up to `budget` seconds for a child, then kill it.

    Probe children get a small budget so a hung mutant cannot wedge the parent.
    Bulk fanout workers get a large one, since they run a whole slice.
    """
    import signal
    import time

    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        try:
            done, _ = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return
        if done:
            return
        time.sleep(0.002)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass


def fanout(jobs: list, workers: int = 4) -> list:
    """Run many jobs across forked sibling workers, preserving order.

    Used only for building ground truth and for baselines, never by a method
    under test. Each worker runs its slice sequentially and writes its results
    to its own file, so no framing is needed on a shared pipe.
    """
    if not jobs:
        return []
    import pickle
    import tempfile

    workers = max(1, min(workers, len(jobs)))
    stride = (len(jobs) + workers - 1) // workers
    slices = [jobs[i * stride : (i + 1) * stride] for i in range(workers)]
    slices = [s for s in slices if s]

    td = tempfile.mkdtemp(prefix="regpt-fanout-")
    paths = [os.path.join(td, f"w{i}.pkl") for i in range(len(slices))]

    pids = []
    for sl, path in zip(slices, paths):
        pid = os.fork()
        if pid == 0:
            out = []
            for job in sl:
                try:
                    out.append(execute(job[0], job[1], job[2]))
                except BaseException as exc:  # noqa: BLE001
                    out.append({"status": "harness-error", "text": repr(exc)[:REPR_CAP], "hash": "ERR"})
            try:
                # os._exit skips buffer flushes, so write and fsync by hand.
                # A single write() can be partial, so loop until fully written.
                blob = pickle.dumps(out, protocol=pickle.HIGHEST_PROTOCOL)
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                try:
                    view = memoryview(blob)
                    while view:
                        view = view[os.write(fd, view):]
                    os.fsync(fd)
                finally:
                    os.close(fd)
            except BaseException:  # noqa: BLE001
                pass
            os._exit(0)
        pids.append(pid)

    for pid in pids:
        _reap(pid, budget=3600.0)

    results: list = []
    for path in paths:
        chunk = None
        try:
            with open(path, "rb") as fh:
                chunk = pickle.load(fh)
        except BaseException:  # noqa: BLE001
            chunk = None
        if chunk is None:
            chunk = [{"status": "harness-error", "text": "worker lost", "hash": "ERR"}] * stride
        results.extend(chunk)
        try:
            os.unlink(path)
        except OSError:
            pass
    try:
        os.rmdir(td)
    except OSError:
        pass

    while len(results) < len(jobs):
        results.append({"status": "harness-error", "text": "worker lost", "hash": "ERR"})
    return results[: len(jobs)]
