#!/usr/bin/env python3

import sys
import csv
import shutil
import logging
import asyncio
from pathlib import Path

import pytest
import httpx
from pytest_httpx import HTTPXMock
from urllib.parse import urlsplit, unquote

sys.path.insert(0, str(Path(__file__).parent / '..'))
import httpeat
from httpeat import Httpeat
logging.basicConfig(level=logging.DEBUG, format='%(levelname)-.1s %(message)s')

@pytest.fixture
def httpeat_conf(request):
    sdir = Path(f"/tmp/test_httpeat/{request.node.name}")
    if sdir.exists():
        shutil.rmtree(sdir)
    sdir.mkdir(parents=True)
    conf = {
        "session_new": True,
        "session_name": request.node.name,
        "session_dir": sdir,
        "targets_file": sdir/"targets.txt",
        "mirrors_file": sdir/"mirrors.txt",
        "proxies_file": sdir/"proxies.txt",
        "log_file": sdir/"log.txt",
        "target_urls": [],
        "mirror": [],
        "proxy": [],
        "no_progress": True,
        "index_only": False,
        "download_only": False,
        "tasks_count": httpeat.TASKS_DEFAULT,
        "no_ssl_verify": None,
        "timeout": httpeat.TO_DEFAULT,
        "skip": [],
        "index_debug": False,
        "no_index_touch": False,
        "wait": 0.0,
        "user_agent": None,
        "retry_dl_networkerror": httpeat.RETRY_DL_NETWORKERROR_DEFAULT,
        "retry_index_networkerror": httpeat.RETRY_INDEX_NETWORKERROR_DEFAULT,
        "retry_global_error": httpeat.RETRY_GLOBAL_ERROR_DEFAULT,
    }
    conf.update(request.param)

    yield conf

def assert_local_files(httpeat_conf, exists=True, isempty=False):
    print(f"test local file exists {exists}")
    for file in httpeat_conf["test_files"]:
        path = httpeat_conf["session_dir"] / "data" / file
        print(f"test {path}")
        if exists:
            assert path.exists()
            if isempty:
                assert path.stat().st_size == 0
            else:
                assert path.stat().st_size > 0
        else:
            assert not path.exists()

def assert_state_dl_file(httpeat_conf, filenum, expected_state):
    print(f"check state of download in CSV: filenum {filenum} expected_state {expected_state}")
    f_state_dl = httpeat_conf["session_dir"] / "state_download.csv"
    csv_state_dl = list(csv.DictReader(f_state_dl.open()))
    assert csv_state_dl[filenum]["state"] == expected_state

class Test_httpx:
    @pytest.mark.asyncio
    async def test_httpx(self, httpx_mock):
        httpx_mock.add_response()
        async with httpx.AsyncClient() as client:
            response = await client.get("http://127.0.0.1/")

