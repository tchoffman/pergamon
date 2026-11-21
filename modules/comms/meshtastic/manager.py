"""Watches USB for Meshtastic radios and runs the ones the host made servers.

Every few seconds: new ports are opened just long enough to read the radio's
node ID. Server radios stay open and answer; clients are closed and left
alone; undecided radios are closed and reported so the host can choose.
Unplugged or dead radios are dropped and picked up again when they return.
"""
from __future__ import annotations

import time
from typing import Any, Callable

from rag import radios


def _preset_name(iface) -> str:
    try:
        from meshtastic.protobuf import config_pb2
        lora = iface.localNode.localConfig.lora
        return config_pb2.Config.LoRaConfig.ModemPreset.Name(lora.modem_preset) if lora.use_preset else "custom"
    except Exception:  # noqa: BLE001
        return ""


class RadioManager:
    def __init__(self, serial_iface, list_ports: Callable[[], list[str]], log: Callable[[str], None],
                 silence_s: float = 3600, fixed_port: str | None = None):
        self.serial_iface = serial_iface
        self.list_ports = list_ports
        self.log = log
        self.silence_s = silence_s
        self.fixed_port = fixed_port        # MESHTASTIC_PORT: always serve this one
        self.active: dict[str, Any] = {}    # port → open interface (servers)
        self.ids: dict[str, dict] = {}      # port → {"id", "name"} for every radio seen
        self.last_heard: dict[int, float] = {}
        self._told: set[str] = set()        # undecided radios already announced

    # ── the scan ──
    def scan(self) -> None:
        ports = set(self.list_ports())
        for port in list(self.active):
            if port not in ports:
                self.log(f"radio on {port} unplugged")
                self._drop(port)
            elif self._role(port, self.ids.get(port, {}).get("id", "")) != "server":
                self.log(f"radio on {port} is no longer a Pergamon radio; letting it go")
                self._drop(port)
        for port in list(self.ids):
            if port not in ports:
                self.ids.pop(port, None)
        for port in sorted(ports):
            if port in self.active:
                continue
            info = self.ids.get(port)
            if info and self._role(port, info["id"]) != "server":
                continue  # known client or undecided: don't keep reopening it
            self._probe(port)
        radios.write_connected([{"port": p, **i} for p, i in sorted(self.ids.items())])

    def _role(self, port: str, node_id: str) -> str | None:
        if self.fixed_port and port == self.fixed_port:
            return "server"
        return radios.role(node_id)

    def _probe(self, port: str) -> None:
        try:
            iface = self.serial_iface.SerialInterface(devPath=port)
        except Exception:  # busy (another app has it) or not a radio
            return
        try:
            user = (iface.getMyNodeInfo() or {}).get("user", {})
            node_id, name = user.get("id"), user.get("longName") or ""
        except Exception:
            node_id, name = None, ""
        if not node_id:
            iface.close()
            return
        self.ids[port] = {"id": node_id, "name": name, "preset": _preset_name(iface)}
        r = self._role(port, node_id)
        if r == "server":
            self.active[port] = iface
            self.last_heard[id(iface)] = time.time()
            self.log(f"serving with {name} {node_id} on {port}")
            return
        iface.close()
        if r is None and node_id not in self._told:
            self._told.add(node_id)
            self.log(f"new radio {name} {node_id} on {port}: is it a Pergamon radio? Choose on the "
                     f"Radios page, or run: pergamon radios serve {node_id}  (or: client {node_id})")

    def apply_preset(self, preset: str) -> None:
        """Switch every radio on USB to `preset`. Each one restarts, so all are
        let go here and picked up again by later scans."""
        from meshtastic.protobuf import config_pb2
        value = config_pb2.Config.LoRaConfig.ModemPreset.Value(preset)
        ports = sorted(self.list_ports())  # whatever is plugged in now, known yet or not
        self.log(f"switching {len(ports)} radio(s) on USB to {preset}" if ports
                 else f"no radios on USB to switch to {preset}")
        for port in ports:
            iface = self.active.get(port)
            try:
                if iface is None:
                    iface = self.serial_iface.SerialInterface(devPath=port)
                name = ((iface.getMyNodeInfo() or {}).get("user", {}).get("longName")) or port
                lora = iface.localNode.localConfig.lora
                if lora.use_preset and lora.modem_preset == value:
                    self.log(f"{name} is already on {preset}")
                else:
                    lora.use_preset, lora.modem_preset, lora.channel_num = True, value, 0
                    iface.localNode.writeConfig("lora")
                    self.log(f"switched {name} on {port} to {preset}; it's restarting")
            except Exception as e:  # noqa: BLE001
                self.log(f"couldn't switch the radio on {port}: {e!r}")
            finally:
                self.active.pop(port, None)
                if iface is not None:
                    try:
                        iface.close()
                    except Exception:
                        pass
        self.ids.clear()  # re-read everything once they're back

    def _drop(self, port: str) -> None:
        iface = self.active.pop(port, None)
        if iface is not None:
            self.last_heard.pop(id(iface), None)
            try:
                iface.close()
            except Exception:
                pass

    # ── health ──
    def serves(self, interface) -> bool:
        return any(iface is interface for iface in self.active.values())

    def heard(self, interface) -> None:
        self.last_heard[id(interface)] = time.time()

    def check_health(self, reader_dead: Callable[[Any], bool], now: float | None = None) -> None:
        """Drop radios whose reader stopped or that went silent; the next scan
        reopens them if they're still plugged in."""
        now = now or time.time()
        for port, iface in list(self.active.items()):
            if reader_dead(iface):
                self.log(f"radio reader on {port} stopped; reconnecting")
                self._drop(port)
                self.ids.pop(port, None)
            elif now - self.last_heard.get(id(iface), now) > self.silence_s:
                self.log(f"no packets on {port} for {int(self.silence_s // 60)} min; reconnecting")
                self._drop(port)
                self.ids.pop(port, None)

    def lost(self, interface) -> None:
        for port, iface in list(self.active.items()):
            if iface is interface:
                self.log(f"radio on {port} disconnected")
                self._drop(port)
                self.ids.pop(port, None)

    def primary(self):
        """The radio for scheduled sends (forecast, feeds, mail retries)."""
        return next(iter(self.active[p] for p in sorted(self.active)), None)

    def close_all(self) -> None:
        for port in list(self.active):
            self._drop(port)
