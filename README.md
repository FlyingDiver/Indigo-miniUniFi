# Indigo-miniUniFi
Minimalistic UniFi plugin for Indigo

Allows for monitoring the on/off-line state of network clients on a UniFi based system.  Primary use is presence detection using mobile devices.

1. Create a UniFi Controller device, configured with host and login information.
2. Create a Client device, specifying the controller, site managed by that controller, and device (by name).
3. Create triggers based on the on/off status of the client device.
