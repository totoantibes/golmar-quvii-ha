"""Exercise the refresh-interval logic without Home Assistant or a network.

Home Assistant is stubbed so the real __init__.py is imported unmodified. The
interesting behaviour is how soon the coordinator comes back: the dynamic
password behind cloud unlock lasts days, so anything that lets an install
inherit the monthly default silently loses cloud unlock partway through a month.
"""
import importlib.util
import os
import sys
import types
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
COMPONENT = os.path.join(os.path.dirname(HERE), "custom_components", "golmar_quvii")

PASSES, FAILS = [], []


def check(name, got, want):
    ok = got == want
    (PASSES if ok else FAILS).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + f"   got={got!r} want={want!r}")


def close_to(name, got: timedelta, want: timedelta, tol=timedelta(minutes=5)):
    ok = abs(got - want) <= tol
    (PASSES if ok else FAILS).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + f"   got={got} want~={want}")


def stub_homeassistant():
    def mod(name, **attrs):
        m = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        sys.modules[name] = m
        return m

    class DataUpdateCoordinator:
        def __init__(self, hass, logger, name=None, update_interval=None):
            self.hass = hass
            self.logger = logger
            self.name = name
            self.update_interval = update_interval
            self.data = None

    class Store:
        def __init__(self, *a, **kw):
            self.saved = None

        async def async_load(self):
            return None

        async def async_save(self, data):
            self.saved = data

    class Platform:
        BUTTON = "button"

    mod("homeassistant")
    mod("homeassistant.config_entries", ConfigEntry=object)
    mod("homeassistant.const", Platform=Platform)
    mod("homeassistant.core", HomeAssistant=object)
    mod("homeassistant.exceptions", ConfigEntryAuthFailed=type(
        "ConfigEntryAuthFailed", (Exception,), {}))
    mod("homeassistant.helpers")
    mod("homeassistant.helpers.start", async_at_started=lambda hass, cb: (lambda: None))
    mod("homeassistant.helpers.storage", Store=Store)
    mod("homeassistant.helpers.update_coordinator",
        DataUpdateCoordinator=DataUpdateCoordinator,
        UpdateFailed=type("UpdateFailed", (Exception,), {}))


def load_component():
    pkg = types.ModuleType("gqc")
    pkg.__path__ = [COMPONENT]
    sys.modules["gqc"] = pkg
    for name in ("const", "cloud", "device", "__init__"):
        spec = importlib.util.spec_from_file_location(
            f"gqc.{name}", os.path.join(COMPONENT, f"{name}.py")
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"gqc.{name}"] = module
        spec.loader.exec_module(module)
    return sys.modules["gqc.__init__"]


class FakeEntry:
    def __init__(self, options: dict | None = None) -> None:
        self.entry_id = "test"
        self.data = {"account": "a", "password": "p", "region": "1"}
        self.options = options or {}


def stamp(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).strftime("%Y-%m-%d %H:%M:%S")


def make_coordinator(mod, mode):
    return mod.GolmarQuviiCoordinator(object(), FakeEntry({"unlock_mode": mode}))


def main():
    stub_homeassistant()
    mod = load_component()

    print("== expiry window: unknown stamps are reported, not swallowed ==")
    c = make_coordinator(mod, "cloud")
    soon, far = stamp(timedelta(days=2)), stamp(timedelta(days=20))

    got, unknown = c._expiry_window([{"password_expired": soon},
                                     {"password_expired": far}])
    check("soonest of two known", got.strftime("%Y-%m-%d %H:%M:%S"), soon)
    check("both known -> nothing unknown", unknown, False)

    # the masking case: a far-off readable stamp must not stand in for a panel
    # whose own expiry could not be read
    _, unknown = c._expiry_window([{"password_expired": far},
                                   {"password_expired": None}])
    check("one unreadable is reported", unknown, True)
    got, unknown = c._expiry_window([{"password_expired": "not a date"}])
    check("unparseable -> no expiry", got, None)
    check("unparseable -> unknown", unknown, True)
    check("no devices -> nothing unknown", c._expiry_window([]), (None, False))

    print("\n== refresh interval ==")
    day = timedelta(days=1)

    # local mode ignores the dynamic password entirely
    c = make_coordinator(mod, "local")
    c.endpoints = {"u1": {"ip": "10.0.0.1", "port": 443}}
    c._apply_interval({"u1": "a"}, [], [{"password_expired": stamp(2 * day)}],
                      True, False, "local")
    check("local mode keeps the monthly cycle", c.update_interval, mod.UPDATE_INTERVAL)

    # cloud mode tracks the credential: expiry minus the 12h margin
    c = make_coordinator(mod, "cloud")
    c._apply_interval({}, [], [{"password_expired": stamp(5 * day)}],
                      False, True, "cloud")
    close_to("cloud mode follows the expiry", c.update_interval,
             5 * day - mod.DYNAMIC_PASSWORD_MARGIN)

    # REGRESSION (review item 4): an unreadable stamp used to inherit 30 days,
    # so the credential expired mid-cycle and cloud unlock stopped with no warning
    c = make_coordinator(mod, "cloud")
    c._apply_interval({}, [], [{"password_expired": None}], False, True, "cloud")
    check("unknown expiry does NOT inherit the monthly default",
          c.update_interval != mod.UPDATE_INTERVAL, True)
    check("unknown expiry clamps to the conservative interval",
          c.update_interval, mod.UNKNOWN_EXPIRY_INTERVAL)

    # REGRESSION: one panel's distant known expiry must not set the schedule for
    # another panel whose expiry is unreadable
    c = make_coordinator(mod, "cloud")
    c._apply_interval({}, [], [{"password_expired": stamp(25 * day)},
                               {"password_expired": "unparseable"}],
                      False, True, "cloud")
    check("a known expiry cannot mask an unknown one",
          c.update_interval, mod.UNKNOWN_EXPIRY_INTERVAL)

    # already expired -> floor, never zero or negative
    c = make_coordinator(mod, "cloud")
    c._apply_interval({}, [], [{"password_expired": stamp(-2 * day)}],
                      False, True, "cloud")
    check("expired credential floors at the minimum",
          c.update_interval, mod.MIN_CLOUD_INTERVAL)

    # an unresolved panel still shortens the cycle in modes that use the LAN
    c = make_coordinator(mod, "auto")
    c.endpoints = {}
    c._apply_interval({"u1": "a"}, [], [{"password_expired": stamp(20 * day)}],
                      True, True, "auto")
    check("unresolved panel retries soon", c.update_interval, mod.RETRY_INTERVAL)

    print(f"\n{len(PASSES)} passed, {len(FAILS)} failed")
    for name in FAILS:
        print("  FAILED:", name)
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
