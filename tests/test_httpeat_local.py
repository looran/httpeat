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

def assert_local_files(httpeat_conf, exists=True):
    print(f"test local file exists {exists}")
    for file in httpeat_conf["test_files"]:
        path = httpeat_conf["session_dir"] / "data" / file
        print(f"test {path}")
        if exists:
            assert path.exists()
        else:
            assert not path.exists()

class Test_httpx:
    @pytest.mark.asyncio
    async def test_httpx(self, httpx_mock):
        httpx_mock.add_response()
        async with httpx.AsyncClient() as client:
            response = await client.get("http://127.0.0.1/")

@pytest.mark.asyncio
class Test_httpeat_download:
    @pytest.mark.parametrize("httpeat_conf", [
        { "target_urls": ["https://host1/a/b.img"],
            "test_files": [ "host1/a/b.img" ], },
        { "target_urls": ["https://host1/a/b.img", "https://host1/a/c.img"],
            "test_files": [ "host1/a/b.img", "host1/a/c.img" ], },
        ], indirect=True)
    @pytest.mark.httpx_mock(can_send_already_matched_responses=True)
    async def test_dl_ok(self, httpx_mock, httpeat_conf):
        httpx_mock.add_response()
        await Httpeat(httpeat_conf).run()
        assert_local_files(httpeat_conf)

    @pytest.mark.parametrize("httpeat_conf", [
        # JRQN6PSE = b32encode(md5(name.encode()).digest()).decode()[:8]
        { "target_urls": [f"https://host1/a/{'b'*300}.img"],
            "test_files": [ f"host1/a/{'b'*117}_JRQN6PSE_{'b'*113}.img" ], },
        ], indirect=True)
    @pytest.mark.httpx_mock(can_send_already_matched_responses=True)
    async def test_dl_filename_too_long_ok(self, httpx_mock, httpeat_conf):
        httpx_mock.add_response()
        await Httpeat(httpeat_conf).run()
        assert_local_files(httpeat_conf)

    @pytest.mark.parametrize("httpeat_conf", [
        { "target_urls": ["https://host1/a/b.img"],
            "test_files": [ "host1/a/b.img" ], },
        ], indirect=True)
    async def test_dl_err_readtimeout(self, httpx_mock: HTTPXMock, httpeat_conf):
        httpx_mock.add_exception(httpx.ReadTimeout("Unable to read within timeout"))
        httpx_mock.add_exception(httpx.ReadTimeout("Unable to read within timeout"))
        httpx_mock.add_response()
        await Httpeat(httpeat_conf).run()
        assert_local_files(httpeat_conf)

    @pytest.mark.parametrize("httpeat_conf", [
        { "target_urls": ["https://host1/a/b.img"],
            "test_files": [ "host1/a/b.img" ], },
        ], indirect=True)
    async def test_dl_err_remoteprotocolerror_2(self, httpx_mock: HTTPXMock, httpeat_conf):
        httpx_mock.add_exception(httpx.RemoteProtocolError("peer closed connection"))
        httpx_mock.add_exception(httpx.RemoteProtocolError("peer closed connection"))
        httpx_mock.add_response()
        await Httpeat(httpeat_conf).run()
        assert_local_files(httpeat_conf)

    @pytest.mark.parametrize("httpeat_conf", [
        { "target_urls": ["https://host1/a/b.img"],
            "test_files": [ "host1/a/b.img" ], },
        ], indirect=True)
    @pytest.mark.httpx_mock(can_send_already_matched_responses=True)
    async def test_dl_err_remoteprotocolerror_fails(self, httpx_mock: HTTPXMock, httpeat_conf):
        httpx_mock.add_exception(httpx.RemoteProtocolError("peer closed connection"))
        httpeat_conf["retry_dl_networkerror"] = 0
        httpeat_conf["retry_global_error"] = 1
        await Httpeat(httpeat_conf).run()
        assert_local_files(httpeat_conf, False)
        # check state of download in CSV
        f_state_dl = httpeat_conf["session_dir"] / "state_download.csv"
        assert list(csv.DictReader(f_state_dl.open()))[0]["state"] == "error"

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
        httpx_mock.add_response(url="https://host1/a/b.img")
        httpx_mock.add_response(url="https://host2/pub/a/b2.img")
        await Httpeat(httpeat_conf).run()
        assert_local_files(httpeat_conf)

