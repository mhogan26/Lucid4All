import json, pickle, dpkt
from scapy.all import *
import subprocess

# helpers
def i2Hex (n):
    hstr = str(hex(int(n)))
    if "0x" in hstr:
        if len(hstr[2:]) == 1: return "0" + hstr[2:].upper()
        else: return hstr[2:].upper()
    else:
        return hstr.upper()

def hexadecimal (ip_addr):
    # 0xC00002EB ( Hexadecimal )
    return "0x" + "".join( map( i2Hex,  ip_addr.split(".") ) )

class Opt:
    def __init__(self, pktpcap):
        self.ground_truth = 0
        self.pkts = pktpcap
        self.events = []
        self.flows = []

    # gen traffic (but don't write json yet, do that in init_iteration)
    def gen_traffic(self):
        #flows = []
        for trace in self.pkts:
            pcap = dpkt.pcap.Reader(open(trace,'rb'))
            for ts, buf in pcap:
                try:
                    #'''
                    #caida parsing
                    #eth = dpkt.ethernet.Ethernet(buf)
                    ip = dpkt.ip.IP(buf)
                    src_uint = struct.unpack("!I", ip.src)[0]
                    dst_uint = struct.unpack("!I", ip.dst)[0]
                    if src_uint not in self.flows:
                        self.flows.append(src_uint)
                    args = [src_uint, dst_uint, ip.len, ip.tos]
                    '''
                    # univ parsing
                    eth = dpkt.ethernet.Ethernet(buf)
                    if type(eth.data) != dpkt.ip.IP:
                        continue
                    src_uint = struct.unpack("!I", eth.ip.src)[0]
                    dst_uint = struct.unpack("!I", eth.ip.dst)[0]
                    if src_uint not in self.flows:
                        self.flows.append(src_uint)
                    args = [src_uint, dst_uint, eth.ip.len, eth.ip.tos]
                    '''
                    p = {"name":"ip_in", "args":args}
                    self.events.append(p)
                    # testing, caida trace 5000000 pkts
                    # training, univ1_pt1 trace 20000 pkts
                    # training 2, all of univ1_pt1
                    #if len(self.events) > 5000:
                    if len(self.events) >= 5000000:
                        break
                    #if len(self.events)%500000 == 0:
                    #    print(len(self.events))
                except dpkt.dpkt.UnpackError:
                    pass
        '''
        for trace in self.pkts:
            with PcapReader(trace) as pcap_reader:
                for pkt in pcap_reader:
                    if not (pkt.haslayer(IP)):
                        continue
                    src_int = int(hexadecimal(pkt[IP].src),0)
                    dst_int = int(hexadecimal(pkt[IP].dst),0)
                    if src_int not in self.flows:
                        self.flows.append(src_int)
                    pktlen = pkt[IP].len
                    tos = pkt[IP].tos
                    args = [src_int, dst_int, pktlen, tos]
                    p = {"name":"ip_in", "args":args}
                    self.events.append(p)
                    # testing, caida trace 10000000 pkts
                    # training, univ trace 20000 pkts
                    #if len(self.events) > 20000:
                    if len(self.events) >= 10000000:
                        break
        '''
        self.ground_truth = len(self.events)
        #print(len(flows), "FLOWS")

    # our measurement is total number of cache evictions/flushes
    def calc_cost(self,measure):
        ratio = measure[0]/self.ground_truth
        '''
        if ratio < 0.4: # all sols that produce eviction ratio less than thresh are equally as good (not considering resources)
            ratio = 0
        '''
        print("ratio", ratio)
        print("total pkts", self.ground_truth)
        print("total flows", len(self.flows)) 
        return ratio

    # we need free block events before we send data traffic to init long cache
    def init_iteration(self, symbs): # all sols that produce eviction ratio less than thresh are equally as good (not considering resources):
        info = {}
        info["switches"] = 1
        info["max time"] = 99999999999999
        info["default input gap"] = 800
        info["random seed"] = 0
        info["python file"] = "starflow.py"

        fb_events = []
        for i in range(1,symbs["L_SLOTS"]):
            fb_events.append({"name":"free_block","args":[i,0]})

        #info["events"] = fb_events
        info["events"] = fb_events+self.events

        with open('starflow.json','w') as f:
            json.dump(info, f, indent=4)


#o = Opt("univ1_pt1.pcap")
#o.gen_traffic()
#s_o = {"max_short_idx": 1, "num_long": 1, "log_s_slots": 16, "log_l_slots": 16, "num_short": 8, "S_SLOTS": 65536,"L_SLOTS": 65536,"dummy_var": 3}
#o.init_iteration(s_o)

'''
o = Opt(["pcap"])
#s_t = {'max_short_idx': 7, 'num_long': 8, 'num_short': 8, 'S_SLOTS': 65536, 'L_SLOTS': 16384}
o.init_iteration(s_t)
cmd = ["make", "interp"]
ret = subprocess.run(cmd)

measurement = []
outfiles = ["total.pkl"]
for out in outfiles:
    measurement.append(pickle.load(open(out,"rb")))
o.calc_cost(measurement)
'''


