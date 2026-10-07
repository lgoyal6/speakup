# Evidence: first slice

- `PYTHONPATH=services python3 -m unittest discover -s tests -p 'test*.py' -v` proves state transition invariants, tenant isolation, duplicate sync handling, revision preservation, export, and deterministic processor recovery.
- `python -m compileall services tests` checks all Python modules compile.
- The local API can be started with `PYTHONPATH=services python -m speakup.api` and exercised with the curl flow in the README.
