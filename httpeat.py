#!/usr/bin/env python3

# 2024, Laurent Ghigonis <ooookiwi@protonmail.com>

VERSION = "0.3"
DESCRIPTION = f"a recursive, parallel and multi-mirror/multi-proxy HTTP downloader"
EPILOG = f"""session directory structure:
<session_name>/
   data/
      ...downloaded files...
   log.txt
   state_download.csv
   state_index.csv
   targets.txt
   mirrors.txt
   proxies.txt

progress output:
unless -P / --no-progress is specified, a live progress output is displayed bellow logs:
   dl-<Mirror><proxy><task_number>
      file download progress lines, defaults to 3 parrallel tasks
      it is specific to a mirror (uppercase letter) and a proxy (lowercase letter)
   dl
      downloader global progress, based on total downloaded size, and also shows downloaded file count and e<error-count>
   idx
      indexer global progress, based on number of pages indexed and e<error-count>

examples:
- crawl HTTP index page and linked files
httpeat antennes https://ferme.ydns.eu/antennes/bands/2024-10/
- resume after interrupt
httpeat antennes
- crawl HTTP index page, using mirror from host2
httpeat bigfilesA https://host1/data/ -m "https://host2/data/ mirrors https://host1/data/"
- crawl HTTP index page, using 2 proxies
httpeat bigfilesB https://host1/data/ -x "socks4://192.168.0.2:3000" -x "socks4://192.168.0.3:3000"
- crawl 2 HTTP index directory pages
httpeat bigfilesC https://host1/data/one/ https://host1/data/six/
- download 3 files
httpeat bigfilesD https://host1/data/bigA.iso https://host1/data/six/bigB.iso https://host1/otherdata/bigC.iso
- download 3 files with URLs from txt file
cat <<-_EOF > ./list.txt
https://host1/data/bigA.iso
https://host1/data/six/bigB.iso
https://host1/otherdata/bigC.iso
_EOF
httpeat bigfilesE ./list.txt
"""
TASKS_DEFAULT = 3
TO_DEFAULT = 15.0
STATE_SAVE_PERIOD = 600
STATE_PROGRESSREFRESH_PERIOD = 0.5
RETRY_DL_NETWORKERROR_DEFAULT = 7
RETRY_INDEX_NETWORKERROR_DEFAULT = 7
RETRY_GLOBAL_ERROR_DEFAULT = 3
FILENAME_MAXLEN = 254

import os
import re
import sys
import csv
import time
import signal
import asyncio
import random
import logging
import argparse
import datetime
import traceback
from hashlib import md5
from pathlib import Path
from base64 import b32encode
from itertools import takewhile
from urllib.parse import urljoin, urlsplit, unquote

import httpx
from bs4 import BeautifulSoup
import dateutil.parser
from humanfriendly import parse_size, format_size
from rich.default_styles import DEFAULT_STYLES
from rich.style import Style
from rich.logging import RichHandler
from rich.theme import Theme
from rich.console import Console
from rich.progress import Progress, TextColumn, BarColumn, TaskProgressColumn, MofNCompleteColumn, TimeRemainingColumn, DownloadColumn, TransferSpeedColumn
from rich.highlighter import NullHighlighter
from rich.console import Group
from rich.live import Live
from tenacity import AsyncRetrying, stop_after_attempt, wait_fixed, wait_random, retry_if_exception_type

log = logging.getLogger("httpeat")

#
# Utilities
#

def raise_sigint(signum, frame):
    signal.raise_signal(signal.SIGINT)

def now():
    return time.strftime('%Y-%m-%d %H:%M:%S %Z', time.localtime())

async def sleepy(wait, status):
    if status == "error":
        wait += 1
    await asyncio.sleep((wait) * random.uniform(1.0, 3.0))

def strs_get_common_prefix(strings):
    return ''.join(c[0] for c in takewhile(lambda x: all(x[0] == y for y in x), zip(*strings)))

def url_is_directory(url):
    if url.endswith('/') or not '.' in url[-10:]:
        return True
    return False

def url_to_path(url, sdir, urls_prefix=None):
    split = urlsplit(url)
    # find the local path (both finished and temporary) for url
    local_path = Path(f"{split.netloc}/{unquote(split.path)}")
    if len(local_path.name) > FILENAME_MAXLEN - len(".download"):
        # shorten the file name
        name = local_path.name
        h = b32encode(md5(name.encode()).digest()).decode()[:8]
        newname = f"{name[:int(FILENAME_MAXLEN/2)-10]}_{h}_{name[len(name)-(int(FILENAME_MAXLEN/2)-10):]}"
        local_path = local_path.with_name(newname)
    path_finished = sdir / "data" / local_path
    path_tmp = sdir / "data" / local_path.with_name(local_path.name + ".download")
    # prepare printable versions
    if urls_prefix:
        url_print = url[len(urls_prefix):]
        path_print = unquote(urlsplit(url_print).path)
    else:
        path_print = None
        url_print = None
    return path_finished, path_tmp, path_print, url_print

def ignore_comments(l):
    """ return a list of elements which do not start by '#' """
    return [e for e in l if not e.startswith("#") ]

def skip_check(url, entry, skip_rules):
    skip = None
    for skiprule in skip_rules:
        rule, pattern = skiprule.split(':', 1)
        match rule:
            case "dl-path":
                if re.match(pattern, url, re.I):
                    skip = skiprule
                    break
            case "dl-size-gt":
                if entry["size"] and entry["size"] > parse_size(pattern):
                    skip = skiprule
                    break
            case _:
                raise Exception(f"invalid skip pattern: {skiprule}")
    return skip

