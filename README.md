# Indigo-miniUniFi
Minimalistic UniFi plugin for Indigo

Allows for monitoring the on/off-line state of network clients on a UniFi based system.  Primary use is presence detection using mobile devices.

| Requirement            |                     |
|------------------------|---------------------|
| Minimum Indigo Version | 2022.1              |
| Python Library (API)   | Unofficial          |
| Requires Local Network | Yes                 |
| Requires Internet      | No                  |
| Hardware Interface     | None                |

1. Create a "UniFi Controller" device, configured with host and login information for your UniFi controller.  This can be a software controller, a cloud key, or a UniFi Dream Machine (or Pro).
2. Create a "UniFi Client" device for network clients you want to monitor, specifying the controller, site managed by that controller, and device (by name).
3. Create a "UniFi Device" device if you want o monitor status of UniFi equipment (APs, switches, gateways, etc).
3. Create triggers based on the on/off status of the client device.  The Wireless Client devices also have an "offline_seconds" state that can be used for delayed triggering.

Does not work with controllers that have 2FA enabled.