@pytest.mark.asyncio
class Test_httpeat_download_proxies:
    @pytest.mark.parametrize("httpeat_conf", [
        # test with 2 URLs both going through the same proxy
        { "target_urls": ["https://host1/a/b.img", "https://host1/a/b2.img"],
            "proxy": ["http://proxy1:3000/"],
            "test_files": [ "host1/a/b.img", "host1/a/b2.img"], },
        ], indirect=True)
    async def test_dl_1proxy_ok(self, httpx_mock, httpeat_conf):
        httpx_mock.add_response(proxy_url="http://proxy1:3000/", url="https://host1/a/b.img")
        httpx_mock.add_response(proxy_url="http://proxy1:3000/", url="https://host1/a/b2.img")
        await Httpeat(httpeat_conf).run()
        assert_local_files(httpeat_conf)

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
        httpx_mock.add_response(proxy_url="http://proxy1:3000/", url="https://host1/a/b.img")
        httpx_mock.add_response(proxy_url="http://proxy2:3000/", url="https://host1/a/b2.img")
        await Httpeat(httpeat_conf).run()
        assert_local_files(httpeat_conf)

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
        httpx_mock.add_response(url="https://host1/a/toto.png")
        await Httpeat(httpeat_conf).run()
        assert_local_files(httpeat_conf)

    @pytest.mark.parametrize("httpeat_conf", [
        { "target_urls": ["https://host1/a/"],
            "test_files": [ "host1/a/toto.png" ], },
        ], indirect=True)
    async def test_index_err_readtimeout(self, httpx_mock: HTTPXMock, httpeat_conf):
        httpx_mock.add_exception(httpx.ReadTimeout("Unable to read within timeout"))
        httpx_mock.add_response(url="https://host1/a/",
                html="<body><a href='toto.png'/></body>")
        httpx_mock.add_response()
        await Httpeat(httpeat_conf).run()
        assert_local_files(httpeat_conf)

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
        httpx_mock.add_response(url="https://host1/a/toto.png")
        await Httpeat(httpeat_conf).run()
        assert_local_files(httpeat_conf)

    @pytest.mark.parametrize("httpeat_conf", [
        { "target_urls": ["https://host1/a/"],
            "test_files": [ ],
            "skip": ["dl-path:.*bibi.*", "dl-path:.*toto.*"]},
        ], indirect=True)
    async def test_skip_rules_dl_path_2(self, httpx_mock: HTTPXMock, httpeat_conf):
        httpx_mock.add_response(url="https://host1/a/", html=self.INDEX)
        await Httpeat(httpeat_conf).run()
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
        httpx_mock.add_response(url="https://host1/a/b.img", match_headers={'User-Agent': ua})
        await Httpeat(httpeat_conf).run()
        assert_local_files(httpeat_conf)

if __name__ == '__main__':
    sys.exit(pytest.main())

"""
    #async def simulate_network_latency(request: httpx.Request):
    #    await asyncio.sleep(1)
    #    response = httpx.Response(
    #        status_code=200, content=b'toto',
    #    )
    #    print(f"XXX headers {response.headers}")
    #    response.headers['content-length'] = "400"
    #    return response
    async def simulate_network_error(request: httpx.Request):
        raise httpx.ReadTimeout("Unable to read within timeout")
    httpx_mock.add_callback(simulate_network_error)
    httpx_mock.add_callback(simulate_network_error)
"""