def mirrors_list(mconf):
    """ parse mirrors arguments """
    mirrors = list()
    for mirror_num, mirror_str in enumerate(mconf):
        mirror = {"name": chr(0x41 + 1 + mirror_num)}
        m = re.match(r"(?P<mirror>[^ ]+) mirrors (?P<source>[^ ]+)", mirror_str)
        if not m:
            raise Exception(f"invalid mirror spec : {mirror_str}")
        mirror.update(m.groupdict())
        mirrors.append(mirror)
    return mirrors

def proxies_list(pconf, tasks_count):
    """ parse proxies arguments """
    proxies = list()
    for proxy_num, proxy_str in enumerate(pconf):
        proxy = {"name": chr(0x61 + proxy_num) if len(pconf) > 1 else ''}
        m = re.match(r"(?P<proxy_url>(http[s]?|socks(4|5)[.]?)://[^ ]+)( tasks-count=(?P<tasks_count>[0-9]+))?", proxy_str)
        if not m:
            raise Exception(f"invalid proxy spec : {proxy_str}")
        proxy.update(m.groupdict())
        if proxy["tasks_count"] is None:
            proxy["tasks_count"] = tasks_count
        else:
            proxy["tasks_count"] = int(proxy["tasks_count"])
        proxies.append(proxy)
    return proxies

def parse_httpindex(dirurl, bs, wk_num) -> list:
    """ parse an HTTP index page """

    def _get_url(dirurl, href):
        """ ensure that url stays within parent """
        url = urljoin(dirurl, href)
        if url.startswith(dirurl):
            return url
        return None

    def _parse_raw(bs):
        # index is based on HTML <a> links in document, hazardous parsing
        # possibly in <pre>, nginx style. maybe random HTML page too.
        log.debug(f"index-{wk_num}: index parse raw")
        entries = list()
        for link in bs.find_all("a"):
            entry = URLQueue.FIELDS.copy()
            entry["url"] = _get_url(dirurl, link.get("href"))
            if link.next_sibling:
                txt = link.next_sibling.text.strip()
                if txt:
                    vals = txt.rsplit(" ", 1)
                    if len(vals) == 2:
                        try:
                            entry["date"] = str(dateutil.parser.parse(vals[0].strip()))
                        except:
                            pass
                        try:
                            entry["size"] = parse_size(vals[1])
                        except:
                            entry["size"] = -1
            entries.append(entry)
        return entries

    def _parse_table(table, n):
        # index is based on HTML <table>, apache style
        log.debug(f"index-{wk_num}: index parse table {n}")
        entries = list()
        trs = table.find_all("tr")

        # parse th table headers
        COLUMNS = {"name": ".*name.*", "date": ".*(last modified|date).*", "size": ".*size.*"}
        colidx = dict.fromkeys(COLUMNS.keys(), None)
        for th_num, th in enumerate(trs.pop(0).find_all("th")):
            txt = th.get_text(strip=True)
            for colname, pattern in COLUMNS.items():
                if re.match(pattern, txt, re.I):
                    colidx[colname] = th_num
        log.debug(f"index-{wk_num}: columns index mapping :\n{colidx}")
        if colidx["name"] is None:
            raise Exception(f"could not find 'name' index table columns")

        # read tr table entries
        for tr in trs:
            entry = URLQueue.FIELDS.copy()
            # read td table columns
            tds = tr.find_all("td")
            max_colid = max(filter(None, colidx.values()))
            if len(tds) < max_colid:
                log.debug(f"index-{wk_num}: tr does not contain enough td, found {len(tds)} expected {max_colid}, skipping:\n{tr}")
                continue
            name = tds[colidx["name"]].get_text(strip=True)
            if re.match('parent directory', name, re.I):
                continue
            if not tds[colidx["name"]].find("a"):
                log.warning(f"index-{wk_num}: could not find link in this index table entry, skipping:\n{tr}")
                continue
            entry["url"] = _get_url(dirurl, tds[colidx["name"]].a.get("href"))
            if colidx["date"]:
                date = tds[colidx["date"]].get_text(strip=True)
                if date != "-":
                    try:
                        entry["date"] = str(dateutil.parser.parse(date))
                    except Exception as e:
                        log.warning(f"index-{wk_num}: could not parse date in this entry:\n{tr}")
            if colidx["size"]:
                try:
                    entry["size"] = parse_size(tds[colidx["size"]].get_text(strip=True))
                except:
                    pass
                entries.append(entry)
        return entries

    entries = list()
    tables = bs.find_all("table")
    if len(tables) == 0:
        entries = _parse_raw(bs)
    else:
        try:
            for n, table in enumerate(tables):
                entries.extend(_parse_table(table, n))
        except Exception as e:
            log.warning(f"could not parse table, trying raw: {e}")
            log.debug(traceback.format_exc())
            entries = _parse_raw(bs)

    return entries

#
# URL Queues
#

