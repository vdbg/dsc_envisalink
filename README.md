# dsc_envisalink

Brute-force installer-code finder for [DSC](https://www.dsc.com/) alarm panels accessible through an **EnvisaLink** Ethernet interface.

**NOTE:** this script finds the **installer** code, *not* the master code. The installer code is much more powerful, and allows (among other things) to change the master code.

## What it does

The script iterates through all 4-digit installer codes (0000 to 9999) until finding one that successfully enters installer mode. Note: this can take several days if the installer code is a high number.

Failed codes are persisted to a file so that a run can be resumed where it left off in case the script is interrupted (e.g., machine reboot, manual stop through `Ctrl-C`).

## Requirements

* Python 3.10+ installed.
* An EnvisaLink card (EVL-3 / EVL-4) connected to the DSC panel and reachable over TCP/IP.
* Knowledge of the IP address of the EnvisaLink card. The router can typically give this information.
* Knowledge of the password to get to EnvisaLink web UI. The board can be factory reset if unknown (default password is `user` on some models); videos of the process are available on Youtube. Note: the login appears to always be `user`

With `192.168.1.XXX` the IP address of the EnvisaLink board, you should able to log in http://192.168.1.XXX/ with the login and password and access the Web UI.

## Quick start


```bash
python main.py --host 192.168.1.XXX
```

This will create `fail.txt` in the current directory and start from code `0000`.  
Subsequent runs will skip codes already recorded in `fail.txt`.

The script stops when it finds the installer code.

### Customized run

```bash
python main.py --host 10.0.0.50 --port 4026 --password custom_passwd --fail-file progress.txt
```

## Stopping & Resuming

Press `Ctrl-C` at any time to stop. The next run will automatically skip every code already logged in the fail file.

If invoking the script on subsequent runs, make sure to invoke it with the same parameters that were used previously, or delete the fail file.

