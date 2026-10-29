"""Exercise the search for an account's home regional server.

No Home Assistant and no network: the HTTP session is a fake that answers per
URL, so each case states exactly which servers exist and what they say.

The behaviour under test matters because getting it wrong is invisible. An
account is served by exactly one region; every other live region refuses it with
a specific code. And which load-balancer index exists differs per region, so a
host that does not resolve says nothing about the account and must not be
treated as an answer.
"""
import asyncio
import importlib.util
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
COMPONENT = os.path.join(os.path.dirname(HERE), "custom_components", "golmar_quvii")

PASSES, FAILS = [], []


def check(name, got, want):
    ok = got == want
    (PASSES if ok else FAILS).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + f"   got={got!r} want={want!r}")


def load():
    pkg = types.ModuleType("gqr")
    pkg.__path__ = [COMPONENT]
    sys.modules["gqr"] = pkg
    for name in ("const", "cloud"):
        spec = importlib.util.spec_from_file_location(
            f"gqr.{name}", os.path.join(COMPONENT, f"{name}.py")
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"gqr.{name}"] = mod
        spec.loader.exec_module(mod)
    return sys.modules["gqr.cloud"], sys.modules["gqr.const"]


def success(sid="SESSION1"):
    return f"<envelope><body><result>0</result><session><id>{sid}</id></session></body></envelope>"


def refusal(code):
    return f"<envelope><body><result>{code}</result></body></envelope>"


class FakeResponse:
    def __init__(self, text):
        self._text = text

    async def text(self):
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class DeadHost:
    """Models a hostname that does not resolve - the failure aiohttp raises."""

    def __init__(self, exc):
        self._exc = exc

    async def __aenter__(self):
        raise self._exc

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """Answers by host. Any host not listed simply does not exist."""

    def __init__(self, hosts, aiohttp_mod, private_cert=()):
        self.hosts = hosts
        self.tried = []
        self.ssl_used = []
        self._aiohttp = aiohttp_mod
        # hosts whose certificate only passes when pinned, like vimar.qvcloud.net
        self.private_cert = set(private_cert)

    def post(self, url, data=None, headers=None, ssl=True, **kw):
        host = url.split("//")[1].split("/")[0]
        self.tried.append(host)
        self.ssl_used.append(ssl)
        key = self._aiohttp.client_reqrep.ConnectionKey(
            host, 443, False, True, None, None, None)
        if host not in self.hosts:
            return DeadHost(self._aiohttp.ClientConnectorError(key, OSError("no host")))
        if host in self.private_cert and not isinstance(ssl, self._aiohttp.Fingerprint):
            return DeadHost(self._aiohttp.ClientConnectorCertificateError(
                key, Exception("unable to get local issuer certificate")))
        return FakeResponse(self.hosts[host])


