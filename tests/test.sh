#!/bin/sh

set -e

D=$(dirname $0)
ITER=${1:-1}
LOG=/tmp/httpeat_tests.log
echo running $ITER iterations
echo logging to $LOG

ok=0
for it in $(seq 1 $ITER); do
	date;
	echo iteration $it;
	echo "" > $LOG
	echo "local tests"
	$D/test_httpeat_local.py >> $LOG 2>&1 ||break
	echo "network tests"
	#$D/test_httpeat_network.sh >> $LOG 2>&1 ||break
	#$D/test_httpeat_network.sh -P >> $LOG 2>&1 ||break
	$D/test_httpeat_network.sh -vP >> $LOG 2>&1 ||break
	ok=1
	date
	echo iteration $it OK
	sleep 3
done

echo "tests logs: $LOG"
if [ $ok -eq 1 ]; then
	echo "[*] OK tests local + network after $it iterations"
else
	echo "[!] FAIL tests on iteration $it"
fi
