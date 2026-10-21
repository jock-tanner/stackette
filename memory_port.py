"""
Word-oriented memory port: the one interface that CPU, cache, MMU and SDRAM
controller all speak, so any of them can be inserted between the others with
a single wiring.connect() change.

MemoryPort is described from the *initiator's* point of view (the side that
issues requests). A target declares it as In(MemoryPort(...)); an initiator
declares it as Out(MemoryPort(...)). A component that is both (a cache)
declares one of each.

Protocol:
  * req: valid/ready handshake. The initiator sets valid together with
    we / addr / wdata and holds them until ready is high; the request is
    accepted on the clock edge where valid & ready.
  * resp: one valid pulse per accepted read request (we = 0), carrying the
    data, in request order. Writes produce no response. There is no
    backpressure on resp: the initiator must always accept it.
  * addr is a word address; its width is a parameter, so a width mismatch
    between two components is caught by wiring.connect() and has to be
    resolved by an explicit adapter (e.g. an MMU or cache).
"""
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out


class MemoryRequest(wiring.Signature):
    """Request channel, from the initiator's point of view."""
    def __init__(self, addr_width, data_width=16):
        super().__init__({
            'valid': Out(1),
            'ready': In(1),
            'we':    Out(1),
            'byte_hi': Out(1),
            'byte_lo': Out(1),
            'addr':  Out(addr_width),
            'wdata': Out(data_width),
        })


class MemoryResponse(wiring.Signature):
    """Read response channel, from the target's point of view."""
    def __init__(self, data_width=16):
        super().__init__({
            'valid': Out(1),
            'data':  Out(data_width),
        })


class MemoryPort(wiring.Signature):
    """A request channel plus a response channel, initiator's view."""
    def __init__(self, addr_width, data_width=16):
        super().__init__({
            'req':  Out(MemoryRequest(addr_width, data_width)),
            'resp': In(MemoryResponse(data_width)),
        })