@pytest.mark.asyncio
class Test_httpeat_download:
    @pytest.mark.parametrize("httpeat_conf", [
        # download 1 file
        { "target_urls": ["https://host1/a/b.img"],
            "test_files": [ "host1/a/b.img" ], },
        # download 2 file
        { "target_urls": ["https://host1/a/b.img", "https://host1/a/c.img"],
            "test_files": [ "host1/a/b.img", "host1/a/c.img" ], },
        ], indirect=True)
    @pytest.mark.httpx_mock(can_send_already_matched_responses=True)
    async def test_dl_ok(self, httpx_mock, httpeat_conf):
        httpx_mock.add_response(content=b"toto")
        h = Httpeat(httpeat_conf)
        assert await h.run() == 0
        assert len(h.warnings) == 0
        assert_local_files(httpeat_conf)
        assert_state_dl_file(httpeat_conf, 0, "ok")

    @pytest.mark.parametrize("httpeat_conf", [
        # JRQN6PSE = b32encode(md5(name.encode()).digest()).decode()[:8]
        { "target_urls": [f"https://host1/a/{'b'*300}.img"],
            "test_files": [ f"host1/a/{'b'*117}_JRQN6PSE_{'b'*113}.img" ], },
        ], indirect=True)
    @pytest.mark.httpx_mock(can_send_already_matched_responses=True)
    async def test_dl_filename_too_long_ok(self, httpx_mock, httpeat_conf):
        httpx_mock.add_response(content=b"toto")
        h = Httpeat(httpeat_conf)
        assert await h.run() == 0
        assert_local_files(httpeat_conf)
        assert_state_dl_file(httpeat_conf, 0, "ok")

    @pytest.mark.parametrize("httpeat_conf", [
        { "target_urls": ["https://host1/a/b.img"],
            "test_files": [ "host1/a/b.img" ], },
        ], indirect=True)
    async def test_dl_err_readtimeout_2_success(self, httpx_mock: HTTPXMock, httpeat_conf):
        httpx_mock.add_exception(httpx.ReadTimeout("Unable to read within timeout"))
        httpx_mock.add_exception(httpx.ReadTimeout("Unable to read within timeout"))
        httpx_mock.add_response(content=b"toto")
        h = Httpeat(httpeat_conf)
        assert await h.run() == 0
        assert_local_files(httpeat_conf)
        assert_state_dl_file(httpeat_conf, 0, "ok")

    @pytest.mark.parametrize("httpeat_conf", [
        { "target_urls": ["https://host1/a/b.img"],
            "test_files": [ "host1/a/b.img" ], },
        ], indirect=True)
    async def test_dl_err_remoteprotocolerror_2_success(self, httpx_mock: HTTPXMock, httpeat_conf):
        httpx_mock.add_exception(httpx.RemoteProtocolError("peer closed connection"))
        httpx_mock.add_exception(httpx.RemoteProtocolError("peer closed connection"))
        httpx_mock.add_response(content=b"toto")
        h = Httpeat(httpeat_conf)
        assert await h.run() == 0
        assert_local_files(httpeat_conf)
        assert_state_dl_file(httpeat_conf, 0, "ok")

    @pytest.mark.parametrize("httpeat_conf", [
        { "target_urls": ["https://host1/a/b.img"],
            "test_files": [ "host1/a/b.img" ], },
        ], indirect=True)
    @pytest.mark.httpx_mock(can_send_already_matched_responses=True)
    async def test_dl_err_transporterror_fails(self, httpx_mock: HTTPXMock, httpeat_conf):
        httpx_mock.add_exception(httpx.RemoteProtocolError("peer closed connection"))
        httpeat_conf["retry_dl_networkerror"] = 0
        httpeat_conf["retry_global_error"] = 0
        h = Httpeat(httpeat_conf)
        assert await h.run() == 0
        assert_local_files(httpeat_conf, exists=False)
        assert_state_dl_file(httpeat_conf, 0, "error")

    @pytest.mark.parametrize("httpeat_conf", [
        { "target_urls": ["https://host1/a/b.img"],
            "test_files": [ "host1/a/b.img" ], },
        ], indirect=True)
    @pytest.mark.httpx_mock(can_send_already_matched_responses=True)
    async def test_dl_err_httpstatuserror_fails(self, httpx_mock: HTTPXMock, httpeat_conf):
        httpx_mock.add_exception(httpx.HTTPStatusError("err", request="req", response=""))
        httpeat_conf["retry_dl_networkerror"] = 0
        httpeat_conf["retry_global_error"] = 0
        h = Httpeat(httpeat_conf)
        assert await h.run() == 0
        assert_local_files(httpeat_conf, exists=False)
        assert_state_dl_file(httpeat_conf, 0, "error")

