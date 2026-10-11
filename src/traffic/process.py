"""Bounded probes and exact ownership of experiment child processes."""

from dataclasses import dataclass
from pathlib import Path
import subprocess
import tempfile
import time


def stop_process(process):
    """Terminate and reap this child only; never search by name or port."""
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
    else:
        process.wait()


def owns_listener(pid, port):
    """Match a Linux listening socket to this child's open file descriptors.

    /proc/PID/net is scoped to the child's network namespace. Checking socket
    ownership also prevents an existing unrelated listener satisfying readiness.
    """
    root = Path('/proc') / str(pid)
    try:
        sockets = {
            entry.readlink().as_posix()[8:-1]
            for entry in (root / 'fd').iterdir()
            if entry.readlink().as_posix().startswith('socket:[')
        }
        for family in ('tcp', 'tcp6'):
            for line in (root / 'net' / family).read_text().splitlines()[1:]:
                fields = line.split()
                if (fields[3] == '0A' and int(fields[1].split(':')[1], 16) == port
                        and fields[9] in sockets):
                    return True
    except (FileNotFoundError, ProcessLookupError):
        return False
    return False


@dataclass
class ManagedService:
    process: object
    log: object
    port: int
    name: str

    @property
    def pid(self):
        return self.process.pid

    def assert_ready(self):
        if self.process.poll() is not None or not owns_listener(self.pid, self.port):
            raise RuntimeError(f'{self.name} is not listening on port {self.port}')

    def stop(self):
        stop_process(self.process)
        self.log.close()


def start_service(host, argv, port, timeout=5):
    log = tempfile.TemporaryFile(mode='w+b')
    service = None
    try:
        process = host.popen(argv, stdout=log, stderr=subprocess.STDOUT)
        service = ManagedService(process, log, port, f'{host.name}:{argv[0]}')
        deadline = time.monotonic() + timeout
        while process.poll() is None:
            if owns_listener(process.pid, port):
                return service
            if time.monotonic() >= deadline:
                break
            time.sleep(0.05)
        stop_process(process)
        log.seek(0)
        detail = log.read().decode(errors='replace').strip()
        raise RuntimeError(f'{service.name} failed to start on port {port}: '
                           f'{detail or "readiness timeout"}')
    except BaseException:
        if service is not None:
            service.stop()
        else:
            log.close()
        raise


def run_probe(host, argv, timeout):
    """Return output, exit status and diagnostic; reap children on all paths."""
    process = None
    try:
        process = host.popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True)
        try:
            output, _ = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            stop_process(process)
            output, _ = process.communicate()
            return output, process.returncode, f'Probe timed out after {timeout}s'
        return output, process.returncode, (
            '' if process.returncode == 0 else f'Probe exited with status {process.returncode}')
    except OSError as exc:
        return '', None, str(exc)
    finally:
        if process is not None:
            stop_process(process)
