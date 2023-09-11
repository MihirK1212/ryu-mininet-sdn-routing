from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet
from ryu.lib.packet import ethernet, ipv4
from ryu.lib.packet import ether_types
from ryu.topology import event, switches
from ryu.topology.api import get_switch, get_link, get_all_host
from ryu.lib import hub

import time
import random
import json

import routing.routing as routing
from utils import *

from ryu.app.wsgi import ControllerBase, WSGIApplication, route
from ryu.lib import dpid as dpid_lib
from webob import Request, Response

simple_switch_instance_name = 'simple_switch_api_app'



class SimpleSwitch13(app_manager.RyuApp):

    _CONTEXTS = {'wsgi': WSGIApplication}
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):

        super(SimpleSwitch13, self).__init__(*args, **kwargs)

        wsgi = kwargs['wsgi']
        wsgi.register(SimpleSwitchController,
                      {simple_switch_instance_name: self})
        
        self.topology_api_app = self
        self.mac_to_port = {}

        self.num_hosts = 0
        self.num_switches = 0

        self.switches = []
        self.hosts = []
        self.edges = []

        self.bandwidth = {}
        self.delay = {}

        self.host_port = {}

        self.datapaths = {}

        self.all_pair_optimal_paths = {}
        self.current_optimal_paths = {}
        self.flows_added = False
        self.num_requests = 0

        clear_log()
        input_data(self)

        
        for (s1,s2), value in self.bandwidth.items():
            bw, delay = self.bandwidth[(s1,s2)], self.delay[(s1,s2)]
            cost = 10*((delay+1)/(bw+1))
            print(f"s{s1}-s{s2}:", cost)




    def add_flow(self, datapath, priority, match, actions, buffer_id=None):

        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS,
                                             actions)]
        
        cookie = random.randint(0, 0xffffffffffffffff)

        if buffer_id:
            mod = parser.OFPFlowMod(datapath=datapath, buffer_id=buffer_id,
                                    priority=priority, match=match,
                                    instructions=inst, cookie=cookie, cookie_mask=0xffffffffffffffff)
        else:
            mod = parser.OFPFlowMod(datapath=datapath, priority=priority,
                                    match=match, instructions=inst, cookie=cookie, cookie_mask=0xffffffffffffffff)
        datapath.send_msg(mod)

    def clear_flows(self):

        for dpid in self.switches:

            datapath = self.datapaths[dpid]
                        
            ofproto = datapath.ofproto
            parser = datapath.ofproto_parser
            match = parser.OFPMatch(eth_type=0x0800)
            mod = parser.OFPFlowMod(
                datapath=datapath,
                command=ofproto.OFPFC_DELETE,
                out_port=ofproto.OFPP_ANY,
                out_group=ofproto.OFPG_ANY,
                match=match
            )
            datapath.send_msg(mod)

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def flow_stats_reply_handler(self, ev):
        flows = []
        print("\n Switch = ",ev.msg.datapath.id)
        for stat in ev.msg.body:
            flows.append({
                "match": stat.match,
                "actions": stat.instructions[0].actions
            })
        print("Flows: {}".format(flows))
        print("\n")

    def show_flows(self):
        for dpid in self.switches:
            datapath = self.datapaths[dpid]
            parser = datapath.ofproto_parser
            req = parser.OFPFlowStatsRequest(datapath)
            datapath.send_msg(req)

    def add_path_flows(self, optimal_paths, service_type):

        try:

            self.flows_added = False
            self.clear_flows()
        
            for (h1, h2), path in optimal_paths.items():

                src = self.hosts[h1-1][service_type]
                dst = self.hosts[h2-1][service_type]

                for i in range(len(path)):

                    (dpid, in_port, out_port) = path[i]

                    datapath = self.datapaths[dpid]
                    
                    parser = datapath.ofproto_parser

                    if service_type == "MAC":
                        match = parser.OFPMatch(in_port=in_port,eth_type=0x0800,eth_src=src,eth_dst=dst) 

                    elif service_type == "IPV4":
                        match = parser.OFPMatch(in_port=in_port,eth_type=0x0800,ipv4_src=src,ipv4_dst=dst) 
                    
                    
                    actions = [parser.OFPActionOutput(out_port)]

                    self.add_flow(datapath, 1, match, actions)

            self.show_flows()
            self.flows_added = True

        except Exception as E:
            print("add_optimal_flows", E)
            self.flows_added = False
        
    def get_host_num(self, addr, service_type):
        for i in range(len(self.hosts)):
            if self.hosts[i][service_type] == addr:
                return i+1
        raise ValueError("Invalid Host Address")
    
    @set_ev_cls(event.EventSwitchEnter)
    def get_topology_data(self, ev):

        switch_list = get_switch(self.topology_api_app, None)
        self.switches = [switch.dp.id for switch in switch_list]

        edge_list = get_link(self.topology_api_app, None)
        self.edges = [[(link.src.dpid, link.src.port_no), (link.dst.dpid, link.dst.port_no)] for link in edge_list if link.src.dpid < link.dst.dpid]
        
        host_list = get_all_host(self.topology_api_app)

        self.hosts = []

        for host in host_list:
            if not is_valid_host(host):
                continue
            self.hosts.append(get_host_addresses(host))
            self.host_port[host.mac] = (host.port.dpid, host.port.port_no) 
            

        print ("switches ", self.switches)
        print ("links ", self.edges)
        print("hosts ",self.hosts)

        self.switches.sort()
        print("Switches:",end=' ')
        for s in self.switches:
            print(f"s{s}",end=' ')
        print()

        print("Links:")
        for edge in self.edges:
            print(f"s{edge[0][0]}-{edge[0][1]} <----> s{edge[1][0]}-{edge[1][1]}")


        if len(self.switches) == self.num_switches and (not self.flows_added):
            time.sleep(2)

    @set_ev_cls(event.EventHostAdd)
    def _event_host_add_handler(self, ev):
        host = ev.host
        self.logger.info("Host %s joined network on port %d of switch %s", host.mac, host.port.port_no, host.port.dpid)
    
    def get_link_params(self, s1, s2):
        s1,s2 = swap(s1,s2)
        return self.bandwidth[(s1,s2)], self.delay[(s1,s2)]

    def parse_request(self, req):
        src, dst, bw, service_type = req["src"], req["dst"], req["bw"], req["service_type"]
        query = None

        if (src!=-1 and dst!=-1):
            src, dst = self.get_host_num(src, service_type), self.get_host_num(dst, service_type)
            query = [src, dst, bw]

        return query, service_type


    def add_optimal_paths(self, req, optimal_paths):

        query, service_type = self.parse_request(req)

        def update_path_bandwidth():
            if query:
                h1, h2, bw = query
                path = optimal_paths[(h1,h2)]
                for i in range(len(path) - 1):
                    s1, s2 = path[i][0], path[i+1][0]
                    s1,s2 = swap(s1,s2)
                    self.bandwidth[(s1,s2)]-=bw
                    if self.bandwidth[(s1,s2)] < 0:
                        raise FloatingPointError("Bandwidth Negative Error")

        update_path_bandwidth()

        if self.num_requests == 0:
            self.all_pair_optimal_paths = optimal_paths

        self.current_optimal_paths = optimal_paths if self.num_requests<=1 else self.current_optimal_paths | optimal_paths

        self.num_requests+=1

        
        self.add_path_flows(self.current_optimal_paths, service_type)


    def get_optimal_paths(self, req):

        try:

            query, service_type = self.parse_request(req)

            f = open("input/graph.txt", "w+")

            if query:
                file_write_line(f, query)
            else:
                file_write_line(f, [-1])

            file_write_line(f, [self.num_hosts, self.num_switches])

            file_write_line(f, [len(self.edges)])

            for edge in self.edges:
                bw, delay = self.get_link_params(edge[0][0],edge[1][0])
                file_write_line(f, [edge[0][0], edge[0][1], edge[1][0], edge[1][1], bw, delay])

            for host, (switch, port) in self.host_port.items():
                file_write_line(f, [self.get_host_num(host, "MAC"), switch, port])

            f.close()

            optimal_paths = routing.find_optimal_paths()
            assert optimal_paths!=-1

            self.add_optimal_paths(req, optimal_paths)
            return optimal_paths

        except Exception as E:
            print("add_optimal_paths", E)
            return -1

        
        
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        self.datapaths[datapath.id] = datapath


        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        
        if not self.flows_added:
            return

        if ev.msg.msg_len < ev.msg.total_len:
            self.logger.debug("packet truncated: only %s of %s bytes",
                              ev.msg.msg_len, ev.msg.total_len)
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']
        dpid = datapath.id

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]        

        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return #ignore lldp packet
    

        dst = eth.dst
        src = eth.src

        # self.logger.info("packet in %s %s %s %s", dpid, src, dst, in_port)

        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][src] = in_port

        if dst in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst]
        else:
            out_port = ofproto.OFPP_FLOOD


        actions = [parser.OFPActionOutput(out_port)]

        data = None
        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data
            

        out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id,
                                  in_port=in_port, actions=actions, data=data)
        datapath.send_msg(out)