@pytest.mark.asyncio
class Test_httpeat_download_mirrors:
    @pytest.mark.parametrize("httpeat_conf", [
        # test with 2 URLs from host1 and a mirror on host2
        { "target_urls": ["https://host1/a/b.img", "https://host1/a/b2.img"],
            "mirror": ["https://host2/pub/a/ mirrors https://host1/a/"],
            "tasks_count": 1,
            "test_files": [ "host1/a/b.img", "host1/a/b2.img"], },
        # test with 1 URL from host1 and 1 URL from host2, mirrors should not be invoked
        { "target_urls": ["https://host1/a/b.img", "https://host2/pub/a/b2.img"],
            "mirror": ["https://host2/pub/a/ mirrors https://host1/a/"],
            "tasks_count": 1,
            "test_files": [ "host1/a/b.img", "host2/pub/a/b2.img"], },
        ], indirect=True)
    async def test_dl_mirror_ok(self, httpx_mock, httpeat_conf):
        httpx_mock.add_response(url="https://host1/a/b.img", content=b"toto")
        httpx_mock.add_response(url="https://host2/pub/a/b2.img", content=b"toto")
        h = Httpeat(httpeat_conf)
        assert await h.run() == 0
        assert_local_files(httpeat_conf)
        assert_state_dl_file(httpeat_conf, 0, "ok")

@pytest.mark.asyncio
class Test_httpeat_download_proxies:
    @pytest.mark.parametrize("httpeat_conf", [
        # test with 2 URLs both going through the same proxy
        { "target_urls": ["https://host1/a/b.img", "https://host1/a/b2.img"],
            "proxy": ["http://proxy1:3000/"],
            "test_files": [ "host1/a/b.img", "host1/a/b2.img"], },
        ], indirect=True)
    async def test_dl_1proxy_ok(self, httpx_mock, httpeat_conf):
        httpx_mock.add_response(proxy_url="http://proxy1:3000/", url="https://host1/a/b.img", content=b"toto")
        httpx_mock.add_response(proxy_url="http://proxy1:3000/", url="https://host1/a/b2.img", content=b"toto")
        h = Httpeat(httpeat_conf)
        assert await h.run() == 0
        assert_local_files(httpeat_conf)
        assert_state_dl_file(httpeat_conf, 0, "ok")

    @pytest.mark.parametrize("httpeat_conf", [
        # test with 2 URLs, one should go to it's own proxy since we have a single worker (tasks_count) per proxy
        { "target_urls": ["https://host1/a/b.img", "https://host1/a/b2.img"],
            "proxy": ["http://proxy1:3000/", "http://proxy2:3000/"],
            "tasks_count": 1,
            "test_files": [ "host1/a/b.img", "host1/a/b2.img"], },
        # test with 2 URLs, one should go to it's own proxy since we have a single worker (proxy tasks_count) per proxy
        { "target_urls": ["https://host1/a/b.img", "https://host1/a/b2.img"],
            "proxy": ["http://proxy1:3000/ tasks-count=1", "http://proxy2:3000/ tasks-count=1"],
            "test_files": [ "host1/a/b.img", "host1/a/b2.img"], },
        ], indirect=True)
    async def test_dl_2proxy_ok(self, httpx_mock, httpeat_conf):
        httpx_mock.add_response(proxy_url="http://proxy1:3000/", url="https://host1/a/b.img", content=b"toto")
        httpx_mock.add_response(proxy_url="http://proxy2:3000/", url="https://host1/a/b2.img", content=b"toto")
        h = Httpeat(httpeat_conf)
        assert await h.run() == 0
        assert_local_files(httpeat_conf)
        assert_state_dl_file(httpeat_conf, 0, "ok")

