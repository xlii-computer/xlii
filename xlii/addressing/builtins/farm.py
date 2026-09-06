"""FarmProvider — the house classifieds board (``farm://``).

``jobs://`` remains the session background-job chip. This scheme is the
farm ads + node beacons. Pools are rooms; a ticket in a room this node
is not in is absent from the listing.
"""

from __future__ import annotations

from xlii.addressing import Address, Node, Resolution, ShellExport
from xlii.farm_view import ambient_farm_view


class FarmProvider:
    scheme = "farm"

    def resolve(self, address: Address) -> Resolution:
        key = address.key.strip()
        if not key or key == "nodes":
            return Resolution(ok=True, address=address, kind="farm")
        view = ambient_farm_view()
        ok = any(a.ticket_id == key for a in view.ads)
        return Resolution(
            ok=ok, address=address, kind="farm",
            reason="" if ok else f"no farm ticket {key!r}",
        )

    def stat(self, address: Address) -> Node:
        key = address.key.strip()
        container = (not key) or key == "nodes"
        return Node(
            address=str(address),
            name=key or "farm",
            kind="container" if container else "leaf",
            extra={"type": "farm"},
        )

    def list(self, address: Address) -> "list[Node]":
        key = address.key.strip()
        view = ambient_farm_view()
        if key == "nodes":
            return [
                Node(
                    address=f"farm://nodes/{n.node}",
                    name=f"{n.node}  {n.offers}  {n.busy}",
                    kind="leaf",
                    extra={"type": "farm-node", "pool": n.pool},
                )
                for n in view.nodes
            ]
        if key:
            return []
        out = [
            Node(
                address="farm://nodes",
                name="nodes (flip)",
                kind="container",
                extra={"type": "farm-nodes"},
            )
        ]
        for a in view.ads:
            out.append(Node(
                address=f"farm://{a.ticket_id}",
                name=f"{a.state}  {a.job}  {a.poster}  {a.budget}",
                kind="leaf",
                extra={"type": "farm-ad", "pool": a.pool, "state": a.state},
            ))
        return out

    def read(self, address: Address) -> bytes:
        key = address.key.strip()
        view = ambient_farm_view()
        if not key:
            raise IsADirectoryError("farm://: a board root — use ls")
        if key == "nodes":
            lines = ["# farm nodes", ""]
            for n in view.nodes:
                lines.append(
                    f"- {n.node}  pool={n.pool}  offers={n.offers}  "
                    f"gig={n.gig}  {n.allowance}  {n.busy}"
                )
            return ("\n".join(lines).rstrip() + "\n").encode()
        for a in view.ads:
            if a.ticket_id == key:
                text = (
                    f"# {a.ticket_id}\n\n"
                    f"- pool: {a.pool}\n"
                    f"- state: {a.state}\n"
                    f"- job: {a.job}\n"
                    f"- poster: {a.poster}\n"
                    f"- budget: {a.budget}\n"
                    f"- claimant: {a.claimant or '—'}\n"
                    f"- age: {a.age}\n"
                    f"- task: {a.task}\n"
                )
                return text.encode()
        raise FileNotFoundError(f"farm://{key}: no such ticket")

    def exists(self, address: Address) -> bool:
        key = address.key.strip()
        if not key or key == "nodes":
            return True
        view = ambient_farm_view()
        return any(a.ticket_id == key for a in view.ads)

    def shell_export(self, address: Address) -> ShellExport:
        if not address.key.strip() or address.key.strip() == "nodes":
            return ShellExport(kind="address")
        return ShellExport(kind="content", content=self.read(address), suffix=".md")