class SimpleSwitchController(ControllerBase):

    def __init__(self, req, link, data, **config):
        super(SimpleSwitchController, self).__init__(req, link, data, **config)
        self.simple_switch_app : SimpleSwitch13 = data[simple_switch_instance_name]

    def create_response(self, res):
        body = json.dumps(res)
        return Response(content_type='application/json', body=body, charset='utf-8')

    @route('simpleswitch', '/path_request', methods=['POST'])
    def path_request(self, req, **kwargs):

        app = self.simple_switch_app

        try:
            req = json.loads(req.body)

            if len(list(app.all_pair_optimal_paths.values())) == 0:
                raise AssertionError("topology_undiscovered")
            
            query, service_type = app.parse_request(req)
            optimal_path = str(app.all_pair_optimal_paths[(query[0], query[1])])

            return self.create_response(res = {
                "success": True,
                "message": "Path found successfully",
                "optimal_path": optimal_path
            })
            
        except Exception as E:
            return self.create_response(res = {
                "success": False,
                "message": str(E)
            })
            

    @route('simpleswitch', '/service_request', methods=['POST'])
    def service_request(self, req, **kwargs):

        app = self.simple_switch_app

        try:
            req = json.loads(req.body)

            if len(app.hosts)!=app.num_hosts or len(app.switches)!=app.num_switches or len(app.host_port)!=app.num_hosts:
                print("topology_undiscovered", app.hosts, app.switches, app.host_port)
                raise AssertionError("topology_undiscovered")
            
            optimal_paths = app.get_optimal_paths(req)

            if optimal_paths == -1:
                raise AssertionError("Could not find a path")
                
            else:
                return self.create_response(res = {
                    "success": True,
                    "message": "Path found successfully",
                    "optimal_path": str(list(optimal_paths.values())[0])
                })
        
        
        except Exception as E:
            return self.create_response(res = {
                "success": False,
                "message": str(E)
            })
            

        
        