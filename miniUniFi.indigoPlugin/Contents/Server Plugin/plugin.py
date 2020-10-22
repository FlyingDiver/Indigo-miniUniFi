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
    #
    # Main Plugin methods
    #
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

        self.unifi_controllers = {}             # dict of controller info dicts keyed by DeviceID.  
        self.unifi_clients = {}                 # dict of device state definitions keyed by DeviceID.
        self.unifi_wireless_clients = {}        # dict of device state definitions keyed by DeviceID.
        self.update_needed = False

        indigo.devices.subscribeToChanges()

       
    def shutdown(self):
        self.logger.info(u"Shutting down miniUniFi")


    def runConcurrentThread(self):
        self.logger.debug(u"Starting runConcurrentThread")

        try:
            while True:

                if (time.time() > self.next_update) or self.update_needed:
                    self.next_update = time.time() + self.updateFrequency
                    self.update_needed = False
                    
                    # update from UniFi Controllers
                    
                    for controllerID in self.unifi_controllers:
                        self.updateUniFiController(indigo.devices[controllerID])

                    # now update all the client devices  
                    
                    for clientID in self.unifi_clients:
                        self.updateUniFiClient(indigo.devices[clientID])
                    for clientID in self.unifi_wireless_clients:
                        self.updateUniFiClient(indigo.devices[clientID])

                self.sleep(1.0)

        except self.StopThread:
            pass

    def deviceStartComm(self, device):
            
        self.logger.info(u"{}: Starting Device".format(device.name))

        if device.deviceTypeId == 'unifiController':
            self.unifi_controllers[device.id] = {'name': device.name}   # all the associated data added during update
            self.update_needed = True
            
        elif device.deviceTypeId == 'unifiClient':
            self.unifi_clients[device.id] = None        # discovered states for the device
            self.update_needed = True
        
        elif device.deviceTypeId == 'unifiWirelessClient':
            self.unifi_wireless_clients[device.id] = None        # discovered states for the device
            self.update_needed = True
        
    def deviceStopComm(self, device):

        self.logger.info(u"{}: Stopping Device".format(device.name))

        if device.deviceTypeId == 'unifiController':
            del self.unifi_controllers[device.id]

        elif device.deviceTypeId == 'unifiClient':
            del self.unifi_clients[device.id]
            
        elif device.deviceTypeId == 'unifiWirelessClient':
            del self.unifi_wireless_clients[device.id]
            
            
    def deviceUpdated(self, oldDevice, newDevice):
        indigo.PluginBase.deviceUpdated(self, oldDevice, newDevice)

        if oldDevice.id in self.unifi_clients and (oldDevice.states != newDevice.states):
            oldStates = set(oldDevice.states)
            newStates = set(newDevice.states)
            diffStates = oldStates ^ newStates
            if len(diffStates):
                self.logger.debug("{}: deviceUpdated".format(oldDevice.name)) 
                for state in diffStates:
                    self.logger.debug("\t{}: {} -> {}".format(state, oldDevice.states.get(state, None), newDevice.states.get(state, None)))


    ########################################
    #
    # PluginConfig methods
    #
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
    #
    # Data Retrieval methods
    #
    ########################################

    def updateUniFiController(self, device):
    
        self.logger.debug("{}: Updating controller".format(device.name))
        
        with requests.Session() as session:
        
            headers = {"Accept": "application/json", "Content-Type": "application/json"}
            base_url = "https://{}:{}".format(device.pluginProps['address'], device.pluginProps['port'])
            login_body = { "username": device.pluginProps['username'], "password": device.pluginProps['password']}

            # set up URL templates based on controller type
            
            if device.pluginProps['controllerType'] == "cloudKey":
                login_url = "{}/api/login"
                sites_url = "{}/api/self/sites"     
                active_url = "{}/api/s/{}/stat/sta"
                
                                         
            elif device.pluginProps['controllerType'] == "UDM":
                login_url = "{}/api/login"
                sites_url = "{}/api/self/sites"     
                active_url = "{}/api/s/{}/stat/sta"
            
            elif device.pluginProps['controllerType'] == "UDMPro":
                login_url = "{}/proxy/network/api/auth/login"
                sites_url = "{}/proxy/network//api/self/sites"     
                active_url = "{}/proxy/network/api/s/{}/stat/sta"
            
            else:
                self.logger.error("UniFi Unknown Controller Type Error: {}".format(device.pluginProps['controllerType']))
                return
                
            # login
    
            url = login_url.format(base_url)
            response = session.post(url, headers=headers, json=login_body, verify=False)
            if not response.status_code == requests.codes.ok:
                self.logger.error("UniFi Controller Login Error: {}".format(response.status_code))
                device.updateStateOnServer(key='status', value="Login Error")
                device.updateStateImageOnServer(indigo.kStateImageSel.SensorTripped)
                return

            device.updateStateOnServer(key='status', value="Login OK")
            device.updateStateImageOnServer(indigo.kStateImageSel.SensorOn)
            api_data = response.json()      
            self.logger.threaddebug("Login response =\n{}".format(json.dumps(api_data, indent=4, sort_keys=True)))
        
            # Get Sites
    
            url = sites_url.format(base_url)
            response = session.get(url, headers=headers, verify=False)
            if not response.status_code == requests.codes.ok:
                self.logger.error("UniFi Controller Get Sites Error: {}".format(response.status_code))
                device.updateStateOnServer(key='status', value="Login Error")
                device.updateStateImageOnServer(indigo.kStateImageSel.SensorTripped)
                return

            api_data = response.json()     
            self.logger.threaddebug("Sites response =\n{}".format(json.dumps(api_data, indent=4, sort_keys=True)))

            siteList = api_data['data']
            sites = {}
            for site in siteList:
                self.logger.threaddebug("Saving Site {} ({})".format(site['name'], site['desc']))
                sites[site['name']] = {'description': site['desc']}
                        
                # Get active Clients for site
    
                url = active_url.format(base_url, site['name'])
                response = session.get(url, headers=headers, verify=False)
                if not response.status_code == requests.codes.ok:
                    self.logger.error("UniFi Controller Get Active Clients Error: {}".format(response.status_code))
                    device.updateStateOnServer(key='status', value="Login Error")
                    device.updateStateImageOnServer(indigo.kStateImageSel.SensorTripped)
                    return

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
                
        # all done, save the data
        
        self.unifi_controllers[device.id]['sites'] = sites


    def updateUniFiClient(self, device):
    
        self.logger.debug("{}: Updating UniFi Client: {}".format(device.name, device.address))

        controller = int(device.pluginProps['unifi_controller'])        
        site = device.pluginProps['unifi_site']        
        uClient = device.address
        
        try:
            client_data = self.unifi_controllers[controller]['sites'][site]['actives'][uClient]
        except:
            self.logger.debug("{}: client_data not found".format(device.name))
            device.updateStateOnServer(key="onOffState", value=False, uiValue=u"Offline")
            device.updateStateImageOnServer(indigo.kStateImageSel.SensorTripped)
            return
        
        self.logger.threaddebug("client_data =\n{}".format(json.dumps(client_data, indent=4, sort_keys=True)))
        
        states_list = []
        if client_data:
            dict_to_states(u"c_", client_data, states_list)      
    
        self.unifi_clients[device.id] = states_list
        device.stateListOrDisplayStateIdChanged()

        try:     
            device.updateStatesOnServer(states_list)
        except TypeError as err:
            self.logger.error(u"{}: invalid state type in states_list: {}".format(device.name, states_list))   
        
        if device.deviceTypeId == "unifiClient":
            self.logger.debug("{}: Online".format(device.name))
            device.updateStateOnServer(key="onOffState", value=True, uiValue=u"Online")
            device.updateStateImageOnServer(indigo.kStateImageSel.SensorOn)
            
        elif device.deviceTypeId == "unifiWirelessClient":
            essid = client_data.get('essid', None)
            if essid:
                self.logger.debug("{}: Online @ {}".format(device.name, essid))
                device.updateStateOnServer(key="onOffState", value=True, uiValue=u"Online @ {}".format(essid))
                device.updateStateImageOnServer(indigo.kStateImageSel.SensorOn)
            else:
                self.logger.debug("{}: Offline".format(device.name))
                device.updateStateOnServer(key="onOffState", value=False, uiValue=u"Offline")
                device.updateStateImageOnServer(indigo.kStateImageSel.SensorTripped)
        
        else:
            self.logger.debug("{}: Unknown Device Type: {}".format(device.name, device.deviceTypeId))


    ################################################################################
    #
    # callback for state list changes, called from stateListOrDisplayStateIdChanged()
    #
    ################################################################################
    
    def getDeviceStateList(self, device):
        state_list = indigo.PluginBase.getDeviceStateList(self, device)
        self.logger.threaddebug(u"{}: getDeviceStateList, base state_list = {}".format(device.name, state_list))

        if device.id in self.unifi_clients and self.unifi_clients[device.id]:
            
            for item in self.unifi_clients[device.id]:
                key = item['key']
                value = item['value']
                if isinstance(value, bool):
                    dynamic_state = self.getDeviceStateDictForBoolTrueFalseType(unicode(key), unicode(key), unicode(key))
                    self.logger.threaddebug(u"{}: getDeviceStateList, adding Bool state {}, value {}".format(device.name, key, value))
                elif isinstance(value, (float, int)):
                    dynamic_state = self.getDeviceStateDictForNumberType(unicode(key), unicode(key), unicode(key))
                    self.logger.threaddebug(u"{}: getDeviceStateList, adding Number state {}, value {}".format(device.name, key, value))
                elif isinstance(value, (str, unicode)):
                    dynamic_state = self.getDeviceStateDictForStringType(unicode(key), unicode(key), unicode(key))
                    self.logger.threaddebug(u"{}: getDeviceStateList, adding String state {}, value {}".format(device.name, key, value))
                else:
                    self.logger.debug(u"{}: getDeviceStateList, unknown type for key = {}, value {}".format(device.name, key, value))
                    continue
                    
                state_list.append(dynamic_state)

        self.logger.threaddebug(u"{}: getDeviceStateList, final state_list = {}".format(device.name, state_list))
        return state_list

        
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
        
    def get_client_list(self, filter="", valuesDict=None, typeId="", targetId=0):
        self.logger.debug("get_client_list: typeId = {}, targetId = {}, filter = {}, valuesDict = {}".format(typeId, targetId, filter, valuesDict))

        try:
            site = self.unifi_controllers[int(valuesDict["unifi_controller"])]['sites'][valuesDict["unifi_site"]]
        except:
            self.logger.debug("get_client_list: no site specified, returning empty list")
            return []

        self.logger.debug("get_client_list: using site {} - {}".format(valuesDict["unifi_site"], site['description']))

        wired = (filter == "Wired")
        client_list = [
            (mac, data.get('name', data.get('hostname', '--none--')))
           for mac, data in site['actives'].iteritems() if data.get('is_wired', False) == wired
        ]
                                                
        self.logger.threaddebug("get_client_list: device_list for {} ({}) = {}".format(typeId, filter, client_list))
        client_list.sort(key=lambda tup: tup[1])
        return client_list     

    # doesn't do anything, just needed to force other menus to dynamically refresh
    def menuChanged(self, valuesDict = None, typeId = None, devId = None):
        return valuesDict


