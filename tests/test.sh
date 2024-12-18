#!/bin/sh

set -e

D=$(dirname $0)
ITER=${1:-2}
LOG=/tmp/httpeat_tests.log
echo running $ITER iterations
echo logging to $LOG

ok=0
export TIMEFORMAT="%R seconds"
for it in $(seq 1 $ITER); do
	echo === iteration $it ===
	date;
	echo "" > $LOG
	echo "local tests"
	time $D/test_httpeat_local.py >> $LOG 2>&1 ||break
	echo "network tests"
	opts="-vP"
	[ $it -eq 1 ] \
		&& echo "first iteration, enabling verbose + progress bar (slower)" \
		&& opts="-v"
	time $D/test_httpeat_network.sh $opts >> $LOG 2>&1 ||break
	ok=1
	echo iteration $it OK
	sleep 3
done

echo "tests logs: $LOG"
if [ $ok -eq 1 ]; then
	echo "[*] OK tests local + network after $it iterations"
else
	echo "[!] FAIL tests on iteration $it"
fi
