"""Exercise channel enumeration against a real panel's own reply.

`fixtures/attachinfo.json` is an unedited `get.device.attachInfo` response from
an ART7W-G2+. It is the ground truth for what a panel actually reports, and it
is what keeps the static fallback honest: the door channels are 1-4 and 9-12,
not 1-8, and channels 5-8 and 13-16 are CCTV inputs carrying no lock at all.

Guessing that the doors run 1..8 is the natural mistake, and it is the one a
user made by hand when the fallback was too short - producing buttons that
silently did nothing.
"""
import asyncio
import importlib.util
import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
COMPONENT = os.path.join(os.path.dirname(HERE), "custom_components", "golmar_quvii")
FIXTURE = os.path.join(HERE, "fixtures", "attachinfo.json")

PASSES, FAILS = [], []


def check(name, got, want):
    ok = got == want
    (PASSES if ok else FAILS).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + f"   got={got!r} want={want!r}")


def load():
    pkg = types.ModuleType("gqe")
    pkg.__path__ = [COMPONENT]
    sys.modules["gqe"] = pkg
    for name in ("const", "device", "cloud"):
        spec = importlib.util.spec_from_file_location(
            f"gqe.{name}", os.path.join(COMPONENT, f"{name}.py")
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"gqe.{name}"] = mod
        spec.loader.exec_module(mod)
    return sys.modules["gqe.device"], sys.modules["gqe.cloud"], sys.modules["gqe.const"]


class FakeResponse:
    status = 200

    def __init__(self, text):
        self._text = text

    async def text(self):
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """Answers the control endpoint; records whether it was called at all."""

    def __init__(self, body):
        self.body = body
        self.posts = []

    def post(self, url, json=None, headers=None, **kw):
        self.posts.append((url, json))
        return FakeResponse(self.body)

    def get(self, url, params=None, **kw):
        # the token mint
        return FakeResponse('{"access_token":"h.e30.s","refresh_token":"r"}')


def read_fixture():
    with open(FIXTURE, encoding="utf-8") as fh:
        return fh.read()


async def main():
    device, cloud, const = load()
    raw = read_fixture()
    doc = json.loads(raw)

    print("== what the real panel reports ==")
    locks = device.parse_locks(raw)
    channels = sorted({lk["door"] for lk in locks})
    check("door channels are 1-4 and 9-12", channels, [1, 2, 3, 4, 9, 10, 11, 12])
    check("two relays each", len(locks), 16)
    check("CCTV channels carry no lock",
          [c for c in (5, 6, 7, 8, 13, 14, 15, 16) if c in channels], [])
    check("names come from the panel", locks[0]["name"], "Door1 Lock 1")
    check("street panels keep their names",
          next(lk["name"] for lk in locks if lk["door"] == 9), "General Panel1 Lock 1")

    print("\n== the static fallback matches what a panel can address ==")
    fallback_channels = sorted({d for d, _, _ in const.DEFAULT_LOCKS})
    check("fallback covers exactly the real door channels",
          fallback_channels, channels)
    check("fallback has an entry per relay",
          len(const.DEFAULT_LOCKS), len(locks))
    # the specific regression: doors 3, 4 and general panels 2-4 were missing,
    # which is what sent a user into const.py to add them by hand
    for missing in (3, 4, 10, 11, 12):
        check(f"channel {missing} is offered", missing in fallback_channels, True)

    print("\n== parsing is indifferent to how the reply arrives ==")
    check("already-decoded document", len(device.parse_locks(doc)), 16)
    check("raw text", len(device.parse_locks(raw)), 16)
    check("bytes", len(device.parse_locks(raw.encode())), 16)
    # through the cloud the same body comes back as a JSON string inside payload
    check("nested json string", len(device.parse_locks(json.dumps(doc))), 16)

    print("\n== nothing usable yields nothing, never an exception ==")
    for label, value in (
        ("not json", "<html>nope</html>"),
        ("empty string", ""),
        ("None", None),
        ("wrong shape", {"body": {"content": {}}}),
        ("devlist not a list", {"body": {"content": {"sub-devlist": "x"}}}),
        ("entries not dicts", {"body": {"content": {"sub-devlist": [1, 2]}}}),
        ("channel with no id",
         {"body": {"content": {"sub-devlist": [{"type": "chn", "sub-type": "cam"}]}}}),
    ):
        check(f"{label} -> []", device.parse_locks(value), [])

    print("\n== cloud enumeration ==")
    ctrl = cloud.QuviiCloudControl("acct", "pw")

    # the panel's reply arrives as a JSON string in payload, like an open does
    s = FakeSession(json.dumps({"result": 0, "payload": raw}))
    got = await ctrl.async_get_locks(s, "umid1", "dynpw")
    check("enumerates through the cloud", len(got), 16)
    check("same channels as local", sorted({lk["door"] for lk in got}), channels)
    check("sent the right command", s.posts[-1][1]["command"], "get.device.attachInfo")
    check("addressed the right panel", s.posts[-1][1]["deviceId"], "umid1")

    # a panel that will not describe itself must not break setup
    for label, body in (
        ("refused", '{"result":3,"message":"not registered"}'),
        ("empty payload", '{"result":0,"payload":null}'),
        ("payload is not json", '{"result":0,"payload":"nope"}'),
        ("not json at all", "<html>502</html>"),
    ):
        s = FakeSession(body)
        check(f"{label} -> fall back", await ctrl.async_get_locks(s, "u", "p"), [])

    # no credential means no request worth making
    s = FakeSession(json.dumps({"result": 0, "payload": raw}))
    check("no dynamic password -> []", await ctrl.async_get_locks(s, "u", ""), [])
    check("and no request was sent", s.posts, [])

    print(f"\n{len(PASSES)} passed, {len(FAILS)} failed")
    for name in FAILS:
        print("  FAILED:", name)
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