async def main():
    cloud, const = load()
    import aiohttp

    def client(region="1", lb=None):
        return cloud.QuviiCloud("acct", "pw", region=region, lb=lb)

    WRONG = const.LOGIN_WRONG_REGION
    BAD = const.LOGIN_BAD_CREDENTIALS

    print("== candidate ordering ==")
    c = client(region="6")
    check("configured region is tried first", c._region_candidates()[0], "6")
    check("every region still reachable",
          sorted(c._region_candidates()), sorted(const.REGION_CANDIDATES))
    check("no region tried twice",
          len(set(c._region_candidates())), len(c._region_candidates()))
    check("default lb order is the constant", client()._lb_candidates(),
          const.LB_CANDIDATES)
    pinned = client(lb="4")._lb_candidates()
    check("a pinned lb is tried first", pinned[0], "4")
    check("pinning does not drop the others", sorted(pinned),
          sorted(const.LB_CANDIDATES))

    print("\n== the happy path costs nothing ==")
    s = FakeSession({"r1-8-sec.qvcloud.net": success()}, aiohttp)
    c = client()
    check("signs in", await c._async_login(s), "SESSION1")
    check("first host only", s.tried, ["r1-8-sec.qvcloud.net"])
    check("region unchanged", c.resolved_region, "1")

    print("\n== a region whose lb8 does not exist (the #2 bug) ==")
    # region 2 has no lb8 host at all; it lives on lb4/lb5
    s = FakeSession({"r2-4-sec.qvcloud.net": success("SYD")}, aiohttp)
    c = client(region="2")
    check("still signs in", await c._async_login(s), "SYD")
    check("tried lb8 first, then fell through", s.tried[0], "r2-8-sec.qvcloud.net")
    check("landed on the host that exists", s.tried[-1], "r2-4-sec.qvcloud.net")
    check("region unchanged", c.resolved_region, "2")

    print("\n== wrong region: search finds the account's real server ==")
    s = FakeSession({
        "r1-8-sec.qvcloud.net": refusal(WRONG),   # live, not this account's
        "r2-4-sec.qvcloud.net": success("SYD"),
    }, aiohttp)
    c = client(region="1")
    check("finds the right region", await c._async_login(s), "SYD")
    check("resolved region recorded", c.resolved_region, "2")
    # a live server that refuses is a definitive answer for that whole region
    check("did not try other lbs in the refusing region",
          [h for h in s.tried if h.startswith("r1-")], ["r1-8-sec.qvcloud.net"])

    print("\n== bad credentials stop the search immediately ==")
    s = FakeSession({
        "r1-8-sec.qvcloud.net": refusal(BAD),
        "r2-4-sec.qvcloud.net": success("SYD"),
    }, aiohttp)
    c = client(region="1")
    try:
        await c._async_login(s)
        check("raises on bad credentials", "no error", "QuviiAuthError")
    except cloud.QuviiAuthError:
        check("raises on bad credentials", True, True)
    check("no other region was tried", s.tried, ["r1-8-sec.qvcloud.net"])

    print("\n== nothing anywhere ==")
    s = FakeSession({"r1-8-sec.qvcloud.net": refusal(WRONG)}, aiohttp)
    c = client(region="1")
    try:
        await c._async_login(s)
        check("raises when no server serves the account", "no error", "QuviiLoginRefused")
    except cloud.QuviiLoginRefused as err:
        check("raises when no server serves the account", WRONG in str(err), True)
    tried_regions = {h.split("-")[0] for h in s.tried}
    check("every region was attempted",
          len(tried_regions), len(const.REGION_CANDIDATES))

    print("\n== the winning server is remembered ==")
    s = FakeSession({
        "r1-8-sec.qvcloud.net": refusal(WRONG),
        "r2-4-sec.qvcloud.net": success("SYD"),
    }, aiohttp)
    c = client(region="1")
    await c._async_login(s)
    before = len(s.tried)
    check("second sign-in goes straight there",
          await c._async_login(s), "SYD")
    check("one request, not another search", len(s.tried) - before, 1)
    check("and it is the right host", s.tried[-1], "r2-4-sec.qvcloud.net")

    print("\n== a remembered server that stops working is re-searched ==")
    s = FakeSession({"r1-8-sec.qvcloud.net": success("A")}, aiohttp)
    c = client(region="1")
    await c._async_login(s)
    s.hosts["r1-8-sec.qvcloud.net"] = refusal(WRONG)
    s.hosts["r3-1-sec.qvcloud.net"] = success("B")
    check("recovers onto the new server", await c._async_login(s), "B")
    check("and records it", c.resolved_region, "3")

    print("\n== a brand with its own server (Vimar) ==")
    VIMAR = "vimar.qvcloud.net"
    s = FakeSession({VIMAR: success("VIMAR")}, aiohttp, private_cert={VIMAR})
    c = cloud.QuviiCloud("acct", "pw", server_host=VIMAR)
    check("signs in on the brand server", await c._async_login(s), "VIMAR")
    check("never touches the regional pool",
          [h for h in s.tried if h != VIMAR], [])
    check("verified first, then pinned",
          [type(x).__name__ for x in s.ssl_used], ["bool", "Fingerprint"])
    check("pin is the Qualvision certificate",
          s.ssl_used[-1].fingerprint.hex(), const.QVCLOUD_CERT_SHA256)

    s = FakeSession({VIMAR: success()}, aiohttp)
    c = cloud.QuviiCloud("acct", "pw", server_host=f"https://{VIMAR}/")
    await c._async_login(s)
    check("scheme and trailing slash are accepted", s.tried, [VIMAR])
    check("a public certificate is never pinned", s.ssl_used, [True])

    print("\n== brand server: wrong account is still an auth error ==")
    # what the Golmar pool says about a View Door account, and vice versa
    s = FakeSession({VIMAR: refusal(BAD)}, aiohttp, private_cert={VIMAR})
    c = cloud.QuviiCloud("acct", "pw", server_host=VIMAR)
    try:
        await c._async_login(s)
        check("raises on bad credentials", "no error", "QuviiAuthError")
    except cloud.QuviiAuthError:
        check("raises on bad credentials", True, True)

    print("\n== brand server: wrong region moves the body, not the host ==")
    s = FakeSession({VIMAR: refusal(WRONG)}, aiohttp)
    c = cloud.QuviiCloud("acct", "pw", server_host=VIMAR)
    try:
        await c._async_login(s)
    except cloud.QuviiLoginRefused:
        pass
    check("one attempt per region, all on the brand server",
          s.tried, [VIMAR] * len(const.REGION_CANDIDATES))

    print("\n== password hashing follows the SDK ==")
    plain = cloud.QuviiCloud("acct", "pw")._pw
    check("a typed password is sha256-hashed",
          plain, "30c952fab122c3f9759f02a6d95c3758b246b4fee239957b2d4fee46e26170c4")
    issued = "ab" * 32
    check("a 64-char SSO password goes through untouched",
          cloud.QuviiCloud("acct", issued)._pw, issued)
    check("pasted with a trailing newline it is still not re-hashed",
          cloud.QuviiCloud("acct", f" {issued}\n")._pw, issued)

    print("\n== Vimar SSO callback ==")
    parse = cloud.parse_sso_credentials
    page = '{"result":0,"account":"vimar_abc","password":"' + issued + '"}'
    check("callback JSON gives account and password",
          parse(page), ("vimar_abc", issued))
    check("whitespace from copy/paste is ignored",
          parse(f"\n  {page}  \n"), ("vimar_abc", issued))
    check("result as a string is accepted", parse(page.replace(":0", ':"0"')),
          ("vimar_abc", issued))
    check("a refused callback is rejected",
          parse('{"result":-1,"account":"","password":""}'), None)
    check("half-copied JSON is rejected", parse(page[:-5]), None)
    check("plain text is rejected", parse("me@example.com"), None)
    check("missing password is rejected", parse('{"result":0,"account":"a"}'), None)
    check("vimar.com password is not mistaken for an issued one",
          cloud.is_issued_password("hunter2"), False)
    check("issued password is recognised", cloud.is_issued_password(issued), True)

    print("\n== brand presets ==")
    check("vimar preset", const.BRANDS[const.BRAND_VIMAR], {
        const.CONF_APP_ID: "2078", const.CONF_OEM_ID: "G0078",
        const.CONF_SERVER_HOST: VIMAR})
    check("golmar preset keeps the old defaults", const.BRANDS[const.BRAND_GOLMAR], {
        const.CONF_APP_ID: const.DEFAULT_APP_ID,
        const.CONF_OEM_ID: const.DEFAULT_OEM_ID,
        const.CONF_SERVER_HOST: ""})

    print("\n== refusal classification ==")
    check("wrong-region is not an auth error",
          type(cloud.QuviiCloud._login_failure(refusal(WRONG))).__name__,
          "QuviiLoginRefused")
    check("bad credentials is",
          type(cloud.QuviiCloud._login_failure(refusal(BAD))).__name__,
          "QuviiAuthError")

    print(f"\n{len(PASSES)} passed, {len(FAILS)} failed")
    for name in FAILS:
        print("  FAILED:", name)
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
