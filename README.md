**httpeat is a recursive, parallel and multi-mirror/multi-proxy HTTP downloader**

![demo: downloading files](doc/httpeat_demo.gif)

Features:
- parses HTTP index pages and HTTP URLs list, provided as arguments or from text file
- **recursive and parallel crawling** of index pages
- **download in parallel** multiple URLs, with configurable tasks count
- **fast interrupt and resume** mechanism, even on hundreds of thousands files directories as it remembers where indexing and downloads were interrupted
- **robust retry** and resumes transfers automatically
- supports downloading **in parallel from multiple mirrors**
- supports downloading **in parallel from multiple proxies**
- best suited for bandwidth limited servers  

![overview](doc/httpeat_overview_0.4.png)

# Usage

```
usage: httpeat.py [-h] [-A USER_AGENT] [-d] [-i] [-I] [-k] [-m MIRROR] [-P] [-q] [-s SKIP] [-t TIMEOUT] [-T] [-v] [-w WAIT] [-x PROXY] [-z TASKS_COUNT] session_name [targets ...]

httpeat v0.3 - a recursive, parallel and multi-mirror/multi-proxy HTTP downloader

positional arguments:
  session_name          name of the session
  targets               to create a session, provide URLs to HTTP index or files, or path of a source txt file

options:
  -h, --help            show this help message and exit
  -A USER_AGENT, --user-agent USER_AGENT
                        user agent
  -d, --download-only   only download already listed files
  -i, --index-only      only list all files recursively, do not download
  -I, --index-debug     drop in interactive ipython shell during indexing
  -k, --no-ssl-verify   do no verify the SSL certificate in case of HTTPS connection
  -m MIRROR, --mirror MIRROR
                        mirror definition to load balance requests, eg. "http://host1/data/ mirrors http://host2/data/"
                        can be specified multiple times.
                        only valid uppon session creation, afterwards you must modify session mirrors.txt.
  -P, --no-progress     disable progress bar
  -q, --quiet           quiet output, show only warnings
  -s SKIP, --skip SKIP  skip rule: dl-(path|size-gt):[pattern]. can be specified multiple times.
  -t TIMEOUT, --timeout TIMEOUT
                        in seconds, default to {TO_DEFAULT}
  -T, --no-index-touch  do not create empty .download files uppon indexing
  -v, --verbose         verbose output, specify twice for http request debug
  -w WAIT, --wait WAIT  wait after request for n to n*3 seconds, for each task
  -x PROXY, --proxy PROXY
                        proxy URL: "(http[s]|socks5)://<host>:<port>[ tasks-count=N]"
                        can be specified multiple times to loadbalance downloads between proxies.
                        optional tasks-count overrides the golbal tasks-count.
                        only valid uppon session creation, afterwards you must modify session proxies.txt.
  -z TASKS_COUNT, --tasks-count TASKS_COUNT
                        number of parallel tasks, defaults to 3
```

## Example usage

- crawl HTTP index page and linked files
```
httpeat antennes https://ferme.ydns.eu/antennes/bands/2024-10/
```

- resume after interrupt
```
httpeat antennes
```

- crawl HTTP index page, using mirror from host2
```
httpeat bigfiles https://host1/data/ -m "https://host2/data/ mirrors https://host1/data/"
```

- crawl HTTP index page, using 2 proxies
```
httpeat bigfiles https://host1/data/ -x "socks4://192.168.0.2:3000" -x "socks4://192.168.0.3:3000"
```

- crawl 2 HTTP index directory pages
```
httpeat bigfiles https://host1/data/one/ https://host1/data/six/
```

- download 3 files
```
httpeat bigfiles https://host1/data/bigA.iso https://host1/data/six/bigB.iso https://host1/otherdata/bigC.iso
```

- download 3 files with URLs from txt file
```
cat <<-_EOF > ./list.txt
https://host1/data/bigA.iso
https://host1/data/six/bigB.iso
https://host1/otherdata/bigC.iso
_EOF
httpeat bigfiles ./list.txt
```

## Session directory structure
```
<session_name>/
   data/
      ...downloaded files...
   log.txt
   state_download.csv
   state_index.csv
   targets.txt
   mirrors.txt
   proxies.txt
```

## Progress output

progress output:
unless -P / --no-progress is specified, a live progress output is displayed bellow logs on several bars:
- `dl-<Mirror><proxy><task_number>`
per-file download progress bars, defaults to 3 parrallel tasks.
will be duplicated per mirror (uppercase letter 'A' to 'Z') and per proxy (lowercase letter 'a' to 'z')
- `dl`
downloader global progress bar, based on total downloaded size, and also shows downloaded file count and e<error-count>
- `idx`
indexer global progress bar, based on number of pages indexed and e<error-count>

