# Evidence: first slice

- `PYTHONPATH=services python3 -m unittest discover -s tests -p 'test*.py' -v` proves state transition invariants, tenant isolation, duplicate and out-of-order sync handling, revision preservation, export, deterministic processor recovery, and restart-safe job claiming.
- `python -m compileall services tests` checks all Python modules compile.
- The local API can be started with `PYTHONPATH=services python3 -m speakup.api` and exercised with the curl flow in the README.
- `/metrics` exposes the in-flight job gauge for a Prometheus scrape; alert rules live under `infra/monitoring/prometheus`.