class URLQueue(asyncio.Queue):
    """ URLQueue: store URLs for index and download tasks
    - polled for new URLs as asyncio Queues
    - keeps done URLs
    - store and load from CSV files
    - displays live progress as 'rich' Progress bar
    childs: URLQueue_idx and URLQueue_dl
    """
    FIELDS = {"type": "", "url": "", "date": "", "size": -1, "state": "todo"}
    STATES_LOAD_ORDER = ["progress", "todo", "skipped", "error", "ok", "ignore"]

    def __init__(self, sdir, fname, retry_count):
        super().__init__()
        self.sdir = sdir
        self.path = sdir / fname
        self.retry_count = retry_count
        self.size = {"total": 0, "completed": 0, "nosize": 0}
        self.stats = {"recv_bytes": 0, "recv_items": 0, "errors": 0}
        self.progress = None
        self.progress_wk = None
        self.progress_created = asyncio.Event()
        self._done = list()
        self._done_urls = list() # store only url strings, to quickly check for duplicates

        if self.path.exists():
            log.info(f"{self.NAME_PROGRESS}: loading existing queue from {self.path}")
            # load existing csv
            with self.path.open(newline='') as fd:
                reader = csv.DictReader(fd)
                for load_state in self.STATES_LOAD_ORDER:
                    for entry in reader:
                        if entry["state"] not in self.STATES_LOAD_ORDER:
                            raise Exception(f"invalid entry state {entry} in CSV {self.path}")
                        if entry["size"] == '':
                            entry["size"] = -1 # compatibility with old format
                        if entry["state"] == load_state:
                            # append entry to todo or done lists
                            if entry["state"] in ["todo", "progress", "skipped"]:
                                self.todo(entry, entry["state"])
                            elif entry["state"] in ["ok", "error", "ignore"]:
                                self.done(entry, entry["state"], no_retry=True, init_load=True)
                    fd.seek(0)
                    fd.readline()
        else:
            log.info(f"{self.NAME_PROGRESS}: starting new queue")

    def todo(self, entry, status="todo", requeue=False, touch=False):
        log.debug(f"{self.NAME}: todo {entry}")
        if entry["url"] not in self._done_urls:
            path_tmp = None
            entry["state"] = status
            self.put_nowait(entry)
            if not requeue:
                # update gobal total size
                size = int(entry["size"]) if (entry["size"] is not None and int(entry["size"]) != -1) else None
                if size is not None:
                    self.size["total"] += size
                else:
                    self.size["nosize"] += 1
                if entry["state"] == "progress":
                    # update global completed size, based on actual file size
                    path_finished, path_tmp, _, _ = url_to_path(entry["url"], self.sdir)
                    if path_tmp.exists():
                        filesize = path_tmp.stat().st_size
                        self.size["completed"] += filesize
            if touch:
                # create empty file / directory
                if path_tmp is None:
                    path_finished, path_tmp, _, _ = url_to_path(entry["url"], self.sdir)
                path_finished_exists = path_finished.exists()
                if not path_finished_exists and not path_tmp.exists():
                    if entry["type"] == 'f':
                        if not path_tmp.parent.exists():
                            path_tmp.parent.mkdir(parents=True)
                        path_tmp.touch()
                    else:
                        if not path_finished_exists:
                            path_finished.mkdir(parents=True)
        else:
            log.debug(f"{self.NAME}: entry already in done URLs, not adding")

    def done(self, entry, status="ok", no_retry=False, init_load=False):
        log.debug(f"{self.NAME}: done with status '{status}' : {entry}")
        if not no_retry and status == "error" and ("err" not in entry or entry["err"] < self.retry_count):
            # requeue in todo items
            if "err" not in entry:
                entry["err"] = 0
            entry["err"] += 1
            self.todo(entry, "progress", requeue=True)
        else:
            # mark as "ok" or "error" in done items
            entry["state"] = status
            self._done.append(entry)
            self._done_urls.append(entry["url"])
            if status == "error":
                self.stats["errors"] += 1
            if init_load:
                size = int(entry["size"]) if (entry["size"] is not None and int(entry["size"]) != -1) else None
                if size is not None:
                    self.size["completed"] += size
                    self.size["total"] += size
            else:
                self.stats["recv_items"] += 1
        if not init_load:
            self.task_done()

    def save(self):
        log.debug(f"{self.NAME}: saving to {self.path}")
        # disable keyboard interrupt to avoid file corruption
        sigint_handler = signal.signal(signal.SIGINT, signal.SIG_IGN)
        # keep a backup
        if self.path.exists():
            self.path.rename(str(self.path) + ".old")
        # store new CSV
        with self.path.open('w', newline='') as fd:
            writer = csv.DictWriter(fd, self.FIELDS.keys())
            writer.writeheader()
            for store in [self._queue, self._done]:
                for entry in sorted(store, key=lambda d: d["url"]):
                    if "err" in entry:
                        # duplicate entry to avoid changing original entry
                        entry = {k: v for k, v in entry.items() if k != "err"}
                    writer.writerow(entry)
        # restore keyboard interrupt
        signal.signal(signal.SIGINT, sigint_handler)

    def size_ajust_total(self, diff):
        self.size["total"] += diff

    def size_ajust_completed(self, diff):
        self.size["completed"] += diff
        self.stats["recv_bytes"] += diff

    def progress_init(self, proxy_list, sources):
        # compute progress bar name identation
        name_len = len("idx ")
        if len(proxy_list) > 1:
            name_len += 1
        if len(sources) > 1:
            name_len += 1
        # create progress
        pb = self.progress_init_getpb(name_len)
        self.progress = {
            "pb": pb,
            "task": pb.add_task(self.NAME_PROGRESS),
            "update": False,
        }
        self.progress_update(refresh=True)
        self.progress_created.set()

    def progress_update(self, refresh=False):
        if self.progress:
            self.progress["update"] = True
            if refresh:
                self.progress_refresh()

    def total_items(self):
        return self.qsize() + len(self._done)

    async def progress_get_renderables(self):
        await self.progress_created.wait() # wait for the tasks to initialize their progress bars
        renderables = list()
        if self.progress_wk:
            renderables.append(self.progress_wk["pb"])
        renderables.append(self.progress["pb"])
        return renderables

