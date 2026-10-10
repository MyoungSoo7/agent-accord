"""Unit tests. Run from research/fdc_secom: python -I -m unittest discover -s tests -t ."""

import os

# Same reason as in run_all.py: spin-waiting OpenMP threads crawl on a shared box.
# Set before any test module imports numpy / scikit-learn.
os.environ.setdefault("OMP_WAIT_POLICY", "PASSIVE")
