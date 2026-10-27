"""Controller-side Prometheus /metrics endpoint, started by `benchprof run
--live-metrics` alongside the per-node benchprof-exporter processes.

Client nodes don't produce elbencho's throughput/IOPS numbers - the
controller does, via the single `elbencho_live.csv` written in run_dir as
the benchmark executes (see collect.py's elbencho_cmd / parsers/elbencho.py
for the full post-run parse). This tails that same file every POLL_S
while the run is in progress and serves the latest "Total" row (the only
row that actually carries MiB/s/IOPS/latency values - see
parsers/elbencho.py) as gauges, so a Prometheus server scraping the
controller gets live throughput alongside each client's live CPU/Mem/
process/link gauges from benchprof-exporter.
"""

import csv
import http.server
import socketserver
import threading
from pathlib import Path
from typing import Optional

POLL_S = 1.0

_METRIC_COLUMNS = [
    ("MiB/s", "benchprof_throughput_mibps"),
    ("IOPS", "benchprof_iops"),
    ("Entries/s", "benchprof_entries_per_sec"),
    ("Lat Ent us", "benchprof_latency_entry_us"),
    ("Lat IO us", "benchprof_latency_io_us"),
]


class _ControllerCollector:
    def __init__(self, csv_path: Path):
        self.csv_path = csv_path
        self.lock = threading.Lock()
        self.values = {}  # (metric_name, entity) -> float

    def poll_once(self):
        # Broad except around the whole body (not just the file open) is
        # deliberate: elbencho appends a new line every --liveint and this
        # runs concurrently, so a read can race a partial/interrupted write
        # (truncated last row, mid-flush header, odd encoding). csv.Error
        # and friends aren't OSErrors, and without this they'd propagate
        # out of poll_once(), kill the daemon poller thread for good (no
        # auto-restart), and silently freeze /metrics for the rest of the
        # run even though the file keeps being written correctly afterwards.
        try:
            self._poll_once_unsafe()
        except Exception:
            pass

    def _poll_once_unsafe(self):
        if not self.csv_path.is_file() or self.csv_path.stat().st_size == 0:
            return
        latest_by_entity = {}
        with open(self.csv_path, newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                return
            for d in reader:
                rank_raw = (d.get("Rank") or "").strip()
                # Only the Total row carries real values (see
                # parsers/elbencho.py) - per-rank rows leave these blank.
                if rank_raw not in ("", "-1", "Total"):
                    continue
                entity = (d.get("MixType") or "").strip() or "io"
                latest_by_entity[entity] = d

        updated = {}
        for entity, d in latest_by_entity.items():
            for col, metric_name in _METRIC_COLUMNS:
                raw = (d.get(col) or "").strip()
                if raw == "":
                    continue
                try:
                    updated[(metric_name, entity)] = float(raw)
                except ValueError:
                    continue
        if updated:
            with self.lock:
                self.values.update(updated)

    def render(self) -> str:
        with self.lock:
            values = dict(self.values)
        lines = []
        for _, metric_name in _METRIC_COLUMNS:
            lines.append(f"# TYPE {metric_name} gauge")
            for (m, entity), v in sorted(values.items()):
                if m != metric_name:
                    continue
                lines.append(f'{m}{{entity="{entity}"}} {v}')
        return "\n".join(lines) + "\n"


def _poll_loop(collector: _ControllerCollector, stop_event: threading.Event):
    while not stop_event.is_set():
        collector.poll_once()
        stop_event.wait(POLL_S)


def _make_handler(collector: _ControllerCollector):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.rstrip("/") in ("", "/metrics"):
                body = collector.render().encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/healthz":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, fmt, *args):
            pass

    return Handler


class ControllerExporter:
    """Background thread + HTTP server; call stop() when the run ends."""

    def __init__(self, run_dir: Path, port: int):
        self.collector = _ControllerCollector(Path(run_dir) / "elbencho_live.csv")
        self._stop_event = threading.Event()
        self._poller = threading.Thread(
            target=_poll_loop, args=(self.collector, self._stop_event), daemon=True
        )
        self._server: Optional[socketserver.ThreadingTCPServer] = None
        self._server_thread: Optional[threading.Thread] = None
        self.port = port

    def start(self) -> None:
        self._poller.start()
        # allow_reuse_address must be a class attribute set *before*
        # construction: ThreadingTCPServer.__init__ calls server_bind()
        # immediately (bind_and_activate defaults to True), so setting the
        # instance attribute afterwards is too late to affect SO_REUSEADDR
        # and a socket left in TIME_WAIT by a prior crashed/killed run
        # would make the next run fail with "Address already in use".
        class _ReusableServer(socketserver.ThreadingTCPServer):
            allow_reuse_address = True

        self._server = _ReusableServer(("0.0.0.0", self.port), _make_handler(self.collector))
        self._server_thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._server_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
