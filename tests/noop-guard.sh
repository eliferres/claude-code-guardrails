#!/usr/bin/env bash
# A guard that guards nothing. The liveness harness runs every red case against
# this stub: a case that still passes here was never testing the guard.
cat > /dev/null
exit 0