Note that several indexer tasks are also running in parallel, but they don't have their own progress bar as their individual tasks are supposedely quickly finished.

## State files format

In session directory, the files `state_{{download,index}}.csv` respect the bellow format.

```
type,url,date,size,state
```

- type
  - f: file
  - d: directory
- url
full url of the file or directory to download
- date
when indexing, modification date of files to download is parsed from HTTP index page
- size
when indexing, size of files to download is parsed from HTTP index page
- state
  - ok: downloading is complete
  - progress: downloading is incomplete and in progress or will be resumed on next startup
  - error: downloading is stopped due to errors and will not be resumed
  - todo: downloading has not started yet
  - skipped: downloading is skipped due to -s / --skip rules, and will be re-evaluated on next startup
  - ignore: downloading is permanently skipped (useful to manualy ignore specific files)

# Installation

```
pip install httpeat
```

# Limitations

files count:
- above approximalety 1 000 000 files in the download queue, httpeat will start to eat your CPU.

live progress:
- showing live progress eats CPU, even if we throtle it to 0.5 frames per second. if it is too much for you, use -P / --no-progress.
- showing live progress while activating verbose messages with -v / --verbose may eat a lot of CPU, since the 'rich' library needs to process all the logs. try using -P / --no-progress when activating verbose logs.

# Change log / todo list

```
v0.1 : introduce robust retry capabilities and stability improvements
- while downloading store <file>.download, then rename when done
- improve index parser capability to handle unknown pages
- test that the URL "unquote" to path works, in dowload mode
- accept text file URL list as argument, also useful for testing
- store local files with full URL path including host
- existing session do not need URL of file list. prepare for "download from multiple hosts"
- retry immediatly on download error
  see "Retrying HTTPX Requests" https://scrapfly.io/blog/web-scraping-with-python-httpx/
  for testing see https://github.com/Colin-b/pytest_httpx
- retry count per entry, then drop it and mark as error
- keep old states, in case last ones get corrupted
- maybe log file with higher log level and timestamp ? or at least time for start and end ? (last option implemented)
- prevent SIGINT during CSV state file saving

v0.2 : download from multiple mirrors and proxies, better progress bar, various fixes
- hide begining of URL on info print when single root prefix is identified
- unit tests for network errors
- fix progress update of indexer in download-only mode: store progress and it's task id in State_*
  and update in indexer/downloader
- argument to skip gt size
- fix modification date of downloaded files when doing final mv. don't fix directories for now
- add rich line for current file of each download task: name, size, retry count
- progress download bar should show size, and file count as additional numbers
- progress bar should be black and white
- progress bars should  display bytes per second for download
- display file path instead of URL after download completed
- display file size after path after download completed
- handle file names len > 255
- create all .download empty files during indexing, option to disable
- download from multiple mirrors
- fix bug with state_dl size progress, grows much too fast
- download from multiple proxies
- configurable user agent

v0.3 : first public version, various fixes
- fix 'rich' flickering on dl workers progress, by creating Group after all progress add_task() are performed.
- fix download size estimation for completed and total, by correctly handling in-progress files on startup.
- fix handling of SIGTERM, by dedirecing raising SIGINT
- fix show 'index' line all the time, even if nothing to do
- fix dl/idx progress bar position to match dl workers
- display errors count on dl progress bar
- print download stats at end of session
- cleanup code and review documentation
- package with pyproject.toml
- public version

TODO v0.4 - indexing supports resume, proxies and mirrors. heavy general internal refactor and better error handling
- fix error reporting in indexer table parsing
- fix error count statistic display at end of session
- refactor session into Httpeat object to share objects easily between downloader and indexer
- support resume in index_worker(), using common Httpeat.download_file() function with download_worker()
- store index pages while indexing in .<dirname>.index
- fix display of completeted items to show only successfully indexed/downloaded items
- report errors and warnings count at end of session
- return process error code as per error count
- support proxies and mirros in indexing
- fix handling of SIGTERM and SIGINT
- extend network tests to validate that repeated interruptions do not corrupt data
- fix download total size
- fix Exception accounting and reporting
- prevent multiple parallel executions of same session
- show name instead of path in progress bar

TODO v1.0
TODO fix rich library flickering of log scrolling over progress bar, see TODO in code at "RICH_USE_TABLE"
TODO cleanup and review

TODO v1.1
TODO ? when size is not found in index, perform HEAD requests in indexer ?

TODO LATER
TODO pluggable indexer
TODO set directories mtime from index
TODO profile code to see if we can improve performance with large download lists / CSV
TODO download single file over multiple mirrors and proxies ?
TODO httpeat-select to edit files status in download state file, using textual
     automatic reload in httpeat when state file change ? dangerous ?
     OR required httpeat to be stopped
```
