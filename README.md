**Stackette** is an experimental 16-bit CPU architecture. It is stack-based,
but oriented more toward procedural languages like C or Pascal rather than Forth.
In this sense, it probably resembles Burroughs B6500, HP 3000, or Tandem/16.

Stackette instruction set is designed to combine one of four stack primitives
(TOS, NOS, PUSH/POP, and TUCK/NIP) with access to constants, procedure
parameters, local variables, indexed memory, and special-purpose registers —
all with one 16-bit instruction word. Memory protection and multitasking are
supported with real-time microkernel OSes in mind. Read more:
[Programming Model and ISA](docs/ISA.md).

This Repository contains a design for SoC with a CPU, obligatory MMU,
and a minimal set of peripheral devices: serial port, timer, SDRAM controller,
and an SPI Flash controller. All currently implemented features takes up to
11000 LUTs.

The SoC is implemented for the
[Terasic DE0-Nano](https://www.terasic.com.tw/cgi-bin/page/archive.pl?No=593)
board (with some impromptu hardware additions) in
[Amaranth HDL](https://amaranth-lang.org/) with
[Quartus](https://www.altera.com/products/software-tools/quartus-prime)
toolchain.

### Building

You need a decently modern Python, probably 3.10 or later.

```shell
$> pip install -r requirements.txt
$> python cpu.py
```

`flashtool` folder contains necessary tools to write binaries to an MX25L8005
or compatible SPI Flash memory. Alternatively, you can use any capable ROM
programmer.

Consult the [docs](docs) on how to connect the peripherals to the board.

### Testing

```shell
$> pytest -n auto --dist worksteal .
```

### The Current State

The [ISA](docs/ISA.md) is 99% stabilized. Everything works in hardware, tests are good.
The CPU has a reasonable integer performance of 1902 dhrystones/s, on par with
10MHz IBM PC/AT.

Additional goodies:
- an LLM-produced [port of ACK](https://gitlab.com/jock_tanner/ack)
  compiler with custom, overlay-capable linker and other stuff (in Python),
- an LLM-produced [port of Minix 2](https://gitlab.com/jock_tanner/minix)
  with networking and multi-user capabilities. A lot of software is untested though.

### Future Plans

- Get the compiler to self-host.
- Add virtual memory support.
- Add hardware floating point device.
- Add hardware support for modern cryptography. 
- Ramp up the usability of the system: port additional software, smooth some rough
  edges in terms of speed and capabilities as a general purpose system.

Being unemployable in my current field (software engineer, backend & full-stack web
development), I applied on a low-rank position in woodworking recently, so the progress
might be quite slow.
