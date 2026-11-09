#!/usr/bin/env python3
"""Prompt locally and save the read-only Klaviyo key in Windows Credential Manager."""
from getpass import getpass
import keyring

key = getpass("Paste the read-only Klaviyo private API key (hidden): ").strip()
if not key:
    raise SystemExit("No key entered")
keyring.set_password("selectblinds-b2b-dashboard", "klaviyo-readonly", key)
print("Key saved for this Windows account. No key was written to the repository.")
print("Setup is complete. Do not paste the key again.")
while getpass("Press Enter to close this window (input stays hidden): "):
    print("No more input is needed. Press Enter to close.")