class URLQueue_idx(URLQueue):
    NAME = "state_idx"
    NAME_PROGRESS = "idx"
    def progress_init_getpb(self, name_len):
        return Progress(TextColumn(f"{self.NAME_PROGRESS:<{name_len}}"),
                BarColumn(), TaskProgressColumn(), MofNCompleteColumn(), TextColumn("e{task.fields[items_errors]}"), TimeRemainingColumn())

    def progress_refresh(self, force=False):
        if self.progress and (self.progress["update"] or force):
            total = self.total_items()
            if total == 0:
                total = 1 # if we have nothing to do yet, show the progress bar as empty
            self.progress["pb"].update(self.progress["task"], total=total, completed=len(self._done), items_errors=self.stats["errors"])
            self.progress["update"] = False

    def __str__(self):
        errors = ""
        if self.stats["errors"] > 0:
            errors = " ({self.stats['errors']} errors)"
        return f"indexed {self.stats['recv_items']} items, progress {len(self._done)}/{self.total_items()}{errors} items"

class URLQueue_dl(URLQueue):
    NAME = "state_dl"
    NAME_PROGRESS = "dl"
    def progress_init_getpb(self, name_len):
        return Progress(TextColumn(f"{self.NAME_PROGRESS:<{name_len}}"),
                BarColumn(), TaskProgressColumn(), DownloadColumn(), TransferSpeedColumn(), TextColumn("{task.fields[items_completed]}/{task.fields[items_total]} e{task.fields[items_errors]}"), TimeRemainingColumn())

    def progress_refresh(self, force=False):
        if self.progress and (self.progress["update"] or force):
            total = self.size["total"]
            if total == 0:
                total = 1 # if we have nothing to do yet, show the progress bar as empty
            self.progress["pb"].update(self.progress["task"], total=total, completed=self.size["completed"], items_total=self.total_items(), items_completed=len(self._done), items_errors=self.stats["errors"])
            self.progress["update"] = False

    def progress_wk_init(self):
        self.progress_wk = {
            "pb": Progress(TextColumn("[progress.description]{task.description}"),
                    BarColumn(), TaskProgressColumn(), DownloadColumn(), TransferSpeedColumn(), TimeRemainingColumn(),
                    TextColumn("e-{task.fields[error]} r-{task.fields[resume]:<2} {task.fields[filename]}")),
            "tasks": dict(),
        }

    def progress_wk_create(self, wk_src, wk_num, wk_proxy):
        if self.progress_wk:
            task_name = self._wk_name(wk_src, wk_num, wk_proxy)
            self.progress_wk["tasks"][task_name] = {
                "task": self.progress_wk["pb"].add_task(task_name, filename="", completed=0, total=1, error=0, resume=0),
                "update": None,
            }
            self.progress_wk_update(wk_src, wk_num, wk_proxy, refresh=True)

    def progress_wk_update(self, wk_src, wk_num, wk_proxy, filename="", completed=0, total=1, error=0, resume=0, refresh=False):
        if self.progress_wk:
            task_name = self._wk_name(wk_src, wk_num, wk_proxy)
            wk = self.progress_wk["tasks"][task_name]
            wk["update"] = {
                "filename": filename, "completed": completed,
                "total": total, "error": error, "resume": resume,
            }
            if refresh:
                self.progress_wk_refresh(task_name)

    def progress_wk_refresh_all(self):
        if self.progress_wk:
            for task_name in self.progress_wk["tasks"].keys():
                self.progress_wk_refresh(task_name)

    def progress_wk_refresh(self, task_name):
        if self.progress_wk:
            wk = self.progress_wk["tasks"][task_name]
            if wk["update"]:
                self.progress_wk["pb"].update(wk["task"], **wk["update"])
                wk["update"] = None

    def _wk_name(self, wk_src, wk_num, wk_proxy):
        return f"{self.NAME_PROGRESS}-{wk_src['name']}{wk_proxy['name']}{wk_num}"

    def __str__(self):
        errors = ""
        if self.stats["errors"] > 0:
            errors = " ({self.stats['errors']} errors)"
        return f"downloaded {format_size(self.stats['recv_bytes'])}, {self.stats['recv_items']} items, progress {format_size(self.size['completed'])}/{format_size(self.size['total'])}, {len(self._done)}/{self.total_items()}{errors} items"

#
# indexer task
#