@pytest.mark.asyncio
@pytest.mark.filterwarnings("ignore:The 'strip_cdata' option of HTMLParser():DeprecationWarning")
class Test_httpeat_index:
    @pytest.mark.parametrize("httpeat_conf", [
        { "target_urls": ["https://host1/a/"],
            "test_files": [ "host1/a/toto.png" ], },
        ], indirect=True)
    async def test_index(self, httpx_mock: HTTPXMock, httpeat_conf):
        httpx_mock.add_response(url="https://host1/a/",
                html="<body><a href='toto.png'/></body>")
        httpx_mock.add_response(url="https://host1/a/toto.png", content=b"toto")
        h = Httpeat(httpeat_conf)
        assert await h.run() == 0
        assert_local_files(httpeat_conf)
        assert_state_dl_file(httpeat_conf, 0, "ok")

    @pytest.mark.parametrize("httpeat_conf", [
        { "target_urls": ["https://host1/a/"],
            "test_files": [ "host1/a/toto.png" ], },
        ], indirect=True)
    async def test_index_err_readtimeout_success(self, httpx_mock: HTTPXMock, httpeat_conf):
        httpx_mock.add_exception(httpx.ReadTimeout("Unable to read within timeout"))
        httpx_mock.add_response(url="https://host1/a/",
                html="<body><a href='toto.png'/></body>")
        httpx_mock.add_response(content=b"toto")
        h = Httpeat(httpeat_conf)
        assert await h.run() == 0
        assert_local_files(httpeat_conf)
        assert_state_dl_file(httpeat_conf, 0, "ok")

@pytest.mark.asyncio
@pytest.mark.filterwarnings("ignore:The 'strip_cdata' option of HTMLParser():DeprecationWarning")
class Test_httpeat_options:
    INDEX = "<body><table><tr><th>name</th><th>size</th></tr>" \
            "<tr><td><a href='toto.png'>toto</a></td><td>1G</td></tr>" \
            "<tr><td><a href='bibi.png'>toto</a></td><td>3G</td></tr>" \
            "</table></body>"
    @pytest.mark.parametrize("httpeat_conf", [
        { "target_urls": ["https://host1/a/"],
            "test_files": [ "host1/a/toto.png" ],
            "skip": ["dl-size-gt:2G"]},
        ], indirect=True)
    async def test_skip_rules_dl_size(self, httpx_mock: HTTPXMock, httpeat_conf):
        httpx_mock.add_response(url="https://host1/a/", html=self.INDEX)
        httpx_mock.add_response(url="https://host1/a/toto.png", content=b"toto")
        h = Httpeat(httpeat_conf)
        assert await h.run() == 0
        assert_local_files(httpeat_conf)
        assert_state_dl_file(httpeat_conf, 0, "skipped")
        assert_state_dl_file(httpeat_conf, 1, "ok")

    @pytest.mark.parametrize("httpeat_conf", [
        { "target_urls": ["https://host1/a/"],
            "test_files": [ ],
            "skip": ["dl-path:.*bibi.*", "dl-path:.*toto.*"]},
        ], indirect=True)
    async def test_skip_rules_dl_path_2(self, httpx_mock: HTTPXMock, httpeat_conf):
        httpx_mock.add_response(url="https://host1/a/", html=self.INDEX, content=b"toto")
        h = Httpeat(httpeat_conf)
        assert await h.run() == 0
        assert_local_files(httpeat_conf)

    @pytest.mark.parametrize("httpeat_conf", [
        # test default user agent
        { "target_urls": ["https://host1/a/b.img"],
            "user_agent": None,
            "test_files": [ "host1/a/b.img" ], },
        # test custom user agent
        { "target_urls": ["https://host1/a/b.img"],
            "user_agent": "i eat http therefore i am",
            "test_files": [ "host1/a/b.img" ], },
        # test custom user agent with proxy
        { "target_urls": ["https://host1/a/b.img"],
            "user_agent": "i eat http therefore i am",
            "proxy": ["http://proxy1:3000/"],
            "test_files": [ "host1/a/b.img" ], },
        ], indirect=True)
    async def test_user_agent(self, httpx_mock: HTTPXMock, httpeat_conf):
        if httpeat_conf["user_agent"] is None:
            ua = httpx._client.USER_AGENT
        else:
            ua = httpeat_conf["user_agent"]
        print(f"matching user agent: {ua}")
        httpx_mock.add_response(url="https://host1/a/b.img", match_headers={'User-Agent': ua}, content=b"toto")
        h = Httpeat(httpeat_conf)
        assert await h.run() == 0
        assert_local_files(httpeat_conf)
        assert_state_dl_file(httpeat_conf, 0, "ok")

