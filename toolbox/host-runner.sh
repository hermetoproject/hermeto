#!/bin/bash
executable=$(basename $0)
set -x
exec flatpak-spawn --host $executable "$@"
