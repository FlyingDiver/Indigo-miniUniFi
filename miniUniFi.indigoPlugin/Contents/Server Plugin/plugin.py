#! /usr/bin/env python
# -*- coding: utf-8 -*-
####################

import time
import requests
import logging
import json

requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)

def dict_to_states(prefix, the_dict, states_list):
     for key in the_dict:
        if isinstance(the_dict[key], list):
            list_to_states(u"{}{}_".format(prefix, key), the_dict[key], states_list)
        elif isinstance(the_dict[key], dict):
            dict_to_states(u"{}{}_".format(prefix, key), the_dict[key], states_list)
        elif the_dict[key]:
            states_list.append({'key': unicode(prefix + key.strip()), 'value': the_dict[key]})

def list_to_states(prefix, the_list, states_list):
     for i in range(len(the_list)):
        if isinstance(the_list[i], list):
            list_to_states(u"{}{}_".format(prefix, i), the_list[i], states_list)
        elif isinstance(the_list[i], dict):
            dict_to_states(u"{}{}_".format(prefix, i), the_list[i], states_list)
        else:
            states_list.append({'key': unicode(prefix + unicode(i)), 'value': the_list[i]})
   

################################################################################
class Plugin(indigo.PluginBase):

    ########################################
    # Main Plugin methods
    ########################################
    def __init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs):
        indigo.PluginBase.__init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs)

        pfmt = logging.Formatter('%(asctime)s.%(msecs)03d\t[%(levelname)8s] %(name)20s.%(funcName)-25s%(msg)s', datefmt='%Y-%m-%d %H:%M:%S')
        self.plugin_file_handler.setFormatter(pfmt)

        try:
            self.logLevel = int(self.pluginPrefs[u"logLevel"])
        except:
            self.logLevel = logging.INFO
        self.indigo_log_handler.setLevel(self.logLevel)


    def startup(self):
        self.logger.info(u"Starting miniUniFi")
        
        self.updateFrequency = float(self.pluginPrefs.get('updateFrequency', "1")) * 60.0
        self.logger.debug(u"updateFrequency = {}".format(self.updateFrequency))
        self.next_update = time.time()

        self.unifi_controllers = {}     # dict of controller info dicts keyed by DeviceID.  
        self.unifi_devices = {}      
        self.unifi_clients = {}        

       
    def shutdown(self):
        self.logger.info(u"Shutting down miniUniFi")


    def runConcurrentThread(self):
        self.logger.debug(u"Starting runConcurrentThread")

        try:
            while True:

                if time.time() > self.next_update:
                    self.next_update = time.time() + self.updateFrequency
                    
                    # update from UniFi Controllers
                    
                    for controllerID in self.unifi_controllers:
                        self.updateUniFiController(indigo.devices[controllerID])

                    # now update all the Indigo devices and clients     
                    
                    for deviceID in self.unifi_devices:
                        self.updateUniFiDevice(indigo.devices[deviceID])
                        
                    for clientID in self.unifi_clients:
                        self.updateUniFiClient(indigo.devices[clientID])

                self.sleep(1.0)

        except self.StopThread:
            pass

    def deviceStartComm(self, device):
            
        self.logger.info(u"{}: Starting Device".format(device.name))

        if device.deviceTypeId == 'unifiController':
            self.unifi_controllers[device.id] = {'name': device.name}   # all the associated data added during update
            
        elif device.deviceTypeId == 'unifiDevice':
            self.unifi_devices[device.id] = device.address

        elif device.deviceTypeId == 'unifiClient':
            self.unifi_clients[device.id] = device.address

        
    def deviceStopComm(self, device):

        self.logger.info(u"{}: Stopping Device".format(device.name))

        if device.deviceTypeId == 'unifiController':
            del self.unifi_controllers[device.id]

        elif device.deviceTypeId == 'unifiDevice':
            del self.unifi_devices[device.id]

        elif device.deviceTypeId == 'unifiClient':
            del self.unifi_clients[device.id]



    ########################################
    # PluginConfig methods
    ########################################

    def closedPrefsConfigUi(self, valuesDict, userCancelled):
        if not userCancelled:
            try:
                self.logLevel = int(valuesDict[u"logLevel"])
            except:
                self.logLevel = logging.INFO
            self.indigo_log_handler.setLevel(self.logLevel)

            try:
                self.updateFrequency = float(valuesDict[u"updateFrequency"]) * 60.0
            except:
                self.updateFrequency = 60.0


    ########################################
    # Data Retrieval methods
    ########################################

    def updateUniFiController(self, device):
    
        self.logger.debug("{}: Updating controller".format(device.name))
        
        with requests.Session() as session:
        
            headers = {"Accept": "application/json", "Content-Type": "application/json"}
            base_url = "https://{}:{}".format(device.pluginProps['address'], device.pluginProps['port'])
            login_body = { "username": device.pluginProps['username'], "password": device.pluginProps['password']}

            # login
    
            url = "{}/api/login".format(base_url)
            response = session.post(url, headers=headers, json=login_body, verify=False)
            if not response.status_code == requests.codes.ok:
                self.logger.error("UniFi Controller Login Error: {}".format(response.status_code))
                device.updateStateOnServer(key='status', value="Login Error")
                return

            device.updateStateOnServer(key='status', value="Login OK")
            api_data = response.json()      
            self.logger.threaddebug("Login response =\n{}".format(json.dumps(api_data, indent=4, sort_keys=True)))
        
            # Get Sites
    
            url = "{}/api/self/sites".format(base_url)
            response = session.get(url, headers=headers, verify=False)
            if not response.status_code == requests.codes.ok:
                self.logger.error("UniFi Controller Get Sites Error: {}".format(response.status_code))
            response.raise_for_status()

            api_data = response.json()     
            self.logger.threaddebug("Sites response =\n{}".format(json.dumps(api_data, indent=4, sort_keys=True)))

            siteList = api_data['data']
            sites = {}
            for site in siteList:
                self.logger.threaddebug("Saving Site {} ({})".format(site['name'], site['desc']))
                sites[site['name']] = {'description': site['desc']}
                
                # Get UniFi Devices for the site
    
                url = "{}/api/s/{}/stat/device".format(base_url, site['name'])
                response = session.get(url, headers=headers, verify=False)
                if not response.status_code == requests.codes.ok:
                    self.logger.error("UniFi Controller Get Devices Error: {}".format(response.status_code))
                response.raise_for_status()

                api_data = response.json()
                self.logger.threaddebug("Devices response =\n{}".format(json.dumps(api_data, indent=4, sort_keys=True)))

                responseList = api_data['data']
                uDevices = {}            
                for uDevice in responseList:
                    self.logger.threaddebug("Saving UniFi device {} - {} ({})".format(uDevice['name'], uDevice['ip'], uDevice['mac']))
                    uDevices[uDevice['mac']] = uDevice
                sites[site['name']]['devices'] = uDevices
        
                # Get active Clients for site
    
                url = "{}/api/s/{}/stat/sta".format(base_url, site['name'])
                response = session.get(url, headers=headers, verify=False)
                if not response.status_code == requests.codes.ok:
                    self.logger.error("UniFi Controller Get Active Clients Error: {}".format(response.status_code))
                response.raise_for_status()

                api_data = response.json()
                self.logger.threaddebug("Active clients response =\n{}".format(json.dumps(api_data, indent=4, sort_keys=True)))

                responseList = api_data['data']
                actives = {}
                for client in responseList:
                    name = client.get('name', client.get('hostname', '--none--'))
                    ip = client.get('ip', "--unknown--")
                    mac = client.get('mac', "--unknown--")
                    wired = "Wired" if client['is_wired'] else "Wireless"
                    self.logger.threaddebug("Saving {} Active Client {} - {} ({})".format(wired, name, ip, mac))
                    actives[client['mac']] = client
                sites[site['name']]['actives'] = actives
    
                # Get Inactive Clients for site
    
                url = "{}/api/s/{}/rest/user".format(base_url, site['name'])
                response = session.get(url, headers=headers, verify=False)
                if not response.status_code == requests.codes.ok:
                    self.logger.error("UniFi Controller Get All Clients Error: {}".format(response.status_code))
                response.raise_for_status()

                api_data = response.json()
                self.logger.threaddebug("Known clients response =\n{}".format(json.dumps(api_data, indent=4, sort_keys=True)))

                responseList = api_data['data']
                clients = {}
                for client in responseList:
                    name = client.get('name', client.get('hostname', '--none--'))
                    mac = client.get('mac', "--unknown--")
                    wired = "Wired" if client['is_wired'] else "Wireless"
                    if client['mac'] not in actives:
                        self.logger.threaddebug("Saving {} Inactive Client {} ({})".format(wired, name, mac))
                        clients[client['mac']] = client
                sites[site['name']]['inactives'] = clients
            
        # all done, save the data
        
        self.unifi_controllers[device.id]['sites'] = sites


    def updateUniFiDevice(self, device):
    
        self.logger.debug("{}: Updating UniFi Device: {}".format(device.name, device.address))
        
        controller = int(device.pluginProps['unifi_controller'])        
        site = device.pluginProps['unifi_site']        
        uDevice = device.address
        
        device_data = self.unifi_controllers[controller]['sites'][site]['devices'][uDevice]
        self.logger.threaddebug("{}".format(json.dumps(device_data, indent=4, sort_keys=True)))
        

    def updateUniFiClient(self, device):
    
        self.logger.debug("{}: Updating UniFi Client: {}".format(device.name, device.address))

        controller = int(device.pluginProps['unifi_controller'])        
        site = device.pluginProps['unifi_site']        
        uClient = device.address
        
        client_data = self.unifi_controllers[controller]['sites'][site]['actives'][uClient]
        self.logger.debug("client_data =\n{}".format(json.dumps(client_data, indent=4, sort_keys=True)))
        
        stateList = [
            {'key': 'hostname', 'value': client_data.get("hostname", None)},
            {'key': 'ip',       'value': client_data.get("ip", None)},
            {'key': 'is_wired', 'value': client_data.get("is_wired", None)},
        ]
        device.updateStatesOnServer(stateList)
        
        
    ########################################
    #
    # device UI methods
    #
    ########################################

    def get_controller_list(self, filter="", valuesDict=None, typeId="", targetId=0):
        self.logger.debug("get_controller_list: typeId = {}, targetId = {}, valuesDict = {}".format(typeId, targetId, valuesDict))
        controller_list = [
            (addr, info['name'])
            for addr, info in self.unifi_controllers.iteritems()
        ]
        self.logger.threaddebug("get_controller_list: controller_list = {}".format(controller_list))
        controller_list.sort(key=lambda tup: tup[1])
        return controller_list
        
    def get_site_list(self, filter="", valuesDict=None, typeId="", targetId=0):
        self.logger.debug("get_site_list: typeId = {}, targetId = {}, valuesDict = {}".format(typeId, targetId, valuesDict))
        
        try:
            controller = self.unifi_controllers[int( valuesDict["unifi_controller"])]
        except:
            self.logger.debug("get_site_list: controller not found, returning empty list")
            return []
        
        self.logger.debug("get_site_list: using controller {}".format(controller['name']))
        site_list = [
            (name, controller['sites'][name]['description'])
            for name in controller['sites']
        ]

        self.logger.threaddebug("get_site_list: site_list = {}".format(site_list))
        site_list.sort(key=lambda tup: tup[1])
        return site_list
        
    def get_device_list(self, filter="", valuesDict=None, typeId="", targetId=0):
        self.logger.debug("get_device_list: typeId = {}, targetId = {}, filter = {}, valuesDict = {}".format(typeId, targetId, filter, valuesDict))

        try:
            site = self.unifi_controllers[int(valuesDict["unifi_controller"])]['sites'][valuesDict["unifi_site"]]
        except:
            self.logger.debug("get_device_list: no site specified, returning empty list")
            return []

        self.logger.debug("get_device_list: using site {} - {}".format(valuesDict["unifi_site"], site['description']))

        device_list = [
            (mac, data.get('name', data.get('hostname', '--none--')))
           for mac, data in site['devices'].iteritems()
        ]
                                                
        self.logger.threaddebug("get_device_list: device_list for {} ({}) = {}".format(typeId, filter, device_list))
        device_list.sort(key=lambda tup: tup[1])
        return device_list     

    def get_client_list(self, filter="", valuesDict=None, typeId="", targetId=0):
        self.logger.debug("get_client_list: typeId = {}, targetId = {}, filter = {}, valuesDict = {}".format(typeId, targetId, filter, valuesDict))

        try:
            site = self.unifi_controllers[int(valuesDict["unifi_controller"])]['sites'][valuesDict["unifi_site"]]
        except:
            self.logger.debug("get_client_list: no site specified, returning empty list")
            return []

        self.logger.debug("get_client_list: using site {} - {}".format(valuesDict["unifi_site"], site['description']))

        client_list = [
            (mac, data.get('name', data.get('hostname', '--none--')))
           for mac, data in site['actives'].iteritems()
        ]
                                                
        self.logger.threaddebug("get_client_list: device_list for {} ({}) = {}".format(typeId, filter, client_list))
        client_list.sort(key=lambda tup: tup[1])
        return client_list     

    # doesn't do anything, just needed to force other menus to dynamically refresh
    def menuChanged(self, valuesDict = None, typeId = None, devId = None):
        return valuesDict



