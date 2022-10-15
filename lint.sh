#!/bin/bash

set -e

BASE="$(dirname $0)"
cd "$BASE"

echo 'pylint...'
pylint ./*.py
echo 'Black...'
black --diff --check -q ./*.py
echo 'flake8...'
flake8

echo All checks passed.