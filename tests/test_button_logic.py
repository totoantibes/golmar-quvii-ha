"""Exercise button availability and unlock routing without Home Assistant.

Home Assistant is stubbed so the real button.py is imported unmodified; only the
coordinator and the transports are fakes. Nothing touches the network.
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


def stub_homeassistant():
    def mod(name, **attrs):
        m = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        sys.modules[name] = m
        return m

    class ButtonEntity:
        pass

    class CoordinatorEntity:
        def __init__(self, coordinator):
            self.coordinator = coordinator

        @property
        def available(self):
            return True

    class HomeAssistantError(Exception):
        pass

    mod("homeassistant")
    mod("homeassistant.components")
    mod("homeassistant.components.button", ButtonEntity=ButtonEntity)
    mod("homeassistant.config_entries", ConfigEntry=object)
    mod("homeassistant.core", HomeAssistant=object)
    mod("homeassistant.exceptions", HomeAssistantError=HomeAssistantError)
    mod("homeassistant.helpers")
    mod("homeassistant.helpers.aiohttp_client",
        async_get_clientsession=lambda hass: "session")
    mod("homeassistant.helpers.device_registry", DeviceInfo=dict)
    mod("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
    mod("homeassistant.helpers.update_coordinator",
        CoordinatorEntity=CoordinatorEntity)
    return HomeAssistantError


def load_component():
    pkg = types.ModuleType("gq")
    pkg.__path__ = [COMPONENT]
    sys.modules["gq"] = pkg
    for name in ("const", "cloud", "device", "button"):
        spec = importlib.util.spec_from_file_location(
            f"gq.{name}", os.path.join(COMPONENT, f"{name}.py")
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"gq.{name}"] = module
        spec.loader.exec_module(module)
    return sys.modules["gq.button"], sys.modules["gq.cloud"]


class FakeLocal:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def async_open(self, session, door, lock):
        self.calls.append((door, lock))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeCloudControl:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    async def async_open(self, session, umid, dynpw, authcode, door, lock):
        self.calls.append((umid, door, lock, bool(dynpw)))
        if self.error:
            raise self.error


class FakeCoordinator:
    def __init__(self, mode, local=None, dynpw="dyn", cloud_error=None):
        self.unlock_mode = mode
        self.devices = {"u1": local} if local else {}
        self.cloud_control = FakeCloudControl(cloud_error)
        # mirrors the coordinator: an address is published only when a local
        # endpoint was actually resolved, so cloud-only entries carry None
        self.data = {"u1": {"name": "Panel", "model": "X", "authcode": "a" * 64,
                            "dynamic_password": dynpw,
                            "ip": "192.168.1.82" if local else None,
                            "port": 443 if local else None}}

    def cloud_ready(self, umid):
        return bool((self.data.get(umid) or {}).get("dynamic_password"))


def make_button(button_mod, coordinator):
    btn = button_mod.GolmarQuviiButton(
        coordinator, "u1", coordinator.data["u1"], 9, 2, "Gate")
    btn.hass = object()
    return btn


async def main():
    ha_error = stub_homeassistant()
    button_mod, cloud_mod = load_component()

    print("== availability matrix ==")
    cases = [
        # mode,   local found, dynamic pw, expected available
        ("local", True, "dyn", True),
        ("local", False, "dyn", False),          # unchanged 0.5.0 behaviour
        ("cloud", False, "dyn", True),           # the whole point of cloud mode
        ("cloud", True, None, False),            # no dynamic password -> nothing to send
        ("auto", False, "dyn", True),
        ("auto", True, None, True),
        ("auto", False, None, False),
    ]
    for mode, has_local, dynpw, want in cases:
        coord = FakeCoordinator(mode, FakeLocal(True) if has_local else None, dynpw)
        btn = make_button(button_mod, coord)
        check(f"{mode}: local={has_local} dynpw={bool(dynpw)}", btn.available, want)

    print("\n== press routing ==")
    # local mode never reaches the cloud, even when the panel refuses
    local = FakeLocal(False)
    coord = FakeCoordinator("local", local)
    btn = make_button(button_mod, coord)
    try:
        await btn.async_press()
        check("local mode surfaces rejection", "no error", "HomeAssistantError")
    except ha_error as err:
        check("local mode surfaces rejection", "rejected" in str(err), True)
    check("local mode did not call cloud", coord.cloud_control.calls, [])

    # cloud mode goes straight out, local client untouched
    local = FakeLocal(True)
    coord = FakeCoordinator("cloud", local)
    btn = make_button(button_mod, coord)
    await btn.async_press()
    check("cloud mode skipped local", local.calls, [])
    check("cloud mode sent door/lock", coord.cloud_control.calls,
          [("u1", 9, 2, True)])

    # auto: local succeeds -> cloud never touched
    local = FakeLocal(True)
    coord = FakeCoordinator("auto", local)
    btn = make_button(button_mod, coord)
    await btn.async_press()
    check("auto used local first", local.calls, [(9, 2)])
    check("auto skipped cloud on success", coord.cloud_control.calls, [])

    # auto: local refuses -> cloud takes over
    local = FakeLocal(False)
    coord = FakeCoordinator("auto", local)
    btn = make_button(button_mod, coord)
    await btn.async_press()
    check("auto fell back after rejection", coord.cloud_control.calls,
          [("u1", 9, 2, True)])

    # auto: local raises (panel closed its port mid-press) -> cloud takes over
    local = FakeLocal(OSError("connection refused"))
    coord = FakeCoordinator("auto", local)
    btn = make_button(button_mod, coord)
    await btn.async_press()
    check("auto fell back after exception", coord.cloud_control.calls,
          [("u1", 9, 2, True)])

    # auto: no local endpoint at all -> cloud directly
    coord = FakeCoordinator("auto", None)
    btn = make_button(button_mod, coord)
    await btn.async_press()
    check("auto with no local endpoint", coord.cloud_control.calls,
          [("u1", 9, 2, True)])

    # auto: both fail -> one error naming both
    local = FakeLocal(False)
    coord = FakeCoordinator("auto", local,
                            cloud_error=cloud_mod.QuviiCloudError("not registered"))
    btn = make_button(button_mod, coord)
    try:
        await btn.async_press()
        check("auto reports both failures", "no error", "HomeAssistantError")
    except ha_error as err:
        check("auto reports both failures",
              "local failed" in str(err) and "cloud failed" in str(err), True)

    print("\n== diagnostics attribute ==")
    coord = FakeCoordinator("auto", FakeLocal(True))
    btn = make_button(button_mod, coord)
    check("address exposed", btn.extra_state_attributes["local_address"],
          "192.168.1.82:443")
    check("mode exposed", btn.extra_state_attributes["unlock_mode"], "auto")
    coord = FakeCoordinator("cloud", None)
    btn = make_button(button_mod, coord)
    check("no address when cloud only",
          btn.extra_state_attributes["local_address"], None)

    print(f"\n{len(PASSES)} passed, {len(FAILS)} failed")
    for name in FAILS:
        print("  FAILED:", name)
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
