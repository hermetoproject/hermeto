#!/bin/bash
executable=$(basename $0)
exec flatpak-spawn --host $executable "$@"
