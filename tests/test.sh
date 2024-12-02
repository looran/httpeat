#!/bin/sh

set -e

D=$(dirname $0)
$D/test_httpeat_local.py
$D/test_httpeat_network.sh

echo "[*] tests local + network OK"