async def indexer_worker(wk_num, client, state_idx, state_dl, conf):
    log.debug(f"idx-{wk_num} startup")
    exiting = False
    while True:
        dirent = await state_idx.get()
        dirent["state"] = "progress"
        url = dirent["url"]
        url_print = url[len(conf["target_urls_prefix"]):]
        try:
            status = "error"
            response = None

            # request page
            log.debug(f"index-{wk_num}    {url}")
            # set download retry rules
            response = None
            async for attempt in AsyncRetrying(stop=stop_after_attempt(conf["retry_index_networkerror"]),
                    wait=wait_random(0, 2),
                    retry=retry_if_exception_type(httpx.TransportError),
                    reraise=True):
                with attempt:
                    # download page
                    response = await client.get(url)
                if attempt.retry_state.outcome.failed:
                    log.info(f"index-{wk_num} retry {attempt.retry_state.attempt_number} {url}")
            log.debug(f"index-{wk_num}: received {response}")
            if response:
                # parse page
                bs = BeautifulSoup(response.content, "lxml")
                if conf["index_debug"]:
                    from IPython import embed; import nest_asyncio; nest_asyncio.apply(); embed(using='asyncio')
                entries = parse_httpindex(url, bs, wk_num)
                # queue URL entries
                if len(entries) > 0:
                    for entry in entries:
                        if entry["url"]:
                            if url_is_directory(entry["url"]):
                                entry["type"] = "d"
                                state_idx.todo(entry, touch=not conf["no_index_touch"])
                            else:
                                entry["type"] = "f"
                                state_dl.todo(entry, touch=not conf["no_index_touch"])
                    status = "ok"

        except asyncio.CancelledError as e:
            log.debug(f"index-{wk_num}: canceled")
            exiting = True
            break

        except Exception as e:
            log.warning(f"index-{wk_num}: Exception while indexing directory, requeing it : {e}")
            log.warning(traceback.format_exc())
            if response:
                log.debug(response.content.decode())

        finally:
            log.info(f"index-{wk_num}    {status} {url_print}")
            state_idx.done(dirent, status=status)
            state_idx.progress_update()
            state_dl.progress_update()
            if not exiting:
                await sleepy(conf["wait"], status)

async def indexer(state_idx, state_dl, conf):
    log.debug("indexer start")
    tasks = list()

    try:
        if not conf["no_progress"]:
            state_idx.progress_init(conf["proxy_list"], conf["sources"])

        limits = httpx.Limits(max_connections=conf["tasks_count"])
        # TODO for indexing we choose the first proxy, we should try to load balance
        async with httpx.AsyncClient(proxy=conf["proxy_list"][0]["proxy_url"], limits=limits, verify=not conf["no_ssl_verify"], timeout=conf["timeout"], headers=conf["headers"]) as client:
            # start indexer tasks
            for wk_num in range(conf["tasks_count"]):
                tsk = asyncio.create_task(indexer_worker(wk_num, client, state_idx, state_dl, conf))
                tasks.append(tsk)
            # wait for the index state queues to be empty
            await state_idx.join()

    except Exception as e:
        log.warning(f"indexer: Exception {e}")

    finally:
        log.debug("indexer exit")
        state_idx.progress_update(refresh=True)
        for tsk in tasks:
            tsk.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

#
# downloader task
#

async def downloader_worker(wk_src, wk_num, wk_proxy, client, state_dl, conf):
    wk_name = f"dl-{wk_src['name']}{wk_proxy['name']}{wk_num}"
    log.debug(f"{wk_name} startup : proxy {wk_proxy['proxy_url']}")
    exiting = False
    while True:
        entry = await state_dl.get()
        status = "error"
        try:
            #entry["state"] = "progress"
            status = "error"
            filesize = 0
            url = entry["url"]
            # get local file path, check it's existence and create local directories
            path_finished, path_tmp, path_print, url_print = url_to_path(url, conf["session_dir"], conf["target_urls_prefix"])
            log.debug(f"{wk_name} {url}")
            if path_finished.exists():
                status = "ok"
            else:
                try:
                    filesize = path_tmp.stat().st_size
                except FileNotFoundError:
                    if not path_tmp.parent.exists():
                        path_tmp.parent.mkdir(parents=True)
                # check if we should skip the URL
                skip = skip_check(url, entry, conf["skip"])
                # check for file to be complete or skipped, or download it
                if entry["size"] and int(entry["size"]) > 0 and filesize == int(entry["size"]):
                    log.debug(f"{wk_name} file is already fully downloaded")
                    status = "ok"
                elif skip:
                    log.info(f"{wk_name} skipped, matching rule {skip}")
                    status = "skipped"
                else:
                    if not conf["no_progress"]:
                        state_dl.progress_wk_update(wk_src, wk_num, wk_proxy, path_finished.name, filesize, int(entry["size"]), 0, 0)
                    # open temporary local file
                    log.debug(f"{wk_name} writing to {path_tmp}")
                    with path_tmp.open('ab') as fd:
                        log.debug(f"{wk_name} HTTP GET")
                        # prepare the download url depending on the source
                        if wk_src["name"] not in ['A', '']:
                            if url.find(wk_src["source"]) >= 0:
                                # replace original url by mirror information
                                url = url.replace(wk_src["source"], wk_src["mirror"])
                                log.debug(f"{wk_name} url to mirror : {url}")
                            else:
                                # this url does not exist on the mirror that this worker is focused on, put it back in the queue
                                log.debug(f"{wk_name} url is not configured for this mirror, requeing")
                                state_dl.todo(entry, requeue=True)
                        # set download retry rules
                        resume_number = 0
                        async for attempt in AsyncRetrying(stop=stop_after_attempt(conf["retry_dl_networkerror"]),
                                wait=wait_random(0, 2),
                                retry=retry_if_exception_type(httpx.TransportError),
                                reraise=True):
                            with attempt:
                                filesize = path_tmp.stat().st_size
                                headers = {'Range': f'bytes={filesize}-'} if filesize else None
                                # download file
                                received = 0
                                async with client.stream('GET', url, headers=headers) as response:
                                    async for chunk in response.aiter_bytes():
                                        status = "progress"
                                        fd.write(chunk)
                                        received += len(chunk)
                                        if not conf["no_progress"]:
                                            state_dl.size_ajust_completed(len(chunk))
                                            state_dl.progress_update()
                                            state_dl.progress_wk_update(wk_src, wk_num, wk_proxy, path_finished.name, filesize+received, int(entry["size"]), attempt.retry_state.attempt_number-1, resume_number)
                            if attempt.retry_state.outcome.failed:
                                filesize_new = path_tmp.stat().st_size
                                if filesize_new > filesize:
                                    # filesize has increased, use dedicated resume counter and don't count it as error
                                    resume_number += 1
                                    attempt.retry_state.attempt_number -= 1
                                filesize = filesize_new
                                if not conf["no_progress"]:
                                    state_dl.progress_wk_update(wk_src, wk_num, wk_proxy, path_finished.name, filesize, int(entry["size"]), attempt.retry_state.attempt_number, resume_number)
                                log.debug(f"{wk_name} retry e-{attempt.retry_state.attempt_number} r-{resume_number} size {filesize} {path_tmp.name}")
                    # file was fully downloaded, rename it to final path
                    filesize = path_tmp.stat().st_size
                    if not entry["size"] or filesize != int(entry["size"]):
                        log.debug(f"{wk_name} updated size of downloaded file from {entry['size']} to {filesize}")
                        state_dl.size_ajust_total(filesize - int(entry["size"]))
                        entry["size"] = filesize
                    log.debug(f"{wk_name} renaming to {path_finished}")
                    path_tmp.rename(path_finished)
                    if entry["date"]:
                        d = dateutil.parser.parse(entry["date"])
                        os.utime(path_finished, (d.timestamp(), d.timestamp()))
                    status = "ok"

        except asyncio.CancelledError as e:
            log.info(f"{wk_name} canceled")
            exiting = True
            break

        except httpx.TransportError as e:
            log.warning(f"{wk_name} transport error, requeing file : {type(e).__name__} {e} ({conf['retry_dl_networkerror']} previous transport errors)")

        except Exception as e:
            log.warning(f"{wk_name} Exception while downloading file, requeing it : {e}")
            log.warning(traceback.format_exc())

        finally:
            try:
                if status != "ok":
                    size_print = f"{format_size(filesize)} / {format_size(int(entry['size']))}"
                else:
                    size_print = format_size(int(entry['size']))
                log.info(f"{wk_name} {status} {path_print} ({size_print})")
                state_dl.done(entry, status)
                state_dl.progress_update()
                if not exiting:
                    await sleepy(conf["wait"], status)
            except Exception as e:
                log.warning(f"{wk_name} error in task cleanup: {e}") # TODO remove that try except, debug only
                log.warning(traceback.format_exc())

    log.debug(f"{wk_name} exiting")

