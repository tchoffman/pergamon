"""Small Meshtastic utilities for bring-up and testing.

    pergamon mesh info                  radio, firmware, region, known nodes
    pergamon mesh send <node> "text"    one-off DM (node id like !a1b2c3d4)
    pergamon mesh send ^all "text"      broadcast on the primary channel

Set MESHTASTIC_PORT=/dev/cu.usbserial-XXXX if auto-detect picks the wrong port.
"""
from __future__ import annotations

import os
import sys
import time

from rich.console import Console
from rich.table import Table

from modules.comms.meshtastic.common import MAX_MESH_CHARS

console = Console()


def _open():
    try:
        import meshtastic.serial_interface as serial_iface
    except ImportError:
        console.print("[red]meshtastic package not installed[/red]")
        return None
    port = os.environ.get("MESHTASTIC_PORT")
    try:
        return serial_iface.SerialInterface(devPath=port) if port else serial_iface.SerialInterface()
    except Exception as e:
        console.print(f"[red]no Meshtastic device found:[/red] {e}")
        console.print("Is it plugged in with a data (not charge-only) USB cable? "
                      "Try: ls /dev/cu.usbserial* /dev/cu.usbmodem*")
        return None


def info() -> int:
    iface = _open()
    if not iface:
        return 1
    try:
        me = iface.getMyNodeInfo() or {}
        user = me.get("user", {})
        console.print(f"[bold]this radio:[/bold] {user.get('longName', '?')} "
                      f"({user.get('id', '?')}) hw={user.get('hwModel', '?')}")
        try:
            lora = iface.localNode.localConfig.lora
            console.print(f"region={lora.region} preset={lora.modem_preset}")
        except Exception:
            pass
        nodes = getattr(iface, "nodes", {}) or {}
        t = Table(title=f"{len(nodes)} known node(s)")
        for c in ("id", "name", "last heard", "SNR", "hops"):
            t.add_column(c)
        for n in nodes.values():
            u = n.get("user", {})
            heard = n.get("lastHeard")
            ago = f"{int((time.time() - heard) / 60)} min ago" if heard else ""
            t.add_row(u.get("id", ""), u.get("longName", ""), ago,
                      str(n.get("snr", "")), str(n.get("hopsAway", "")))
        console.print(t)
        return 0
    finally:
        iface.close()


def send(dest: str, text: str) -> int:
    if len(text.encode()) > MAX_MESH_CHARS:
        console.print(f"[yellow]message is over {MAX_MESH_CHARS} bytes; it may be cut off[/yellow]")
    iface = _open()
    if not iface:
        return 1
    try:
        if dest in ("^all", "all", "broadcast"):
            iface.sendText(text)
        else:
            iface.sendText(text, destinationId=dest, wantAck=True)
        console.print(f"[green]sent[/green] → {dest}")
        time.sleep(2)  # let the serial write flush before closing
        return 0
    finally:
        iface.close()


def main(argv: list[str]) -> int:
    if argv and argv[0] == "info":
        return info()
    if argv and argv[0] == "send" and len(argv) >= 3:
        return send(argv[1], " ".join(argv[2:]))
    console.print(__doc__.split("\n\n")[1])
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
