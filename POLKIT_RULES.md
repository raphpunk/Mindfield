Polkit rule for allowing non-root Bluetooth power toggling

Purpose
- Allow local (or group-restricted) users to toggle Bluetooth power via BlueZ DBus without requiring sudo.

Why this is recommended
- The GUI now uses BlueZ via DBus (preferred) and falls back to `rfkill` when DBus is unavailable or denied.
- Installing a small polkit rule is a one-time admin step that avoids adding per-user sudoers entries and provides a secure, auditable policy.

Install (one-time, run as root)
1. Create the polkit rule file:

   ```sh
   sudo tee /etc/polkit-1/rules.d/50-org.bluez.allow-power-toggle.rules > /dev/null <<'RULE'
   // Allow local users in the 'bluetooth' group to toggle BlueZ adapter power
   polkit.addRule(function(action, subject) {
       if (action.id.indexOf('org.bluez') == 0 && subject.isInGroup && subject.isInGroup('bluetooth')) {
           return polkit.Result.YES;
       }
   });
   RULE
   ```

2. Add users who should be allowed to the `bluetooth` group (example for user `legion`):

   ```sh
   sudo groupadd -f bluetooth
   sudo usermod -aG bluetooth legion
   ```

3. No reboot is usually required; the polkit rule will be picked up automatically for new sessions. Users may need to log out/in for group changes to take effect.

Simple (less-restrictive) alternative
- If you trust the environment and want to allow all local users to control BlueZ, use this rule instead (less restrictive):

```sh
sudo tee /etc/polkit-1/rules.d/50-org.bluez.allow-local.rules > /dev/null <<'RULE'
polkit.addRule(function(action, subject) {
    if (action.id.indexOf('org.bluez') == 0 && subject.local) {
        return polkit.Result.YES;
    }
});
RULE
```

Notes and security
- Prefer the group-based rule for multi-user systems: it limits permission to only users in the `bluetooth` group.
- These rules affect who may control BlueZ (power, adapter settings). Only install them on trusted machines where non-admin users should be allowed to toggle Bluetooth.
- To revoke access, remove the rule file from `/etc/polkit-1/rules.d/`.

Troubleshooting
- If DBus toggling still seems denied, check syslog/journal for polkit/bluez messages:

```sh
journalctl -u bluetooth -f
journalctl --since "5 minutes ago" | grep polkit
```

- If DBus isn't installed or `dbus-python` is missing, the GUI will fall back to the `rfkill` approach (which may require sudo). To enable DBus support in Python:

```sh
sudo apt-get install python3-dbus
# or pip install dbus-python (may require system headers)
```

Appendix: What the GUI now does
- Preferred: uses BlueZ DBus (`org.bluez.Adapter1` Powered property) to toggle adapter power; this respects polkit and works without sudo when polkit rules allow it.
- Fallback: runs `rfkill block/unblock bluetooth` if DBus is missing or toggling is denied.

If you'd like, I can create an automated helper script that an admin can run to install the recommended polkit rule and add a list of users to the `bluetooth` group.