async def downloader(state_idx, state_dl, conf):
    log.debug("downloader start")
    tasks = list()

    try:
        if not conf["no_progress"]:
            state_dl.progress_wk_init()
            state_dl.progress_init(conf["proxy_list"], conf["sources"])

        limits = httpx.Limits(max_connections=conf["tasks_count"] * len(conf["sources"]))
        clients = list()
        for wk_proxy in conf["proxy_list"]:
            client = httpx.AsyncClient(proxy=wk_proxy["proxy_url"], limits=limits, verify=not conf["no_ssl_verify"], timeout=conf["timeout"], headers=conf["headers"])
            clients.append(client)
            # start downloader tasks
            for wk_src in conf["sources"]:
                for wk_num in range(wk_proxy["tasks_count"]):
                    state_dl.progress_wk_create(wk_src, wk_num, wk_proxy)
                    tsk = asyncio.create_task(downloader_worker(wk_src, wk_num, wk_proxy, client, state_dl, conf))
                    tasks.append(tsk)
        # wait for the indexer queues to be empty
        await state_idx.join()
        # wait for the downloader queues to be empty
        await state_dl.join()
        # close httx clients
        await asyncio.gather(*[client.aclose() for client in clients])

    except Exception as e:
        log.warning(f"downloader: Exception {e}")
        log.warning(traceback.format_exc())

    finally:
        try:
            log.debug("downloader exit")
            state_dl.progress_update(refresh=True)
            for tsk in tasks:
                tsk.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as e:
            log.warning(f"downloader error in cleanup: {e}") # TODO remove that try except, debug only
            log.warning(traceback.format_exc())

#
# maintainer task
#

async def maintainer(state_idx, state_dl):
    log.debug(f"maintainer: started")
    queues = list(filter(None, [state_idx, state_dl]))
    t_lastsave = time.monotonic()
    while True:
        try:
            await asyncio.sleep(STATE_PROGRESSREFRESH_PERIOD)
            for queue in queues:
                if queue.progress_wk:
                    queue.progress_wk_refresh_all()
                queue.progress_refresh()
            t = time.monotonic()
            if t - t_lastsave > STATE_SAVE_PERIOD:
                log.debug(f"saving state...")
                for queue in queues:
                    queue.save()
                t_lastsave = t

        except Exception as e:
            log.warning(f"maintainer: Exception: {e}")
            log.warning(traceback.format_exc())

        except asyncio.CancelledError as e:
            log.debug(f"maintainer: canceled")
            break

#
# session & main
#

