#!/bin/bash
#
# -p '*.py' required because our unit test files don't match default 'test_*.py' pattern
#
# use coverage like this to make sure it runs within the context of our virtualenv (if you run coverage directly this doesn't work)
python3 -m coverage run -m unittest discover -p '*.py' -s tests/ -t . --verbose && coverage html --omit "virtual_env/*","*dist-packages*","tests/*"