@pytest.mark.asyncio
class Test_httpeat_progress:
    @pytest.mark.parametrize("httpeat_conf", [
        { "target_urls": ["https://host1/a/toto.png"],
            "test_files": [ "host1/a/toto.png" ],
            "no_progress": False }
        ], indirect=True)
    async def test_progress_dl(self, httpx_mock: HTTPXMock, httpeat_conf):
        httpx_mock.add_response(url="https://host1/a/toto.png", content=b"toto")
        h = Httpeat(httpeat_conf)
        assert await h.run() == 0
        assert_local_files(httpeat_conf)
        assert_state_dl_file(httpeat_conf, 0, "ok")
        # check state_dl
        assert h.state_dl.stats["size_completed"] == 4
        assert h.state_dl.stats["size_total"] == 4
        assert h.state_dl.stats["items_ok"] == 1
        assert h.state_dl.stats["items_error"] == 0
        assert h.state_dl.progress['pb'].tasks[0].finished == True
        assert h.state_dl.progress['pb'].tasks[0].completed == 4
        assert h.state_dl.progress['pb'].tasks[0].total == 4
        assert h.state_dl.progress['pb'].tasks[0].fields == {'items_total': 1, 'items_completed': 1, 'items_error': 0}

    @pytest.mark.filterwarnings("ignore:The 'strip_cdata' option of HTMLParser():DeprecationWarning")
    @pytest.mark.parametrize("httpeat_conf", [
        { "target_urls": ["https://host1/a/"],
            "test_files": [ "host1/a/toto.png" ],
            "no_progress": False }
        ], indirect=True)

    async def test_progress_index(self, httpx_mock: HTTPXMock, httpeat_conf):
        httpx_mock.add_response(url="https://host1/a/",
                html="<body><a href='toto.png'/></body>")
        httpx_mock.add_response(url="https://host1/a/toto.png", content=b"toto")
        h = Httpeat(httpeat_conf)
        assert await h.run() == 0
        assert_local_files(httpeat_conf)
        assert_state_dl_file(httpeat_conf, 0, "ok")
        # check state_idx
        assert h.state_idx.stats["items_ok"] == 1
        assert h.state_idx.stats["items_error"] == 0
        assert h.state_idx.progress['pb'].tasks[0].finished == True
        assert h.state_idx.progress['pb'].tasks[0].completed == 1
        assert h.state_idx.progress['pb'].tasks[0].total == 1
        assert h.state_idx.progress['pb'].tasks[0].fields == {'items_error': 0}
        # check state_dl
        assert h.state_dl.stats["size_completed"] == 4
        assert h.state_dl.stats["size_total"] == 4
        assert h.state_dl.stats["items_ok"] == 1
        assert h.state_dl.stats["items_error"] == 0
        assert h.state_dl.progress['pb'].tasks[0].finished == True
        assert h.state_dl.progress['pb'].tasks[0].completed == 4
        assert h.state_dl.progress['pb'].tasks[0].total == 4
        assert h.state_dl.progress['pb'].tasks[0].fields == {'items_total': 1, 'items_completed': 1, 'items_error': 0}

if __name__ == '__main__':
    sys.exit(pytest.main())

"""
    #async def simulate_network_latency(request: httpx.Request):
    #    await asyncio.sleep(1)
    #    response = httpx.Response(
    #        status_code=200, content=b'toto',
    #    )
    #    print(f"headers {response.headers}")
    #    response.headers['content-length'] = "400"
    #    return response
    async def simulate_network_error(request: httpx.Request):
        raise httpx.ReadTimeout("Unable to read within timeout")
    httpx_mock.add_callback(simulate_network_error)
    httpx_mock.add_callback(simulate_network_error)
"""
