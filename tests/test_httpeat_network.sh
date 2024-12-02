#!/bin/sh

trace() { echo "$ $*" >&2; "$@"; }

assert_states_diff() {
	trace diff -u $DIR/expect_$s/targets.txt $s/targets.txt
	trace diff -u $DIR/expect_$s/mirrors.txt $s/mirrors.txt
	trace diff -u $DIR/expect_$s/proxies.txt $s/proxies.txt
	trace diff -u $DIR/expect_$s/state_index.csv $s/state_index.csv
	trace diff -u $DIR/expect_$s/state_download.csv $s/state_download.csv
}

set -e

DIR="$(realpath $(dirname $0))"
cd $DIR
tc=0

[ ! -z "$1" ] && [ "$1" -eq "$1" ] && test=$1 && shift

if [ "$test" = "1" -o -z "$test" ]; then
	s=test_1
	echo "[-] $s: index-only an nginx style HTTP index"
	trace cd /tmp
	trace rm -rf $s
	trace $DIR/../httpeat.py -A "myua" -i $@ $s https://ferme.ydns.eu/antennes/split/
	assert_states_diff
	echo "[*] $s OK"
	tc=$(($tc+1))
fi

if [ "$test" = "2" -o -z "$test" ]; then
	s=test_2
	echo "[-] $s: index and download an nginx style HTTP index"
	trace cd /tmp
	trace rm -rf $s
	trace $DIR/../httpeat.py $@ $s https://ferme.ydns.eu/antennes/bands/2024-10/
	assert_states_diff
	trace diff -r -u $DIR/expect_$s/data/ $s/data/
	echo "[*] $s OK"
	tc=$(($tc+1))
fi

if [ "$test" = "3" -o -z "$test" ]; then
	s=test_3
	echo "[-] $s: interrupt and resume as index and download an nginx style HTTP index"
	trace cd /tmp
	trace rm -rf $s
	trace timeout 1 $DIR/../httpeat.py $@ $s https://ferme.ydns.eu/antennes/bands/2024-10/ ||true
	trace timeout 2 $DIR/../httpeat.py $@ $s ||true
	trace $DIR/../httpeat.py $@ $s
	assert_states_diff
	trace diff -r -u $DIR/expect_$s/data/ $s/data/
	echo "[*] $s OK"
	tc=$(($tc+1))
fi

if [ "$test" = "4" -o -z "$test" ]; then
	echo "[-] test_4: using mirrors, interrupt and resume as index and download an nginx style HTTP index"
	s=test_4
	trace cd /tmp
	trace rm -rf $s
	trace timeout 1 $DIR/../httpeat.py $@ -m "https://ferme.ydns.eu/ant/ mirrors https://ferme.ydns.eu/antennes/" $s https://ferme.ydns.eu/antennes/bands/2024-10/ ||true
	trace timeout 2 $DIR/../httpeat.py $@ $s ||true
	trace $DIR/../httpeat.py $@ $s
	assert_states_diff
	trace diff -r -u $DIR/expect_$s/data/ $s/data/
	echo "[*] $s OK"
	tc=$(($tc+1))
fi

[ $tc -eq 0 ] && echo "usage: $0 [1-4] [<httpeat_arguments>]" && exit 1

echo "[*] all ($tc) network tests OK, success"
