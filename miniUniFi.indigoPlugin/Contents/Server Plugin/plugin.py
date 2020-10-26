#! /usr/bin/env python
# -*- coding: utf-8 -*-
####################

import time
import requests
import logging
import json

from datetime import datetime

requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)

# Indigo really doesn't like dicts with keys that start with a number or symbol...
def safeKey(key):
    if not key[0].isalpha():
        return u'sk{}'.format(key.strip())
    else:
        return unicode(key.strip())
        
def dict_to_states(prefix, the_dict, states_list):
     for key in the_dict:
        if isinstance(the_dict[key], list):
            list_to_states(u"{}{}_".format(prefix, key), the_dict[key], states_list)
        elif isinstance(the_dict[key], dict):
            dict_to_states(u"{}{}_".format(prefix, key), the_dict[key], states_list)
        elif the_dict[key]:
            states_list.append({'key': unicode(safeKey(key.strip())), 'value': the_dict[key]})

def list_to_states(prefix, the_list, states_list):
     for i in range(len(the_list)):
        if isinstance(the_list[i], list):
            list_to_states(u"{}{}_".format(prefix, i), the_list[i], states_list)
        elif isinstance(the_list[i], dict):
            dict_to_states(u"{}{}_".format(prefix, i), the_list[i], states_list)
        else:
            states_list.append({'key': safeKey(unicode(i)), 'value': the_list[i]})
   

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
        
        self.updateFrequency = float(self.pluginPrefs.get('updateFrequency', "60"))
        if self.updateFrequency < 30.0: 
            self.updateFrequency = 30.0
        self.logger.debug(u"updateFrequency = {}".format(self.updateFrequency))
        self.next_update = time.time()

        self.unifi_controllers = {}             # dict of controller info dicts keyed by DeviceID.  
        self.unifi_clients = {}                 # dict of device state definitions keyed by DeviceID.
        self.unifi_devices = {}                 # dict of device state definitions keyed by DeviceID.
        self.update_needed = False
        self.last_controller = None
        self.last_site = u'default'
        self.controller_sessions = {}           # save session object to stay logged in
        self.controller_cookies = {}           # save session object to stay logged in
        
        
       
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
                    
                    self.logger.debug(u"Updating UniFi Clients")
                    for clientID in self.unifi_clients:
                        self.updateUniFiClient(indigo.devices[clientID])
                        
                    # now update all the UniFi devices  

                    self.logger.debug(u"Updating UniFi Devices")
                    for deviceID in self.unifi_devices:
                        self.updateUniFiDevice(indigo.devices[deviceID])
                        
                self.sleep(1.0)

        except self.StopThread:
            pass

    def deviceStartComm(self, device):
            
        self.logger.info(u"{}: Starting Device".format(device.name))

        if device.deviceTypeId == 'unifiController':
            self.unifi_controllers[device.id] = {'name': device.name}   # all the associated data added during update
            self.controller_sessions[device.id] = None
            self.update_needed = True
            if not self.last_controller:
                self.last_controller = unicode(device.id)
            
        elif device.deviceTypeId in ['unifiClient', 'unifiWirelessClient']:
            self.unifi_clients[device.id] = None                        # discovered states for the device
            self.update_needed = True
                
        elif device.deviceTypeId == 'unifiDevice':
            self.unifi_devices[device.id] = None                        # discovered states for the device
            self.update_needed = True

    def deviceStopComm(self, device):

        self.logger.info(u"{}: Stopping Device".format(device.name))

        if device.deviceTypeId == 'unifiController':
            del self.unifi_controllers[device.id]
            del self.controller_sessions[device.id]

        elif device.deviceTypeId in ['unifiClient', 'unifiWirelessClient']:
            del self.unifi_clients[device.id]

        elif device.deviceTypeId == 'unifiDevice':
            del self.unifi_devices[device.id]

    def validateDeviceConfigUi(self, valuesDict, typeId, devId):

        if not len(valuesDict.get('port', None)):       # no port specified
            if valuesDict['controllerType'] == "UDMPro":
                valuesDict['port'] = 443
            else:
                valuesDict['port'] = 8443

        return (True, valuesDict)

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
                self.updateFrequency = float(valuesDict[u"updateFrequency"])
                if self.updateFrequency < 30.0: 
                    self.updateFrequency = 30.0
            except:
                self.updateFrequency = 60.0


    ########################################
    #
    # Data Retrieval methods
    #
    ########################################

    def is_unifi_os(self, device):
        '''
        check for Unifi OS controller eg UDM, UDM Pro.
        HEAD request will return 200 id Unifi OS,
        if this is a Standard controller, we will get 302 (redirect) to /manage
        '''
        ssl_verify = device.pluginProps.get('ssl_verify', False)
        if ssl_verify is False:
            # Disable insecure warnings - our server doesn't have root certs
            from requests.packages.urllib3.exceptions import InsecureRequestWarning
            requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
    
        r = requests.head('https://{}:{}'.format(device.pluginProps['address'], device.pluginProps['port']), verify=ssl_verify, timeout=10.0)
        if r.status_code == 200:
            self.logger.debug('{}: Unifi OS controller detected'.format(device.name))
            return True
        if r.status_code == 302:
            self.logger.debug('{}: Unifi Standard controller detected'.format(device.name))
            return False
        self.logger.warning('{}: Unable to determine controller type - using Unifi Standard controller'.format(device.name))
        return False
        

    def updateUniFiController(self, device):
    
        self.logger.debug(u"{}: Updating controller".format(device.name))
                
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        login_headers = {"Accept": "application/json", "Content-Type": "application/json", "referer": "/login"}
        base_url = "https://{}:{}/".format(device.pluginProps['address'], device.pluginProps['port'])
        login_body = { "username": device.pluginProps['username'], "password": device.pluginProps['password'], 'strict': True}
        ssl_verify = device.pluginProps.get('ssl_verify', False)

        # set up URL templates based on controller type
        
        if device.pluginProps['controllerType'] == "cloudKey":
            login_url = "{}api/login"
            sites_url = "{}api/self/sites"     
            active_url = "{}api/s/{}/stat/sta"
            device_url = "{}api/s/{}/stat/device"
                                     
        elif device.pluginProps['controllerType'] == "UDM":
            login_url = "{}api/login"
            sites_url = "{}api/self/sites"     
            active_url = "{}api/s/{}/stat/sta"
            device_url = "{}api/s/{}/stat/device"
        
        elif device.pluginProps['controllerType'] == "UDMPro":
            login_url = "{}api/auth/login"
            sites_url = "{}proxy/network/api/self/sites"     
            active_url = "{}proxy/network/api/s/{}/stat/sta"
            device_url = "{}proxy/network/api/s/{}/stat/device"
        
        else:
            self.logger.error(u"UniFi Unknown Controller Type Error: {}".format(device.pluginProps['controllerType']))
            return
                
        ws_cookies = self.controller_cookies.get(device.id, None)
        session = self.controller_sessions.get(device.id, None)
        if not session:
            self.logger.debug(u"{}: UniFi Controller Login".format(device.name))
        
            try:
                response = requests.head(base_url, verify=ssl_verify, timeout=10.0)
            except Exception as err:
                self.logger.error(u"UniFi Controller Test Connection Error: {}".format(err))
            self.logger.debug(u"UniFi Controller Test Status: {}".format(response.status_code))
            self.logger.debug(u"UniFi Controller Test URL: {}".format(response.url))
            self.logger.debug(u"UniFi Controller Test Headers: {}".format(response.headers))
            self.logger.debug(u"UniFi Controller Test Cookies: {}".format(requests.utils.dict_from_cookiejar(response.cookies)))
            self.logger.debug(u"UniFi Controller Test Content: {}".format(response.text))

            unifi_os = self.is_unifi_os(device)

            session = requests.Session()
            self.controller_sessions[device.id] = session
            
            # login
            
            try:
                url = login_url.format(base_url)
                response = session.post(url, headers=login_headers, json=login_body, verify=ssl_verify, timeout=10.0)
            except Exception as err:
                self.logger.error(u"UniFi Controller Login Connection Error: {}".format(err))
                device.updateStateOnServer(key='status', value="Connection Error")
                device.updateStateImageOnServer(indigo.kStateImageSel.SensorTripped)
                return
            
            cookies = requests.utils.dict_from_cookiejar(session.cookies)
            if unifi_os:
                ws_cookies = {"TOKEN": cookies.get('TOKEN')}
            else:
                ws_cookies = {"unifises": cookies.get('unifises'), "csrf_token": cookies.get('csrf_token')}
            self.controller_cookies[device.id] = ws_cookies
            
            self.logger.debug(u"UniFi Controller Login Status: {}".format(response.status_code))
            self.logger.debug(u"UniFi Controller Login URL: {}".format(response.url))
            self.logger.debug(u"UniFi Controller Login Headers: {}".format(response.headers))
            self.logger.debug(u"UniFi Controller Login Cookies: {}".format(cookies))
            self.logger.debug(u"UniFi Controller Login Content: {}".format(response.text))
                
            if response.status_code != requests.codes.ok:
                device.updateStateOnServer(key='status', value="Login Error")
                device.updateStateImageOnServer(indigo.kStateImageSel.SensorTripped)
                return

            device.updateStateOnServer(key='status', value="Login OK")
            device.updateStateImageOnServer(indigo.kStateImageSel.SensorOn)
            api_data = response.json()      
        
        # Get Sites

        self.logger.debug(u"{}: UniFi Controller Getting Sites".format(device.name))

        url = sites_url.format(base_url)
        response = session.get(url, headers=headers, cookies=ws_cookies, verify=ssl_verify)
        if not response.status_code == requests.codes.ok:
            self.logger.error(u"UniFi Controller Get Sites Error: {}".format(response.status_code))
            device.updateStateOnServer(key='status', value="Login Error")
            device.updateStateImageOnServer(indigo.kStateImageSel.SensorTripped)
            return

        self.logger.debug(u"UniFi Controller Sites Status: {}".format(response.status_code))
        self.logger.debug(u"UniFi Controller Sites URL: {}".format(response.url))
        self.logger.debug(u"UniFi Controller Sites Headers: {}".format(response.headers))
        self.logger.debug(u"UniFi Controller Sites Cookies: {}".format(requests.utils.dict_from_cookiejar(response.cookies)))

        api_data = response.json()     
        self.logger.threaddebug(u"Sites response =\n{}".format(json.dumps(api_data, indent=4, sort_keys=True)))

        siteList = api_data['data']
        sites = {}
        for site in siteList:
            self.logger.threaddebug(u"Saving Site {} ({})".format(site['name'], site['desc']))
            sites[site['name']] = {'description': site['desc']}
                    
            # Get active Clients for site

            url = active_url.format(base_url, site['name'])
            response = session.get(url, headers=headers, cookies=ws_cookies, verify=ssl_verify)
            if not response.status_code == requests.codes.ok:
                self.logger.error(u"UniFi Controller Get Active Clients Error: {}".format(response.status_code))
                device.updateStateOnServer(key='status', value="Login Error")
                device.updateStateImageOnServer(indigo.kStateImageSel.SensorTripped)
                return

            api_data = response.json()
            self.logger.threaddebug(u"Active clients response =\n{}".format(json.dumps(api_data, indent=4, sort_keys=True)))

            responseList = api_data['data']
            actives = {}
            for client in responseList:
                name = client.get('name', client.get('hostname', '--none--'))
                ip = client.get('ip', "--unknown--")
                mac = client.get('mac', "--unknown--")
                wired = "Wired" if client['is_wired'] else "Wireless"
                self.logger.threaddebug(u"Saving {} Active Client {} - {} ({})".format(wired, name, ip, mac))
                actives[mac] = client
            sites[site['name']]['actives'] = actives
            
           
            # Get UniFi Devices for the site

            url = device_url.format(base_url, site['name'])
            response = session.get(url, headers=headers, cookies=ws_cookies, verify=ssl_verify)
            if not response.status_code == requests.codes.ok:
                self.logger.error("UniFi Controller Get Devices Error: {}".format(response.status_code))
            response.raise_for_status()

            api_data = response.json()
            self.logger.threaddebug("Devices response =\n{}".format(json.dumps(api_data, indent=4, sort_keys=True)))

            responseList = api_data['data']
            uDevices = {}            
            for uDevice in responseList:
                name = uDevice.get('name', uDevice.get('hostname', '--none--'))
                ip = uDevice.get('ip', "--unknown--")
                mac = uDevice.get('mac', "--unknown--")
                self.logger.threaddebug(u"Saving UniFi device {} - {} ({})".format(name, ip, mac))
                uDevices[mac] = uDevice
            sites[site['name']]['devices'] = uDevices

        # all done, save the data
        
        self.unifi_controllers[device.id]['sites'] = sites


    def updateUniFiClient(self, device):
    
        self.logger.threaddebug(u"{}: Updating UniFi Client: {}".format(device.name, device.address))

        controller = int(device.pluginProps['unifi_controller'])        
        site = device.pluginProps['unifi_site']        
        uClient = device.address
        offline = False
        client_data = {}
        
        try:
            client_data = self.unifi_controllers[controller]['sites'][site]['actives'][uClient]
        except:
            self.logger.debug(u"{}: client_data not found".format(device.name))
            offline = True
        
        if not offline:
            self.logger.threaddebug(u"client_data =\n{}".format(json.dumps(client_data, indent=4, sort_keys=True)))
        
            states_list = []
            if client_data:
                dict_to_states(u"", client_data, states_list)      
    
            self.unifi_clients[device.id] = states_list
            device.stateListOrDisplayStateIdChanged()

            try:     
                device.updateStatesOnServer(states_list)
            except TypeError as err:
                self.logger.error(u"{}: invalid state type in states_list: {}".format(device.name, states_list))   
        
        
        if device.deviceTypeId == "unifiClient":
            if offline:
                self.logger.debug(u"{}: Offline".format(device.name))
                device.updateStateOnServer(key="onOffState", value=False, uiValue=u"Offline")
                device.updateStateImageOnServer(indigo.kStateImageSel.SensorTripped)
            
            else:
                self.logger.debug(u"{}: Online".format(device.name))
                device.updateStateOnServer(key="onOffState", value=True, uiValue=u"Online")
                device.updateStateImageOnServer(indigo.kStateImageSel.SensorOn)
            
        elif device.deviceTypeId == "unifiWirelessClient":
            essid = client_data.get('essid', None)
            if offline or not essid:
                device.updateStateOnServer(key="onOffState", value=False, uiValue=u"Offline")
                device.updateStateImageOnServer(indigo.kStateImageSel.SensorTripped)
                last_seen = device.states.get('last_seen', None) 
                if last_seen:
                    offline_seconds = (datetime.now() - datetime.fromtimestamp(last_seen)).total_seconds()
                    minutes, seconds = divmod(offline_seconds, 60)
                    hours, minutes = divmod(minutes, 60)
                    status = u"Offline {:02}:{:02}:{:02}".format(int(hours), int(minutes), int(seconds))
                else:
                    status = "Offline"
                device.updateStateOnServer(key="onOffState", value=False, uiValue=status)
                device.updateStateOnServer(key='offline_seconds', value=offline_seconds)
                device.updateStateImageOnServer(indigo.kStateImageSel.SensorTripped)
                self.logger.debug(u"{}: {}".format(device.name, status))

            else:
                self.logger.debug(u"{}: Online @ {}".format(device.name, essid))
                device.updateStateOnServer(key="onOffState", value=True, uiValue=u"Online @ {}".format(essid))
                device.updateStateImageOnServer(indigo.kStateImageSel.SensorOn)
        
        else:
            self.logger.debug(u"{}: Unknown Device Type: {}".format(device.name, device.deviceTypeId))


    def updateUniFiDevice(self, device):
    
        self.logger.debug("{}: Updating UniFi Device: {}".format(device.name, device.address))
        
        controller = int(device.pluginProps['unifi_controller'])        
        site = device.pluginProps['unifi_site']        
        uDevice = device.address
        
        try:
            device_data = self.unifi_controllers[controller]['sites'][site]['devices'][uDevice]
        except:
            self.logger.debug(u"{}: device_data not found".format(device.name))
            device.updateStateOnServer(key="onOffState", value=False, uiValue=u"Offline")
            device.updateStateImageOnServer(indigo.kStateImageSel.SensorTripped)
            return
        
        self.logger.threaddebug(u"device_data =\n{}".format(json.dumps(device_data, indent=4, sort_keys=True)))
        
        states_list = []
        if device_data:
            dict_to_states(u"", device_data, states_list)      
    
        self.unifi_devices[device.id] = states_list
        device.stateListOrDisplayStateIdChanged()

        try:     
            device.updateStatesOnServer(states_list)
        except TypeError as err:
            self.logger.error(u"{}: invalid state type in states_list: {}".format(device.name, states_list))   
        
        self.logger.debug(u"{}: Online".format(device.name))
        device.updateStateOnServer(key="onOffState", value=True, uiValue=u"Online")
        device.updateStateImageOnServer(indigo.kStateImageSel.SensorOn)
            

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

        elif device.id in self.unifi_devices and self.unifi_devices[device.id]:
            
            for item in self.unifi_devices[device.id]:
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
            (devID, indigo.devices[devID].name)
            for devID in self.unifi_controllers
        ]
        controller_list.sort(key=lambda tup: tup[1])
        self.logger.threaddebug(u"get_controller_list: controller_list = {}".format(controller_list))
        return controller_list
        
    def get_site_list(self, filter="", valuesDict=None, typeId="", targetId=0):
        self.logger.debug(u"get_site_list: typeId = {}, targetId = {}, valuesDict = {}".format(typeId, targetId, valuesDict))
        
        try:
            controller = self.unifi_controllers[int( valuesDict["unifi_controller"])]
        except:
            self.logger.debug(u"get_site_list: controller not found, returning empty list")
            return []
        
        self.logger.debug(u"get_site_list: using controller {}".format(controller['name']))
        self.last_controller = valuesDict["unifi_controller"]
        
        site_list = [
            (name, controller['sites'][name]['description'])
            for name in controller['sites']
        ]

        site_list.sort(key=lambda tup: tup[1])
        self.logger.threaddebug(u"get_site_list: site_list = {}".format(site_list))
        return site_list
        
    def get_client_list(self, filter="", valuesDict=None, typeId="", targetId=0):
        self.logger.debug(u"get_client_list: typeId = {}, targetId = {}, filter = {}, valuesDict = {}".format(typeId, targetId, filter, valuesDict))

        try:
            site = self.unifi_controllers[int(valuesDict["unifi_controller"])]['sites'][valuesDict["unifi_site"]]
        except:
            self.logger.debug(u"get_client_list: no site specified, returning empty list")
            return []

        self.logger.debug(u"get_client_list: using site {} - {}".format(valuesDict["unifi_site"], site['description']))
        self.last_site = valuesDict["unifi_site"]

        wired = (filter == "Wired")
        client_list = [
            (mac, data.get('name', data.get('hostname', '--none--')))
           for mac, data in site['actives'].iteritems() if data.get('is_wired', False) == wired
        ]
        client_list.sort(key=lambda tup: tup[1])
                             
        if targetId:
            try:
                dev = indigo.devices[targetId]
                client_list.insert(0, (dev.pluginProps["address"], dev.name))
            except:
                pass
                                            
        self.logger.threaddebug(u"get_client_list: client_list for {} ({}) = {}".format(typeId, filter, client_list))
        return client_list     

    def get_device_list(self, filter="", valuesDict=None, typeId="", targetId=0):
        self.logger.debug(u"get_client_list: typeId = {}, targetId = {}, filter = {}, valuesDict = {}".format(typeId, targetId, filter, valuesDict))

        try:
            site = self.unifi_controllers[int(valuesDict["unifi_controller"])]['sites'][valuesDict["unifi_site"]]
        except:
            self.logger.debug(u"get_device_list: no site specified, returning empty list")
            return []

        self.logger.debug(u"get_device_list: using site {} - {}".format(valuesDict["unifi_site"], site['description']))
        self.last_site = valuesDict["unifi_site"]

        device_list = [
            (mac, data.get('name', data.get('hostname', '--none--')))
           for mac, data in site['devices'].iteritems()
        ]
        device_list.sort(key=lambda tup: tup[1])

        if targetId:
            try:
                dev = indigo.devices[targetId]
                device_list.insert(0, (dev.pluginProps["address"], dev.name))
            except:
                pass
                                                                                            
        self.logger.threaddebug(u"get_device_list: device_list for {} ({}) = {}".format(typeId, filter, device_list))
        return device_list     

    # doesn't do anything, just needed to force other menus to dynamically refresh
    def menuChanged(self, valuesDict = None, typeId = None, devId = None):
        return valuesDict

    ########################################
                        
    def getDeviceConfigUiValues(self, pluginProps, typeId, devId):
        valuesDict = indigo.Dict(pluginProps)
        errorsDict = indigo.Dict()
        self.logger.debug("getDeviceConfigUiValues: devId = {}, typeId = {}, pluginProps =\n{}".format(devId, typeId, pluginProps))

        if typeId == 'unifiController':
            pass
                    
        else:
            valuesDict["unifi_controller"] = self.last_controller
            valuesDict["unifi_site"] = self.last_site
        
        return (valuesDict, errorsDict)