async def session(conf):
    log.info(f"start session {conf['session_name']} at {now()}")
    log.info(f"session directory : {conf['session_dir']}")
    log.debug(f"log file : {conf['log_file']}")
    time_begin = time.monotonic()

    # create or load session and states
    if conf["session_new"]:
        # populate session directory
        conf["targets_file"].write_text('\n'.join(conf["target_urls"]))
        conf["mirrors_file"].write_text('\n'.join(conf["mirror"]))
        conf["proxies_file"].write_text('\n'.join(conf["proxy"]))
    else:
        conf["target_urls"] = ignore_comments(conf["targets_file"].read_text().splitlines())
        conf["mirror"] = ignore_comments(conf["mirrors_file"].read_text().splitlines())
        conf["proxy"] = ignore_comments(conf["proxies_file"].read_text().splitlines())
    state_idx = URLQueue_idx(conf["session_dir"], "state_index.csv", conf["retry_global_error"])
    log.debug(str(state_idx))
    state_dl = URLQueue_dl(conf["session_dir"], "state_download.csv", conf["retry_global_error"])
    log.debug(str(state_dl))

    # enqueue targets urls if it is a new session
    if conf["session_new"]:
        for url in conf["target_urls"]:
            entry = URLQueue.FIELDS.copy()
            if url_is_directory(url):
                entry.update({"url": url, "type": "d"})
                state_idx.todo(entry, touch=not conf["no_index_touch"])
            else:
                entry.update({"url": url, "type": "f"})
                state_dl.todo(entry, touch=not conf["no_index_touch"])
        state_idx.save()
        state_dl.save()

    if not state_idx.empty() or not state_dl.empty():
        # find the smallest common URL prefix, to hide it when printing status
        if len(conf["target_urls"]) == 1 and not url_is_directory(conf["target_urls"][0]):
            prefix = ""
        else:
            prefix = strs_get_common_prefix(conf["target_urls"])
        if prefix in ["", "http://", "https://"]:
            conf["target_urls_prefix"] = ""
        else:
            conf["target_urls_prefix"] = prefix
            log.info(f"common download prefix: {prefix}")

        # create sources list from mirrors list
        conf["sources"] = [ {"name": 'A' if len(conf["mirror"]) else ''} ]
        conf["sources"].extend(mirrors_list(conf["mirror"]))
        if len(conf["sources"]) > 1:
            log.info(f"using mirrors:")
            for source in conf["sources"]:
                if source['name'] not in ['A', '']:
                    log.info(f"{source['name']}: {source['source']} mirrors {source['mirror']}")

        # create proxies list
        conf["proxy_list"] = proxies_list(conf["proxy"], conf["tasks_count"])
        if len(conf["proxy_list"]) > 0:
            log.info(f"using proxies:")
            for proxy in conf["proxy_list"]:
                name = f"{proxy['name']}: " if proxy['name'] else ''
                log.info(f"{name}{proxy['proxy_url']} tasks-count={proxy['tasks_count']}")
        else:
            conf["proxy_list"] = [ {"name": '', "proxy_url": None, "tasks_count": conf["tasks_count"]} ]

        # preparing request headers
        conf["headers"] = dict()
        if conf["user_agent"]:
            conf["headers"]["User-Agent"] = conf["user_agent"]

        # start tasks
        task_indexer = None
        task_downloader = None
        # start indexer
        if not conf["download_only"]:
            task_indexer = asyncio.create_task(indexer(state_idx, state_dl, conf))
        # start downloader
        if not conf["index_only"]:
            task_downloader = asyncio.create_task(downloader(state_idx, state_dl, conf))
        # start queue maintainer: save to CSV and update progress periodicaly
        task_maintainer = asyncio.create_task(maintainer(state_idx, state_dl))

        # setup rich live display to show progress
        pbars = None
        if not conf["no_progress"]:
            log.debug("starting progress bars")
            pblist = list()
            if not conf["index_only"]:
                pblist.extend(await state_dl.progress_get_renderables())
            if not conf["download_only"]:
                pblist.extend(await state_idx.progress_get_renderables())
            pbars = Live(Group(*pblist))
            theme = dict.fromkeys(["bar.complete", "bar.pulse", "bar.finished", "progress.percentage", "progress.description", "progress.filesize", "progress.filesize.total", "progress.download", "progress.elapsed", "progress.remaining", "progress.data.speed" ], "default")
            theme["bar.back"] = "conceal" # TODO may not work in every terminal, from documentation https://rich.readthedocs.io/en/latest/style.html
            pbars.console.push_theme(Theme(theme))

        # wait for tasks to finish
        try:
            if pbars:
                pbars.start()
            tasks = list(filter(None, [task_indexer, task_downloader]))
            await asyncio.gather(*tasks, return_exceptions=True)

        except (asyncio.exceptions.CancelledError, KeyboardInterrupt):
            log.info("saving state and exiting...")

        except Exception as e:
            log.warning(f"Exception {e}")

        finally:
            task_maintainer.cancel()
            await asyncio.gather(task_maintainer, return_exceptions=True)
            state_idx.save()
            state_dl.save()
            if pbars:
                if not conf["index_only"]:
                    state_dl.progress_wk_refresh_all()
                state_dl.progress_refresh(force=True)
                state_idx.progress_refresh(force=True)
                pbars.stop()
            log.info(str(state_idx))
            log.info(str(state_dl))
    else:
        log.info("nothing to do")

    log.debug(f"log file : {conf['log_file']}")
    time_end = time.monotonic()
    elapsed = int(time_end - time_begin)
    log.info(f"end session {conf['session_name']} at {now()} after {datetime.timedelta(seconds=elapsed)}")

