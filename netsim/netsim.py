#!/usr/bin/python3.10

import sys
import os
import time
import logging
import argparse
try:
	from csee_4119_abr_project.common.util import check_output, check_both, run_bg, strip_comments
	from csee_4119_abr_project.netsim.apache_setup import configure_apache, reset_apache, restart_apache, is_apache_configured
	from csee_4119_abr_project.netsim.tc_setup import TC_Wrapper
except ModuleNotFoundError:
	sys.path.append("..")
	from common.util import check_output, check_both, run_bg, strip_comments
	from apache_setup import configure_apache, reset_apache, restart_apache, is_apache_configured
	from tc_setup import TC_Wrapper
# click
CLICK_CONF = 'autogen.click'
CLICK = '/usr/local/bin/click'
CLICK_LOCAL = '~/click/userlevel/click'

class Netsim():
	def __init__(self, args):
		self.args = args

	def get_tc_default_args(self):
		# Default args passed to the TC_Wrapper class
		args = type("", (), {})()
		args.ip_pair = None
		args.bandwidth = '1000mbit'
		args.interface = 'lo'
		args.latency = '0ms'
		args.traffic_class = 0
		return args

	def is_click_running(self):
		return 'click' in check_both('ps -e | grep click | grep -v grep'\
			, shouldPrint=False, check=False)[0][0]

	# Return True if a qdisc is already attached to the specified interface
	def is_tc_configured(self):
		args = self.get_tc_default_args()
		args.command = 'show'
		tcw = TC_Wrapper(args)
		return 'htb' in tcw.show()

	def network_running(self):
		return is_apache_configured()\
			or self.is_click_running()\
			or self.is_tc_configured()

	def get_topo_file(self,suffix):
		if suffix == 'events' and self.args.events:
			return self.args.events

		if self.args.topology[-1] == '/':
			self.args.topology = self.args.topology[0:-1]
		topo_name = os.path.basename(self.args.topology)
		filepath = os.path.join(self.args.topology, '%s.%s' % (topo_name, suffix))
		if not os.path.isfile(filepath):
			logging.getLogger(__name__).error('Could not find %s' % filepath)
			exit(-1)
		return filepath

	def get_server_ip_list(self):
		ip_list = []
		with open(self.get_topo_file('servers'), 'r') as serversfile:
			for line in strip_comments(serversfile):
				ip_list.append(line.strip())
		return ip_list

	# move func to tc_setup? util?
	def bw_to_kbps(self,bw):
		# TODO: make this more robust
		if 'kbit' in bw:
			return bw.split('kbit')[0]
		elif 'mbit' in bw:
			return str(int(bw.split('mbit')[0]) * 1000)
		elif 'kbps' in bw:
			return str(int(bw.split('kbps')[0]) * 8)
		elif 'mbps' in bw:
			return str(int(bw.split('mbps')[0]) * 1000 * 8)
		elif 'bps' in bw:
			return str(float(bw.split('bps')[0]) / 1000 * 8)
		else:
			return bw

	# move func to tc_setup? util?
	def lat_to_ms(self,lat):
		# TODO: make this more robust
		if 'msecs' in lat:
			return lat.split('msecs')[0]
		elif 'msec' in lat:
			return lat.split('msec')[0]
		elif 'ms' in lat:
			return lat.split('ms')[0]
		elif 'secs' in lat:
			return str(int(lat.split('secs')[0]) * 1000)
		elif 'sec' in lat:
			return str(int(lat.split('sec')[0]) * 1000)
		elif 's' in lat:
			return str(int(lat.split('s')[0]) * 1000)
		else:
			return lat

	def autogen_click_conf(self,servers_file, clients_file, dns_file):
		logging.getLogger(__name__).debug('Autogenerating %s from %s and %s'\
			% (CLICK_CONF, servers_file, clients_file))
		with open(CLICK_CONF, 'w') as clickfile:
			clickfile.write('// This file is autogenerated. Do not hand edit.\n\n')
			with open(servers_file, 'r') as serversfile:
				for line in strip_comments(serversfile):
					clickfile.write('KernelTun(%s/8) -> Discard;\n' % (line.strip()))
			with open(clients_file, 'r') as clientsfile:
				for line in strip_comments(clientsfile):
					clickfile.write('KernelTun(%s/8) -> Discard;\n' % (line.strip()))
			with open(dns_file, 'r') as dnsfile:
				for line in strip_comments(dnsfile):
					clickfile.write('KernelTun(%s/8) -> Discard;\n' % (line.strip()))

	def install_filters(self,links_file):
		with open(links_file, 'r') as linksf:
			for line in strip_comments(linksf):
				elems = line.split(' ')
				args = self.get_tc_default_args()
				args.command = 'update'
				args.ip_pair = [elems[0], elems[2]]
				args.traffic_class = int(elems[1].split('link')[1])
				tcw = TC_Wrapper(args)
				tcw.update()
				# check_output('%s update %s %s -c %i'\
				# 	% (TC_SETUP, elems[0], elems[2], int(elems[1].split('link')[1])))

	def execute_event(self,event):
		logging.getLogger(__name__).info('Updating link:  %s' % ' '.join(event))
		try:
			args = self.get_tc_default_args()
			args.command = 'update'
			args.traffic_class = int(event[1].split('link')[1])
			args.bandwidth = event[2]
			args.latency = event[3]
			logging.getLogger(__name__).info(event)
			tcw = TC_Wrapper(args)
			tcw.update()
			# check_output('%s update -c %i -b %s -l %s'\
			# 	% (TC_SETUP, int(event[1].split('link')[1]), event[2], event[3]))
			
			if self.args.log:
				with open(self.args.log, 'a') as logfile:
					logfile.write('%f %s %s %s %s\n' % (time.time(), event[0], event[1], self.bw_to_kbps(event[2]), self.lat_to_ms(event[3])))
				logfile.closed
		except Exception as e:
			logging.getLogger(__name__).error(e)

	def run_events(self):
		# Remove existing log file
		if self.args.log and os.path.isfile(self.args.log):
			os.remove(self.args.log)

		# Read event list
		events = []
		with open(self.get_topo_file('events'), 'r') as eventsf:
			for line in strip_comments(eventsf):
				events.append(line.split(' '))

		# Run events
		logging.getLogger(__name__).info('Running link events...')
		for event in events:
			# decide when to execute this event
			if event[0] == '*':
				input('Press enter to run event:  %s' % ' '.join(event))
			else:
				try:
					time.sleep(float(event[0]))
				except:
					logging.getLogger(__name__).warning('Skipping invalid event: %s' % ' '.join(event))
					continue
			self.execute_event(event)
		logging.getLogger(__name__).info('Done running events.')

	def checkstopnetsim(self):
		if self.network_running():
			logging.getLogger(__name__).info('Stopping netsim...')
			self.stop_network()

	def start_network(self):
		logging.getLogger(__name__).info('Starting simulated network...')

		# Set up traffic shaping
		logging.getLogger(__name__).info('Enabling traffic shaping...')
		try:
			args = self.get_tc_default_args()
			args.command = 'start'
			tcw = TC_Wrapper(args)
			tcw.start()			
			# check_output('%s start' % TC_SETUP)
			self.install_filters(self.get_topo_file('bottlenecks'))
		except Exception as e:
			import traceback
			print(traceback.print_exc())
			logging.getLogger(__name__).error(e)

		# Launch apache instances
		logging.getLogger(__name__).info('Configuring apache...')
		try:
			configure_apache(self.get_server_ip_list())
			restart_apache()
		except Exception as e:
			logging.getLogger(__name__).error(e)
			import traceback
			traceback.print_exc()


		logging.getLogger(__name__).info('Network started.')

	def stop_network(self):
		logging.getLogger(__name__).info('Stopping simulated network...')

		# stop apache instances
		logging.getLogger(__name__).info('Stopping apache...')
		try:
			reset_apache(self.get_server_ip_list())
			restart_apache()
		except Exception as e:
			logging.getLogger(__name__).error(e)

		# Stop traffic shaping
		logging.getLogger(__name__).info('Disabling traffic shaping...')
		try:
			args = self.get_tc_default_args()
			args.command = 'stop'
			tcw = TC_Wrapper(args)
			tcw.stop()
			# check_output('%s stop' % TC_SETUP)
		except Exception as e:
			logging.getLogger(__name__).error(e)

		# Destroy fake NICs
		logging.getLogger(__name__).info('Destroying network interfaces...')
		try:
			check_both('killall -9 click', shouldPrint=False)
			time.sleep(1)
		except:
			pass
		# Remove autogen file
		if os.path.exists(CLICK_CONF):
			check_output('rm {}'.format(CLICK_CONF), shouldPrint=False)

		logging.getLogger(__name__).info('Network stopped.')
	
	def buildclick(self):
		self.autogen_click_conf(self.get_topo_file('servers'), self.get_topo_file('clients'), self.get_topo_file('dns'))


def main(args):
	ns = Netsim(args)
	if args.command == 'start':
		ns.start_network()
	elif args.command == 'run':
		ns.run_events()
	elif args.command == 'stop':
		ns.stop_network()
	elif args.command == 'restart':
		ns.stop_network()
		ns.start_network()
	elif args.command == 'checkstopnetsim':
		ns.checkstopnetsim()
	elif args.command == 'buildclick':
		ns.buildclick()

if __name__ == "__main__":
	# set up command line args
	parser = argparse.ArgumentParser(description='Launch a simulated network.')
	parser.add_argument('topology', help='directory containing the topology files (topo.clients, topo.servers, topo.bottlenecks, topo.events, where topo is the name of the topology)')
	parser.add_argument('command', choices=['start','stop','restart','run','checkstopnetsim','buildclick'], 
		help='start/stop/restart the network, or run a series of link events?')
	parser.add_argument('-l', '--log', default=None, help='log file for logging events (overwrites file if it already exists)')
	parser.add_argument('-e', '--events', default=None, help='specify a custom events file to use in place of the one contained in the topology directory')
	parser.add_argument('-q', '--quiet', action='store_true', default=False, help='only print errors')
	parser.add_argument('-v', '--verbose', action='store_true', default=False, help='print debug info. --quiet wins if both are present')
	args = parser.parse_args()
	
	# set up logging
	if args.quiet:
		level = logging.WARNING
	elif args.verbose:
		level = logging.DEBUG
	else:
		level = logging.INFO
	logging.basicConfig(
		format = "%(levelname) -10s %(asctime)s %(module)s:%(lineno) -7s %(message)s",
		level = level
	)

	main(args)