def main():
    parser = argparse.ArgumentParser(description=f"httpeat v{VERSION} - {DESCRIPTION}", epilog=EPILOG, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("-A", "--user-agent", help=f"user agent")
    parser.add_argument("-d", "--download-only", action="store_true", help="only download already listed files")
    parser.add_argument("-i", "--index-only", action="store_true", help="only list all files recursively, do not download")
    parser.add_argument("-I", "--index-debug", action="store_true", help="drop in interactive ipython shell during indexing")
    parser.add_argument("-k", "--no-ssl-verify", action='store_true', help="do no verify the SSL certificate in case of HTTPS connection")
    parser.add_argument("-m", "--mirror", action="append", default=[], help="mirror definition to load balance requests, eg. \"http://host1/data/ mirrors http://host2/data/\"\ncan be specified multiple times.\nonly valid uppon session creation, afterwards you must modify session mirrors.txt.")
    parser.add_argument("-P", "--no-progress", action="store_true", help="disable progress bar")
    parser.add_argument('-q', '--quiet', action="store_true", help='quiet output, show only warnings')
    parser.add_argument("-s", "--skip", action="append", default=[], help="skip rule: dl-(path|size-gt):[pattern]. can be specified multiple times.")
    parser.add_argument("-t", "--timeout", type=float, default=TO_DEFAULT, help="in seconds, default to {TO_DEFAULT}")
    parser.add_argument("-T", "--no-index-touch", action="store_true", help="do not create empty .download files uppon indexing")
    parser.add_argument('-v', '--verbose', action="count", help='verbose output, specify twice for http request debug')
    parser.add_argument("-w", "--wait", type=float, default=0.0, help="wait after request for n to n*3 seconds, for each task")
    parser.add_argument("-x", "--proxy", action="append", default=[], help="proxy URL: \"(http[s]|socks5)://<host>:<port>[ tasks-count=N]\"\ncan be specified multiple times to loadbalance downloads between proxies.\noptional tasks-count overrides the golbal tasks-count.\nonly valid uppon session creation, afterwards you must modify session proxies.txt.")
    parser.add_argument("-z", "--tasks-count", type=int, default=TASKS_DEFAULT, help=f"number of parallel tasks, defaults to {TASKS_DEFAULT}")
    parser.add_argument('session_name', help="name of the session")
    parser.add_argument('targets', nargs='*', help="to create a session, provide URLs to HTTP index or files, or path of a source txt file")

    # verify arguments, populate runtime 'conf' and create session directory if new session
    args = parser.parse_args()
    conf = vars(args)
    if args.index_only and args.download_only:
        parser.error("index_only and download_only are exclusive")
    if args.index_debug and not args.no_progress:
        parser.error("must specify --no-progress when using --index-debug")
    conf["retry_dl_networkerror"] = RETRY_DL_NETWORKERROR_DEFAULT
    conf["retry_index_networkerror"] = RETRY_INDEX_NETWORKERROR_DEFAULT
    conf["retry_global_error"] = RETRY_GLOBAL_ERROR_DEFAULT
    conf["session_dir"] = Path(args.session_name).resolve()
    conf["targets_file"] = conf["session_dir"] / "targets.txt"
    conf["mirrors_file"] = conf["session_dir"] / "mirrors.txt"
    conf["proxies_file"] = conf["session_dir"] / "proxies.txt"
    conf["log_file"] = conf["session_dir"] / "log.txt"
    conf["session_new"] = not conf["session_dir"].exists()
    if conf["session_new"]:
        if len(args.targets) == 0:
            parser.error("must specify targets when session directory does not exist yet")
        # check if targets is arguments or file
        if re.match("http[s]://", args.targets[0], re.I):
            # targets are argument urls
            conf["target_urls"] = args.targets
        else:
            # targets are in a source txt file
            if len(args.targets) > 1:
                parser.error("if target is a source txt file, there can only be one")
            targets_source = Path(args.targets[0])
            if not targets_source.exists():
                parser.error(f"targets file does not exist : {targets_source}")
            conf["target_urls"] = targets_source.read_text().splitlines()
        # create session directory
        conf["session_dir"].mkdir()
    else:
        if len(args.targets) > 0:
            parser.error(f"cannot specify targets when session directory already exists. see {args.session_name}/targets.txt")
        if len(args.mirror) > 0:
            parser.error(f"cannot specify mirrors when session directory already exists. see {args.session_name}/mirrors.txt")
        if len(args.proxy) > 0:
            parser.error(f"cannot specify proxies when session directory already exists. see {args.session_name}/proxies.txt")
    try:
        # check skip rules arguments
        skip_check("", {"size": 0}, args.skip)
        # check mirror definition arguments
        mirrors_list(conf["mirror"])
        # check proxies definition arguments
        proxies_list(conf["proxy"], conf["tasks_count"])
    except Exception as e:
        parser.error(str(e))

    # logging setup
    level = logging.INFO
    if args.quiet:
        level = logging.WARNING
    elif args.verbose:
        level = logging.DEBUG
    if args.verbose == 1:
        logging.getLogger("httpx").setLevel(logging.INFO)
        logging.getLogger("httpcore").setLevel(logging.INFO)
    else:
        logging.getLogger("httpx").setLevel(logging.WARNING)
    log_file = logging.FileHandler(conf["log_file"])
    if args.no_progress:
        log_console = logging.StreamHandler()
    else:
        log_console = RichHandler(show_time=False, show_level=False, show_path=False, highlighter=NullHighlighter())
    logging.basicConfig(level=level, format='%(asctime)s %(levelname)-.1s %(message)s', handlers=[log_console, log_file], datefmt='%d-%H:%M:%S')

    # catch SIGTERM and send SIGINT instead, so program terminates clean using the try except for KeyboardInterrupt
    signal.signal(signal.SIGTERM, raise_sigint)

    asyncio.run(session(conf))

if __name__ == "__main__":
    sys.exit(main())
